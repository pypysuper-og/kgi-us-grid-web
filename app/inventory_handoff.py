"""Explicit, local ownership handoff; never sends a brokerage order."""

from copy import deepcopy
import json
import re
import time
import uuid

from .models import TERMINAL, decimal, utcnow
from .recovery import digest, normalized
from .storage import encode


class InventoryHandoff:
    def __init__(self, core):
        self.core = core
        self.plan = None

    def completed(self, operation_id, kind="inventory_handoff"):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise ValueError("交接識別不符，請重新查核")
        c = self.core
        for row in c.store.rows("SELECT strategy_id,data FROM ledger_entries WHERE kind=?", (kind,)):
            data = json.loads(row["data"])
            if data["operation_id"] == operation_id:
                source, params = c.params(row["strategy_id"])
                if source["owner"] != data["owner"]:
                    raise ValueError("原操作的策略歸屬已變更，請核對帳本")
                if params.mode == "live":
                    c.broker.require_selected()
                    if data["owner"] != f"live:{c.owner}":
                        raise ValueError("請先選擇交接原帳戶")
                return deepcopy(data["receipt"])
        return None

    def local(self, source_id, target_id, replace_duplicate):
        c = self.core
        if c.fault:
            raise ValueError("交易服務需先排除異常，再進行部位交接")
        if source_id == target_id:
            raise ValueError("來源與接管策略必須不同")
        source, sp = c.params(source_id)
        target, tp = c.params(target_id)
        if sp.mode != tp.mode or source["owner"] != target["owner"] or sp.symbol != tp.symbol:
            raise ValueError("部位交接限同模式、同帳戶、同商品的策略")
        if sp.mode == "live" and (not c.owner or source["owner"] != f"live:{c.owner}"):
            raise ValueError("請先登入並選擇策略所屬帳戶")
        if source["status"] != "paused" or target["status"] != "paused":
            raise ValueError("請先停止來源與接管策略；封存策略不能交接")
        strategies = [s for s in c.store.strategies() if s["owner"] == source["owner"] and s["params"]["symbol"] == sp.symbol]
        orders = [o for o in c.store.orders() if o["owner"] == source["owner"] and o["symbol"] == sp.symbol]
        if any(s["status"] == "active" for s in strategies):
            raise ValueError("請先停止此帳戶同商品的策略監控")
        if any(o["unknown"] or o["status"] not in TERMINAL for o in orders):
            raise ValueError("同商品仍有在途／未知委託，請先查核並處理")
        positions = [r for r in c.store.report()["rows"] if r["owner"] == source["owner"] and r["symbol"] == sp.symbol]
        before = {p["strategy_id"]: p for p in positions}
        source_position, target_position = before[source_id], before[target_id]
        quantity, existing = source_position["quantity"], target_position["quantity"]
        if quantity <= 0:
            raise ValueError("來源策略目前沒有可交接部位")
        if tp.max_inventory is not None and quantity > tp.max_inventory or quantity < tp.min_inventory:
            raise ValueError("接管股數不符合目的策略進階持股界線，請先調整或取消上限")
        ledger = c.store.rows("SELECT * FROM ledger_entries WHERE strategy_id IN (?,?) ORDER BY id", (source_id, target_id))
        if existing:
            pristine = not any(o["strategy_id"] == target_id for o in orders) and not any(
                r["strategy_id"] == target_id and r["kind"] in {"inventory_handoff", "inventory_handoff_ref"} for r in ledger
            )
            if not pristine or existing != quantity or tp.initial_inventory != existing:
                raise ValueError("接管策略已有其他部位或交易；目前僅支援空策略或同股數的重複期初登記")
        elif replace_duplicate:
            raise ValueError("接管策略沒有重複期初部位，不需勾選更正")
        allocated = sum(p["quantity"] for p in positions)
        destination_cost = source_position["cost"]
        if existing and not replace_duplicate:
            destination_cost = None if destination_cost is None or target_position["cost"] is None else str(decimal(destination_cost) + decimal(target_position["cost"]))
        local = {"strategies": strategies, "positions": positions, "orders": orders, "ledger": ledger,
                 "owner": c.owner, "generation": c.generation}
        view = {
            "source": {"id": source_id, "name": sp.name, "quantity": quantity, "cost": source_position["cost"]},
            "target": {"id": target_id, "name": tp.name, "quantity": existing, "cost": target_position["cost"]},
            "symbol": sp.symbol, "mode": sp.mode, "strategy_owner": source["owner"],
            "replace_duplicate": replace_duplicate, "has_duplicate": bool(existing),
            "source_after": 0, "target_after": quantity + (0 if replace_duplicate else existing),
            "target_cost_after": destination_cost, "allocated_before": allocated,
            "allocated_after": allocated - (existing if replace_duplicate else 0),
            "context": {"owner": c.owner, "generation": c.generation},
        }
        return local, view

    def inspect(self, source_id, target_id, replace_duplicate):
        c = self.core
        local, view = self.local(source_id, target_id, replace_duplicate)
        if view["mode"] != "live":
            book = deepcopy(c.paper(view["strategy_owner"]).book)
            held = book["positions"].get(view["symbol"], 0)
            if any(o["symbol"] == view["symbol"] and o["status"] not in TERMINAL for o in book["orders"].values()):
                raise ValueError("模擬帳本同商品仍有在途委託，請先停止並撤單")
            view.update(broker_quantity=held, reference_label="模擬帳本持股",
                        reference_after=held - (view["target"]["quantity"] if replace_duplicate else 0),
                        checked_at=utcnow().isoformat(), can_apply=True, blocker="")
            if held != view["allocated_before"]:
                view.update(can_apply=False, blocker="模擬帳本與策略總分配不一致，請先核對庫存")
            elif view["has_duplicate"] and not replace_duplicate:
                view.update(can_apply=False, blocker="接管策略已登記同股數；若為同一批部位，請明確勾選更正重複登記後重新查核")
            return digest(local), digest(book), view
        positions = c.broker.positions()  # A failure is unknown, never zero.
        held = c.broker.holdings(view["symbol"], positions)
        snapshot = c.recovery.report_snapshot({view["symbol"]})
        reports, completed_orgs = [], set()
        for report in snapshot["reports"]:
            data = normalized(report, snapshot["executions"], c.broker.report_timezone)
            if data["status"] not in TERMINAL:
                raise ValueError("券商同商品仍有在途／未知委託，請先查核並處理")
            completed_orgs.add(data["broker_org"])
            for order in local["orders"]:
                if order["broker_org"] and order["broker_org"] == data["broker_org"]:
                    if order["filled"] != data["filled"] or decimal(order["filled_value"]) != decimal(data["filled_value"]):
                        raise ValueError("券商成交與本地帳本已變更，請先對帳再交接")
            reports.append({"org": data["broker_org"], "id": data["broker_id"], "status": data["status"],
                            "filled": data["filled"], "value": data["filled_value"]})
        for cached in c.broker.reports():
            if cached["symbol"] != view["symbol"]:
                continue
            if cached.get("tracking_conflict") or cached["status"] not in TERMINAL and cached["broker_org"] not in completed_orgs:
                raise ValueError("同商品委託快取尚待查核，不能交接")
        fresh, _ = self.local(source_id, target_id, replace_duplicate)
        if digest(local) != digest(fresh):
            raise ValueError("查核期間帳戶或本地部位已變更，請重新預覽")
        view.update(broker_quantity=held, reference_label="券商持股", reference_after=held,
                    checked_at=utcnow().isoformat(), can_apply=True, blocker="")
        if view["has_duplicate"] and not replace_duplicate:
            view.update(can_apply=False, blocker="接管策略已登記同股數；若為同一批部位，請明確勾選更正重複登記後重新查核")
        elif view["allocated_after"] > held:
            view.update(can_apply=False, blocker="交接後本地總分配仍超過券商持股，請先核對其他策略與庫存")
        return digest(local), digest({"held": held, "reports": sorted(reports, key=digest)}), view

    def command(self, source_id, target_id, replace_duplicate=False, confirm=False, version=None, operation_id=None):
        if type(replace_duplicate) is not bool or type(confirm) is not bool:
            raise ValueError("請明確選擇交接與重複登記更正")
        c = self.core
        if confirm:
            receipt = self.completed(operation_id)
            if receipt:
                if receipt["source"]["id"] != source_id or receipt["target"]["id"] != target_id or receipt["replace_duplicate"] != replace_duplicate:
                    raise ValueError("交接識別與原操作不符")
                return {**receipt, "already_applied": True}
            plan = self.plan
            if not plan or operation_id != plan["view"]["operation_id"] or version != plan["view"]["version"] or time.monotonic() > plan["expires"]:
                raise ValueError("交接預覽已過期或變更，請重新查核")
            if (source_id, target_id, replace_duplicate) != plan["inputs"] or plan["view"]["context"] != {"owner": c.owner, "generation": c.generation}:
                raise ValueError("交接策略或帳戶會話已變更，請重新查核")
            c.recovery.take_over([source_id, target_id], "inventory_handoff")
        local_hash, broker_hash, view = self.inspect(source_id, target_id, replace_duplicate)
        if not confirm:
            view.update(operation_id=uuid.uuid4().hex, expires_seconds=180, applied=False)
            view["version"] = digest([local_hash, broker_hash, view["operation_id"], replace_duplicate])
            self.plan = {"inputs": (source_id, target_id, replace_duplicate), "local": local_hash,
                         "broker": broker_hash, "view": deepcopy(view), "expires": time.monotonic() + 180}
            return view
        if local_hash != plan["local"] or broker_hash != plan["broker"]:
            raise ValueError("部位、成本、委託或券商庫存已變更，請重新查核後確認")
        if not view["can_apply"]:
            raise ValueError(view["blocker"])
        c.audit.require_writable()
        receipt = {**view, "operation_id": operation_id, "version": version, "applied": True,
                   "completed_at": utcnow().isoformat(), "already_applied": False}
        data = {"operation_id": operation_id, "target_id": target_id, "source_name": view["source"]["name"],
                "owner": view["strategy_owner"], "mode": view["mode"], "quantity": view["source"]["quantity"],
                "duplicate_replaced": replace_duplicate, "discarded_quantity": view["target"]["quantity"] if replace_duplicate else 0,
                "source_cost_at_confirmation": view["source"]["cost"], "receipt": receipt}
        with c.store.transaction():
            final, _ = self.local(source_id, target_id, replace_duplicate)
            if digest(final) != local_hash:
                raise ValueError("交接前本地部位已變更，請重新查核")
            next_book = None
            if view["mode"] != "live":
                next_book = deepcopy(c.paper(data["owner"]).book)
                if digest(next_book) != broker_hash:
                    raise ValueError("模擬帳本已變更，請重新查核")
                next_book["positions"][view["symbol"]] = view["reference_after"]
                c.store.db.execute("INSERT INTO broker_snapshots(owner,data,time) VALUES(?,?,?)",
                                   (data["owner"], encode({"paper_book": next_book}), receipt["completed_at"]))
            c.store.db.execute("INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                               (source_id, "inventory_handoff", encode(data), receipt["completed_at"]))
            c.store.db.execute("INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                               (target_id, "inventory_handoff_ref", encode({"operation_id": operation_id, "source_id": source_id,
                                "duplicate_replaced": replace_duplicate}), receipt["completed_at"]))
            c.store.db.execute("UPDATE strategies SET reconciled=0,reason='部位已交接，請重新查核後啟動' WHERE id IN (?,?)", (source_id, target_id))
            c.store.event("inventory_handoff", f"已將 {view['source']['quantity']} 股交給 {view['target']['name']}，原歷史保留", source_id)
            c.store.event("inventory_handoff", f"承接 {view['source']['name']} 的 {view['source']['quantity']} 股與原成本；保持暫停", target_id)
            after = {p["strategy_id"]: p for p in c.store.report()["rows"]}
            if after[source_id]["quantity"] != 0 or after[target_id]["quantity"] != view["target_after"]:
                raise ValueError("交接投影核對失敗，整批未寫入")
            actual = after[target_id]["cost"]
            expected = view["target_cost_after"]
            if (actual is None) != (expected is None) or actual is not None and decimal(actual) != decimal(expected):
                raise ValueError("交接成本核對失敗，整批未寫入")
        if next_book is not None:
            c.paper(data["owner"]).book = next_book
        self.plan = None
        c.inventory_offsets.pop((data["owner"], view["symbol"]), None)
        for sid in (source_id, target_id):
            c.quotes.pop(sid, None)
            c.quote_warnings.pop(sid, None)
        return receipt
