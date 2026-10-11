"""Deterministic moving-average ledger projection, never a broker balance substitute."""

from collections import defaultdict
import hashlib
import json

from .models import ZERO, ET, decimal
from datetime import datetime


def project(strategies, fills, ledger, day=None):
    fees = {}
    opening_costs = {}
    entries = []
    for row in ledger:
        data = json.loads(row["data"])
        if row["kind"] == "fee":
            fees[data["order_id"]] = data
        elif row["kind"] == "opening_cost":
            opening_costs[row["strategy_id"]] = data["cost"]
        elif row["kind"] == "opening":
            entries.append(
                {
                    **data,
                    "strategy_id": row["strategy_id"],
                    "time": row["time"],
                    "side": "opening",
                    "order_id": None,
                }
            )
    totals = defaultdict(int)
    for fill in fills:
        totals[fill["order_id"]] += fill["quantity"]
    for fill in fills:
        fee = fees.get(fill["order_id"])
        allocated = (decimal(fee["amount"]) * fill["quantity"] / totals[fill["order_id"]]) if fee else ZERO
        entries.append({**fill, "fee": allocated, "fee_known": fee is not None})
    provisional = {f["strategy_id"] for f in fills if f.get("time_quality", "source") != "source"}
    # A strategy with unzoned fills uses receipt order consistently; never mix guessed
    # source dates with receipt dates and accidentally place a sell before its buy.
    entries.sort(
        key=lambda x: (
            datetime.fromisoformat(
                x.get("received_at", x["time"]) if x["strategy_id"] in provisional else x["time"]
            ),
            x.get("received_at", ""),
        )
    )
    configs = {s["id"]: s for s in strategies}
    result = {}
    daily = defaultdict(lambda: {"realized": ZERO, "known": True, "buy_value": ZERO, "sell_value": ZERO})
    limit_buys = defaultdict(lambda: ZERO)
    for entry in entries:
        sid = entry["strategy_id"]
        if sid not in configs:
            continue
        state = result.setdefault(
            sid,
            {"quantity": 0, "cost": ZERO, "realized": ZERO, "realized_known": True, "fees_complete": True},
        )
        qty = entry["quantity"]
        dated = entry.get("time_quality", "source") == "source"
        d = datetime.fromisoformat(entry["time"]).astimezone(ET).date().isoformat() if dated else None
        bucket = daily[(sid, d)]
        if entry["side"] == "opening":
            state["quantity"] += qty
            cost = opening_costs.get(sid, entry["cost"])
            state["cost"] = None if cost is None else decimal(cost)
            continue
        value = decimal(entry["value"])
        fee = entry["fee"]
        state["fees_complete"] &= entry["fee_known"]
        if entry["side"] == "buy":
            state["quantity"] += qty
            if state["cost"] is not None:
                state["cost"] += value + fee
            bucket["buy_value"] += value
            limit_day = (
                datetime.fromisoformat(entry["time"] if dated else entry["received_at"])
                .astimezone(ET)
                .date()
                .isoformat()
            )
            limit_buys[(sid, limit_day)] += value
        else:
            if qty > state["quantity"]:
                raise ValueError("成交賣出數量超過已歸屬部位，需對帳")
            cost_out = None if state["cost"] is None else state["cost"] * qty / state["quantity"]
            pnl = None if cost_out is None else value - fee - cost_out
            state["quantity"] -= qty
            if state["cost"] is not None:
                state["cost"] -= cost_out
            if not state["quantity"]:
                state["cost"] = ZERO
            state["realized_known"] &= pnl is not None
            bucket["known"] &= pnl is not None
            if pnl is not None:
                state["realized"] += pnl
                bucket["realized"] += pnl
            bucket["sell_value"] += value
    rows = []
    for sid, strategy in configs.items():
        state = result.get(
            sid,
            {"quantity": 0, "cost": ZERO, "realized": ZERO, "realized_known": True, "fees_complete": True},
        )
        filtered = [v for (s, d), v in daily.items() if s == sid and (day is None or d == day)]
        period_known = all(v["known"] for v in filtered)
        period_realized = sum((v["realized"] for v in filtered), ZERO)
        average = (
            state["cost"] / state["quantity"] if state["quantity"] and state["cost"] is not None else None
        )
        rows.append(
            {
                "strategy_id": sid,
                "name": strategy["params"]["name"],
                "symbol": strategy["params"]["symbol"],
                "mode": strategy["mode"],
                "owner": strategy["owner"],
                "currency": "USD",
                "quantity": state["quantity"],
                "cost": None if state["cost"] is None else str(state["cost"]),
                "average_cost": None if average is None else str(average),
                "realized": str(period_realized)
                if period_known and not (day and sid in provisional)
                else None,
                "time_complete": sid not in provisional,
                "cost_provisional": sid in provisional,
                "undated_buy_value": str(daily[(sid, None)]["buy_value"]),
                "undated_sell_value": str(daily[(sid, None)]["sell_value"]),
                "limit_buy_value": str(
                    sum(
                        (v for (s, d), v in limit_buys.items() if s == sid and (day is None or day == d)),
                        ZERO,
                    )
                ),
                "fees_complete": state["fees_complete"],
                "buy_value": str(sum((v["buy_value"] for v in filtered), ZERO)),
                "sell_value": str(sum((v["sell_value"] for v in filtered), ZERO)),
            }
        )
    settlement = {}
    details = []
    for fill in fills:
        sid = fill["strategy_id"]
        dated = fill.get("time_quality", "source") == "source"
        date = datetime.fromisoformat(fill["time"]).astimezone(ET).date().isoformat() if dated else None
        if sid not in configs or (day is not None and date != day):
            continue
        currency = fill.get("settlement_currency", "unknown")
        bucket = settlement.setdefault(
            (sid, currency),
            {
                "strategy_id": sid,
                "name": configs[sid]["params"]["name"],
                "symbol": configs[sid]["params"]["symbol"],
                "settlement_currency": currency,
                "buy_quantity": 0,
                "sell_quantity": 0,
                "buy_value_usd": ZERO,
                "sell_value_usd": ZERO,
                "time_complete": True,
            },
        )
        bucket[fill["side"] + "_quantity"] += fill["quantity"]
        bucket[fill["side"] + "_value_usd"] += decimal(fill["value"])
        bucket["time_complete"] &= dated
        details.append(
            {
                **fill,
                "name": configs[sid]["params"]["name"],
                "symbol": configs[sid]["params"]["symbol"],
                "settlement_currency": currency,
            }
        )
    for row in rows:
        row["current_settlement_currency"] = configs[row["strategy_id"]]["params"].get("currency", "unknown")
        row["historical_settlement_currencies"] = sorted(
            {f.get("settlement_currency", "unknown") for f in fills if f["strategy_id"] == row["strategy_id"]}
        )
    settlement_rows = [
        {**r, "buy_value_usd": str(r["buy_value_usd"]), "sell_value_usd": str(r["sell_value_usd"])}
        for r in settlement.values()
    ]
    version = hashlib.sha256(json.dumps([fills, ledger], sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]
    return {
        "day": day,
        "timezone": "America/New_York",
        "method": "moving_average",
        "version": version,
        "rows": rows,
        "settlement_rows": settlement_rows,
        "fill_details": details,
        "holdings_as_of": "current",
    }
