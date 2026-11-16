"""Read-only Windows artifact inventory and redacted startup failure facts.

Neither local Authenticode metadata nor a completed scan is a SAC acceptance verdict.
This module uses only the standard library and never imports the brokerage SDK.
"""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import struct
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CONTROLLED_SCRIPTS = ("start.vbs", "install.vbs", "start.ps1", "install.ps1", "bootstrap.ps1",
                      "tools/Get-GridAuthenticode.ps1")
NATIVE_SUFFIXES = {".exe", ".dll", ".pyd", ".ocx", ".node"}
MUTABLE_SUFFIXES = {".log", ".db", ".db-wal", ".db-shm", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pe_file(path):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            return False
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset < 64 or offset > Path(path).stat().st_size - 4:
            return False
        stream.seek(offset)
        return stream.read(4) == b"PE\0\0"


def pe_bytes(content):
    if len(content) < 64 or content[:2] != b"MZ":
        return False
    offset = struct.unpack_from("<I", content, 60)[0]
    return 64 <= offset <= len(content) - 4 and content[offset:offset + 4] == b"PE\0\0"


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def policy_hint():
    result = {"sac_registry_hint": "UNAVAILABLE", "active_policy": "NOT_VERIFIED"}
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\CI\Policy") as key:
                result["sac_registry_hint"] = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")[0]
        except OSError:
            pass
    return result


def failure_details(exc, *, file=None, module=None, project=None, file_evidence="selected_executable"):
    """Preserve codes and bounded code-file identity, never raw SDK exception text."""
    project = Path(project or ROOT).resolve()
    result = {"error_type": type(exc).__name__, "sac_cause": "NOT_ESTABLISHED"}
    selected_file = file is not None
    chain = exc
    for _ in range(4):
        if chain is None:
            break
        for attribute in ("winerror", "errno", "hresult"):
            value = getattr(chain, attribute, None)
            if isinstance(value, int) and not isinstance(value, bool):
                result.setdefault(attribute, value)
        file = file or getattr(chain, "filename", None) or getattr(chain, "path", None)
        chain = chain.__cause__ or chain.__context__
    if module and module in {"fastapi", "uvicorn", "pydantic", "tzdata", "exchange_calendars",
                            "openpyxl", "kgisuperpy", "paramiko", "tqdm", "websocket", "webview"}:
        result["module"] = module
    if file:
        try:
            path = Path(file).resolve()
            if path.suffix.lower() in NATIVE_SUFFIXES | {".py", ".pyw", ".ps1", ".vbs"}:
                for label, root in (("project", project), ("python", Path(sys.base_prefix).resolve())):
                    if path.is_relative_to(root) and path.is_file() and not path.is_symlink():
                        result["file"] = label + "/" + path.relative_to(root).as_posix()
                        result["sha256"] = digest(path)
                        result["file_evidence"] = file_evidence if selected_file else "exception_attribute"
                        break
        except (OSError, ValueError):
            result["file_evidence"] = "UNAVAILABLE"
    result["windows_build"] = platform.version() if os.name == "nt" else "NOT_WINDOWS"
    result.update(policy_hint())
    return result


def signature_metadata(paths, timeout=30):
    """One bounded host call; preserve completed rows if a trust lookup times out."""
    if not paths or os.name != "nt":
        return {}, "NOT_WINDOWS" if os.name != "nt" else None
    host = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helper = ROOT / "tools/Get-GridAuthenticode.ps1"
    # Python launched from PowerShell 7 can inherit its incompatible module path.
    # Select only this Windows PowerShell host's built-in modules in the child.
    environment = dict(os.environ)
    environment["PSModulePath"] = str(host.parent / "Modules")
    try:
        completed = subprocess.run(
            [str(host), "-NoProfile", "-NonInteractive", "-File", str(helper),
             "-TimeBudgetSeconds", str(max(1, timeout - 5))],
            input=json.dumps([str(path) for path in paths]).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=environment,
        )
        output, issue = completed.stdout, None if completed.returncode == 0 else "SIGNATURE_HOST_FAILED"
    except subprocess.TimeoutExpired as exc:
        output, issue = exc.stdout or b"", "SIGNATURE_TIMEOUT"
    except OSError:
        return {}, "SIGNATURE_HOST_UNAVAILABLE"
    rows = {}
    allowed_statuses = {"Valid", "NotSigned", "HashMismatch", "NotTrusted", "UnknownError",
                        "NotSupportedFileFormat", "Incompatible", "INSPECTION_ERROR"}
    for line in output.decode("utf-8-sig", errors="replace").splitlines():
        try:
            row = json.loads(line)
            index = row["index"]
            if type(index) is int and 0 <= index < len(paths) and row.get("status") in allowed_statuses:
                rows[index] = {key: row[key] for key in
                               ("status", "signature_type", "file_version", "signer", "timestamp_signer", "error_type")
                               if key in row}
        except (ValueError, TypeError, KeyError):
            continue
    if len(rows) != len(paths):
        issue = issue or "SIGNATURE_INCOMPLETE"
    if any(row.get("status") == "INSPECTION_ERROR" for row in rows.values()):
        issue = issue or "SIGNATURE_INSPECTION_ERROR"
    return rows, issue


def reparse_entry(entry):
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return entry.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def inventory(roots, *, project=None, scope="installed", signature_timeout=30, max_files=50000,
              max_seconds=20):
    project = Path(project or ROOT)
    started = time.monotonic()
    enumeration_deadline = started + min(5, max_seconds / 3)
    deadline = started + max_seconds
    files, absolute, issues, candidates = [], [], [], []
    visited, excluded = 0, 0
    seen = set()
    # Enumerate once with cached DirEntry metadata; opening every package asset first
    # makes a large SDK installation unnecessarily slow. Never follow reparse points.
    for label, original in sorted(roots, key=lambda item: item[0] == "environment"):
        root = Path(original)
        if not root.exists():
            issues.append({"scope": label, "reason": "ROOT_MISSING"})
            continue
        if root.is_symlink() or root.is_junction():
            issues.append({"scope": label, "reason": "REPARSE_POINT_SKIPPED"})
            continue
        root_file = root.is_file()
        pending = [] if root_file else [root]
        discovered = [root] if root_file else []
        while pending:
            if time.monotonic() >= enumeration_deadline or len(candidates) + len(discovered) >= max_files:
                issues.append({"scope": label, "reason": "ENUMERATION_LIMIT"})
                break
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if time.monotonic() >= enumeration_deadline or len(candidates) + len(discovered) >= max_files:
                            issues.append({"scope": label, "reason": "ENUMERATION_LIMIT"})
                            pending.clear()
                            break
                        if entry.name in {".git", "__pycache__"}:
                            continue
                        if reparse_entry(entry):
                            issues.append({"scope": label, "reason": "REPARSE_POINT_SKIPPED"})
                        elif entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            discovered.append(Path(entry.path))
            except OSError:
                issues.append({"scope": label, "reason": "DIRECTORY_UNREADABLE"})
        for path in discovered:
            if len(candidates) >= max_files:
                issues.append({"scope": label, "reason": "FILE_LIMIT"})
                break
            if path.suffix.lower() in MUTABLE_SUFFIXES:
                excluded += 1
                continue
            identity = str(path.absolute())
            if identity in seen:
                continue
            seen.add(identity)
            name = (path.relative_to(project).as_posix() if label == "project"
                    else path.name if root_file else path.relative_to(root).as_posix())
            controlled = label == "project" and name in CONTROLLED_SCRIPTS
            priority = 0 if controlled or path.suffix.lower() in NATIVE_SUFFIXES else 1 if path.suffix.lower() in {"", ".bin"} else 2
            candidates.append((priority, label, name, path, controlled))
    native_candidates = sum(item[0] == 0 for item in candidates)
    native_inspected = 0
    for priority, label, name, path, controlled in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        if time.monotonic() >= deadline:
            issues.append({"reason": "DISCOVERY_TIME_LIMIT"})
            break
        visited += 1
        if priority == 0:
            native_inspected += 1
        try:
            native = pe_file(path)
            if not native and not controlled:
                if path.suffix.lower() in NATIVE_SUFFIXES:
                    issues.append({"file": label + "/" + name, "reason": "UNRECOGNIZED_PE_HEADER"})
                continue
            files.append({"file": label + "/" + name, "kind": "PE" if native else "SCRIPT_HOST",
                          "sha256": digest(path), "size": path.stat().st_size,
                          "signature": {"status": "NOT_INSPECTED"}})
            absolute.append(path)
        except OSError:
            issues.append({"file": label + "/" + name, "reason": "FILE_UNREADABLE"})
    discovery_elapsed = time.monotonic() - started
    signatures, signature_issue = signature_metadata(absolute, timeout=signature_timeout)
    for index, row in signatures.items():
        files[index]["signature"] = row
    if signature_issue:
        issues.append({"reason": signature_issue})
    known_native_complete = (native_candidates == native_inspected and
                             all(issue["reason"] == "DISCOVERY_TIME_LIMIT" for issue in issues))
    package_path = project / ".venv/Lib/site-packages"
    versions = {d.metadata["Name"]: d.version for d in metadata.distributions(path=[str(package_path)])}
    return {"schema_version": 1, "scope": scope, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "sac_compatibility": "NOT_TESTED", "trust_evidence": "LOCAL_AUTHENTICODE_ONLY",
            "scan_complete": not issues, "files_discovered": len(candidates), "files_visited": visited,
            "known_native_inventory_complete": known_native_complete,
            "native_candidates_discovered": native_candidates, "native_candidates_inspected": native_inspected,
            "discovery_budget_seconds": max_seconds, "signature_budget_seconds": signature_timeout,
            "discovery_elapsed_seconds": round(discovery_elapsed, 3),
            "excluded_mutable_files": excluded, "python_version": platform.python_version(), "package_versions": versions,
            "policy": policy_hint(), "files": files, "issues": issues,
            "limitations": ["Local signature status does not establish SAC acceptance or cloud reputation.",
                            "Installed files are not proof that every architecture or component was loaded.",
                            "Files are not frozen against concurrent changes during this read-only inventory.",
                            "Discovery deadlines are checked between local reads; incomplete work remains explicit.",
                            "Mutable logs/databases and reparse points are excluded; archives are not expanded."]}


def source_manifest(data):
    """Deterministic public metadata; future downloaded binaries remain uninspected."""
    lock = tomllib.loads(data["uv.lock"].decode("utf-8-sig"))
    project = tomllib.loads(data["pyproject.toml"].decode("utf-8-sig"))["project"]
    files = []
    for name, content in sorted(data.items()):
        native = pe_bytes(content)
        if native or name in CONTROLLED_SCRIPTS:
            files.append({"file": name, "kind": "PE" if native else "SCRIPT_HOST", "sha256": hashlib.sha256(content).hexdigest(),
                          "signature_status": "NOT_INSPECTED_IN_MANIFEST"})
    bootstrap = data.get("bootstrap.ps1", b"").decode("utf-8-sig")
    uv_version = re.search(r"uv-(\d+\.\d+\.\d+)\\uv[.]exe", bootstrap)
    uv_checksum = re.search(r"\$expected\s*=\s*'([0-9a-f]{64})'", bootstrap)
    uv_url = re.search(r"https://github[.]com/astral-sh/uv/releases/download/[^\s']+", bootstrap)
    return {"schema_version": 1, "scope": "SOURCE_PACKAGE", "version": project["version"],
            "sac_compatibility": "NOT_TESTED", "native_runtime": "PREPARED_AT_INSTALLATION",
            "native_payload_count": sum(row["kind"] == "PE" for row in files),
            "python_requirement": project["requires-python"],
            "bootstrap_uv": {"configured_version": uv_version[1] if uv_version else "UNRESOLVED",
                             "archive_sha256": uv_checksum[1] if uv_checksum else "UNRESOLVED",
                             "download_url": uv_url[0] if uv_url else "UNRESOLVED",
                             "actual_selected_uv": "RECORDED_AT_INSTALLATION"},
            "locked_packages": {p["name"]: p["version"] for p in lock.get("package", []) if "version" in p},
            "files": files,
            "installation_report": ".bootstrap/windows-native-inventory.json",
            "limitations": ["A source archive excludes the downloaded Python, uv and wheel native payloads.",
                            "Inspect the installation report and test the actual workflow under SAC enforcement."]}


def installed_inventory(project, *, uv_path=None, output=None):
    project = Path(project)
    roots = [("environment", project / ".venv"), ("python", Path(sys.base_prefix))]
    roots += [("project", project / name) for name in CONTROLLED_SCRIPTS]
    if uv_path:
        roots.append(("uv", Path(uv_path)))
    report = inventory(roots, project=project)
    try:
        report["requirements"] = source_manifest({name: (project / name).read_bytes()
                                                  for name in ("pyproject.toml", "uv.lock", "bootstrap.ps1")})
    except (OSError, ValueError, KeyError):
        report["issues"].append({"reason": "REQUIREMENTS_UNAVAILABLE"})
        report["scan_complete"] = False
    write_report(output or project / ".bootstrap/windows-native-inventory.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--uv-path", type=Path)
    parser.add_argument("--scope", choices=("installed", "release"), default="installed")
    args = parser.parse_args()
    output = args.output or args.project / ".bootstrap/windows-native-inventory.json"
    if args.scope == "installed":
        report = installed_inventory(args.project, uv_path=args.uv_path, output=output)
    else:
        report = inventory([("project", args.project)], project=args.project, scope="release")
        write_report(output, report)
    print(json.dumps({"inventory_written": True, "scan_complete": report["scan_complete"],
                      "files": len(report["files"]), "sac_compatibility": "NOT_TESTED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
