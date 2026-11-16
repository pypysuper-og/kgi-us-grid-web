from contextlib import redirect_stdout, redirect_stderr, contextmanager, nullcontext
from datetime import datetime, timezone
import threading
import re
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from collections import deque
import time
from ..audit import ScreenStream, observed, broker_reference
from .order_diagnostics import error_catalog, normalize_rows

from ..models import ET, Quote, decimal, utcnow


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def shares(value):
    qty = decimal(value)
    if qty < 0 or qty != int(qty):
        raise ValueError("券商股數必須為非負整數")
    return int(qty)


def enum_name(value):
    return str(getattr(value, "name", value) or "")


def source_time(value, naive_timezone=None):
    """Only accept explicitly zoned ISO or epoch; never guess a vendor timezone."""
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    else:
        text = str(value)
        if re.fullmatch(r"\d{10}|\d{13}", text):
            result = datetime.fromtimestamp(int(text) / (1000 if len(text) == 13 else 1), timezone.utc)
        elif re.fullmatch(r"\d{14}(?:\d{1,6})?", text):
            result = datetime.strptime(text[:14], "%Y%m%d%H%M%S")
        else:
            result = datetime.fromisoformat(text.replace("Z", "+00:00").replace("/", "-"))
    if result.tzinfo is None and naive_timezone:
        zone = ZoneInfo(naive_timezone)
        aware = result.replace(tzinfo=zone)
        if (
            aware.utcoffset() != aware.replace(fold=1).utcoffset()
            or aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != result
        ):
            raise ValueError("時間位於夏令時間切換歧義，需明確時區偏移")
        result = aware
    if result.tzinfo is None:
        raise ValueError("券商時間缺少可確認的時區，請先完成資料契約核對")
    return result


def quote_time(value):
    """Return explicit source time, or a naive ordering key without inventing a zone."""
    text = str(value)
    if re.fullmatch(r"(?:19|20|21)\d{12}(?:\d{1,6})?", text):
        try:
            naive = datetime.strptime(text[:14], "%Y%m%d%H%M%S")
            if len(text) > 14:
                naive = naive.replace(microsecond=int(text[14:].ljust(6, "0")))
            return None, naive
        except ValueError:
            return None, None
    if re.fullmatch(r"\d{10}(?:\.\d+)?|\d{13}|\d{16}|\d{19}", text):
        digits = len(text.split(".")[0])
        scale = {10: 1, 13: 1000, 16: 1000000, 19: 1000000000}[digits]
        return datetime.fromtimestamp(float(decimal(text) / scale), timezone.utc), None
    try:
        return source_time(value), None
    except (ValueError, TypeError, OverflowError):
        text = str(value)
        try:
            if re.fullmatch(r"\d{14}", text):
                naive = datetime.strptime(text, "%Y%m%d%H%M%S")
            elif re.search(r"[T ]\d{2}:\d{2}", text):
                naive = datetime.fromisoformat(text.replace("/", "-"))
            else:
                return None, None
            return (None, naive) if naive.tzinfo is None else (naive, None)
        except ValueError:
            return None, None


class SuperPyBroker:
    def __init__(self, sdk=None, audit=None):
        self.sdk = sdk
        self.audit = audit
        self.report_timezone = None
        self.callback_generation = 0
        self.connection_lock = threading.RLock()
        self.api = None
        self.accounts = []
        self.selected = None
        self.disconnected = threading.Event()
        self.dirty = threading.Event()
        self.quote_observations = {}
        self.quote_generation = None
        self.submitted = {}
        self.order_events = deque(maxlen=200)
        self.order_generation = 0

    def reset_order_session(self):
        with self.connection_lock:
            self.order_generation += 1
            self.submitted.clear()
            self.order_events.clear()
            self.dirty.clear()

    def awaiting_identity(self, client_ref):
        pending = self.submitted.get(client_ref)
        return bool(pending and time.monotonic() - pending["sent_at"] < 30)

    def status(self, **fields):
        if self.audit:
            self.audit.set_connection(**fields)

    @contextmanager
    def capture(self):
        stream = ScreenStream(self.audit or SimpleNamespace(screen=lambda _: None))
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                yield stream
        finally:
            stream.finish()

    def invoke(self, operation, fn, *args, **kwargs):
        with self.audit.call("SDK." + operation) if self.audit else nullcontext():
            with self.capture():
                return fn(*args, **kwargs)

    def accounts_from(self, api):
        accounts = self.invoke("show_account", api.show_account)
        if not isinstance(accounts, list):
            raise ValueError("帳號清單格式不符")
        return [
            {
                "account": str(a["account"]),
                "broker_id": str(a["broker_id"]),
                "account_flag": str(a["account_flag"]),
            }
            for a in accounts
            if "複委託" in str(a.get("account_flag", ""))
        ]

    def disconnect_callback(self, generation):
        with self.connection_lock:
            if generation != self.callback_generation:
                return
            self.disconnected.set()
            self.status(phase="disconnected", message="券商斷線；策略將暫停")

    def bind_disconnect(self):
        self.callback_generation += 1
        generation = self.callback_generation
        self.invoke(
            "set_disconnect_cb", self.api.set_disconnect_cb, lambda: self.disconnect_callback(generation)
        )

    def login(self, person_id, password):
        if self.api is not None:
            raise ValueError("請先登出目前會話")
        if self.audit:
            self.audit.redactor.add(person_id, password)
            with self.audit.lock:
                self.audit.lines.clear()
        self.status(phase="logging_in", production=True, selected=None, attempt=0, retry_at=None)
        api = None
        try:
            with self.capture() as stream:
                if self.sdk is None:
                    import kgisuperpy

                    self.sdk = kgisuperpy
                with self.audit.call("SDK.login", simulation=False) if self.audit else nullcontext():
                    api = self.sdk.login(person_id, password, simulation=False)
            if len(stream.flags) != 3:
                raise ValueError("未取得完整登入成功／CA驗證／API初始化輸出，請查看SDK輸出")
            accounts = self.accounts_from(api)
            if not accounts:
                raise ValueError("沒有可選擇的複委託帳戶")
            self.api = api
            self.accounts = accounts
            if self.audit:
                self.audit.redactor.add(*(a["account"] for a in accounts))
            self.disconnected.clear()
            self.bind_disconnect()
            self.status(phase="select_account", message="登入成功，請選擇複委託帳戶")
            return self.accounts
        except Exception:
            if api is not None and callable(getattr(api, "logout", None)):
                try:
                    self.invoke("logout_failed_login", api.logout)
                except Exception:
                    self.api = api
                    self.status(phase="logout_failed", message="登入未完成且清理登出失敗，請先登出再試")
                    raise
            self.api = None
            self.accounts = []
            self.selected = None
            self.status(phase="login_failed", production=False, message="登入未完成，請查閱輸出")
            raise

    def on_event(self, event, generation):
        with self.connection_lock:
            if generation != (self.callback_generation, self.order_generation):
                return
            safe = {
                key: enum_name(field(event, key))
                for key in (
                    "task",
                    "status",
                    "symbol",
                    "action",
                    "quantity",
                    "price",
                    "org_seqnum",
                    "order_id",
                    "ts",
                    "seqno",
                    "msg",
                )
            }
            if self.audit:
                safe["msg"] = self.audit.redactor.text(safe["msg"])
            safe["received_at"] = utcnow().isoformat()
            self.order_events.append(safe)
            self.dirty.set()
        if self.audit:
            self.audit.emit(
                "broker_callback",
                generation=generation,
                report=safe,
                broker_reference=broker_reference(safe["org_seqnum"], safe["order_id"]),
                original_reference=broker_reference(safe["org_seqnum"], ""),
            )

    def select(self, account, broker_id):
        if not self.api or self.disconnected.is_set():
            raise ValueError("交易會話未就緒")
        selected = next(
            (a for a in self.accounts if a["account"] == account and a["broker_id"] == broker_id), None
        )
        if not selected:
            raise ValueError("帳戶不在本次登入回傳清單")
        self.status(phase="selecting_account", message="正在初始化複委託帳戶")
        self.reset_order_session()
        try:
            self.invoke("set_SubAccount", self.api.set_SubAccount, account=account)
            generation = (self.callback_generation, self.order_generation)
            self.invoke(
                "SubOrder.set_event",
                self.api.SubOrder.set_event,
                lambda event: self.on_event(event, generation),
            )
            with self.connection_lock:
                if self.disconnected.is_set():
                    raise ValueError("帳戶初始化期間已斷線")
                self.selected = selected
                self.status(
                    phase="connected",
                    selected=dict(selected),
                    message="正式登入／帳戶選定完成",
                    connected_at=utcnow().isoformat(),
                    retry_at=None,
                )
        except Exception:
            self.status(
                phase="disconnected" if self.disconnected.is_set() else "select_account",
                message="選帳失敗，請查核連線後重選",
            )
            raise

    def reconnect(self):
        if not self.api:
            raise ValueError("沒有可重連會話；請重新登入")
        selected = dict(self.selected) if self.selected else None
        self.status(phase="reconnecting", message="正在重新登入")
        self.disconnected.clear()
        try:
            with self.capture() as stream:
                with self.audit.call("SDK.login.reconnect") if self.audit else nullcontext():
                    self.api.login()
            if len(stream.flags) != 3 or self.disconnected.is_set():
                raise ValueError("重連缺少完整成功證據或登入期間已斷線")
            self.accounts = self.accounts_from(self.api)
            if self.audit:
                self.audit.redactor.add(*(a["account"] for a in self.accounts))
            self.bind_disconnect()
            if selected:
                self.select(selected["account"], selected["broker_id"])
            else:
                self.status(phase="select_account", message="重連完成，請選帳")
        except Exception:
            self.disconnected.set()
            raise
        return selected

    def require_selected(self):
        if not self.api or not self.selected or self.disconnected.is_set():
            raise ValueError("交易會話未就緒或已斷線")

    def quote(self, symbol, generation):
        self.require_selected()
        data = self.invoke("USData.get_snapshots", self.api.USData.get_snapshots, symbol)
        item = data.get(symbol) if isinstance(data, dict) else None
        if item is None:
            raise ValueError("查無該商品行情；請核對下單與行情代號")
        stamp = field(item, "timestamp")
        aware, naive = quote_time(stamp)
        received = utcnow()
        updated = False
        identity = (generation, self.callback_generation)
        if identity != self.quote_generation:
            self.quote_observations.clear()
            self.quote_generation = identity
        if aware is None and naive is not None:
            previous = self.quote_observations.get(symbol)
            if previous and naive > previous[0]:
                updated = True
                self.quote_observations[symbol] = (naive, received, updated)
            elif previous and naive == previous[0]:
                _, received, updated = previous
            elif previous is None:
                self.quote_observations[symbol] = (naive, received, False)
            # No real timezone can make a multi-day-old wall date a current quote.
            if abs((utcnow().date() - naive.date()).days) > 1:
                updated = False
        return Quote(
            symbol=symbol,
            price=decimal(field(item, "close")),
            source_time=aware,
            raw_source_time=str(stamp),
            source_updated=updated,
            received_time=received,
            generation=generation,
            source="SuperPy USData",
        )

    def create(self, order, currency):
        self.require_selected()
        if self.audit:
            self.audit.emit(
                "order_request_parameters",
                order_id=order["id"],
                symbol=order["symbol"],
                side=order["side"],
                quantity=order["quantity"],
                price=str(order["price"]),
                settlement_currency=currency,
            )
        response = self.invoke(
            "SubOrder.create_order",
            self.api.SubOrder.create_order,
            action=self.sdk.Action.Buy if order["side"] == "buy" else self.sdk.Action.Sell,
            symbol=order["symbol"],
            qty=order["quantity"],
            price=float(decimal(order["price"])),
            currency=getattr(self.sdk.Currency, currency),
            name=order["client_ref"],
        )
        obj = field(response, "order")
        if response is None or obj is None:
            raise ValueError("SDK 未返回 Trade；送單結果未明，請查完整委託報表，不重送")
        self.submitted[order["client_ref"]] = {"trade": response, "sent_at": time.monotonic()}
        if self.audit:
            self.audit.emit(
                "order_tracking_started",
                order_id=order["id"],
                client_ref=order["client_ref"],
                request_ids=[enum_name(field(op, "nid")) for op in field(response, "operations", [])],
            )
        return str(field(obj, "org_seqnum", "") or ""), str(field(obj, "order_id", "") or "")

    def cancel(self, org):
        self.require_selected()
        if not org:
            raise ValueError("缺少原單號，先對帳，不重送")
        return self.invoke("SubOrder.cancel_order", self.api.SubOrder.cancel_order, org)

    def reports(self):
        self.require_selected()
        trades = self.invoke("SubOrder.get_trades", self.api.SubOrder.get_trades, full=True)
        if not isinstance(trades, dict):
            raise ValueError("委託查詢結構不符；不當成空清單")
        trades = dict(trades)
        for tracked in self.submitted.values():
            trade = tracked["trade"]
            org = str(field(field(trade, "order"), "org_seqnum", "") or "")
            if org and org not in trades:
                trades[org] = trade
        result = []
        status_map = {
            "Submitted": "working",
            "PendingSubmitted": "working",
            "PartFilled": "partially_filled",
            "Filled": "filled",
            "Cancelled": "canceled",
            "PartFilled_Cancelled": "canceled",
            "Failed": "rejected",
            "Pending": "awaiting_report",
            "委託成功": "working",
            "委託失敗": "rejected",
            "逾期單": "expired",
            "作廢單": "canceled",
            "作癈單": "canceled",
            "無效單": "rejected",
            "處理失敗": "rejected",
            "預約單": "working",
        }
        for org, trade in trades.items():
            order, state = field(trade, "order"), field(trade, "order_status")
            raw = enum_name(field(state, "status"))
            actual_org = str(field(order, "org_seqnum") or org)
            refs = [
                ref
                for ref, t in self.submitted.items()
                if t["trade"] is trade
                or (
                    actual_org
                    and actual_org == str(field(field(t["trade"], "order"), "org_seqnum", "") or "")
                )
            ]
            deals = field(state, "deals", []) or []
            qty = sum(shares(field(d, "quantity", 0)) for d in deals)
            operations = field(trade, "operations", []) or []
            new_ops = [op for op in operations if enum_name(field(op, "task")) == "NewOrder"]
            latest_op = new_ops[-1] if new_ops else None
            rejection = enum_name(field(latest_op, "status"))
            if not qty and rejection in {"Failed", "委託失敗", "無效單", "處理失敗"}:
                raw = rejection
            value = sum(
                (decimal(field(d, "price")) * shares(field(d, "quantity")) for d in deals), decimal(0)
            )
            stamp = None
            time_error = False
            if qty:
                try:
                    dates = [source_time(field(d, "ts"), self.report_timezone) for d in deals]
                    if len({t.astimezone(ET).date() for t in dates}) != 1:
                        raise ValueError("跨日累計成交需逐日核對")
                    stamp = max(dates).isoformat()
                except (ValueError, TypeError):
                    time_error = True
                    if self.report_timezone:
                        try:
                            frame = self.invoke("SubAccount.ExecReport", self.api.SubAccount.ExecReport)
                            records = frame.to_dict("records")
                            rows = [
                                r
                                for r in records
                                if str(r.get("orderno")) == str(field(order, "order_id"))
                                and str(r.get("symbol")) == str(field(order, "symbol"))
                            ]
                            reported_qty = sum(shares(r["exe_qty"]) for r in rows)
                            reported_value = sum(
                                (decimal(r["exe_qty"]) * decimal(r["exe_price"]) for r in rows), decimal(0)
                            )
                            dates = [source_time(r["trade_date"], self.report_timezone) for r in rows]
                            if (
                                rows
                                and reported_qty == qty
                                and reported_value == value
                                and len({t.astimezone(ET).date() for t in dates}) == 1
                            ):
                                stamp = max(dates).isoformat()
                                time_error = False
                        except Exception as exc:
                            if self.audit:
                                self.audit.emit("fill_time_lookup_unavailable", error_type=type(exc).__name__)
            result.append(
                {
                    "broker_org": actual_org,
                    "broker_id": str(field(order, "order_id", "") or ""),
                    "client_ref": str(field(order, "name", "") or ""),
                    "tracked_ref": refs[0] if len(refs) == 1 else None,
                    "tracking_conflict": len(refs) > 1,
                    "message": self.audit.redactor.text(enum_name(field(latest_op, "msg")))
                    if self.audit
                    else enum_name(field(latest_op, "msg")),
                    "symbol": str(field(order, "symbol", "") or ""),
                    "side": {"Buy": "buy", "Sell": "sell"}.get(enum_name(field(order, "action")), "unknown"),
                    "quantity": shares(field(order, "quantity", 0)),
                    "price": str(field(order, "price", 0)),
                    "status": status_map.get(raw, "unknown"),
                    "raw_status": raw,
                    "filled": qty,
                    "filled_value": str(value),
                    "time": stamp,
                    "time_error": time_error,
                    "source_time": " / ".join(str(field(d, "ts", "")) for d in deals),
                }
            )
        return result

    def order_diagnostics(self, symbol):
        self.require_selected()
        catalog, info = error_catalog()
        result = {
            "source": "SubAccount.OrderReport",
            "queried_at": utcnow().isoformat(),
            "available": False,
            "rows": [],
            "error": None,
            "catalog": info,
        }
        try:
            frame = self.invoke("SubAccount.OrderReport", self.api.SubAccount.OrderReport)
            records = frame.to_dict("records")
            if not isinstance(records, list):
                raise ValueError("券商委託報表結構不符")
            rows = normalize_rows(records, symbol, catalog, self.audit.redactor if self.audit else None)
            result.update(
                available=True, rows=rows[:200], total_rows=len(rows), omitted=max(0, len(rows) - 200)
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}：券商報表未取得；不視為無委託，請保留未知狀態"
        with self.connection_lock:
            result["callbacks"] = [dict(e) for e in self.order_events if e["symbol"] == symbol]
        return result

    def positions(self):
        self.require_selected()
        frame = self.invoke("SubAccount.StockPositionReport", self.api.SubAccount.StockPositionReport)
        if not hasattr(frame, "to_dict"):
            raise ValueError("庫存回報結構不符")
        records = frame.to_dict("records")
        # Kept as evidence; Qty is NOT silently treated as sellable inventory.
        allowed = (
            "symbol",
            "symbol_name",
            "market",
            "currency",
            "settle_currency",
            "Qty",
            "buyqty",
            "close_date",
        )
        return [{key: str(row[key]) for key in allowed if key in row} for row in records]

    def contract(self, symbol):
        self.require_selected()
        catalog = self.invoke("SubOrder.contract", self.api.SubOrder.contract, type="dic")
        item = catalog.get(symbol) if isinstance(catalog, dict) else None
        if item is None:
            raise ValueError("下單商品不在已驗證的公開合約清單")
        market = enum_name(field(item, "market"))
        if market not in ("US", "1"):
            raise ValueError("商品不是已確認的美股合約")
        return item

    def holdings(self, symbol, positions=None):
        rows = [
            r for r in (self.positions() if positions is None else positions) if r.get("symbol") == symbol
        ]
        total = decimal(0)
        for row in rows:
            qty = decimal(row["Qty"])
            if qty < 0 or qty != int(qty):
                raise ValueError("券商持股欄位不是非負整數股")
            total += qty
        return int(total)

    def logout(self):
        # A failed logout retains the session so it cannot be presented as complete.
        self.status(phase="logging_out", message="正在登出券商")
        if self.api:
            try:
                if self.invoke("logout", self.api.logout) is False:
                    raise ValueError("SDK 未確認登出")
            except Exception:
                self.status(phase="logout_failed", message="登出未完成，保留會話供查核")
                raise
        self.reset_order_session()
        self.api = None
        self.callback_generation += 1
        self.selected = None
        self.accounts = []
        self.disconnected.set()
        self.status(phase="logged_out", production=False, selected=None, retry_at=None, message="已登出")


for _name in (
    "login",
    "select",
    "reconnect",
    "quote",
    "create",
    "cancel",
    "reports",
    "order_diagnostics",
    "positions",
    "contract",
    "holdings",
    "logout",
):
    setattr(SuperPyBroker, _name, observed(getattr(SuperPyBroker, _name)))
