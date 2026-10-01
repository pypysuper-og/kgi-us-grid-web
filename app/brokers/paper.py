from copy import deepcopy
from decimal import Decimal
import uuid

from ..models import TERMINAL, decimal, utcnow


class PaperBroker:
    """Independent, serializable matching book. Never invokes SuperPy."""

    def __init__(self, saved=None):
        self.book = deepcopy(saved or {"orders": {}, "positions": {}})

    def opening(self, symbol, quantity):
        self.book["positions"][symbol] = self.book["positions"].get(symbol, 0) + quantity

    def create(self, order):
        oid = uuid.uuid4().hex
        self.book["orders"][oid] = {
            **order,
            "broker_org": oid,
            "broker_id": "P-" + oid[:12],
            "status": "working",
            "filled": 0,
            "filled_value": "0",
            "time": None,
        }
        return oid, self.book["orders"][oid]["broker_id"]

    def cancel(self, org):
        order = self.book["orders"][org]
        if order["status"] not in TERMINAL:
            order["status"] = "canceled"

    def quote(self, symbol, price, fill_limit=None):
        for order in self.book["orders"].values():
            if order["symbol"] != symbol or order["status"] in TERMINAL:
                continue
            limit = decimal(order["price"])
            if (order["side"] == "buy" and price > limit) or (order["side"] == "sell" and price < limit):
                continue
            quantity = order["quantity"] - order["filled"]
            if fill_limit is not None:
                quantity = min(quantity, fill_limit)
            if order["side"] == "sell":
                quantity = min(quantity, self.book["positions"].get(symbol, 0))
            if not quantity:
                continue
            self.book["positions"][symbol] = self.book["positions"].get(symbol, 0) + (
                quantity if order["side"] == "buy" else -quantity
            )
            order["filled"] += quantity
            order["filled_value"] = str(decimal(order["filled_value"]) + Decimal(quantity) * limit)
            order["status"] = "filled" if order["filled"] == order["quantity"] else "partially_filled"
            order["time"] = utcnow().isoformat()

    def reports(self):
        return list(deepcopy(self.book["orders"]).values())

    def positions(self):
        return deepcopy(self.book["positions"])
