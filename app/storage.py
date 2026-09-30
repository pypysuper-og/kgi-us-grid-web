from .audit import observed
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import uuid

from .accounting import project
from .models import TERMINAL, StrategyInput, decimal, utcnow


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_preferences(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(owner TEXT PRIMARY KEY, mode TEXT NOT NULL, identity TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategies(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(owner), mode TEXT NOT NULL,
 params TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'paused',
 anchor TEXT NOT NULL, cycle INTEGER NOT NULL DEFAULT 0, run_id TEXT, reconciled INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, import_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS strategy_runs(id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL REFERENCES strategies(id),
 revision INTEGER NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, stop_policy TEXT);
CREATE TABLE IF NOT EXISTS order_intents(id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL REFERENCES strategies(id),
 kind TEXT NOT NULL, parent_id TEXT, request_key TEXT UNIQUE NOT NULL, outcome TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders(
 id TEXT PRIMARY KEY REFERENCES order_intents(id), strategy_id TEXT NOT NULL REFERENCES strategies(id),
 owner TEXT NOT NULL, mode TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity>0), price TEXT NOT NULL, status TEXT NOT NULL,
 filled INTEGER NOT NULL DEFAULT 0, filled_value TEXT NOT NULL DEFAULT '0',
 broker_org TEXT, broker_id TEXT, client_ref TEXT UNIQUE NOT NULL, raw_status TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, trade_date TEXT, unknown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
 strategy_id TEXT NOT NULL REFERENCES strategies(id), side TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
 value TEXT NOT NULL, time TEXT NOT NULL, received_at TEXT NOT NULL, cumulative INTEGER NOT NULL,
 UNIQUE(order_id,cumulative));
CREATE TABLE IF NOT EXISTS ledger_entries(id INTEGER PRIMARY KEY AUTOINCREMENT,
 strategy_id TEXT NOT NULL REFERENCES strategies(id),kind TEXT NOT NULL,data TEXT NOT NULL,time TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS broker_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT NOT NULL,
 time TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT NOT NULL,
 strategy_id TEXT,kind TEXT NOT NULL,message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS orders_strategy ON orders(strategy_id);
CREATE INDEX IF NOT EXISTS fills_time ON fills(time);
CREATE TRIGGER IF NOT EXISTS fills_no_update BEFORE UPDATE ON fills BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS fills_no_delete BEFORE DELETE ON fills BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_entries BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 3:
            raise ValueError("資料庫版本較新；請使用對應程式，不會降版")
        self.db.executescript(SCHEMA)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(fills)")}
        if "time_quality" not in columns:
            self.db.execute("ALTER TABLE fills ADD COLUMN time_quality TEXT NOT NULL DEFAULT 'source'")
        if "source_time" not in columns:
            self.db.execute("ALTER TABLE fills ADD COLUMN source_time TEXT NOT NULL DEFAULT ''")
        order_columns = {r[1] for r in self.db.execute("PRAGMA table_info(orders)")}
        if "settlement_currency" not in order_columns:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "ALTER TABLE orders ADD COLUMN settlement_currency TEXT NOT NULL DEFAULT 'unknown'"
            )
            # Before v3, strategy currency was immutable. Capture it once; never
            # derive historical settlement from an editable strategy thereafter.
            for strategy in self.strategies():
                currency = strategy["params"].get("currency", "unknown")
                if currency not in {"TWD", "MUT"}:
                    currency = "unknown"
                self.db.execute(
                    "UPDATE orders SET settlement_currency=? WHERE strategy_id=?", (currency, strategy["id"])
                )
        self.db.execute("PRAGMA user_version=3")
        self.db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1,?)", (utcnow().isoformat(),))
        self.db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2,?)", (utcnow().isoformat(),))
        self.db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3,?)", (utcnow().isoformat(),))
        self.db.execute("INSERT OR IGNORE INTO accounts VALUES('demo','demo','離線展示')")
        self.db.commit()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            savepoint = "nested_" + uuid.uuid4().hex
            self.db.execute("SAVEPOINT " + savepoint)
            try:
                yield
                self.db.execute("RELEASE SAVEPOINT " + savepoint)
            except BaseException:
                self.db.execute("ROLLBACK TO SAVEPOINT " + savepoint)
                self.db.execute("RELEASE SAVEPOINT " + savepoint)
                raise
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def preferences(self):
        rows = self.rows("SELECT data FROM ui_preferences WHERE id=1")
        return json.loads(rows[0]["data"]) if rows else {"left": 50, "upper": 46, "font_size": 14}

    def set_preferences(self, values):
        limits = {"left": (25, 75), "upper": (20, 80), "font_size": (11, 22)}
        if set(values) != set(limits):
            raise ValueError("版面設定欄位不完整")
        for key, (low, high) in limits.items():
            if type(values[key]) not in (int, float) or not low <= values[key] <= high:
                raise ValueError("版面設定超出範圍")
        with self.transaction():
            self.db.execute(
                "INSERT INTO ui_preferences VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (encode(values),),
            )
        return values

    def event(self, kind, message, sid=None):
        self.db.execute(
            "INSERT INTO events(time,strategy_id,kind,message) VALUES(?,?,?,?)",
            (utcnow().isoformat(), sid, kind, message),
        )

    def strategies(self):
        rows = self.rows("SELECT * FROM strategies ORDER BY created_at,id")
        for row in rows:
            row["params"] = json.loads(row["params"])
        return rows

    def strategy(self, sid):
        return next((s for s in self.strategies() if s["id"] == sid), None)

    def orders(self, sid=None):
        return self.rows(
            "SELECT * FROM orders" + (" WHERE strategy_id=?" if sid else "") + " ORDER BY created_at,id",
            (sid,) if sid else (),
        )

    def pending(self, sid=None):
        return [o for o in self.orders(sid) if o["status"] not in TERMINAL or o["unknown"]]

    def report(self, day=None):
        return project(
            self.strategies(),
            self.fills(),
            self.rows("SELECT * FROM ledger_entries ORDER BY id"),
            day,
        )

    def fills(self):
        return self.rows(
            "SELECT f.*,o.settlement_currency FROM fills f JOIN orders o ON o.id=f.order_id ORDER BY f.time,f.id"
        )

    def position(self, sid):
        return next(r for r in self.report()["rows"] if r["strategy_id"] == sid)

    def add_strategy(self, params: StrategyInput, owner: str, import_key=None):
        sid = uuid.uuid4().hex
        with self.transaction():
            self.db.execute(
                "INSERT INTO strategies(id,owner,mode,params,anchor,reconciled,created_at,import_key) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    sid,
                    owner,
                    params.mode,
                    params.model_dump_json(),
                    str(params.start_price),
                    int(params.mode == "demo"),
                    utcnow().isoformat(),
                    import_key,
                ),
            )
            if params.initial_inventory:
                self.db.execute(
                    "INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                    (
                        sid,
                        "opening",
                        encode(
                            {
                                "quantity": params.initial_inventory,
                                "cost": None if params.initial_cost is None else str(params.initial_cost),
                            }
                        ),
                        utcnow().isoformat(),
                    ),
                )
            self.db.execute(
                "INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                (
                    sid,
                    "strategy_config",
                    encode({"revision": 1, "params": params.model_dump(mode="json")}),
                    utcnow().isoformat(),
                ),
            )
            self.event("strategy_created", "策略已建立，尚未啟動", sid)
        return sid

    def recover(self):
        with self.transaction():
            self.db.execute(
                "UPDATE strategies SET status='paused',reconciled=0,reason='重啟後請對帳' WHERE status!='archived'"
            )
            self.db.execute(
                "UPDATE orders SET unknown=1 WHERE status NOT IN ('filled','canceled','rejected','expired')"
            )
            self.db.execute(
                "UPDATE order_intents SET outcome='unknown' WHERE outcome IN ('prepared','dispatching','returned')"
            )
            self.event("recovery", "已恢復歷史資料；策略保持暫停，未重播任何委託")

    def prepare(self, sid, side, price):
        strategy = self.strategy(sid)
        if not strategy or strategy["status"] != "active" or self.pending(sid):
            raise ValueError("策略未執行或仍有未結束委託")
        params = StrategyInput.model_validate(strategy["params"])
        oid = uuid.uuid4().hex
        key = f"{strategy['owner']}:{params.mode}:{sid}:{strategy['revision']}:{strategy['run_id']}:{strategy['cycle']}"
        now = utcnow().isoformat()
        with self.transaction():
            self.db.execute(
                "INSERT INTO order_intents VALUES(?,?,?,?,?,?,?)",
                (oid, sid, "create", None, key, "prepared", now),
            )
            self.db.execute(
                "INSERT INTO orders(id,strategy_id,owner,mode,symbol,side,quantity,price,status,client_ref,created_at,settlement_currency) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    oid,
                    sid,
                    strategy["owner"],
                    params.mode,
                    params.symbol,
                    side,
                    params.quantity,
                    str(price),
                    "prepared",
                    "G" + oid[:19],
                    now,
                    params.currency,
                ),
            )
        return next(o for o in self.orders(sid) if o["id"] == oid)

    def mark_dispatch(self, oid):
        with self.transaction():
            self.db.execute("UPDATE order_intents SET outcome='dispatching' WHERE id=?", (oid,))
            self.db.execute("UPDATE orders SET status='awaiting_report',unknown=1 WHERE id=?", (oid,))

    def returned(self, oid, org, broker_id=""):
        with self.transaction():
            self.db.execute("UPDATE order_intents SET outcome='returned' WHERE id=?", (oid,))
            self.db.execute("UPDATE orders SET broker_org=?,broker_id=? WHERE id=?", (org, broker_id, oid))

    def bind_identity(self, oid, org, broker_id=""):
        """Persist proven identifiers even if fill time still needs reconciliation."""
        order = next(o for o in self.orders() if o["id"] == oid)
        if (org and order["broker_org"] and org != order["broker_org"]) or (
            broker_id and order["broker_id"] and broker_id != order["broker_id"]
        ):
            raise ValueError("券商委託識別衝突")
        if org and self.rows(
            "SELECT id FROM orders WHERE owner=? AND broker_org=? AND id<>?", (order["owner"], org, oid)
        ):
            raise ValueError("券商原始序號已歸屬另一委託")
        if (not org or org == order["broker_org"]) and (not broker_id or broker_id == order["broker_id"]):
            return
        with self.transaction():
            self.db.execute(
                "UPDATE orders SET broker_org=COALESCE(NULLIF(?,''),broker_org), "
                "broker_id=COALESCE(NULLIF(?,''),broker_id) WHERE id=?",
                (org, broker_id, oid),
            )

    def apply_report(
        self,
        oid,
        status,
        cumulative,
        cumulative_value,
        trade_time,
        raw_status="",
        org="",
        broker_id="",
        source_time="",
    ):
        order = next(o for o in self.orders() if o["id"] == oid)
        if type(cumulative) is not int or status not in TERMINAL | {
            "working",
            "partially_filled",
            "awaiting_report",
            "unknown",
        }:
            raise ValueError("成交數量或狀態格式不符")
        if (org and order["broker_org"] and org != order["broker_org"]) or (
            broker_id and order["broker_id"] and broker_id != order["broker_id"]
        ):
            raise ValueError("券商委託識別變更，需對帳")
        qty = int(cumulative)
        value = decimal(cumulative_value)
        if qty < order["filled"] or qty > order["quantity"] or value < decimal(order["filled_value"]):
            raise ValueError("成交累計倒退或超量，需對帳")
        if qty == order["filled"] and value != decimal(order["filled_value"]):
            raise ValueError("同成交數量金額改變，需處理成交更正")
        if order["status"] in TERMINAL and status not in TERMINAL:
            status = order["status"]
        if status == "filled" and qty != order["quantity"]:
            raise ValueError("成交終態數量不一致")
        if qty == order["quantity"]:
            status = "filled"
        delta = qty - order["filled"]
        received = utcnow().isoformat()
        quality = "source" if trade_time else "received"
        if delta:
            when = datetime.fromisoformat(trade_time or received)
            if when.tzinfo is None or value <= decimal(order["filled_value"]):
                raise ValueError("成交時間或金額不足，需對帳")
        terminal_changed = status in TERMINAL and (order["status"] not in TERMINAL or order["unknown"])
        with self.transaction():
            if delta:
                self.db.execute(
                    "INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        oid,
                        order["strategy_id"],
                        order["side"],
                        delta,
                        str(value - decimal(order["filled_value"])),
                        trade_time or received,
                        received,
                        qty,
                        quality,
                        source_time,
                    ),
                )
                # Validate allocation before commit. Sell fills must not create a short ledger.
                self.position(order["strategy_id"])
                if quality == "received":
                    self.event(
                        "fill_time_warning",
                        "成交量價已確認；來源時間／時區待核對，接收時間僅供記錄及額度計算",
                        order["strategy_id"],
                    )
            self.db.execute(
                "UPDATE orders SET status=?,filled=?,filled_value=?,raw_status=?,unknown=?, "
                "broker_org=COALESCE(NULLIF(?,''),broker_org),broker_id=COALESCE(NULLIF(?,''),broker_id) WHERE id=?",
                (
                    status,
                    qty,
                    str(value),
                    raw_status,
                    int(status in {"unknown", "awaiting_report"}),
                    org,
                    broker_id,
                    oid,
                ),
            )
            if terminal_changed:
                self.db.execute(
                    "UPDATE order_intents SET outcome='resolved' WHERE id=? OR parent_id=?", (oid, oid)
                )
                if status == "filled":
                    self.db.execute(
                        "UPDATE strategies SET anchor=?,cycle=cycle+1 WHERE id=?",
                        (order["price"], order["strategy_id"]),
                    )
                else:
                    self.db.execute(
                        "UPDATE strategies SET status='paused',reconciled=0,cycle=cycle+1,reason=? WHERE id=?",
                        ("委託終止，請核對部位後重新啟動", order["strategy_id"]),
                    )
            if delta or terminal_changed:
                self.event("order_report", f"{order['symbol']} {status}：累計 {qty} 股", order["strategy_id"])
            if delta and order["status"] in TERMINAL:
                self.db.execute(
                    "UPDATE strategies SET status='paused',reconciled=0,reason='終態後收到新增成交，請重新對帳' WHERE id=?",
                    (order["strategy_id"],),
                )

    def set_fee(self, oid, amount, reason):
        order = next((o for o in self.orders() if o["id"] == oid), None)
        if not order or not order["filled"] or order["status"] not in TERMINAL:
            raise ValueError("請等待委託終態後核對費用")
        amount = decimal(amount)
        if amount < 0 or not reason.strip():
            raise ValueError("費用不可負數且須填來源／原因")
        with self.transaction():
            self.db.execute(
                "INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                (
                    order["strategy_id"],
                    "fee",
                    encode(
                        {"order_id": oid, "amount": str(amount), "currency": "USD", "reason": reason[:200]}
                    ),
                    utcnow().isoformat(),
                ),
            )
            self.event("fee", "已追加美元費用核對／更正，損益將重算", order["strategy_id"])

    def set_opening_cost(self, sid, amount, reason):
        if not self.rows("SELECT id FROM ledger_entries WHERE strategy_id=? AND kind='opening'", (sid,)):
            raise ValueError("此策略沒有期初部位")
        amount = decimal(amount)
        if amount < 0 or not reason.strip():
            raise ValueError("成本不可負數且須填來源／原因")
        with self.transaction():
            self.db.execute(
                "INSERT INTO ledger_entries(strategy_id,kind,data,time) VALUES(?,?,?,?)",
                (
                    sid,
                    "opening_cost",
                    encode({"cost": str(amount), "currency": "USD", "reason": reason[:200]}),
                    utcnow().isoformat(),
                ),
            )
            self.event("opening_cost", "已追加期初成本核對／更正，歷史損益將重算", sid)

    def backup(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"trading-{utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}.sqlite3"
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
            if (
                target.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or target.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("備份完整性檢查失敗")
        finally:
            target.close()
        return path.name

    def close(self):
        self.db.close()


for _name in (
    "add_strategy",
    "prepare",
    "mark_dispatch",
    "returned",
    "apply_report",
    "set_fee",
    "set_opening_cost",
    "recover",
    "backup",
):
    setattr(Store, _name, observed(getattr(Store, _name)))
