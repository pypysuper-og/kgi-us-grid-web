from collections import OrderedDict
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time
import uuid

from .brokers.paper import PaperBroker
from .brokers.superpy import SuperPyBroker
from .brokers.order_diagnostics import report_matches
from .calendar import MarketCalendar
from .models import (
    ET,
    TW,
    TERMINAL,
    GridLayout,
    Quote,
    StrategyInput,
    decimal,
    grid_action,
    plan_rows,
    utcnow,
)
from .market import MarketWatch
from .storage import Store, encode
from .recovery import Recovery, compatible
from .audit import Audit, AuditUnavailable, COMMAND, observed, broker_reference


class Core:
    def __init__(self, path: Path, broker=None, calendar=None, audit=None):
        self.audit = audit or Audit(path.parent / "logs")
        self.audit.emit("startup_step", phase="database")
        self.store = Store(path)
        self.store.audit = self.audit
        self.store.recover()
        self.broker = broker or SuperPyBroker(audit=self.audit)
        self.broker.audit = self.audit
        self.market = MarketWatch(self.broker)
        self.market_consumers = {}
        self.live_armed = False
        self.reconnect_enabled = False
        self.reconnect_attempt = 0
        self.reconnect_due = None
        self.reconnect_resume = None
        self.inventory_offsets = {}
        self.session_order_limit = 0
        self.session_order_count = 0
        self.audit.emit("startup_step", phase="market_calendar")
        self.calendar = calendar or MarketCalendar()
        self.generation = 0
        self.owner = None
        self.quotes = {}
        self.quote_warnings = {}
        self.stage = "離線展示就緒"
        self.last_poll = 0.0
        self.books = {}
        self.reconciliation = {}
        self.account_limit = None
        self.fault = None
        self.closing = False
        self.match_observations = {}
        self.recovery = Recovery(self)
        self.audit.emit("startup_step", phase="restore_books")
        # History is append-only. Restore just the last matching book per owner;
        # unrelated snapshot kinds must not replace a saved matching book.
        saved_books = self.store.rows(
            "SELECT s.owner,s.data FROM (SELECT DISTINCT owner FROM broker_snapshots) AS owners "
            "JOIN broker_snapshots AS s ON s.id=(SELECT id FROM broker_snapshots "
            "WHERE owner=owners.owner AND json_type(data,'$.paper_book') IS NOT NULL "
            "ORDER BY id DESC LIMIT 1) ORDER BY s.id"
        )
        for row in saved_books:
            data = json.loads(row["data"])
            self.books[row["owner"]] = PaperBroker(data["paper_book"])
        self.books.setdefault("demo", PaperBroker())
        self.audit.emit("startup_step", phase="books_restored", books_loaded=len(saved_books))

    def paper(self, owner):
        return self.books.setdefault(owner, PaperBroker())

    def save_book(self, owner):
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO broker_snapshots(owner,time,data) VALUES(?,?,?)",
                (owner, utcnow().isoformat(), encode({"paper_book": self.paper(owner).book})),
            )

    def fail(self, sid, reason):
        current = self.store.strategy(sid)
        if (
            current
            and current["status"] == "paused"
            and not current["reconciled"]
            and current["reason"] == reason
        ):
            return
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE strategies SET status='paused',reconciled=0,reason=? WHERE id=?", (reason, sid)
            )
            self.store.event("needs_attention", reason, sid)

    def quote_warning(self, sid, message=None):
        if self.quote_warnings.get(sid) == message:
            return
        if message:
            self.quote_warnings[sid] = message
        else:
            self.quote_warnings.pop(sid, None)
        with self.store.transaction():
            self.store.event(
                "quote_warning" if message else "quote_recovered", message or "行情已恢復，繼續監控", sid
            )
        self.audit.emit("quote_warning" if message else "quote_recovered", strategy_id=sid, message=message)

    def params(self, sid):
        row = self.store.strategy(sid)
        if not row:
            raise ValueError("找不到策略")
        return row, StrategyInput.model_validate(row["params"])

    def handle(self, action, payload):
        if self.closing and action != "shutdown":
            raise ValueError("程式正在結束")
        method = getattr(self, "cmd_" + action, None)
        if method is None:
            raise ValueError("不支援的操作")
        return method(**payload)

    def cmd_preview(self, params, strategy_id=None, revision=None, reset_anchor=False):
        p = StrategyInput.model_validate(params)
        anchor, inventory = p.start_price, p.initial_inventory
        if type(reset_anchor) is not bool:
            raise ValueError("重設網格基準必須明確選擇")
        if strategy_id:
            row, _ = self.params(strategy_id)
            if row["revision"] != revision:
                raise ValueError("策略版本已變更，請重新開啟編輯")
            inventory = self.store.position(strategy_id)["quantity"]
            if not reset_anchor:
                anchor = decimal(row["anchor"])
            if not p.lower_price <= anchor <= p.upper_price:
                raise ValueError("新價格界線不涵蓋目前網格基準；請調整界線或明確重設基準")
        return {
            "rows": plan_rows(p, anchor, inventory),
            "price_gap": str(p.price_gap),
            "anchor": str(anchor),
            "inventory": inventory,
        }

    def cmd_grid_layout(self, draft=False, **values):
        return GridLayout.model_validate(values).calculate(draft=draft)

    def cmd_create(self, params):
        p = StrategyInput.model_validate(params)
        if len([s for s in self.store.strategies() if s["status"] != "archived"]) >= 15:
            raise ValueError("最多 15 組有效策略")
        if p.mode != "demo" and not self.owner:
            raise ValueError("請先登入並選擇帳戶")
        if p.mode != "demo":
            self.broker.contract(p.symbol)
        owner = "demo" if p.mode == "demo" else f"{p.mode}:{self.owner}"
        with self.store.transaction():
            self.store.db.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,?)", (owner, p.mode, owner))
        sid = self.store.add_strategy(p, owner)
        if p.mode != "live":
            self.paper(owner).opening(p.symbol, p.initial_inventory)
            self.save_book(owner)
        return {"id": sid}

    def cmd_edit(self, strategy_id, params, revision, reset_anchor=False):
        row, previous = self.params(strategy_id)
        p = StrategyInput.model_validate(params)
        if type(reset_anchor) is not bool:
            raise ValueError("重設網格基準必須明確選擇")
        if row["status"] == "active" or self.store.pending(strategy_id) or revision != row["revision"]:
            raise ValueError("請先停止並處理在途單，且使用最新策略版本")
        for key in ("symbol", "mode", "initial_inventory", "initial_cost"):
            if getattr(previous, key) != getattr(p, key):
                raise ValueError("商品、模式與期初帳不可覆寫；請另建策略")
        qty = self.store.position(strategy_id)["quantity"]
        if not p.min_inventory <= qty <= p.max_inventory:
            raise ValueError("新界線不涵蓋目前部位")
        anchor = str(p.start_price) if reset_anchor else row["anchor"]
        if not p.lower_price <= decimal(anchor) <= p.upper_price:
            raise ValueError("新價格界線不涵蓋目前網格基準；請調整界線或明確重設基準")
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE strategies SET params=?,revision=revision+1,anchor=?,reason='',reconciled=0 WHERE id=?",
                (p.model_dump_json(), anchor, strategy_id),
            )
            self.store.db.execute(
                "INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                (
                    strategy_id,
                    "strategy_config",
                    encode(
                        {
                            "revision": revision + 1,
                            "params": p.model_dump(mode="json"),
                            "anchor_before": row["anchor"],
                            "anchor_after": anchor,
                            "reset_anchor": reset_anchor,
                            "cycle": row["cycle"],
                        }
                    ),
                    utcnow().isoformat(),
                ),
            )
            self.store.event(
                "strategy_edit",
                f"已更新策略；{'明確重設' if reset_anchor else '保留'}網格基準 {anchor}，請重新對帳",
                strategy_id,
            )
        self.quotes.pop(strategy_id, None)
        return {"id": strategy_id}

    def cmd_archive(self, strategy_id):
        row, _ = self.params(strategy_id)
        if (
            row["status"] == "active"
            or self.store.pending(strategy_id)
            or self.store.position(strategy_id)["quantity"]
        ):
            raise ValueError("有部位、在途单或仍啟動的策略不可封存")
        with self.store.transaction():
            self.store.db.execute("UPDATE strategies SET status='archived' WHERE id=?", (strategy_id,))
            self.store.event("archive", "已封存，歷史仍保留", strategy_id)
        return {"archived": True}

    def cmd_start(self, strategy_id, revision, confirm_live=False):
        if self.fault:
            raise ValueError(self.fault)
        row, p = self.params(strategy_id)
        if row["revision"] != revision or row["status"] == "archived":
            raise ValueError("策略版本已變更或已封存")
        if row["status"] == "active":
            return {"already_active": True}
        if p.end_date < utcnow().astimezone(TW).date():
            raise ValueError("策略已超過台灣停止日期")
        if not row["reconciled"] or any(o["unknown"] for o in self.store.pending(strategy_id)):
            raise ValueError("尚未完成對帳或仍有在途／未知委託")
        if p.mode != "demo" and row["owner"] != f"{p.mode}:{self.owner}":
            raise ValueError("策略與目前帳戶不一致")
        if p.mode != "demo":
            self.broker.contract(p.symbol)
        if p.mode == "live":
            if not confirm_live or not self.live_armed or self.broker.disconnected.is_set():
                raise ValueError("請先在實單控制中明確啟用本次會話")
            if self.account_limit is None:
                raise ValueError("請設定帳戶每日買入上限")
        rid = uuid.uuid4().hex
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO strategy_runs VALUES(?,?,?,?,NULL,NULL)",
                (rid, strategy_id, revision, utcnow().isoformat()),
            )
            self.store.db.execute(
                "UPDATE strategies SET status='active',run_id=?,reason='' WHERE id=?", (rid, strategy_id)
            )
            self.store.event("start", "策略已啟動；等待新行情", strategy_id)
        self.quotes.pop(strategy_id, None)
        return {"run_id": rid}

    def cmd_stop(self, strategy_id, cancel):
        if not isinstance(cancel, bool):
            raise ValueError("請明確選擇保留或撤銷未成交單")
        row, p = self.params(strategy_id)
        if cancel and p.mode == "live" and row["owner"] != f"live:{self.owner}":
            raise ValueError("不可透過其他帳戶撤單；請登入原委託帳戶")
        if self.reconnect_resume is not None:
            self.reconnect_resume["strategies"].pop(strategy_id, None)
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE strategies SET status='paused',reason=? WHERE id=?",
                ("已停止新單；" + ("撤單追蹤中" if cancel else "保留未成交單"), strategy_id),
            )
            self.store.db.execute(
                "UPDATE strategy_runs SET ended_at=?,stop_policy=? WHERE id=?",
                (utcnow().isoformat(), "cancel" if cancel else "keep", row["run_id"]),
            )
            self.store.event(
                "stop", "停止新單，" + ("撤銷本策略未成交單" if cancel else "保留未成交單"), strategy_id
            )
        outcomes = []
        if cancel:
            self.sync_reports(row["owner"], p.mode)
            for order in self.store.pending(strategy_id):
                if p.mode == "live" and not order["broker_org"]:
                    message = "缺少原始委託序號，未送出撤單；請至券商查核"
                    outcomes.append({"order": order["id"], "result": message})
                    self.audit.emit("cancel_not_dispatched", order_id=order["id"], reason=message)
                    continue
                existing = self.store.rows(
                    "SELECT * FROM order_intents WHERE parent_id=? AND kind='cancel'", (order["id"],)
                )
                if existing:
                    outcomes.append({"order": order["id"], "result": "撤單已送過，請查核而非重送"})
                    continue
                cid = uuid.uuid4().hex
                with self.store.transaction():
                    self.store.db.execute(
                        "INSERT INTO order_intents VALUES(?,?,?,?,?,?,?)",
                        (
                            cid,
                            strategy_id,
                            "cancel",
                            order["id"],
                            "cancel:" + order["id"],
                            "dispatching",
                            utcnow().isoformat(),
                        ),
                    )
                try:
                    if p.mode == "live":
                        self.audit.emit("effect_dispatch", kind="cancel", order_id=order["id"], intent_id=cid)
                        self.audit.require_writable()
                        self.broker.cancel(order["broker_org"])
                        self.audit.emit("effect_returned", kind="cancel", order_id=order["id"], intent_id=cid)
                    else:
                        self.paper(row["owner"]).cancel(order["broker_org"])
                        self.save_book(row["owner"])
                    outcomes.append({"order": order["id"], "result": "已要求撤單；以回報為準"})
                except Exception as exc:
                    with self.store.transaction():
                        self.store.db.execute("UPDATE order_intents SET outcome='unknown' WHERE id=?", (cid,))
                        self.store.db.execute("UPDATE orders SET unknown=1 WHERE id=?", (order["id"],))
                    message = self.audit.redactor.text(str(exc))[:500]
                    outcomes.append({"order": order["id"], "result": "撤單結果未明；" + message})
                    self.audit.emit("cancel_unconfirmed", order_id=order["id"], message=message)
            self.sync_reports(row["owner"], p.mode)
        return {"orders": outcomes, "position_preserved": True}

    def cmd_quote(self, symbol, price, fill_limit=None):
        value = decimal(price)
        if value <= 0 or (fill_limit is not None and (type(fill_limit) is not int or fill_limit < 0)):
            raise ValueError("行情／成交股數不合法")
        symbol = symbol.strip().upper()
        now = utcnow()
        q = Quote(
            symbol=symbol,
            price=value,
            source_time=now,
            received_time=now,
            generation=self.generation,
            source="離線情境行情",
        )
        self.paper("demo").quote(symbol, value, fill_limit)
        self.save_book("demo")
        self.sync_reports("demo", "demo")
        for row in self.store.strategies():
            if row["mode"] == "demo" and row["params"]["symbol"] == symbol:
                self.on_quote(row["id"], row["revision"], q, fill_limit)
        return {"symbol": symbol, "price": str(value)}

    def on_quote(self, sid, revision, quote, fill_limit=None):
        row, p = self.params(sid)
        if revision != row["revision"] or quote.generation != self.generation or quote.symbol != p.symbol:
            return
        self.quotes[sid] = quote.model_dump(mode="json")
        if self.fault or row["status"] != "active" or not row["reconciled"] or self.store.pending(sid):
            return
        if p.end_date < utcnow().astimezone(TW).date():
            self.cmd_stop(sid, False)
            return
        if not quote.fresh(utcnow(), p.quote_max_age):
            self.quote_warning(sid, "行情過期或等待來源更新；保持監控，新行情到達後自動恢復")
            return
        if p.pause_buys_below_lower and p.direction in ("both", "buy") and quote.price < p.lower_price:
            self.quote_warning(
                sid, "行情低於買價下界；不新增買單，保持監控，回到範圍後自動恢復；已送委託不會自動撤銷"
            )
            return
        self.quote_warning(sid)
        if p.mode != "demo":
            if row["owner"] != f"{p.mode}:{self.owner}":
                self.fail(sid, "會話身分或連線已失效")
                return
            if self.broker.disconnected.is_set():
                # Capture active monitoring before pausing; a callback can arrive mid-quote.
                self.recover_connection()
                return
            if not self.calendar.is_open(utcnow()):
                return
        if p.mode == "live" and not self.live_armed:
            self.fail(sid, "本次會話尚未啟用實單")
            return
        pos = self.store.position(sid)
        action = grid_action(p, decimal(row["anchor"]), pos["quantity"], quote.price)
        if not action:
            return
        side, price = action
        if price * p.quantity > p.max_order_value:
            self.fail(sid, "觸價金額超過單筆上限")
            return
        today = utcnow().astimezone(ET).date().isoformat()
        report = self.store.report(today)
        spent = sum(
            (decimal(r["limit_buy_value"]) for r in report["rows"] if r["strategy_id"] == sid), decimal(0)
        )
        if side == "buy" and spent + price * p.quantity > p.daily_buy_limit:
            self.fail(sid, "超過策略每日買入上限")
            return
        broker_held, sell_reserved = None, None
        account_spent, reserved = None, None
        if p.mode == "live":
            self.broker.contract(p.symbol)
            self.audit.require_writable()
            if self.session_order_count >= self.session_order_limit:
                self.fail(sid, "已達本次會話實單筆數上限")
                return
            if side == "sell":
                held = self.broker.holdings(p.symbol)
                reports = self.broker.reports()
                reserved = sum(
                    max(0, r["quantity"] - r["filled"])
                    for r in reports
                    if r["symbol"] == p.symbol and r["side"] == "sell" and r["status"] not in TERMINAL
                )
                if held - reserved < p.quantity:
                    self.fail(sid, "券商持股扣除未結賣單後不足；不把持股視為已確認可賣量")
                    return
                broker_held, sell_reserved = held, reserved
            account_spent = sum(
                (decimal(r["limit_buy_value"]) for r in report["rows"] if r["owner"] == row["owner"]),
                decimal(0),
            )
            reserved = sum(
                (
                    decimal(o["price"]) * (o["quantity"] - o["filled"])
                    for o in self.store.pending()
                    if o["owner"] == row["owner"] and o["side"] == "buy"
                ),
                decimal(0),
            )
            if side == "buy" and (
                self.account_limit is None
                or account_spent + reserved + price * p.quantity > self.account_limit
            ):
                self.fail(sid, "超過帳戶每日買入預算")
                return
        decision_now = utcnow()
        # Contract/holding/report calls can block; their return does not keep an old quote fresh.
        if not quote.fresh(decision_now, p.quote_max_age):
            self.quote_warning(sid, "送單前查核期間行情已過期；保持監控，等待下一筆有效行情")
            return
        if p.mode != "demo":
            if self.broker.disconnected.is_set():
                self.recover_connection()
                return
            if not self.calendar.is_open(decision_now):
                return
        if decision_now.astimezone(ET).date().isoformat() != today:
            self.quote_warning(sid, "送單前預算日期已切換；保持監控，下一筆行情重新計算預算")
            return
        decision = {
            "schema": 1,
            "evaluated_at": decision_now.isoformat(),
            "generation": self.generation,
            "account_reference": hashlib.sha256(row["owner"].encode()).hexdigest()[:24],
            "strategy": {
                "id": sid,
                "revision": row["revision"],
                "run_id": row["run_id"],
                "cycle": row["cycle"],
                "anchor": row["anchor"],
                "price_gap": str(p.price_gap),
                "params": p.model_dump(mode="json", exclude={"name"}),
            },
            "quote": quote.model_dump(mode="json"),
            "position": {
                "quantity": pos["quantity"],
                "broker_held": broker_held,
                "broker_reserved_sells": sell_reserved,
            },
            "order": {
                "symbol": p.symbol,
                "mode": p.mode,
                "side": side,
                "price": str(price),
                "quantity": p.quantity,
                "value": str(price * p.quantity),
                "currency": p.currency,
            },
            "checks": {
                "result": "allowed",
                "quote_fresh": True,
                "pending_orders": 0,
                "session": "offline" if p.mode == "demo" else "XNYS_normal",
                "budget_day": today,
                "budget_day_timezone": "America/New_York",
                "strategy_buy_spent": str(spent),
                "strategy_daily_limit": str(p.daily_buy_limit),
                "account_buy_spent": str(account_spent) if account_spent is not None else None,
                "reserved_buys": str(reserved) if reserved is not None else None,
                "account_daily_limit": str(self.account_limit) if p.mode == "live" else None,
                "session_orders_used": self.session_order_count if p.mode == "live" else None,
                "session_orders_limit": self.session_order_limit if p.mode == "live" else None,
            },
        }
        order = self.store.prepare(sid, side, price, decision)
        self.audit.emit("order_decision", order_id=order["id"], strategy_id=sid, decision=decision)
        self.audit.emit(
            "effect_prepared",
            order_id=order["id"],
            strategy_id=sid,
            mode=p.mode,
            owner=row["owner"],
            symbol=p.symbol,
            side=side,
            quantity=p.quantity,
            price=str(price),
        )
        if p.mode == "live":
            self.audit.require_writable()
            self.session_order_count += 1
        self.store.mark_dispatch(order["id"])
        self.audit.emit("effect_dispatch", order_id=order["id"], strategy_id=sid, mode=p.mode)
        if p.mode == "live":
            self.audit.require_writable()
        try:
            if p.mode == "live":
                org, bid = self.broker.create(order, order["settlement_currency"])
            else:
                org, bid = self.paper(row["owner"]).create(order)
                self.save_book(row["owner"])
            self.store.returned(order["id"], org, bid)
            self.audit.emit(
                "effect_returned",
                order_id=order["id"],
                broker_org=org,
                broker_order_id=bid,
                broker_reference=broker_reference(org, bid),
            )
            if p.mode != "live":
                self.paper(row["owner"]).quote(p.symbol, quote.price, fill_limit)
                self.save_book(row["owner"])
            self.sync_reports(row["owner"], p.mode)
        except Exception:
            self.fail(sid, "送單結果未明，保留意圖與保留量，請對帳")

    def sync_reports(self, owner, mode):
        reports = self.broker.reports() if mode == "live" else self.paper(owner).reports()
        local = [o for o in self.store.orders() if o["owner"] == owner]
        if mode == "live":
            recovered = self.recovery.missing_reports(local, reports)
            recovered_orgs = {r["broker_org"] for r in recovered}
            reports = [r for r in reports if r["broker_org"] not in recovered_orgs] + recovered
        for order in local:
            matches = [r for r in reports if not r.get("tracking_conflict") and report_matches(order, r)]
            ambiguous_owner = len(matches) == 1 and sum(report_matches(o, matches[0]) for o in local) != 1
            observation = (len(reports), len(matches), ambiguous_owner)
            if mode == "live" and self.match_observations.get(order["id"]) != observation:
                self.match_observations[order["id"]] = observation
                self.audit.emit(
                    "order_match",
                    order_id=order["id"],
                    report_count=len(reports),
                    match_count=len(matches),
                    ambiguous_owner=ambiguous_owner,
                    original_reference=broker_reference(order["broker_org"], ""),
                )
            if len(matches) != 1 or ambiguous_owner:
                if mode == "live" and order in self.store.pending():
                    if not matches and self.broker.awaiting_identity(order["client_ref"]):
                        continue
                    self.fail(order["strategy_id"], "未唯一匹配券商委託；保留未知，不重送")
                continue
            report = matches[0]
            if (
                order["status"] in TERMINAL
                and not order["unknown"]
                and report["status"] not in TERMINAL
                and report["filled"] <= order["filled"]
            ):
                continue  # A stale SDK cache must not erase a confirmed terminal report.
            if order["status"] != report["status"] or order["filled"] != report["filled"] or order["unknown"]:
                self.audit.emit(
                    "order_observed",
                    order_id=order["id"],
                    strategy_id=order["strategy_id"],
                    mode=mode,
                    previous_status=order["status"],
                    broker_reference=broker_reference(report.get("broker_org"), report.get("broker_id")),
                    report=report,
                )
            if (
                report["symbol"] != order["symbol"]
                or report["side"] != order["side"]
                or report["quantity"] != order["quantity"]
            ):
                self.fail(order["strategy_id"], "委託識別欄位不一致")
                continue
            try:
                self.store.bind_identity(order["id"], report["broker_org"], report["broker_id"])
            except ValueError:
                self.fail(order["strategy_id"], "券商委託身分衝突；保留未知，請查單")
                continue
            try:
                self.store.apply_report(
                    order["id"],
                    report["status"],
                    report["filled"],
                    report["filled_value"],
                    report["time"],
                    report.get("raw_status", report["status"])
                    + (
                        "：" + report["message"]
                        if report.get("message") and report["message"] not in report.get("raw_status", "")
                        else ""
                    ),
                    report["broker_org"],
                    report["broker_id"],
                    source_time=report.get("source_time", ""),
                )
                if report["status"] == "rejected" and (order["status"] != "rejected" or order["unknown"]):
                    self.fail(
                        order["strategy_id"],
                        "委託遭拒：" + (report.get("message") or "未取得原因，請查詢券商委託"),
                    )
                if mode != "live" and report["status"] in TERMINAL and report["filled"]:
                    fee_exists = self.store.rows(
                        "SELECT id FROM ledger_entries WHERE kind='fee' AND data LIKE ?",
                        ("%" + order["id"] + "%",),
                    )
                    if not fee_exists:
                        self.store.set_fee(order["id"], "0", "離線撮合費用預設；可另行核對")
            except (ValueError, sqlite3.IntegrityError):
                self.fail(order["strategy_id"], "成交累計或成本資料不一致，請對帳")

    def cmd_order_evidence(self, order_id):
        order = next((o for o in self.store.orders() if o["id"] == order_id), None)
        if order is None:
            raise ValueError("找不到委託")
        evidence = self.store.order_evidence(order_id)
        return {
            "order": order,
            "decision": evidence.get("order_decision"),
            "pairing": evidence.get("order_pairing"),
            "note": "決策快照記錄送單當時資料；缺少代表舊版本未留存，不能用目前行情補猜。人工配對不是原始送單的識別證明。",
        }

    def diagnostics_history(self, strategy_id):
        rows = self.store.rows(
            "SELECT id,time,data FROM ledger_entries WHERE strategy_id=? "
            "AND kind='broker_query' ORDER BY id DESC LIMIT 20",
            (strategy_id,),
        )
        return [
            {
                "id": r["id"],
                "queried_at": r["time"],
                "available": json.loads(r["data"])["available"],
                "match_counts": json.loads(r["data"]).get("match_counts", {}),
            }
            for r in rows
        ]

    def cmd_order_diagnostics(self, strategy_id, refresh=True, snapshot_id=None):
        row, p = self.params(strategy_id)
        if not refresh:
            saved = self.store.rows(
                "SELECT data FROM ledger_entries WHERE strategy_id=? "
                "AND kind='broker_query' AND (? IS NULL OR id=?) ORDER BY id DESC LIMIT 1",
                (strategy_id, snapshot_id, snapshot_id),
            )
            if not saved:
                raise ValueError("此策略尚無已保存的券商查核；登入後請先查詢")
            result = json.loads(saved[0]["data"])
            result["saved"] = True
            result["snapshot_id"] = snapshot_id
            result["history"] = self.diagnostics_history(strategy_id)
            return result
        if p.mode != "live" or row["owner"] != f"live:{self.owner}":
            raise ValueError("請登入並選定此正式策略所屬帳戶，再查詢券商委託")
        result = self.broker.order_diagnostics(p.symbol)
        local = self.store.orders(strategy_id)
        partition = [o for o in self.store.orders() if o["owner"] == row["owner"] and o["symbol"] == p.symbol]
        for report in result["rows"]:
            matches = [
                o["id"]
                for o in partition
                if report_matches(o, {"broker_org": report["orig_seqnum"], "broker_id": report["orderno"]})
            ]
            report["local_orders"] = matches
            matched = next((o for o in partition if matches == [o["id"]]), None)
            if matched:
                report["match"] = "matched" if compatible(matched, report) else "conflict"
                if report["match"] == "matched" and matched["strategy_id"] != strategy_id:
                    report["match"] = "other_strategy"
            else:
                report["match"] = "ambiguous" if matches else "unmatched"
            report["original_reference"] = broker_reference(report["orig_seqnum"], "")
            report["pairing"] = (
                self.store.order_evidence(matched["id"]).get("order_pairing") if matched else None
            )
        result["local_orders"] = [
            {
                k: o[k]
                for k in (
                    "id",
                    "symbol",
                    "side",
                    "quantity",
                    "price",
                    "status",
                    "filled",
                    "unknown",
                    "broker_org",
                    "broker_id",
                )
            }
            for o in local
        ]
        result["symbol"] = p.symbol
        result["note"] = "僅查詢此帳戶此商品報表；不以商品價格猜配，不自動改帳、重送或撤單。"
        result["coverage_note"] = (
            "只代表查詢當時券商回傳的報表，不能證明歷史委託全數齊備；均價空白或0不覆寫既有成交。"
        )
        result["match_counts"] = {
            label: sum(r["match"] == label for r in result["rows"])
            for label in ("matched", "other_strategy", "unmatched", "ambiguous", "conflict")
        }
        result["saved"] = False
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                (strategy_id, "broker_query", encode(result), result["queried_at"]),
            )
            if (
                result["match_counts"]["unmatched"]
                or result["match_counts"]["ambiguous"]
                or result["match_counts"]["conflict"]
            ):
                self.store.event(
                    "broker_query_attention",
                    "券商查核有未配對或識別衝突；已保留查核快照，不自動認領或重送",
                    strategy_id,
                )
        result["history"] = self.diagnostics_history(strategy_id)
        self.audit.emit("order_diagnostics", strategy_id=strategy_id, result=result)
        return result

    def cmd_recovery_preview(self, strategy_ids=None, report_timezone=None):
        return self.recovery.preview(strategy_ids, report_timezone)

    def cmd_recovery_confirm(self, version, selections, confirm=False, start=False):
        return self.recovery.confirm(version, selections, confirm, start)

    def cmd_ui_preferences(self, values):
        return self.store.set_preferences(values)

    def cmd_bulk_stop(self, cancel=False):
        if type(cancel) is not bool:
            raise ValueError("請明確選擇停止時的委託處理方式")
        results = []
        for row in self.store.strategies():
            if row["status"] == "archived":
                continue
            # Always stop monitoring before an optional account-scoped cancellation.
            self.cmd_stop(row["id"], False)
            try:
                outcome = self.cmd_stop(row["id"], True) if cancel else {"orders": []}
                results.append(
                    {
                        "id": row["id"],
                        "name": row["params"]["name"],
                        "status": "paused",
                        "message": "已停止監控",
                        "orders": outcome["orders"],
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "id": row["id"],
                        "name": row["params"]["name"],
                        "status": "paused",
                        "message": "已停止；撤單未確認：" + self.audit.redactor.text(str(exc)),
                    }
                )
        return {"results": results}

    def cmd_retry_order(self, order_id, revision, confirm=False):
        order = next((o for o in self.store.orders() if o["id"] == order_id), None)
        if not order or not confirm:
            raise ValueError("請明確確認拒單後建立新委託")
        row, p = self.params(order["strategy_id"])
        if p.mode != "live" or row["owner"] != f"live:{self.owner}":
            raise ValueError("請登入原委託帳戶")
        self.sync_reports(row["owner"], "live")
        orders = self.store.orders(row["id"])
        order = next(o for o in orders if o["id"] == order_id)
        if (
            orders[-1]["id"] != order_id
            or order["status"] != "rejected"
            or order["unknown"]
            or order["filled"]
            or self.store.pending(row["id"])
        ):
            raise ValueError("僅最新一筆已確認拒單且零成交、無其他在途單，才可建立新委託")
        if row["status"] == "active" or row["revision"] != revision or not row["reconciled"]:
            raise ValueError("請先停止策略並完成對帳；設定版本必須一致")
        if not self.live_armed or self.fault:
            raise ValueError("請先啟用本次實單並排除故障")
        q = self.broker.quote(p.symbol, self.generation)
        action = grid_action(p, decimal(row["anchor"]), self.store.position(row["id"])["quantity"], q.price)
        if (
            not q.fresh(utcnow(), p.quote_max_age)
            or not self.calendar.is_open(utcnow())
            or p.quantity != order["quantity"]
            or action != (order["side"], decimal(order["price"]))
        ):
            raise ValueError("最新行情、盤別或網格參數不符合原單；請重新檢視策略，不直接重送")
        self.cmd_start(row["id"], revision, True)
        self.audit.emit("retry_requested", previous_order_id=order_id, strategy_id=row["id"])
        self.on_quote(row["id"], revision, q)
        after = self.store.orders(row["id"])
        if len(after) == len(orders):
            reason = self.store.strategy(row["id"])["reason"] or "新委託未通過交易檢查"
            self.fail(row["id"], reason)
            raise ValueError(reason)
        self.audit.emit("retry_created", previous_order_id=order_id, order_id=after[-1]["id"])
        return {"order_id": after[-1]["id"], "status": after[-1]["status"], "previous_order_id": order_id}

    def cmd_reconcile(self, strategy_id, confirm=False, version=None):
        row, p = self.params(strategy_id)
        if p.mode != "demo" and row["owner"] != f"{p.mode}:{self.owner}":
            raise ValueError("請先選定策略所屬帳戶")
        self.sync_reports(row["owner"], p.mode)
        if p.mode == "live":
            snapshot = self.broker.positions()
            allocated = sum(
                r["quantity"]
                for r in self.store.report()["rows"]
                if r["owner"] == row["owner"] and r["symbol"] == p.symbol
            )
            held = self.broker.holdings(p.symbol, snapshot)
            matched = allocated <= held
            explanation = f"本地已分配 {allocated} 股／券商持股 {held} 股。請核對未分配部位、外部委託與可賣量；Qty只用作持股上限，不是券商可賣量保證。"
        else:
            snapshot = self.paper(row["owner"]).positions()
            expected = {}
            for position in self.store.report()["rows"]:
                if position["owner"] == row["owner"]:
                    expected[position["symbol"]] = expected.get(position["symbol"], 0) + position["quantity"]
            matched = all(
                snapshot.get(symbol, 0) == expected.get(symbol, 0)
                for symbol in snapshot.keys() | expected.keys()
            )
            explanation = "獨立模擬帳本與本地成交帳比對"
        unknown = [o for o in self.store.pending(strategy_id) if o["unknown"]]
        fingerprint = hashlib.sha256(
            encode(
                [snapshot, self.store.position(strategy_id), self.store.orders(strategy_id), row["revision"]]
            ).encode()
        ).hexdigest()
        result = {
            "version": fingerprint,
            "matched": matched,
            "unknown_orders": len(unknown),
            "position": self.store.position(strategy_id),
            "broker": snapshot,
            "explanation": explanation,
        }
        self.reconciliation[strategy_id] = result
        if confirm:
            if version != fingerprint or not matched or unknown:
                raise ValueError("對帳版本已變或仍有差異／未知委託，不能確認")
            if p.mode == "live":
                self.inventory_offsets[(row["owner"], p.symbol)] = held - allocated
            with self.store.transaction():
                self.store.db.execute(
                    "UPDATE strategies SET reconciled=1,reason='' WHERE id=?", (strategy_id,)
                )
                self.store.event("reconcile", "已重新核對並確認；需另按啟動", strategy_id)
            self.quotes.pop(strategy_id, None)
        return result

    def cmd_login(self, person_id, password, confirm_production=False):
        if not confirm_production:
            raise ValueError("請確認這是正式環境登入；此操作不授權送單")
        if self.owner:
            raise ValueError("請先登出")
        self.stage = "正在登入與查詢複委託帳戶"
        try:
            accounts = self.broker.login(person_id, password)
            self.reconnect_enabled = True
            self.generation += 1
            self.stage = "登入完成，請明確選擇帳戶"
            return {"accounts": accounts}
        except Exception:
            self.stage = "登入失敗；請核對 API 資格、憑證與帳密"
            raise ValueError(self.stage) from None

    def cmd_select_account(self, account, broker_id):
        if self.owner:
            raise ValueError("單帳戶會話已選定，切換前請登出")
        self.broker.select(account, broker_id)
        self.owner = hashlib.sha256(f"production:US:{broker_id}:{account}".encode()).hexdigest()[:24]
        self.stage = "正式帳戶已選定；實單尚未啟用"
        return {"selected": True}

    def cmd_live_control(
        self, enable, confirm=False, daily_buy_limit=None, max_orders=None, report_timezone=None
    ):
        if type(enable) is not bool:
            raise ValueError("啟用欄位需為布林值")
        if not enable:
            self.live_armed = False
            if self.reconnect_resume is not None:
                self.reconnect_resume["armed"] = False
                self.reconnect_resume["strategies"] = {
                    sid: saved
                    for sid, saved in self.reconnect_resume["strategies"].items()
                    if saved["mode"] != "live"
                }
            for row in self.store.strategies():
                if row["mode"] == "live" and row["status"] == "active":
                    self.cmd_stop(row["id"], False)
            return {"enabled": False}
        self.broker.require_selected()
        if self.fault:
            raise ValueError(self.fault)
        if not self.owner or confirm is not True:
            raise ValueError("請先選帳並明確確認實單")
        self.audit.require_writable()
        budget = decimal(daily_buy_limit)
        if budget <= 0 or type(max_orders) is not int or not 1 <= max_orders <= 100:
            raise ValueError("請設定正數每日買入上限與1至100筆本次會話上限")
        if report_timezone not in (None, "Asia/Taipei", "America/New_York", "UTC"):
            raise ValueError("不支援的成交回報時區")
        self.account_limit = budget
        self.session_order_limit = self.session_order_count + max_orders
        self.broker.report_timezone = report_timezone
        self.live_armed = True
        self.audit.emit(
            "live_armed",
            owner=self.owner,
            budget=str(budget),
            max_additional_orders=max_orders,
            report_timezone=report_timezone,
        )
        return {"enabled": True, "requires_strategy_start": True}

    def cmd_reconnect(self):
        if not getattr(self.broker, "api", None):
            raise ValueError("請先登入")
        self.broker.disconnected.set()
        self.reconnect_enabled = True
        self.reconnect_due = None
        self.reconnect_attempt = 0
        return {"scheduled": True}

    def recover_connection(self, now=None):
        realtime = now is None
        now = time.monotonic() if now is None else now
        if (
            not self.reconnect_enabled
            or not self.broker.disconnected.is_set()
            or not getattr(self.broker, "api", None)
        ):
            return
        if self.reconnect_due is None:
            if self.reconnect_resume is None:
                self.reconnect_resume = {
                    "owner": self.owner,
                    "selected": deepcopy(self.broker.selected),
                    "armed": self.live_armed,
                    "strategies": {
                        row["id"]: row
                        for row in self.store.strategies()
                        if row["status"] == "active"
                        and row["mode"] != "demo"
                        and row["owner"] == f"{row['mode']}:{self.owner}"
                    },
                }
                self.audit.emit(
                    "reconnect_resume_saved",
                    strategy_ids=list(self.reconnect_resume["strategies"]),
                    live_armed=self.live_armed,
                    orders_used=self.session_order_count,
                    orders_limit=self.session_order_limit,
                )
            self.live_armed = False
            self.generation += 1
            self.quotes.clear()
            self.market.invalidate()
            self.stage = "券商斷線；等待重連後自動查核並恢復原監控"
            for sid in self.reconnect_resume["strategies"]:
                self.fail(sid, "券商斷線；重連後自動查核並恢復監控")
            self.reconnect_due = now + 1
            self.broker.status(
                phase="reconnect_wait",
                attempt=0,
                retry_at=utcnow().timestamp() + 1,
                message="等待指數退避重連",
            )
        if now < self.reconnect_due:
            return
        self.reconnect_attempt += 1
        self.broker.status(phase="reconnecting", attempt=self.reconnect_attempt, retry_at=None)
        try:
            self.broker.reconnect()
        except Exception as exc:
            self.broker.disconnected.set()
            self.audit.emit("reconnect_failed", attempt=self.reconnect_attempt, error_type=type(exc).__name__)
            self.reconnect_due = (time.monotonic() if realtime else now) + min(
                300, 2 ** min(self.reconnect_attempt, 9)
            )
            self.broker.status(
                phase="reconnect_wait",
                message="重連未成功，等待下一次嘗試",
                attempt=self.reconnect_attempt,
                retry_at=utcnow().timestamp() + min(300, 2 ** min(self.reconnect_attempt, 9)),
            )
            return
        self.reconnect_due = None
        self.reconnect_attempt = 0
        if not self.owner or not self.broker.selected:
            self.reconnect_resume = None
            self.stage = "重連成功，請先選擇帳戶"
            self.broker.status(phase="select_account", attempt=0, retry_at=None, message=self.stage)
            return
        self.stage = "券商已重連；自動查核委託、成交與庫存"
        self.broker.status(phase="connected", attempt=0, retry_at=None, message=self.stage)
        # Broker login succeeded. Reconciliation errors must not restart login or replenish limits.
        self.recovery.resume_reconnected(self.reconnect_resume)

    def cmd_lookup(self, symbol, consumer="lookup"):
        import re

        symbol = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z0-9./_-]{1,24}", symbol) or len(consumer) > 80:
            raise ValueError("商品代號格式不符")
        self.broker.require_selected()
        self.broker.contract(symbol)
        if consumer not in self.market_consumers and len(self.market_consumers) >= 32:
            raise ValueError("行情查詢視窗過多，請關閉不用的視窗")
        self.market_consumers[consumer] = (symbol, time.monotonic() + 90)
        if symbol in self.market.entries and self.market.entries[symbol]["status"] == "error":
            self.market.remove(symbol)
        self.update_market()
        return {"symbol": symbol, "generation": self.generation, "market": self.market.snapshot().get(symbol)}

    def cmd_market_watch(self, consumer, release=False):
        if release:
            self.market_consumers.pop(consumer, None)
        elif consumer in self.market_consumers:
            symbol, _ = self.market_consumers[consumer]
            self.market_consumers[consumer] = (symbol, time.monotonic() + 90)
        return {"watching": consumer in self.market_consumers}

    def update_market(self):
        now = time.monotonic()
        self.market_consumers = {k: v for k, v in self.market_consumers.items() if v[1] > now}
        symbols = {v[0] for v in self.market_consumers.values()}
        symbols.update(
            s["params"]["symbol"]
            for s in self.store.strategies()
            if s["status"] != "archived" and s["mode"] != "demo" and s["owner"] == f"{s['mode']}:{self.owner}"
        )
        self.market.sync(symbols)

    def release_market(self):
        try:
            self.market.close()
        except Exception as exc:
            self.audit.emit("market_cleanup_unconfirmed", error_type=type(exc).__name__)
        # The caller still must obtain broker logout confirmation.

    def cmd_logout(self, cancel=False):
        self.reconnect_resume = None
        for row in self.store.strategies():
            if (
                row["mode"] != "demo"
                and row["owner"] == f"{row['mode']}:{self.owner}"
                and row["status"] != "archived"
            ):
                self.cmd_stop(row["id"], cancel)
        if cancel and any(o for o in self.store.pending() if o["mode"] == "live"):
            raise ValueError("撤單尚未確認，保留會話供查核")
        self.live_armed = False
        self.reconnect_enabled = False
        self.release_market()
        self.broker.logout()
        self.market = MarketWatch(self.broker)
        self.market_consumers.clear()
        self.reconnect_due = None
        self.reconnect_attempt = 0
        self.owner = None
        self.generation += 1
        self.quotes.clear()
        self.stage = "已登出，未成交委託依選擇保留；離線展示仍可使用"
        return {"logged_out": True}

    def cmd_fee(self, order_id, amount, reason):
        self.store.set_fee(order_id, amount, reason)
        return {"updated": True}

    def cmd_opening_cost(self, strategy_id, amount, reason):
        self.store.set_opening_cost(strategy_id, amount, reason)
        return {"updated": True}

    def cmd_backup(self):
        return {"file": self.store.backup(self.store.path.parent / "backups")}

    def cmd_report(self, day=None):
        if day:
            date.fromisoformat(day)
        return self.store.report(day)

    def cmd_import_legacy(self, records, apply=False):
        if not isinstance(records, list) or len(records) > 15:
            raise ValueError("匯入需為最多15組策略的JSON陣列")
        preview = []
        for record in records:
            try:
                start = decimal(record["start_price"])
                p = StrategyInput(
                    name=str(record["strategy_name"]),
                    symbol=str(record["symbol"]),
                    mode="demo",
                    direction={
                        "雙邊買賣": "both",
                        "單邊買": "buy",
                        "單邊買進": "buy",
                        "單邊賣": "sell",
                        "單邊賣出": "sell",
                    }[record["strategy_type"]],
                    start_price=start,
                    gap=record["grid_gap"],
                    gap_unit="percent" if record.get("grid_gap_unit") == "%" else "amount",
                    quantity=int(record["order_qty"]),
                    min_inventory=0,
                    initial_inventory=0,
                    max_inventory=int(record["max_inventory"]),
                    lower_price=decimal(record.get("buy_lower") or 1) or decimal(1),
                    upper_price=decimal(record.get("sell_upper") or start * 2) or start * 2,
                    max_order_value=start * int(record["order_qty"]) * 2,
                    daily_buy_limit=start * int(record["max_inventory"]) * 2,
                    end_date=date.fromisoformat(record["end_date"].replace("/", "-")),
                )
                key = hashlib.sha256(
                    json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                exists = self.store.rows("SELECT id FROM strategies WHERE import_key=?", (key,))
                item = {
                    "name": p.name,
                    "params": p.model_dump(mode="json"),
                    "existing": bool(exists),
                    "note": "僅匯入離線參數；期初庫存不認領，金額界線為待核對建議",
                }
                if apply and not exists:
                    if len([s for s in self.store.strategies() if s["status"] != "archived"]) >= 15:
                        raise ValueError("策略數已達上限")
                    sid = self.store.add_strategy(p, "demo", key)
                    item["id"] = sid
                preview.append(item)
            except (KeyError, ValueError, TypeError):
                preview.append(
                    {
                        "name": str(record.get("strategy_name", "")) if isinstance(record, dict) else "",
                        "error": "格式不符或策略數已達上限，未匯入",
                    }
                )
        return {"rows": preview, "applied": apply}

    def cmd_shutdown(self, cancel=False):
        if not isinstance(cancel, bool):
            raise ValueError("請明確選擇是否嘗試撤單")
        self.live_armed = False
        self.reconnect_resume = None
        self.reconnect_enabled = False
        outcomes = {}
        for row in self.store.strategies():
            if row["status"] == "active" or self.store.pending(row["id"]):
                # Stop new effects first, including strategies owned by another account.
                self.cmd_stop(row["id"], False)
                if cancel:
                    try:
                        result = self.cmd_stop(row["id"], True)
                        outcomes.update({o["order"]: o["result"] for o in result["orders"]})
                    except Exception as exc:
                        reason = self.audit.redactor.text(str(exc))[:500]
                        for order in self.store.pending(row["id"]):
                            outcomes[order["id"]] = "撤單未確認：" + reason
        unresolved = [
            {
                k: o[k]
                for k in (
                    "id",
                    "symbol",
                    "side",
                    "quantity",
                    "price",
                    "filled",
                    "status",
                    "broker_org",
                    "broker_id",
                )
            }
            | {
                "reason": outcomes.get(
                    o["id"], "撤單未確認，請至券商手動查核" if cancel else "依選擇保留委託，請至券商查核"
                )
            }
            for o in self.store.pending()
            if o["mode"] == "live"
        ]
        self.audit.emit("shutdown_pending_orders", cancel_requested=cancel, orders=unresolved)
        self.release_market()
        self.broker.logout()
        self.audit.emit("shutdown_ready", logout_confirmed=True, unresolved_orders=unresolved)
        self.closing = True
        self.stage = "程式即將結束；歷史與帳本已保存"
        return {"shutdown": True, "logout_confirmed": True, "unresolved_orders": unresolved}

    def tick(self):
        self.audit.retry_pending()
        for row in self.store.strategies():
            if (
                row["status"] == "active"
                and date.fromisoformat(row["params"]["end_date"]) < utcnow().astimezone(TW).date()
            ):
                self.cmd_stop(row["id"], False)
        if self.broker.disconnected.is_set() and getattr(self.broker, "api", None):
            self.recover_connection()
            return
        if self.owner:
            self.update_market()
        if not self.owner or (time.monotonic() - self.last_poll < 3 and not self.broker.dirty.is_set()):
            return
        self.broker.dirty.clear()
        self.last_poll = time.monotonic()
        active = [s for s in self.store.strategies() if s["status"] == "active" and s["mode"] != "demo"]
        watch_symbols = {s["params"]["symbol"] for s in active}
        watch_symbols.update(o["symbol"] for o in self.store.pending() if o["owner"] == f"paper:{self.owner}")
        for mode in ("paper", "live"):
            if any(o["owner"] == f"{mode}:{self.owner}" for o in self.store.pending()):
                self.sync_reports(f"{mode}:{self.owner}", mode)
        for symbol in watch_symbols:
            try:
                quote = self.broker.quote(symbol, self.generation)
            except Exception:
                for s in active:
                    if s["params"]["symbol"] == symbol:
                        self.quote_warning(s["id"], "行情暫不可用；保持監控，等待來源恢復")
                continue
            try:
                max_age = min(
                    (
                        s["params"]["quote_max_age"]
                        for s in self.store.strategies()
                        if s["params"]["symbol"] == symbol
                    ),
                    default=15,
                )
                if self.calendar.is_open(utcnow()) and quote.fresh(utcnow(), max_age):
                    book_owner = f"paper:{self.owner}"
                    self.paper(book_owner).quote(symbol, quote.price)
                    self.save_book(book_owner)
                    self.sync_reports(book_owner, "paper")
                for s in active:
                    if s["params"]["symbol"] == symbol:
                        self.on_quote(s["id"], s["revision"], quote)
            except AuditUnavailable:
                # No intent exists: keep the user's active monitoring and limits.
                # A prepared/dispatched intent has custody and needs reconciliation.
                for s in active:
                    if s["params"]["symbol"] == symbol and self.store.pending(s["id"]):
                        self.fail(s["id"], "稽核寫入中斷且有未結委託意圖；請查核，不自動重送")
            except Exception as exc:
                for s in active:
                    if s["params"]["symbol"] == symbol:
                        self.audit.emit(
                            "strategy_tick_error",
                            strategy_id=s["id"],
                            phase="quote_evaluation",
                            error_type=type(exc).__name__,
                        )
                        self.fail(s["id"], "交易處理失敗；請查核委託與帳本")

    def snapshot(self):
        audit_status = self.audit.status()
        return {
            "audit_seq": audit_status["seq"],
            "audit_error": audit_status["error"],
            "ui_preferences": self.store.preferences(),
            "updated_at": utcnow().isoformat(),
            "stage": self.stage,
            "generation": self.generation,
            "owner": self.owner,
            "accounts": self.broker.accounts,
            "selected": self.broker.selected,
            "live_available": self.live_armed
            and not self.broker.disconnected.is_set()
            and not self.audit.error,
            "live_limits": {
                "daily_buy_limit": str(self.account_limit) if self.account_limit else None,
                "orders_used": self.session_order_count,
                "orders_limit": self.session_order_limit,
            },
            "live_reason": "實單已啟用：僅已確認啟動的正式策略可送單"
            if self.live_armed
            else "實單未啟用：先登入、選帳，再設定實單限制並明確確認",
            "strategies": self.store.strategies(),
            "orders": self.store.orders(),
            "fills": sorted(self.store.fills(), key=lambda f: f["received_at"], reverse=True)[:200],
            "events": self.store.rows("SELECT * FROM events ORDER BY id DESC LIMIT 100"),
            "report": self.store.report(),
            "quotes": deepcopy(self.quotes),
            "quote_warnings": deepcopy(self.quote_warnings),
            "market": self.market.snapshot(
                {
                    symbol: min(
                        s["params"]["quote_max_age"]
                        for s in self.store.strategies()
                        if s["params"]["symbol"] == symbol and s["mode"] != "demo"
                    )
                    for symbol in {
                        s["params"]["symbol"] for s in self.store.strategies() if s["mode"] != "demo"
                    }
                }
            ),
            "reconciliation": deepcopy(self.reconciliation),
            "fault": self.fault,
            "closing": self.closing,
        }

    def close(self):
        try:
            self.release_market()
            self.broker.logout()
        finally:
            self.store.close()


for _name in [n for n in vars(Core) if n.startswith("cmd_")] + ["on_quote", "sync_reports", "fail"]:
    setattr(Core, _name, observed(getattr(Core, _name)))


class Service:
    """One actor owns SDK and SQLite. HTTP only reads immutable snapshots."""

    DEFAULT_STARTUP_TIMEOUT = 90

    def __init__(self, path, core_factory=Core, startup_timeout=DEFAULT_STARTUP_TIMEOUT):
        self.path = Path(path)
        self.audit = Audit(self.path.parent / "logs")
        self.core_factory = core_factory
        self.commands = queue.Queue(maxsize=64)
        self.results = OrderedDict()
        self.seen = set()
        self.lock = threading.Lock()
        self.state = {
            "stage": "初始化中",
            "strategies": [],
            "orders": [],
            "events": [],
            "report": {"rows": []},
        }
        self.stop_event = threading.Event()
        self.cleanup_complete = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.startup_timeout = startup_timeout
        self.startup_failure = None
        self.thread = threading.Thread(target=self.run, name="grid-actor", daemon=True)

    def start(self):
        self.audit.emit("actor_initializing", timeout_seconds=self.startup_timeout)
        self.thread.start()
        if not self.ready.wait(self.startup_timeout):
            self.startup_failure = {
                "stage": "actor_initialization",
                "reason": "timeout",
                "timeout_seconds": self.startup_timeout,
            }
            self.audit.emit("startup_failed", **self.startup_failure)
            raise TimeoutError("工作台初始化逾時，請查看初始化階段紀錄")
        if self.error:
            self.startup_failure = {
                "stage": "actor_initialization",
                "reason": "actor_error",
                "error_type": type(self.error).__name__,
            }
            self.audit.emit("startup_failed", **self.startup_failure)
            raise RuntimeError("工作台初始化失敗，請檢查 DB 與環境") from self.error

    def submit(self, action, payload, request_id):
        if len(request_id) > 80 or not request_id:
            raise ValueError("缺少操作識別")
        with self.lock:
            if request_id in self.results:
                return request_id
            if request_id in self.seen:
                raise ValueError("操作紀錄已過期；請查核結果，不可重送")
            if self.error or not self.thread.is_alive():
                raise ValueError("背景服務未運作，請保留資料並重新啟動")
            if self.stop_event.is_set() or self.state.get("closing"):
                raise ValueError("程式正在關閉")
            if self.commands.full():
                raise ValueError("命令佇列已滿，請稍後查核")
            self.results[request_id] = {"id": request_id, "status": "queued", "action": action}
            self.seen.add(request_id)
            self.audit.emit("command_received", command_id=request_id, action=action)
            self.commands.put_nowait((request_id, action, payload))
        return request_id

    def get_state(self):
        with self.lock:
            return deepcopy(self.state)

    def get_result(self, cid):
        with self.lock:
            return deepcopy(self.results.get(cid))

    def run(self):
        core = None
        shutdown_result = None
        initialized_at = time.monotonic()
        try:
            core = (
                Core(self.path, audit=self.audit)
                if self.core_factory is Core
                else self.core_factory(self.path)
            )
            core.audit = self.audit
            core.broker.audit = self.audit
            core.store.audit = self.audit
            self.state = core.snapshot()
            self.audit.emit("actor_ready", elapsed_seconds=round(time.monotonic() - initialized_at, 3))
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    cid, action, payload = self.commands.get(timeout=0.25)
                except queue.Empty:
                    cid = None
                if cid:
                    context = COMMAND.set(cid)
                    self.audit.emit("command_started", action=action)
                    with self.lock:
                        self.results[cid]["status"] = "running"
                        self.state["stage"] = "處理中：" + action
                    try:
                        value = core.handle(action, payload)
                        result = {"id": cid, "status": "done", "result": value}
                    except Exception as exc:
                        # Never expose vendor exception payloads or submitted credentials.
                        message = (
                            str(exc)
                            if isinstance(exc, ValueError) and action != "login"
                            else "操作未完成；請檢查狀態並核對，不要重送交易"
                        )
                        if action == "login":
                            message = "登入未完成；請核對 API 資格、憑證與帳密"
                        result = {
                            "id": cid,
                            "status": "error",
                            "message": self.audit.redactor.text(message)[:500],
                        }
                    finally:
                        payload.clear()
                    self.audit.emit(
                        "command_finished",
                        action=action,
                        status=result["status"],
                        result=result.get("result"),
                        message=result.get("message"),
                    )
                    COMMAND.reset(context)
                    # A completed command and its state are one publication. Background
                    # SDK work may block; it must not leave readers with pre-command state.
                    command_state = core.snapshot()
                    with self.lock:
                        self.state = command_state
                        if core.closing and result.get("result", {}).get("shutdown"):
                            # Publish terminal success only after DB/SDK cleanup below.
                            shutdown_result = (cid, result)
                        else:
                            self.results[cid] = result
                        while len(self.results) > 500:
                            self.results.popitem(last=False)
                    if core.closing:
                        break  # No queued trading command may run after shutdown.
                try:
                    if not core.closing:
                        core.tick()
                    state = core.snapshot()
                    with self.lock:
                        self.state = state
                except Exception as exc:
                    self.audit.emit("actor_tick_error", error_type=type(exc).__name__)
                    core.fault = "帳本或背景處理異常；暫停新效果，請保留資料並檢查"
                    for s in core.store.strategies():
                        if s["status"] == "active":
                            core.fail(s["id"], core.fault)
                    with self.lock:
                        self.state["fault"] = core.fault
        except BaseException as exc:
            self.error = exc
            self.audit.emit("actor_failed", error_type=type(exc).__name__)
            self.ready.set()
        finally:
            cleanup_ok = False
            try:
                if core:
                    core.close()
            except BaseException as exc:
                self.error = exc
                self.audit.emit("actor_cleanup_failed", error_type=type(exc).__name__)
            else:
                # A factory that never returned a Core cannot attest to its partial resources.
                cleanup_ok = core is not None or self.error is None
            if shutdown_result:
                cid, result = shutdown_result
                if not cleanup_ok:
                    result = {
                        "id": cid,
                        "status": "error",
                        "message": "登出後的帳本／背景清理尚未完成；請保留資料並查核，不要重送交易",
                    }
                while True:
                    try:
                        queued_id, action, payload = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    payload.clear()
                    with self.lock:
                        self.results[queued_id] = {
                            "id": queued_id,
                            "status": "error",
                            "message": "程式已停止接收操作；此排隊命令未執行",
                        }
                    self.audit.emit("command_canceled_by_shutdown", command_id=queued_id, action=action)
            self.audit.emit("actor_stopped", cleanup_complete=cleanup_ok)
            if cleanup_ok:
                self.cleanup_complete.set()
            if shutdown_result:
                with self.lock:
                    self.results[cid] = result

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("SDK 尚未返回；不可宣稱已安全關閉")
