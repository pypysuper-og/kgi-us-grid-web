"""Persist incomplete drafts and reusable parameters, never execution state."""

import json
import uuid

from .models import StrategyInput, utcnow


FIELDS = set(StrategyInput.model_fields)
LAYOUT_FIELDS = {"wizard", "step", "auto", "intervals", "midpoint", "basis", "reset_anchor", "advanced", "opening"}


def reusable_params(values):
    # Keep sizing limits, but no existing shares, cost attribution or inventory reservation.
    params = StrategyInput.model_validate(values).model_dump(mode="json")
    params.update(initial_inventory=0, initial_cost=None, opening_confirmed=False, min_inventory=0)
    return params


def draft_data(values):
    if not isinstance(values, dict) or set(values) - {"params", "layout", "base"}:
        raise ValueError("草稿格式不符")
    params, layout, base = values.get("params", {}), values.get("layout", {}), values.get("base")
    for data, allowed in ((params, FIELDS), (layout, LAYOUT_FIELDS)):
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("草稿含不支援的欄位")
        if any(type(v) not in (str, int, bool, type(None)) or len(str(v)) > 512 for v in data.values()):
            raise ValueError("草稿欄位格式不符或過長")
    if base is not None and (
        not isinstance(base, dict) or set(base) != {"id", "revision"}
        or not isinstance(base["id"], str) or len(base["id"]) > 80
        or type(base["revision"]) is not int or base["revision"] < 1
    ):
        raise ValueError("草稿策略版本不符")
    return {"params": params, "layout": layout, "base": base}


class EditorLibrary:
    def __init__(self, store):
        self.store = store

    def entries(self):
        return self.store.rows(
            "SELECT id,kind,title,revision,updated_at FROM editor_library ORDER BY updated_at DESC,id"
        )

    def get(self, entry_id):
        rows = self.store.rows("SELECT * FROM editor_library WHERE id=?", (entry_id,))
        if not rows:
            raise ValueError("草稿或模板不存在，請重新整理清單")
        return {**rows[0], "data": json.loads(rows[0]["data"])}

    def save(self, kind, data, title="", entry_id=None, revision=None):
        if kind not in {"draft", "template"}:
            raise ValueError("不支援的保存類型")
        if not isinstance(title, str) or len(title.strip()) > 60:
            raise ValueError("名稱最多60字")
        if kind == "draft":
            saved = draft_data(data)
            title = title.strip() or str(saved["params"].get("name", "")).strip()[:60] or "未命名草稿"
        else:
            if not title.strip():
                raise ValueError("請輸入模板名稱")
            if not isinstance(data, dict) or set(data) != {"params"}:
                raise ValueError("模板只能保存策略參數")
            saved = {"params": reusable_params(data["params"])}
            title = title.strip()
        with self.store.transaction():
            previous = self.get(entry_id) if entry_id else None
            if previous and (previous["kind"] != kind or type(revision) is not int or previous["revision"] != revision):
                raise ValueError("此草稿或模板已在另一個視窗更新，請重新載入；目前輸入仍保留")
            if not previous and len([r for r in self.entries() if r["kind"] == kind]) >= (20 if kind == "draft" else 30):
                raise ValueError("已達保存數量上限，請先刪除不用的草稿或模板")
            key = entry_id or uuid.uuid4().hex
            version = previous["revision"] + 1 if previous else 1
            self.store.db.execute(
                "INSERT INTO editor_library VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,data=excluded.data,"
                "revision=excluded.revision,updated_at=excluded.updated_at",
                (key, kind, title, json.dumps(saved, ensure_ascii=False), version, utcnow().isoformat()),
            )
        return self.get(key)

    def delete(self, entry_id, revision):
        with self.store.transaction():
            previous = self.get(entry_id)
            if type(revision) is not int or revision != previous["revision"]:
                raise ValueError("此草稿或模板已變更，請重新整理後刪除")
            self.store.db.execute("DELETE FROM editor_library WHERE id=?", (entry_id,))
        return {"deleted": True}
