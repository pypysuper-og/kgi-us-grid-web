"""Broker-backed recovery; automatic reconnect never guesses order ownership."""

import hashlib
import json
import time

from .brokers.order_diagnostics import error_catalog, normalize_rows, report_matches
from .brokers.superpy import shares, source_time
from .models import ET, decimal, utcnow


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def side(value):
    return {
        "B": "buy",
        "Buy": "buy",
        "買進": "buy",
        "買": "buy",
        "S": "sell",
        "Sell": "sell",
        "賣出": "sell",
        "賣": "sell",
    }.get(str(value), "unknown")


def compatible(order, report):
    try:
        return (
            order["symbol"] == report["symbol"]
            and order["side"] == side(report["BuySell"])
            and order["quantity"] == shares(report["qty"])
            and decimal(order["price"]) == decimal(report["price"])
            and bool(report["orig_seqnum"])
            and (not order["broker_org"] or order["broker_org"] == report["orig_seqnum"])
            and (not order["broker_id"] or not report["orderno"] or order["broker_id"] == report["orderno"])
        )
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return False


def normalized(report, executions, timezone):
    qty = shares(report["exe_qty"] or "0")
    action, status = report["action"], report["sales_status_code"]
    if action not in {"", "0", "1"}:
        raise ValueError("委託有改量／替換歷程，請逐筆核對原始單，不能直接認領")
    if report["exe_status_code"] == "2":
        state = "filled"
    elif action == "1" and status == "2":
        state = "canceled"
    elif status in {"3", "6", "9"} and action != "1":
        state = "canceled" if qty else "rejected"
    elif status in {"4", "5"}:
        state = "expired" if status == "4" else "canceled"
    elif status in {"0", "2", "8"} and report["order_err_code"] in {"", "0"}:
        state = "partially_filled" if qty else "working"
    else:
        raise ValueError("券商尚無可確認的委託終態／有效狀態，先保留未知")
    value, stamp, raw_times = decimal(0), None, []
    if qty:
        fills = [
            r
            for r in executions
            if r.get("orderno") == report["orderno"]
            and report["orderno"]
            and r.get("symbol") == report["symbol"]
            and side(r.get("trade_type")) == side(report["BuySell"])
        ]
        if not fills or sum(shares(r.get("exe_qty", 0)) for r in fills) != qty:
            raise ValueError("成交明細與委託累計量未吻合；請補查 ExecReport，不以均價猜入帳")
        raw_times = [str(r.get("trade_date", "")) for r in fills]
        try:
            dates = [source_time(t, timezone) for t in raw_times]
            if len({d.astimezone(ET).date() for d in dates}) == 1:
                stamp = max(dates).isoformat()
        except (ValueError, TypeError, OverflowError, OSError):
            pass  # A missing calendar attribution does not invalidate confirmed quantity/value.
        value = sum((shares(r["exe_qty"]) * decimal(r["exe_price"]) for r in fills), decimal(0))
    return {
        "status": state,
        "filled": qty,
        "filled_value": str(value),
        "time": stamp,
        "source_time": " / ".join(raw_times),
        "time_warning": "成交時間／時區未完整確認；以接收時間記錄，日期報表待核對，不阻擋恢復"
        if qty and not stamp
        else "",
        "broker_org": report["orig_seqnum"],
        "broker_id": report["orderno"],
        "raw_status": (report["sales_status"] or report["sales_label"])
        + "："
        + (f"[{report['order_err_code']}] " if report["order_err_code"] not in {"", "0"} else "")
        + report["order_err_msg"],
        "message": (f"[{report['order_err_code']}] " if report["order_err_code"] not in {"", "0"} else "")
        + report["order_err_msg"],
    }


class Recovery:
    def __init__(self, core):
        self.core = core
        self.plan = None

    def resume_reconnected(self, intent):
        """Restore this process's previous run intent after a same-account reconnect.

        Confirmed reports are applied before inventory checks across sibling strategies.
        No order creation, cancellation, inferred identity, or budget refresh occurs here.
        """
        c = self.core
        self.plan = None
        results, ready = [], []

        def blocked(sid, exc):
            message = "重連查核待處理：" + c.audit.redactor.text(str(exc))[:400]
            c.fail(sid, message)
            results.append({"id": sid, "status": "blocked", "message": message})

        same_account = (
            intent is not None
            and intent["owner"] == c.owner
            and intent["selected"] is not None
            and all(
                intent["selected"].get(k) == (c.broker.selected or {}).get(k)
                for k in ("account", "broker_id")
            )
        )
        if not same_account:
            for sid in (intent or {}).get("strategies", {}):
                blocked(sid, "帳戶已改變；不能沿用原會話的啟動授權")
        else:
            c.live_armed = bool(intent["armed"]) and not c.broker.disconnected.is_set()
            for sid, saved in intent["strategies"].items():
                row = c.store.strategy(sid)
                if row["revision"] != saved["revision"] or row["status"] == "archived":
                    results.append({"id": sid, "status": "skipped", "message": "策略已修改或封存，維持停止"})
                    continue
                try:
                    c.broker.require_selected()
                    if row["mode"] == "live":
                        snapshot = self.fetch([row])
                        terminal_problem = ""
                        with c.store.transaction():
                            for order in c.store.pending(sid):
                                candidates = []
                                for report in snapshot["reports"]:
                                    identity = {
                                        "broker_org": report["orig_seqnum"],
                                        "broker_id": report["orderno"],
                                    }
                                    strong = report_matches(order, identity) or any(
                                        report_matches(order, cached)
                                        and cached["broker_org"] == report["orig_seqnum"]
                                        for cached in snapshot["cached"]
                                    )
                                    claimed = any(
                                        other["id"] != order["id"]
                                        and other["owner"] == order["owner"]
                                        and report_matches(other, identity)
                                        for other in c.store.orders()
                                    )
                                    if strong and not claimed and compatible(order, report):
                                        candidates.append(report)
                                if len(candidates) != 1:
                                    raise ValueError("委託無唯一可靠單號對應，請開啟對帳；未知單不重送")
                                data = normalized(
                                    candidates[0], snapshot["executions"], c.broker.report_timezone
                                )
                                c.store.bind_identity(order["id"], data["broker_org"], data["broker_id"])
                                c.store.apply_report(
                                    order["id"],
                                    data["status"],
                                    data["filled"],
                                    data["filled_value"],
                                    data["time"],
                                    data["raw_status"],
                                    data["broker_org"],
                                    data["broker_id"],
                                    source_time=data["source_time"],
                                )
                                c.store.event("reconnect_order_reconciled", data["raw_status"], sid)
                                if data["status"] in {"rejected", "canceled", "expired"}:
                                    terminal_problem = (
                                        "委託已拒絕／撤銷／到期，保留部位待確認：" + data["raw_status"]
                                    )
                        if terminal_problem:
                            raise ValueError(terminal_problem)
                    else:
                        check = c.cmd_reconcile(sid)
                        if not check["matched"] or check["unknown_orders"]:
                            raise ValueError("模擬帳本有未對應差異")
                    ready.append(sid)
                except Exception as exc:
                    blocked(sid, exc)

            # Requery holdings after applying all observed fills, not before them.
            positions, position_error = None, None
            if any(c.store.strategy(sid)["mode"] == "live" for sid in ready):
                try:
                    positions = c.broker.positions()
                except Exception as exc:
                    position_error = exc
            for sid in ready:
                try:
                    row = c.store.strategy(sid)
                    c.broker.require_selected()
                    if row["mode"] == "live":
                        if position_error is not None:
                            raise position_error
                        symbol = row["params"]["symbol"]
                        allocated = sum(
                            r["quantity"]
                            for r in c.store.report()["rows"]
                            if r["owner"] == row["owner"] and r["symbol"] == symbol
                        )
                        expected = allocated + c.inventory_offsets.get((row["owner"], symbol), 0)
                        if c.broker.holdings(symbol, positions) != expected:
                            raise ValueError("券商庫存與已確認部位不一致，請核對外部成交或未對應委託")
                    if any(o["unknown"] for o in c.store.pending(sid)):
                        raise ValueError("仍有未知委託，不自動重送")
                    with c.store.transaction():
                        c.store.db.execute("UPDATE strategies SET reconciled=1,reason='' WHERE id=?", (sid,))
                        c.cmd_start(sid, row["revision"], confirm_live=True)
                        c.store.event(
                            "reconnect_resumed", "重連自動查核完成，恢復斷線前監控；沿用剩餘額度", sid
                        )
                    results.append({"id": sid, "status": "active", "message": "已自動恢復監控"})
                except Exception as exc:
                    blocked(sid, exc)

        if c.broker.disconnected.is_set():
            c.live_armed = False
            for sid in (intent or {}).get("strategies", {}):
                if c.store.strategy(sid)["status"] == "active":
                    c.fail(sid, "查核期間再次斷線；等待重連後自動查核")
            c.stage = "查核期間再次斷線；將繼續自動重連"
            # Keep intent for the next connection attempt; never restore from database on boot.
        else:
            c.reconnect_resume = None
            count = sum(r["status"] == "active" for r in results)
            blocked_count = sum(r["status"] == "blocked" for r in results)
            c.stage = f"券商已重連；自動恢復 {count} 組監控"
            if blocked_count:
                c.stage += f"；{blocked_count} 組需處理，請查看策略提示並開啟對帳"
            c.broker.status(phase="connected", attempt=0, retry_at=None, message=c.stage)
        c.audit.emit(
            "reconnect_recovery",
            results=results,
            live_armed=c.live_armed,
            orders_used=c.session_order_count,
            orders_limit=c.session_order_limit,
        )
        return results

    def local_state(self, ids):
        c = self.core
        return {
            "owner": c.owner,
            "generation": c.generation,
            "strategies": [s for s in c.store.strategies() if s["id"] in ids],
            "orders": c.store.orders(),
            "positions": c.store.report()["rows"],
        }

    def fetch(self, strategies):
        c = self.core
        live = [s for s in strategies if s["mode"] == "live"]
        result = {"reports": [], "executions": [], "holdings": {}, "cached": []}
        if not live:
            return result
        c.broker.require_selected()
        symbols = {s["params"]["symbol"] for s in live}
        positions = c.broker.positions()
        result["holdings"] = {symbol: c.broker.holdings(symbol, positions) for symbol in symbols}
        if not any(c.store.pending(s["id"]) for s in live):
            return result
        result.update(self.report_snapshot(symbols))
        try:
            result["cached"] = [
                {k: r.get(k) for k in ("broker_org", "broker_id", "tracked_ref", "client_ref")}
                for r in c.broker.reports()
            ]
        except Exception:
            pass  # Complete report remains useful; never infer an identity from a failed cache read.
        result["cached"].sort(key=digest)
        return result

    def report_snapshot(self, symbols, originals=None):
        c = self.core
        result = {"reports": [], "executions": []}
        rows = c.broker.invoke("SubAccount.OrderReport", c.broker.api.SubAccount.OrderReport).to_dict(
            "records"
        )
        catalog, _ = error_catalog()
        for symbol in sorted(symbols):
            result["reports"].extend(normalize_rows(rows, symbol, catalog, c.audit.redactor))
        if originals is not None:
            result["reports"] = [r for r in result["reports"] if r["orig_seqnum"] in originals]
        if any(shares(r["exe_qty"] or "0") for r in result["reports"]):
            fields = ("symbol", "orderno", "trade_type", "exe_qty", "exe_price", "trade_date")
            fills = c.broker.invoke("SubAccount.ExecReport", c.broker.api.SubAccount.ExecReport).to_dict(
                "records"
            )
            result["executions"] = [
                {k: str(r.get(k, "")) for k in fields} for r in fills if r.get("symbol") in symbols
            ]
        for key in ("reports", "executions"):
            result[key].sort(key=digest)
        return result

    def missing_reports(self, local, cached):
        """Resolve known orders whose cache entry is absent or lacks a usable status."""
        c = self.core
        pending_ids = {o["id"] for o in c.store.pending()}
        missing = []
        for order in local:
            if order["id"] not in pending_ids or not order["broker_org"]:
                continue
            matches = [r for r in cached if report_matches(order, r)]
            if not matches or (
                len(matches) == 1
                and not matches[0].get("tracking_conflict")
                and (
                    matches[0]["status"] in {"unknown", "awaiting_report"}
                    or matches[0]["filled"] < order["filled"]
                )
                and all(matches[0][k] == order[k] for k in ("symbol", "side", "quantity"))
            ):
                missing.append(order)
        if not missing:
            return []
        result = []
        try:
            snapshot = self.report_snapshot(
                {o["symbol"] for o in missing}, {o["broker_org"] for o in missing}
            )
            for order in missing:
                for report in snapshot["reports"]:
                    if not compatible(order, report) or order["broker_org"] != report["orig_seqnum"]:
                        continue
                    data = normalized(report, snapshot["executions"], c.broker.report_timezone)
                    result.append(
                        {
                            **data,
                            "symbol": order["symbol"],
                            "side": order["side"],
                            "quantity": order["quantity"],
                            "price": order["price"],
                        }
                    )
        except Exception as exc:
            c.audit.emit("recovery_tracking_unconfirmed", error_type=type(exc).__name__)
            return []  # Existing sync marks unresolved orders for reconciliation; never fabricate fills.
        return result

    def preview(self, strategy_ids=None, report_timezone=None):
        c = self.core
        self.plan = None
        if report_timezone not in {None, "UTC", "Asia/Taipei", "America/New_York"}:
            raise ValueError("請選擇已核對的成交來源時區")
        eligible = [
            s
            for s in c.store.strategies()
            if s["status"] != "archived" and (s["mode"] == "demo" or s["owner"] == f"{s['mode']}:{c.owner}")
        ]
        ids = [s["id"] for s in eligible] if strategy_ids is None else list(dict.fromkeys(strategy_ids))
        if not ids or len(ids) > 100 or any(sid not in {s["id"] for s in eligible} for sid in ids):
            raise ValueError("請選取目前帳戶可管理的策略")
        strategies = [s for s in eligible if s["id"] in ids]
        snapshot = self.fetch(strategies)
        local = self.local_state(ids)
        candidates = {}
        for r in snapshot["reports"]:
            key = digest(r)
            candidates[key] = r
        view = []
        for s in strategies:
            row = {
                "id": s["id"],
                "name": s["params"]["name"],
                "symbol": s["params"]["symbol"],
                "mode": s["mode"],
                "status": s["status"],
                "orders": [],
                "local_quantity": c.store.position(s["id"])["quantity"],
                "broker_quantity": snapshot["holdings"].get(s["params"]["symbol"]),
            }
            for order in c.store.pending(s["id"]):
                if s["mode"] != "live":
                    continue
                options, strong = [], []
                for key, r in candidates.items():
                    claimed = any(
                        o["id"] != order["id"]
                        and o["owner"] == order["owner"]
                        and o["broker_org"] == r["orig_seqnum"]
                        for o in c.store.orders()
                    )
                    if claimed or not compatible(order, r):
                        continue
                    auto = report_matches(order, {"broker_org": r["orig_seqnum"], "broker_id": r["orderno"]})
                    auto = auto or any(
                        report_matches(order, cached) and cached["broker_org"] == r["orig_seqnum"]
                        for cached in snapshot["cached"]
                    )
                    try:
                        data = normalized(
                            r, snapshot["executions"], report_timezone or c.broker.report_timezone
                        )
                        warning = data["time_warning"]
                        error = None
                    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                        error = str(exc)
                        warning = ""
                    options.append(
                        {
                            "key": key,
                            "org": r["orig_seqnum"],
                            "number": r["orderno"],
                            "quantity": r["qty"],
                            "price": r["price"],
                            "filled": r["exe_qty"],
                            "status": r["sales_status"] or r["sales_label"],
                            "message": r["order_err_msg"],
                            "time": r["create_time"],
                            "error": error,
                            "warning": warning,
                        }
                    )
                    if auto:
                        strong.append(key)
                chosen = strong[0] if len(strong) == 1 else options[0]["key"] if len(options) == 1 else ""
                row["orders"].append(
                    {
                        "id": order["id"],
                        "side": order["side"],
                        "quantity": order["quantity"],
                        "price": order["price"],
                        "created_at": order["created_at"],
                        "options": options,
                        "choice": chosen,
                        "basis": "identity" if len(strong) == 1 else "manual",
                    }
                )
            view.append(row)
        version = digest([local, snapshot, report_timezone])
        self.plan = {
            "version": version,
            "ids": ids,
            "local": local,
            "snapshot": snapshot,
            "candidates": candidates,
            "view": view,
            "timezone": report_timezone,
            "created": time.monotonic(),
        }
        c.audit.emit("recovery_preview", version=version, strategies=view)
        return {
            "version": version,
            "strategies": view,
            "report_timezone": report_timezone,
            "queried_at": utcnow().isoformat(),
            "expires_seconds": 180,
        }

    def confirm(self, version, selections, confirm=False, start=False):
        c, plan = self.core, self.plan
        if confirm is not True or type(start) is not bool or not isinstance(selections, dict):
            raise ValueError("請核對配對與庫存後明確確認")
        if not plan or version != plan["version"] or time.monotonic() - plan["created"] > 180:
            raise ValueError("對帳預覽已過期，請重新查核")
        local = self.local_state(plan["ids"])
        if digest(local) != digest(plan["local"]):
            raise ValueError("本地委託／策略已更新，請重新查核")
        fresh = self.fetch(local["strategies"])
        if digest(fresh) != digest(plan["snapshot"]):
            raise ValueError("券商回報或庫存已更新，請重新查核後確認")
        allowed = {o["id"] for s in plan["view"] for o in s["orders"]}
        if set(selections) - allowed:
            raise ValueError("配對包含預覽以外的委託")
        used = set()
        for oid, key in selections.items():
            if not key:
                continue
            report = plan["candidates"].get(key)
            if not report or not report["orig_seqnum"] or report["orig_seqnum"] in used:
                raise ValueError("同一券商委託不能重複配對，請逐筆選擇")
            used.add(report["orig_seqnum"])
        results = []
        # Each strategy is atomic; a blocked sibling does not prevent a proven strategy from recovery.
        self.plan = None
        for s in plan["view"]:
            if s["status"] == "active":
                results.append(
                    {"id": s["id"], "name": s["name"], "status": "active", "message": "原已監控中"}
                )
                continue
            try:
                with c.store.transaction():
                    for item in s["orders"]:
                        key = selections.get(item["id"], "")
                        if key not in {o["key"] for o in item["options"]}:
                            raise ValueError("仍有未配對委託；請選擇券商單或保留待處理")
                        report = plan["candidates"][key]
                        data = normalized(
                            report, fresh["executions"], plan["timezone"] or c.broker.report_timezone
                        )
                        c.store.bind_identity(item["id"], data["broker_org"], data["broker_id"])
                        c.store.apply_report(
                            item["id"],
                            data["status"],
                            data["filled"],
                            data["filled_value"],
                            data["time"],
                            data["raw_status"],
                            data["broker_org"],
                            data["broker_id"],
                            source_time=data["source_time"],
                        )
                        c.store.event(
                            "recovery_pair",
                            f"確認配對 {item['id']}：{item['basis']}；{data['raw_status']}",
                            s["id"],
                        )
                    pending = c.store.pending(s["id"])
                    if any(o["unknown"] for o in pending):
                        raise ValueError("仍有未知委託，保留暫停")
                    if s["mode"] == "live":
                        allocated = sum(
                            r["quantity"]
                            for r in c.store.report()["rows"]
                            if r["owner"] == f"live:{c.owner}" and r["symbol"] == s["symbol"]
                        )
                        if allocated > fresh["holdings"][s["symbol"]]:
                            raise ValueError("分配後持股超過券商持股，請核對外部成交或庫存")
                    else:
                        result = c.cmd_reconcile(s["id"])
                        if not result["matched"] or result["unknown_orders"]:
                            raise ValueError("模擬帳本仍有差異")
                    c.store.db.execute("UPDATE strategies SET reconciled=1,reason='' WHERE id=?", (s["id"],))
                    c.store.event("reconcile", "已確認配對、成交與庫存", s["id"])
                if plan["timezone"]:
                    c.broker.report_timezone = plan["timezone"]
                message, status = "已完成對帳", "reconciled"
                if start:
                    row = c.store.strategy(s["id"])
                    c.cmd_start(s["id"], row["revision"], confirm_live=True)
                    message, status = "已恢復監控；在途委託持續追蹤、不重送", "active"
                if not c.store.position(s["id"])["time_complete"]:
                    message += "；成交時間待核對，不影響監控（日期損益暫不確定）"
                results.append({"id": s["id"], "name": s["name"], "status": status, "message": message})
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                results.append({"id": s["id"], "name": s["name"], "status": "blocked", "message": str(exc)})
        c.audit.emit(
            "recovery_confirmed", version=version, start=start, selections=selections, results=results
        )
        for s in plan["view"]:
            if s["mode"] == "live" and any(r["id"] == s["id"] and r["status"] != "blocked" for r in results):
                allocated = sum(
                    r["quantity"]
                    for r in c.store.report()["rows"]
                    if r["owner"] == f"live:{c.owner}" and r["symbol"] == s["symbol"]
                )
                c.inventory_offsets[(f"live:{c.owner}", s["symbol"])] = (
                    fresh["holdings"][s["symbol"]] - allocated
                )
        return {"results": results}
