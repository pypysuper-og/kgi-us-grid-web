"""Correlated, redacted diagnostics readable even while the SDK actor is blocked."""

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import hashlib
import inspect
from importlib.metadata import version, PackageNotFoundError
import platform
import io
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote
import uuid

COMMAND = ContextVar("command_id", default=None)
CALL = ContextVar("call_id", default=None)


def broker_reference(org, order_id):
    """Stable join key even when numeric broker identifiers must be redacted."""
    return hashlib.sha256(f"{org or ''}:{order_id or ''}".encode()).hexdigest()[:24]


class Redactor:
    def __init__(self):
        self.secrets = set()

    def add(self, *values):
        for value in values:
            if value:
                value = str(value)
                self.secrets.update(
                    (value, repr(value)[1:-1], json.dumps(value)[1:-1], quote(value, safe=""))
                )

    def text(self, value):
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[已遮蔽]")
        text = re.sub(r"(?i)\b[A-Z][12]\d{8}\b", "[身分證已遮蔽]", text)
        if re.search(
            r"(?i)(password|passwd|pwd|token|authorization|cookie|secret|person_id|customer_id|account|密碼|帳號)['\"]?\s*[:=]",
            text,
        ):
            return "[驗證／帳戶欄位已遮蔽]"
        text = re.sub(r"\b\d{7,}\b", "[長號碼已遮蔽]", text)
        text = re.sub(r"(?i)(https?://[^\s?]+)\?\S+", r"\1?[查詢參數已遮蔽]", text)
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)[:8192]


class ScreenStream(io.TextIOBase):
    def __init__(self, audit):
        self.audit = audit
        self.pending = ""
        self.discard = False
        self.lock = threading.RLock()
        self.flags = set()

    def write(self, text):
        with self.lock:
            for i, part in enumerate(text.split("\n")):
                if i:
                    self.line()
                if not self.discard:
                    if len(self.pending) + len(part) > 8192:
                        self.pending = ""
                        self.discard = True
                    else:
                        self.pending += part
        return len(text)

    def line(self):
        for marker in ("IsSucceed:True", "CA 驗證通過", "API 加載完成"):
            if marker in self.pending:
                self.flags.add(marker)
        self.audit.screen("[過長輸出略過]" if self.discard else self.pending)
        self.pending = ""
        self.discard = False

    def flush(self):
        pass  # A partial write/flush is not a credential-safe line boundary.

    def finish(self):
        with self.lock:
            self.line()


class Audit:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id = uuid.uuid4().hex
        self.path = self.directory / f"audit-{datetime.now(timezone.utc):%Y%m%d}-{self.run_id}.jsonl"
        self.lock = threading.RLock()
        self.seq = 0
        self.lines = deque(maxlen=300)
        self.redactor = Redactor()
        self.error = None
        self.connection = {
            "phase": "logged_out",
            "production": False,
            "selected": None,
            "attempt": 0,
            "retry_at": None,
        }
        root = Path(__file__).parent
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*") if p.suffix in (".py", ".js", ".css", ".html")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        for path in (root.parent / "run.py", root.parent / "uv.lock"):
            if path.exists():
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
        packages = {}
        for package in ("kgisuperpy", "fastapi", "uvicorn", "pydantic"):
            try:
                packages[package] = version(package)
            except PackageNotFoundError:
                packages[package] = "not installed"
        self.emit(
            "process_start",
            source_sha256=digest.hexdigest(),
            pid=os.getpid(),
            python=platform.python_version(),
            packages=packages,
        )

    def clean(self, value):
        if isinstance(value, dict):
            return {
                str(k): (
                    "[已遮蔽]"
                    if re.search(r"password|person_id|token|secret|account$", str(k), re.I)
                    else self.clean(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value[:200]]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return self.redactor.text(value)

    def emit(self, event, **data):
        with self.lock:
            self.seq += 1
            row = {
                "schema": 1,
                "time": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "seq": self.seq,
                "event": event,
                "command_id": COMMAND.get(),
                "call_id": CALL.get(),
                **self.clean(data),
            }
            try:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    file.flush()
                    if event in ("effect_dispatch", "command_received"):
                        os.fsync(file.fileno())
            except OSError:
                self.error = "稽核紀錄無法寫入；停止新增實單，已送出的結果仍須查核"
            return row

    def require_writable(self):
        if self.error:
            raise ValueError(self.error)

    def screen(self, text):
        with self.lock:
            text = self.redactor.text(text)
            if text.strip():
                row = self.emit("sdk_output", message=text)
                self.lines.append({"seq": row["seq"], "time": row["time"], "message": text})

    def set_connection(self, **fields):
        with self.lock:
            if all(self.connection.get(k) == v for k, v in fields.items()):
                return
            self.connection.update(fields)
            self.emit("connection_changed", **fields)

    def snapshot(self):
        with self.lock:
            return {
                "run_id": self.run_id,
                "seq": self.seq,
                "lines": list(self.lines),
                "connection": dict(self.connection),
                "error": self.error,
            }

    @contextmanager
    def call(self, name, **parameters):
        parent = CALL.get()
        token = CALL.set(uuid.uuid4().hex)
        start = time.monotonic()
        self.emit("call_started", function=name, parent_call_id=parent, parameters=parameters)
        try:
            yield
        except Exception as exc:
            self.emit(
                "call_error",
                function=name,
                error_type=type(exc).__name__,
                message=str(exc),
                duration_ms=round((time.monotonic() - start) * 1000, 2),
            )
            raise
        else:
            self.emit("call_returned", function=name, duration_ms=round((time.monotonic() - start) * 1000, 2))
        finally:
            CALL.reset(token)


def observed(method):
    """Instrument business boundaries; never dump objects, credentials or frames."""
    signature = inspect.signature(method)
    allowed = {
        "strategy_id",
        "sid",
        "revision",
        "cancel",
        "symbol",
        "price",
        "order_id",
        "oid",
        "side",
        "amount",
        "confirm",
        "day",
        "status",
        "cumulative",
        "cumulative_value",
        "trade_time",
        "org",
        "broker_id",
        "owner",
        "mode",
    }

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        audit = getattr(self, "audit", None)
        if audit is None:
            return method(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        safe = {k: v for k, v in bound.arguments.items() if k in allowed}
        with audit.call(type(self).__name__ + "." + method.__name__, **safe):
            return method(self, *args, **kwargs)

    return wrapped
