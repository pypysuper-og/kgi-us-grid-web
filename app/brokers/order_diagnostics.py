"""Public US report fields only; no vendor internals or credential-bearing rows."""

import hashlib
from pathlib import Path

from ..audit import Redactor

REPORT_FIELDS = (
    "create_time",
    "trade_date",
    "symbol",
    "market",
    "BuySell",
    "action",
    "price",
    "qty",
    "replace_price",
    "replace_qty",
    "trade_currency",
    "settle_currency",
    "seqnum",
    "orig_seqnum",
    "orderno",
    "order_err_code",
    "order_err_msg",
    "sales_status",
    "sales_status_code",
    "exe_qty",
    "exe_avg_price",
    "exe_status",
    "exe_status_code",
)
SALES = {
    "0": "預約單",
    "1": "委託上手中",
    "2": "委託成功",
    "3": "委託失敗",
    "4": "逾期單",
    "5": "作廢單",
    "6": "無效單",
    "7": "處理中",
    "8": "已下單至交易室",
    "9": "處理失敗",
    "10": "上手系統處理中",
}
EXECUTIONS = {"0": "未成交", "1": "部分成交", "2": "全部成交"}


def text(value):
    if value is None:
        return ""
    result = str(value)
    return "" if result.lower() in {"nan", "nat", "<na>"} else result


def error_catalog(path=None):
    """Follow SDK 2.1.2's working-directory UTF-8 lookup, preserving collisions."""
    path = Path(path) if path else Path.cwd() / "errMsg.ini"
    try:
        data = path.read_bytes()
        source = data.decode("utf-8-sig")
    except (OSError, UnicodeError):
        return {}, {
            "available": False,
            "sha256": None,
            "message": "errMsg.ini 無法讀取；保留券商原始碼與原因",
        }
    entries = {}
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        entries.setdefault(key, []).append(value)
    return entries, {"available": True, "sha256": hashlib.sha256(data).hexdigest(), "message": ""}


def normalize_rows(records, symbol, catalog, redactor=None):
    redactor = redactor or Redactor()
    rows = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("券商委託報表列格式不符")
        if text(record.get("symbol")) != symbol:
            continue
        row = {key: text(record.get(key)) for key in REPORT_FIELDS}
        code = row["order_err_code"]
        # The SDK uses digits for its lookup; retain the unmodified code separately.
        lookup = "".join(c for c in code if c.isdigit())
        values = list(dict.fromkeys(catalog.get(lookup, []))) if code not in {"", "0"} else []
        row["ini_code"] = lookup
        row["ini_message"] = redactor.text(values[-1]) if values else ""
        row["ini_ambiguous"] = len(values) > 1
        row["order_err_msg"] = redactor.text(row["order_err_msg"])
        row["sales_status"] = redactor.text(row["sales_status"])
        row["sales_label"] = SALES.get(row["sales_status_code"], "未知狀態，保留原值")
        row["exe_label"] = EXECUTIONS.get(row["exe_status_code"], "未知狀態，保留原值")
        rows.append(row)
    return rows


def report_matches(order, report):
    """Require a strong identity and reject conflicting identity evidence."""
    org, bid = report.get("broker_org"), report.get("broker_id")
    trusted = report.get("tracked_ref")
    if trusted and trusted != order["client_ref"]:
        return False
    if order.get("broker_org") and org and order["broker_org"] != org:
        return False
    if order.get("broker_id") and bid and order["broker_id"] != bid:
        return False
    return bool(
        trusted == order["client_ref"]
        or (org and order.get("broker_org") == org)
        or (report.get("client_ref") and report["client_ref"] == order["client_ref"])
    )
