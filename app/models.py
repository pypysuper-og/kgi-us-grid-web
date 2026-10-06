from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ZERO = Decimal("0")
ET = ZoneInfo("America/New_York")
TW = ZoneInfo("Asia/Taipei")
TERMINAL = {"filled", "canceled", "rejected", "expired"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def decimal(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("數值必須有限")
    return result


class StrategyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    symbol: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9./_-]+$")
    mode: Literal["demo", "paper", "live"] = "demo"
    direction: Literal["both", "buy", "sell"] = "both"
    start_price: Decimal = Field(gt=0, max_digits=16, decimal_places=6)
    gap: Decimal = Field(gt=0, max_digits=16, decimal_places=6)
    gap_unit: Literal["amount", "percent"] = "amount"
    quantity: int = Field(gt=0, le=1000000, strict=True)
    min_inventory: int = Field(default=0, ge=0, strict=True)
    initial_inventory: int = Field(default=0, ge=0, strict=True)
    max_inventory: int = Field(gt=0, le=10000000, strict=True)
    initial_cost: Decimal | None = Field(default=None, ge=0)
    opening_confirmed: bool = False
    lower_price: Decimal = Field(gt=0)
    upper_price: Decimal = Field(gt=0)
    max_order_value: Decimal = Field(gt=0)
    daily_buy_limit: Decimal = Field(gt=0)
    currency: Literal["MUT", "TWD"] = "MUT"
    end_date: date
    quote_max_age: int = Field(default=15, ge=1, le=60)
    pause_buys_below_lower: bool = Field(default=False, strict=True)

    @field_validator("name", "symbol")
    @classmethod
    def clean_text(cls, value, info):
        value = value.strip()
        if not value:
            raise ValueError("不可空白")
        return value.upper() if info.field_name == "symbol" else value

    @model_validator(mode="after")
    def validate_bounds(self):
        if not self.min_inventory <= self.initial_inventory <= self.max_inventory:
            raise ValueError("庫存須符合 最少 ≤ 期初 ≤ 最多")
        if not self.lower_price <= self.start_price <= self.upper_price:
            raise ValueError("起始價須位於價格界線內")
        if self.initial_inventory and not self.opening_confirmed:
            raise ValueError("請明確確認期初部位與成本來源")
        if self.initial_inventory == 0 and self.initial_cost not in (None, ZERO):
            raise ValueError("沒有期初庫存時不可填期初成本")
        # This product's first version admits cent-priced stocks at >= USD 1.
        if self.lower_price < 1:
            raise ValueError("首版僅支援價格至少 1 元的商品")
        for value in (self.start_price, self.price_gap, self.lower_price, self.upper_price):
            if value != value.quantize(Decimal("0.01")):
                raise ValueError("首版價位與換算後間距須為 0.01 的整數倍")
        return self

    @property
    def price_gap(self):
        return self.start_price * self.gap / 100 if self.gap_unit == "percent" else self.gap


class Quote(BaseModel):
    symbol: str
    price: Decimal = Field(gt=0)
    source_time: datetime | None = None
    raw_source_time: str | None = None
    source_updated: bool = False
    received_time: datetime
    generation: int
    source: str

    @field_validator("source_time", "received_time")
    @classmethod
    def aware(cls, value):
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("行情時間缺少時區")
        return value

    def fresh(self, now: datetime, age: int):
        if self.source_time is None:
            return self.source_updated and -2 <= (now - self.received_time).total_seconds() <= age
        return all(-2 <= (now - t).total_seconds() <= age for t in (self.source_time, self.received_time))


def grid_action(params: StrategyInput, anchor: Decimal, inventory: int, price: Decimal):
    buy, sell = anchor - params.price_gap, anchor + params.price_gap
    if (
        params.direction in ("both", "buy")
        and price <= buy
        and (not params.pause_buys_below_lower or price >= params.lower_price)
        and params.lower_price <= buy <= params.upper_price
        and inventory + params.quantity <= params.max_inventory
    ):
        return "buy", buy
    if (
        params.direction in ("both", "sell")
        and price >= sell
        and params.lower_price <= sell <= params.upper_price
        and inventory - params.quantity >= params.min_inventory
    ):
        return "sell", sell
    return None


def plan_rows(params: StrategyInput, anchor=None, inventory=None):
    anchor = params.start_price if anchor is None else anchor
    inventory = params.initial_inventory if inventory is None else inventory
    rows = []
    for side, sign, cap in (
        ("sell", 1, inventory - params.min_inventory),
        ("buy", -1, params.max_inventory - inventory),
    ):
        if params.direction not in ("both", side):
            continue
        for level in range(1, min(cap // params.quantity, 500) + 1):
            price = anchor + sign * params.price_gap * level
            if not params.lower_price <= price <= params.upper_price:
                break
            rows.append(
                {
                    "side": side,
                    "price": str(price),
                    "quantity": params.quantity,
                    "inventory": inventory - sign * params.quantity * level,
                }
            )
    return rows


class GridLayout(BaseModel):
    """Price intervals, not outstanding orders or position sizing."""

    model_config = ConfigDict(extra="forbid")
    lower_price: Decimal = Field(ge=1)
    upper_price: Decimal = Field(gt=1)
    intervals: int | None = Field(default=None, ge=1, le=1000, strict=True)
    basis: Literal["count", "gap"] = "count"
    gap: Decimal | None = Field(default=None, gt=0)
    start_price: Decimal | None = Field(default=None, ge=1)

    def calculate(self, draft=False):
        low, high = self.lower_price, self.upper_price
        if high <= low:
            raise ValueError("上界必須大於下界")
        span = high - low
        if self.basis == "gap":
            if self.gap is None:
                raise ValueError("請填入間距")
            gap = self.gap
            count = span / gap
        else:
            if self.intervals is None:
                raise ValueError("請填入格子數")
            count = Decimal(self.intervals)
            gap = span / count
        start = self.start_price if self.start_price is not None else (low + high) / 2
        errors = []
        if not low <= start <= high:
            errors.append("起始價須位於上下界內")
        cent = Decimal("0.01")
        if any(v != v.quantize(cent) for v in (low, high)):
            errors.append("上下界必須精確到美分（0.01）")
        if start != start.quantize(cent):
            errors.append(f"起始價 {start} 未精確到美分（0.01）；請自訂起始價")
        if gap != gap.quantize(cent):
            errors.append(f"間距 {gap} 不是美分（0.01）的整數倍；請調整格數或間距")
        integral = count == int(count) and 1 <= count <= 1000
        if not integral:
            errors.append(f"此間距反算為 {count} 格；格數須為 1～1000 的整數")
        suggestions = []
        if gap != gap.quantize(cent) or not integral:
            candidates = [n for n in range(1, 1001) if span % (cent * n) == 0]
            for n in sorted(candidates, key=lambda n: (abs(Decimal(n) - count), n))[:2]:
                suggestions.append({"intervals": n, "gap": str(span / n)})
        error = "；".join(errors)
        if error and not draft:
            raise ValueError(error)
        return {
            "valid": not errors,
            "error": error,
            "suggestions": suggestions,
            "gap": str(gap),
            "gap_unit": "amount",
            "start_price": str(start),
            "intervals": int(count) if integral else str(count),
            "levels": int(count) + 1 if integral else None,
            "anchor_aligned": not errors and (start - low) % gap == 0,
            "prices": [str(low + gap * i) for i in range(int(count) + 1)] if not errors else [],
        }
