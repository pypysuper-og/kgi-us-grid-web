"""Build a public source-only release from an allowlist; never copy a live environment."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile

from tools.windows_support import inventory, source_manifest, write_report

ROOT = Path(__file__).resolve().parent
FILES = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "LICENSE",
    "run.py",
    "launch.pyw",
    "restore.py",
    "start.vbs",
    "start.cmd",
    "start.ps1",
    "install.vbs",
    "install.cmd",
    "install.ps1",
    "bootstrap.ps1",
    "tools/check_environment.py",
    "tools/windows_support.py",
    "tools/Get-GridAuthenticode.ps1",
    "app/static/workbench-logo.svg",
    "app/static/workbench.ico",
    "build_release.py",
    "tools/build_tauri.py",
    "desktop/Cargo.toml",
    "desktop/Cargo.lock",
    "desktop/build.rs",
    "desktop/tauri.conf.json",
    "desktop/src/main.rs",
    "desktop/assets/index.html",
    "desktop/capabilities/workbench.json",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def payload():
    names = list(FILES)
    names += [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "app").rglob("*")
        if p.is_file() and p.suffix in {".py", ".html", ".css", ".js"} and "__pycache__" not in p.parts
    ]
    result = {}
    for name in sorted(names):
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Release input must not be a symlink: " + name)
        result[name] = path.read_bytes()
    # The maintained workspace projects public prose from packaging/. A clean
    # public checkout already has that prose and its assets at the repository root.
    packaging = ROOT / "packaging"
    public = packaging if packaging.is_dir() else ROOT
    result["README.md"] = (public / "README.md").read_bytes()
    result[".gitignore"] = (public / ("release.gitignore" if packaging.is_dir() else ".gitignore")).read_bytes()
    result[".gitattributes"] = (public / ("release.gitattributes" if packaging.is_dir() else ".gitattributes")).read_bytes()
    for name in ("hero.png", "README.md"):
        result["assets/" + name] = (public / "assets" / name).read_bytes()
    result["WINDOWS-DEPENDENCIES.json"] = (
        json.dumps(source_manifest(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return result


def validate(data):
    forbidden = {".env", ".git", ".venv", ".bootstrap", "logs", "runtime", "data", ".codegraph"}
    for name, content in data.items():
        if forbidden.intersection(Path(name).parts) or Path(name).suffix.lower() in {
            ".exe",
            ".dll",
            ".pyd",
            ".log",
            ".ini",
            ".sqlite3",
        }:
            raise ValueError("Private or generated file in release: " + name)
        if name == "assets/hero.png":
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Hero must be a PNG image")
            continue
        if name == "app/static/workbench.ico":
            if len(content) < 22 or content[:4] != b"\x00\x00\x01\x00" or not int.from_bytes(content[4:6], "little"):
                raise ValueError("Workbench icon must be a Windows ICO image")
            continue
        text = content.decode("utf-8-sig")
        if re.search(r"(?i)\b[a-z]:[\\/]+(?:users|warehouse)[\\/]+", text):
            raise ValueError("Private machine detail in release: " + name)


def verify(directory):
    expected = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        value, name = line.split("  ", 1)
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
            raise ValueError("Invalid manifest path")
        expected[name] = value
        if sha(path.read_bytes()) != value:
            raise ValueError("Release hash mismatch: " + name)
    actual = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(directory).parts
    }
    if actual != set(expected) | {"SHA256SUMS"}:
        raise ValueError("Release has extra or missing files; never rebuild over an installed copy")
    validate({name: (directory / name).read_bytes() for name in expected})
    return len(expected) + 1


def build(destination=None):
    destination = (destination or ROOT / "release").resolve()
    if destination != ROOT / "release":
        raise ValueError("Build destination is the repository-owned release folder")
    if (destination / ".git").exists():
        raise ValueError("Release is a publication Git checkout; preserve it and update its files explicitly")
    data = payload()
    validate(data)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    inputs = list(data) + ["packaging"]
    modified = bool(subprocess.check_output(["git", "status", "--porcelain", "--", *inputs], cwd=ROOT))
    content_fingerprint = sha(b"".join(name.encode() + content for name, content in sorted(data.items())))
    version = tomllib.loads(data["pyproject.toml"].decode())["project"]["version"]
    data["RELEASE.json"] = (
        json.dumps(
            {
                "name": "kgi-us-grid-web",
                "version": version,
                "source_commit": commit,
                "source_modified": modified,
                "content_fingerprint": content_fingerprint,
                "type": "source",
                "sdk": "2.1.2",
                "python": "3.12-x64",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode()
    data["SHA256SUMS"] = "".join(
        f"{sha(content)}  {name}\n" for name, content in sorted(data.items())
    ).encode()
    if destination.exists():
        verify(destination)
        history = ROOT / "dist/release-history" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        history.parent.mkdir(parents=True, exist_ok=True)
        # Both absolute paths are fixed under this repository; retain the previous complete generation.
        destination.rename(history)
    destination.mkdir()
    for name, content in data.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    count = verify(destination)
    archive = ROOT / "dist" / f"KGI-US-Grid-Web-{version}-{commit[:8]}-{content_fingerprint[:8]}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in sorted(data.items()):
                info = zipfile.ZipInfo("KGI-US-Grid-Web/" + name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, content)
    with zipfile.ZipFile(archive) as readback:
        expected_names = ["KGI-US-Grid-Web/" + name for name in sorted(data)]
        if (
            sorted(readback.namelist()) != expected_names
            or readback.testzip() is not None
            or any(readback.read("KGI-US-Grid-Web/" + name) != content for name, content in data.items())
        ):
            raise ValueError("ZIP readback mismatch")
    archive.with_suffix(".zip.sha256").write_text(
        sha(archive.read_bytes()) + "  " + archive.name + "\n", encoding="ascii"
    )
    inventory_path = archive.with_suffix(".windows-inventory.json")
    write_report(inventory_path, inventory([("project", destination)], project=destination, scope="release"))
    return {
        "folder": str(destination),
        "files": count,
        "archive": str(archive),
        "sha256": sha(archive.read_bytes()),
        "windows_inventory": str(inventory_path),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"files": verify(ROOT / "release")} if args.verify else build(), ensure_ascii=False))
