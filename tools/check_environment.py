"""Validate an installed environment without login or trading effects."""

import argparse
from contextlib import redirect_stdout, redirect_stderr
import hashlib
from importlib import metadata, util, import_module
import io
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".bootstrap" / "environment.json"
MODULES = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "tzdata",
    "exchange_calendars",
    "openpyxl",
    "kgisuperpy",
    "paramiko",
    "tqdm",
    "websocket",
)


def fingerprint():
    files = ("pyproject.toml", "uv.lock", ".python-version", "bootstrap.ps1", "tools/check_environment.py")
    return hashlib.sha256(b"".join((ROOT / name).read_bytes() for name in files)).hexdigest()


def packages():
    return {d.metadata["Name"].lower().replace("_", "-"): d.version for d in metadata.distributions()}


def check(verify=False):
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise ValueError("python_version_or_architecture")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        raise ValueError("wrong_virtual_environment")
    if not Path(sys.executable).with_name("pythonw.exe").is_file():
        raise ValueError("windowless_python_missing")
    if not verify:
        receipt = json.loads(MARKER.read_text(encoding="utf-8"))
        if receipt["fingerprint"] != fingerprint() or receipt["packages"] != packages():
            raise ValueError("environment_changed")
    if metadata.version("kgisuperpy") != "2.1.2":
        raise ValueError("broker_version")
    for name in MODULES:
        if util.find_spec(name) is None:
            raise ModuleNotFoundError(name)
    if verify:
        # SDK imports are checked once during installation, never call its login.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for name in MODULES:
                import_module(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--write-marker", action="store_true")
    args = parser.parse_args()
    try:
        check(args.verify)
        if args.write_marker:
            if not args.verify:
                raise ValueError("verify_required")
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            temporary = MARKER.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"fingerprint": fingerprint(), "packages": packages()}, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(MARKER)
        print(json.dumps({"ready": True, "python": ".".join(map(str, sys.version_info[:3]))}))
        return 0
    except Exception as exc:
        # No raw SDK exception, personal paths or environment variables in setup output.
        print(json.dumps({"ready": False, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
