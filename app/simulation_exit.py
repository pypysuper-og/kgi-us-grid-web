"""Retire simulated holdings without inventing a sale or deleting history."""

from copy import deepcopy
import time
import uuid

from .models import TERMINAL, utcnow
from .recovery import digest
from .storage import encode


class SimulationExit:
    def __init__(self, core):
        self.core = core
        self.plan = None

    def inspect(self, strategy_id):
        c = self.core
        row, params = c.params(strategy_id)
        if params.mode not in {"demo", "paper"}:
            raise ValueError("僅模擬策略可結束模擬部位；正式持股請先交接或實際處理")
        if c.fault:
            raise ValueError("請先排除交易服務異常")
        if row["status"] != "paused":
            raise ValueError("請先停止策略，已封存策略不能重複結束")
        strategies = [s for s in c.store.strategies() if s["owner"] == row["owner"] and s["params"]["symbol"] == params.symbol]
        orders = [o for o in c.store.orders() if o["owner"] == row["owner"] and o["symbol"] == params.symbol]
        if any(s["status"] == "active" for s in strategies):
            raise ValueError("請先停止同模式、同帳戶、同商品的監控")
        if any(o["unknown"] or o["status"] not in TERMINAL for o in orders):
            raise ValueError("同商品仍有在途／未知模擬委託，請先停止並撤單")
        positions = [p for p in c.store.report()["rows"] if p["owner"] == row["owner"] and p["symbol"] == params.symbol]
        position = next(p for p in positions if p["strategy_id"] == strategy_id)
        if position["quantity"] <= 0:
            raise ValueError("沒有需要結束的模擬部位，可直接封存")
        book = deepcopy(c.paper(row["owner"]).book)
        if any(o["symbol"] == params.symbol and o["status"] not in TERMINAL for o in book["orders"].values()):
            raise ValueError("模擬帳本仍有在途委託，請先停止並撤單")
        held = book["positions"].get(params.symbol, 0)
        if held != sum(p["quantity"] for p in positions):
            raise ValueError("模擬帳本與策略總分配不一致，請先核對庫存")
        local = {"strategies": strategies, "orders": orders, "positions": positions,
                 "ledger": c.store.rows("SELECT * FROM ledger_entries WHERE strategy_id=? ORDER BY id", (strategy_id,)),
                 "book": book, "owner": c.owner, "generation": c.generation}
        view = {"strategy_id": strategy_id, "name": params.name, "symbol": params.symbol,
                "owner": row["owner"], "mode": params.mode, "quantity": position["quantity"],
                "cost": position["cost"], "quantity_after": 0, "book_before": held,
                "book_after": held - position["quantity"], "checked_at": utcnow().isoformat()}
        return digest(local), view, book

    def command(self, strategy_id, confirm=False, version=None, operation_id=None):
        if type(confirm) is not bool:
            raise ValueError("請明確確認結束模擬")
        c = self.core
        if confirm:
            receipt = c.inventory_handoff.completed(operation_id, "simulation_exit")
            if receipt:
                if receipt["strategy_id"] != strategy_id:
                    raise ValueError("結束識別與原策略不符")
                return {**receipt, "already_applied": True}
            plan = self.plan
            if not plan or plan["view"]["operation_id"] != operation_id or plan["view"]["version"] != version or time.monotonic() > plan["expires"]:
                raise ValueError("結束模擬預覽已過期或變更，請重新預覽")
            if strategy_id != plan["view"]["strategy_id"]:
                raise ValueError("預覽策略不符")
            c.recovery.take_over([strategy_id], "simulation_exit")
        current, view, book = self.inspect(strategy_id)
        if not confirm:
            view.update(operation_id=uuid.uuid4().hex, expires_seconds=180, applied=False)
            view["version"] = digest([current, view["operation_id"]])
            self.plan = {"fingerprint": current, "view": deepcopy(view), "expires": time.monotonic() + 180}
            return view
        if current != plan["fingerprint"]:
            raise ValueError("策略、部位、成本或模擬帳本已變更，請重新預覽")
        c.audit.require_writable()
        receipt = {**view, "operation_id": operation_id, "version": version, "applied": True,
                   "already_applied": False, "completed_at": utcnow().isoformat()}
        book["positions"][view["symbol"]] = view["book_after"]
        with c.store.transaction():
            final, _, _ = self.inspect(strategy_id)
            if final != current:
                raise ValueError("確認前模擬部位已變更，請重新預覽")
            c.store.db.execute("INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                               (strategy_id, "simulation_exit", encode({**receipt, "receipt": receipt}), receipt["completed_at"]))
            c.store.db.execute("INSERT INTO broker_snapshots(owner,data,time) VALUES(?,?,?)",
                               (view["owner"], encode({"paper_book": book}), receipt["completed_at"]))
            c.store.db.execute("UPDATE strategies SET status='archived',reconciled=0,reason='已結束模擬並封存；歷史保留' WHERE id=?", (strategy_id,))
            c.store.event("simulation_exit", f"已結束 {view['quantity']} 股模擬部位並封存；未產生成交或損益", strategy_id)
            if c.store.position(strategy_id)["quantity"] != 0:
                raise ValueError("模擬結束投影核對失敗，整批未寫入")
        c.paper(view["owner"]).book = book
        c.quotes.pop(strategy_id, None)
        c.quote_warnings.pop(strategy_id, None)
        self.plan = None
        return receipt
