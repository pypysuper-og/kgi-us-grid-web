"""Correlated, redacted diagnostics readable even while the SDK actor is blocked."""

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import errno
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


class AuditUnavailable(ValueError):
    """No new brokerage effect may pass an incomplete audit checkpoint."""


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
        self._clock = time.monotonic
        self._pending = deque()
        self._pending_bytes = 0
        self.max_pending_records = 2000
        self.max_pending_bytes = 8 * 1024 * 1024
        self._missing_records = 0
        self._missing_first = None
        self._missing_last = None
        self._write_failure = None
        self._retry_due = 0.0
        self._retry_delay = 1.0
        self._retry_attempt = 0
        self._partial_tail = False
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

    def _record(self, event, data):
        self.seq += 1
        return {
            "schema": 1,
            "time": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "seq": self.seq,
            "event": event,
            "command_id": COMMAND.get(),
            "call_id": CALL.get(),
            **self.clean(data),
        }

    def _enqueue(self, row):
        encoded = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        if (
            len(self._pending) >= self.max_pending_records
            or self._pending_bytes + len(encoded) > self.max_pending_bytes
        ):
            self._missing_records += 1
            self._missing_first = self._missing_first or row["seq"]
            self._missing_last = row["seq"]
            self.error = "稽核緩衝已滿，存在紀錄缺漏；停止新增實單，請保存紀錄並查核"
            return False
        self._pending.append((row, encoded))
        self._pending_bytes += len(encoded)
        return True

    def _write_failed(self, exc, now):
        winerror = getattr(exc, "winerror", None)
        reason = (
            "檔案暫被其他程序使用"
            if winerror in (32, 33)
            else "磁碟空間不足"
            if exc.errno == errno.ENOSPC or winerror == 112
            else "檔案寫入被拒絕（可能被佔用或權限不足）"
            if exc.errno in (errno.EACCES, errno.EPERM) or winerror == 5
            else "儲存裝置寫入異常"
        )
        # Native exception strings may contain private paths; retain codes, not the payload.
        self._write_failure = {
            "error_type": type(exc).__name__,
            "errno": exc.errno,
            "winerror": winerror,
            "reason": reason,
        }
        self._retry_attempt += 1
        self._retry_due = now + self._retry_delay
        self._retry_delay = min(30.0, self._retry_delay * 2)
        if not self._missing_records:
            self.error = "稽核寫入暫不可用；停止新增實單並自動重試，監控與委託查核持續"

    def _drain(self, now, durable=False):
        try:
            with self.path.open("a+b") as file:
                while self._pending:
                    row, encoded = self._pending[0]
                    file.seek(0, 2)
                    end = file.tell()
                    # Flush/fsync may have failed after the complete row was appended.
                    # Do not append that same (run_id, seq) a second time.
                    file.seek(max(0, end - len(encoded)))
                    already_appended = end >= len(encoded) and file.read() == encoded
                    if not already_appended:
                        if end:
                            file.seek(end - 1)
                            if file.read(1) != b"\n":
                                file.write(b"\n")
                                self._partial_tail = True
                        file.write(encoded)
                    file.flush()
                    if durable or row["event"] in ("effect_dispatch", "command_received"):
                        os.fsync(file.fileno())
                    self._pending.popleft()
                    self._pending_bytes -= len(encoded)
            return True
        except OSError as exc:
            self._write_failed(exc, now)
            return False

    def retry_pending(self, now=None):
        """Retry only audit records, never SDK operations, with a 1..30 second backoff."""
        with self.lock:
            if not self.error:
                return True
            if self._write_failure is None:
                return False
            now = self._clock() if now is None else now
            if now < self._retry_due:
                return False
            pending = len(self._pending)
            if not self._drain(now, durable=True):
                return False
            event = "audit_gap_checkpoint" if self._missing_records else "audit_recovered"
            self._enqueue(
                self._record(
                    event,
                    {
                        **self._write_failure,
                        "attempt": self._retry_attempt,
                        "buffered_records": pending,
                        "partial_tail_preserved": self._partial_tail,
                        "missing_records": self._missing_records,
                        "missing_seq_first": self._missing_first,
                        "missing_seq_last": self._missing_last,
                    },
                )
            )
            if not self._drain(now, durable=True):
                return False
            if self._missing_records:
                self._retry_due = now + 30
                return False  # Successful storage cannot undo evidence that was dropped.
            self.error = None
            self._write_failure = None
            self._retry_attempt = 0
            self._retry_due = 0.0
            self._retry_delay = 1.0
            self._partial_tail = False
            return True

    def emit(self, event, **data):
        with self.lock:
            row = self._record(event, data)
            self._enqueue(row)
            if self.error:
                self.retry_pending()
            else:
                self._drain(self._clock())
            return row

    def require_writable(self):
        if self.error:
            raise AuditUnavailable(self.error)

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
                "writer": {
                    **(self._write_failure or {}),
                    "pending_records": len(self._pending),
                    "pending_bytes": self._pending_bytes,
                    "missing_records": self._missing_records,
                    "attempt": self._retry_attempt,
                    "retry_in_seconds": max(0, self._retry_due - self._clock()) if self.error else None,
                    "recoverable": bool(self._write_failure) and not self._missing_records,
                },
            }

    def status(self):
        with self.lock:
            return {"seq": self.seq, "error": self.error}

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
