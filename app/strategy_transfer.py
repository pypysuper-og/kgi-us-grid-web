"""Versioned parameter files. No execution, account, position or order state travels."""

import hashlib
import json
import re
import uuid

from .editor_library import reusable_params
from .models import StrategyInput, utcnow

FORMAT = "kgi-us-grid-strategies"
VERSION = 2
# Fixed allowlist; v2 permits an unset holding cap. v1 keeps its original contract.
PORTABLE_FIELDS = {
    "name", "symbol", "mode", "direction", "start_price", "gap", "gap_unit",
    "quantity", "max_inventory", "lower_price", "upper_price", "max_order_value",
    "daily_buy_limit", "currency", "end_date", "quote_max_age", "pause_buys_below_lower",
}


def digest(value):
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("檔案不是支援的 JSON 資料") from None
    if len(data.encode("utf-8")) > 500_000:
        raise ValueError("策略檔案最多 500 KB")
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def export_parameters(core, selections):
    if not isinstance(selections, list) or not 1 <= len(selections) <= 15:
        raise ValueError("請選擇1至15組策略匯出")
    entries, seen = [], set()
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"id", "revision"}:
            raise ValueError("匯出選擇格式不符")
        sid = selection["id"]
        if not isinstance(sid, str) or sid in seen or type(selection["revision"]) is not int:
            raise ValueError("請勿重複選擇策略，並使用目前版本")
        row, params = core.params(sid)
        if row["revision"] != selection["revision"]:
            raise ValueError("策略參數已變更，請重新整理後再匯出")
        safe = reusable_params(params.model_dump(mode="json"))
        entries.append({"params": {k: v for k, v in safe.items() if k in PORTABLE_FIELDS}})
        seen.add(sid)
    return {"format": FORMAT, "version": VERSION, "export_id": uuid.uuid4().hex,
            "exported_at": utcnow().isoformat(), "strategies": entries}


def preview_rows(core, document):
    digest(document)
    legacy = isinstance(document, list)
    if legacy:
        records = document
        if not 1 <= len(records) <= 15:
            raise ValueError("策略檔案須包含1至15組策略")
        legacy_rows = core.cmd_import_legacy(records)["rows"]
    else:
        required = {"format", "version", "export_id", "strategies"}
        if not isinstance(document, dict) or not required <= set(document) or set(document) - required - {"exported_at"}:
            raise ValueError("不是支援的策略檔案；請使用本程式匯出的JSON或舊版策略陣列")
        if document["format"] != FORMAT or type(document["version"]) is not int or document["version"] not in {1, VERSION}:
            raise ValueError("不支援的策略檔案格式或版本")
        if not isinstance(document["export_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", document["export_id"]):
            raise ValueError("策略檔案識別不符")
        if "exported_at" in document and (not isinstance(document["exported_at"], str) or len(document["exported_at"]) > 50):
            raise ValueError("匯出時間格式不符")
        records = document["strategies"]
        if not isinstance(records, list) or not 1 <= len(records) <= 15:
            raise ValueError("策略檔案須包含1至15組策略")
    rows, seen_keys = [], set()
    for index, record in enumerate(records):
        item = {"index": index, "name": f"第 {index + 1} 組"}
        try:
            if legacy:
                converted = legacy_rows[index]
                if converted.get("error"):
                    raise ValueError(converted["error"])
                params = reusable_params(converted["params"])
                key = digest(record)
            else:
                if not isinstance(record, dict) or set(record) != {"params"}:
                    raise ValueError("每組只能包含策略參數")
                values = record["params"]
                if not isinstance(values, dict) or set(values) - PORTABLE_FIELDS:
                    raise ValueError("含不支援欄位；不接受庫存、成本、帳戶或執行狀態")
                if document["version"] == 1 and values.get("max_inventory") is None:
                    raise ValueError("版本1策略檔案必須明確提供持股上限")
                params = reusable_params(values)
                owner = "demo" if params["mode"] == "demo" else f'{params["mode"]}:{core.owner}'
                key = f"params-v{document['version']}:" + digest([document["export_id"], index, owner, params])
            item.update(name=params["name"], params=params, import_key=key,
                        note="舊格式轉為離線展示；額外限制請核對" if legacy else "只建立參數；庫存為零、保持暫停")
            item["duplicate"] = key in seen_keys
            seen_keys.add(key)
            existing = core.store.rows("SELECT id FROM strategies WHERE import_key=?", (key,))
            item["existing"] = bool(existing)
            if existing:
                item["id"] = existing[0]["id"]
            if params["mode"] != "demo" and not core.owner:
                item["error"] = "此模式需先登入並選帳，再重新預覽"
        except (ValueError, TypeError, KeyError):
            item["error"] = "參數格式不符；請檢查名稱、商品、價格、股數及檔案欄位"
        rows.append(item)
    return rows, "legacy" if legacy else f"v{document['version']}"


def import_parameters(core, document, apply=False, selected=None, preview_token=None):
    if type(apply) is not bool:
        raise ValueError("請明確確認匯入")
    rows, file_format = preview_rows(core, document)
    token = digest([document, core.owner, core.generation])
    if apply:
        if preview_token != token:
            raise ValueError("檔案或帳戶會話已變更，請重新預覽後確認")
        if not isinstance(selected, list) or not selected or any(type(i) is not int or not 0 <= i < len(rows) for i in selected) or len(set(selected)) != len(selected):
            raise ValueError("請勾選要匯入的策略")
        chosen = [rows[i] for i in selected]
        if any(r.get("error") for r in chosen):
            raise ValueError("所選策略尚未通過檢查；沒有建立任何策略")
        new = [r for r in chosen if not r["existing"] and not r["duplicate"]]
        # All selected products are checked before the first local write.
        for symbol in {r["params"]["symbol"] for r in new if r["params"]["mode"] != "demo"}:
            core.broker.contract(symbol)
        with core.store.transaction():
            if sum(s["status"] != "archived" for s in core.store.strategies()) + len(new) > 15:
                raise ValueError("最多15組有效策略；請減少本次勾選數量")
            for item in new:
                params = StrategyInput.model_validate(item["params"])
                owner = "demo" if params.mode == "demo" else f"{params.mode}:{core.owner}"
                core.store.db.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,?)", (owner, params.mode, owner))
                item["id"] = core.store.add_strategy(params, owner, item["import_key"])
                item.update(created=True, existing=True)
            if new:
                core.store.event("strategy_import", f"已匯入 {len(new)} 組參數，策略保持暫停")
    public_rows = [{k: v for k, v in row.items() if k != "import_key"} for row in rows]
    return {"rows": public_rows, "format": file_format, "preview_token": token,
            "context": {"owner": core.owner, "generation": core.generation},
            "created": sum(bool(r.get("created")) for r in rows), "applied": apply}
