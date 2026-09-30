"""Read-only market display. Never feeds orders, fills, or the execution ledger."""

from copy import deepcopy
import threading

from .brokers.superpy import enum_name, field, source_time, quote_time
from .models import decimal, utcnow


class MarketWatch:
    def __init__(self, broker):
        self.broker = broker
        self.lock = threading.RLock()
        self.epoch = 0
        self.session = None
        self.channel = None
        self.entries = {}
        self.pending = {}
        self.events = []
        self.desired = set()

    def emit(self, event, **values):
        if self.broker.audit:
            self.broker.audit.emit("market_" + event, epoch=self.epoch, **values)

    def invalidate(self):
        with self.lock:
            self.epoch += 1
            self.pending.clear()
            self.events.clear()
            for entry in self.entries.values():
                entry["status"] = "disconnected"
            self.session = None

    def receive(self, data, epoch):
        # Bounded, coalesced handoff. No SDK calls, disk IO, or order effects here.
        with self.lock:
            symbol = field(data, "symbol")
            if (
                epoch != self.epoch
                or symbol not in self.entries
                or symbol not in self.desired
                or self.broker.disconnected.is_set()
            ):
                return
            incoming = {
                "close": field(data, "close"),
                "timestamp": field(data, "datetime"),
                "volume": field(data, "volume"),
                "received": utcnow(),
            }
            pending = self.pending.get(symbol)
            if pending:
                try:
                    if decimal(pending["volume"]) > 0 and decimal(incoming["volume"]) == 0:
                        return
                    new_time = next((t for t in quote_time(incoming["timestamp"]) if t is not None), None)
                    old_time = next((t for t in quote_time(pending["timestamp"]) if t is not None), None)
                    if new_time is not None and old_time is not None and new_time < old_time:
                        return
                except (ValueError, TypeError, ArithmeticError):
                    pass
            self.pending[symbol] = incoming

    def event(self, data, epoch):
        with self.lock:
            if epoch == self.epoch:
                self.events.append(
                    (enum_name(field(data, "event_code")), enum_name(field(data, "respond_code")))
                )
                self.events = self.events[-32:]

    def bind(self):
        self.epoch += 1
        self.pending.clear()
        self.events.clear()
        previous = self.entries
        self.entries = {}
        self.session = self.broker.callback_generation
        self.channel = getattr(self.broker.api, "USQuote", None)
        if self.channel:
            epoch = self.epoch
            try:
                self.broker.invoke(
                    "USQuote.set_cb_tick", self.channel.set_cb_tick, lambda data: self.receive(data, epoch)
                )
                self.broker.invoke(
                    "USQuote.set_cb_event", self.channel.set_cb_event, lambda data: self.event(data, epoch)
                )
            except Exception as exc:
                self.channel = None
                self.emit("callback_unavailable", error_type=type(exc).__name__)
        # A consumer may disappear while disconnected. Do not orphan its retained key.
        for symbol in set(previous) - self.desired:
            self.entries[symbol] = previous[symbol]
            self.entries[symbol]["quote"] = None
            try:
                self.remove(symbol)
            except Exception as exc:
                self.entries[symbol].update(status="error", release_failed=True)
                self.emit("release_failed", symbol=symbol, error_type=type(exc).__name__)

    def keys(self):
        keys = self.broker.invoke("USQuote.get_subscriptions", self.channel.get_subscriptions)
        if not isinstance(keys, (dict, list, tuple)):
            raise ValueError("行情訂閱清單格式不符")
        return list(keys)

    def remove(self, symbol):
        entry = self.entries[symbol]
        if self.channel and entry.get("requested"):
            # Only this facade's reported exact Tick key, never substring matching.
            key = "qtTickv1.USStock." + symbol
            if key in self.keys():
                self.broker.invoke("USQuote.unsubscribe", self.channel.unsubscribe, key)
                if key in self.keys():
                    raise ValueError("行情解除尚未由訂閱清單確認")
        with self.lock:
            self.entries.pop(symbol, None)
            self.pending.pop(symbol, None)
        self.emit("released", symbol=symbol)

    def close(self):
        # Retain failed subscription custody for a later explicit cleanup attempt.
        self.invalidate()
        for symbol in list(self.entries):
            self.remove(symbol)
        self.channel = None

    def add(self, symbol):
        item = self.broker.contract(symbol)
        entry = {
            "symbol": symbol,
            "name": str(field(item, "name_zh") or field(item, "name_en") or symbol),
            "catalog_verified": True,
            "status": "subscribing",
            "quote": None,
            "requested": False,
            "requested_at": utcnow().isoformat(),
        }
        self.entries[symbol] = entry
        self.emit("validated", symbol=symbol)
        try:
            data = self.broker.invoke("USData.get_snapshots", self.broker.api.USData.get_snapshots, symbol)
            raw = data.get(symbol) if isinstance(data, dict) else None
            if raw is not None:
                self.accept(symbol, field(raw, "close"), field(raw, "timestamp"), utcnow(), "snapshot")
        except Exception:
            self.emit("snapshot_unavailable", symbol=symbol)
        if not self.channel:
            entry["status"] = "unavailable"
            entry["message"] = "目前 SDK 未提供 USQuote 訂閱"
            return
        # One request per desired symbol/session. Failure does not create a retry storm.
        entry["requested"] = True
        self.emit("subscribe_requested", symbol=symbol)
        key = "qtTickv1.USStock." + symbol
        if key not in self.keys():
            self.broker.invoke("USQuote.subscribe_tick", self.channel.subscribe_tick, symbol)
        entry["status"] = "waiting" if key in self.keys() else "subscribing"

    def accept(self, symbol, price, stamp, received, kind):
        entry = self.entries[symbol]
        price = decimal(price)
        if price <= 0:
            raise ValueError("無有效行情價格")
        parsed, naive = quote_time(stamp)
        zoned = parsed.isoformat() if parsed else None
        old = entry.get("quote")
        if old and old.get("source_time") and parsed and parsed < source_time(old["source_time"]):
            return
        if old and naive is not None:
            _, old_naive = quote_time(old.get("raw_time"))
            if old_naive is not None and naive < old_naive:
                return
        # A zero-volume update must not replace the last actual trade with a reference.
        if old and old["kind"] == "trade" and kind != "trade":
            return
        entry["quote"] = {
            "price": str(price),
            "source_time": zoned,
            "raw_time": str(stamp)[:64],
            "received_time": received.isoformat(),
            "kind": kind,
            "source": "SuperPy USData 快照" if kind == "snapshot" else "SuperPy USQuote Tick",
        }
        if kind == "trade" and (not old or old["kind"] != "trade"):
            self.emit("first_trade", symbol=symbol, quote=entry["quote"])

    def sync(self, symbols):
        self.broker.require_selected()
        self.desired = set(symbols)
        if self.session != self.broker.callback_generation:
            self.bind()
        for symbol in set(self.entries) - set(symbols):
            entry = self.entries[symbol]
            if entry.get("release_failed"):
                continue
            try:
                self.remove(symbol)
            except Exception as exc:
                entry.update(
                    status="error", release_failed=True, message="行情解除未確認；保留紀錄，登出時再清理"
                )
                self.emit("release_failed", symbol=symbol, error_type=type(exc).__name__)
        for symbol in set(symbols) - set(self.entries):
            try:
                self.add(symbol)
            except Exception as exc:
                entry = self.entries.setdefault(
                    symbol, {"symbol": symbol, "quote": None, "catalog_verified": False}
                )
                entry.update(status="error", message="商品查核或行情訂閱失敗，請確認代號／行情權限後重新查核")
                self.emit("error", symbol=symbol, error_type=type(exc).__name__)
        with self.lock:
            pending, events = self.pending, self.events
            self.pending, self.events = {}, []
        for code, response in events:
            self.emit("event", code=code, response=response)
            if code in {
                "DISCONNECTED",
                "HEARTBEAT_TIMEOUT",
                "WS_TIMEOUT",
                "WS_ERROR",
                "RECONNECT_FAILED",
                "RECONNECT_MAX_REACHED",
                "SUBSCRIBE_FAIL",
            }:
                for entry in self.entries.values():
                    entry["status"] = "error"
                    entry["message"] = "行情通道異常，等待恢復或重新查核"
                # A buffered pre-error tick cannot prove recovery.
                pending.clear()
            elif code in {"RECONNECTED", "CONNECTED"}:
                for entry in self.entries.values():
                    entry["status"] = "waiting"
                    entry["quote"] = None
                # The SDK owns quote-channel reconnect. Reconcile required subscriptions once.
                if self.channel:
                    try:
                        keys = self.keys()
                        for symbol in self.desired:
                            if "qtTickv1.USStock." + symbol not in keys:
                                self.broker.invoke(
                                    "USQuote.subscribe_tick", self.channel.subscribe_tick, symbol
                                )
                    except Exception as exc:
                        for entry in self.entries.values():
                            entry["status"] = "error"
                        self.emit("resubscribe_failed", error_type=type(exc).__name__)
                        pending.clear()
        for symbol, raw in pending.items():
            if symbol not in self.entries:
                continue
            try:
                volume = decimal(raw["volume"])
                if volume < 0:
                    raise ValueError("無效成交量")
                kind = "trade" if volume > 0 else "reference"
                self.accept(symbol, raw["close"], raw["timestamp"], raw["received"], kind)
                self.entries[symbol]["status"] = "receiving"
            except (ValueError, TypeError, ArithmeticError):
                self.entries[symbol]["status"] = "error"
                self.emit("invalid_tick", symbol=symbol)

    def snapshot(self, max_ages=None):
        result = deepcopy(self.entries)
        for symbol, entry in result.items():
            quote = entry.get("quote")
            if self.broker.disconnected.is_set():
                entry["status"] = "disconnected"
            elif quote and entry["status"] not in {"error", "unavailable", "disconnected"}:
                if not quote["source_time"]:
                    entry["status"] = "time_unverified"
                else:
                    age = (utcnow() - source_time(quote["source_time"])).total_seconds()
                    receive_age = (utcnow() - source_time(quote["received_time"])).total_seconds()
                    if not -2 <= age <= (max_ages or {}).get(symbol, 15) or receive_age > (
                        max_ages or {}
                    ).get(symbol, 15):
                        entry["status"] = "stale"
                    elif quote["kind"] == "trade":
                        entry["status"] = "live"
            previous = self.entries[symbol].get("reported_status")
            if entry["status"] != previous:
                self.emit("state", symbol=symbol, previous=previous, status=entry["status"])
                self.entries[symbol]["reported_status"] = entry["status"]
        return result
