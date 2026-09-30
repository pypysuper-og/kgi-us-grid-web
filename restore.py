"""Restore a verified backup into a NEW runtime directory; never overwrite a live DB."""

import argparse
from pathlib import Path
import sqlite3

from app.server import RuntimeLock


def restore(source: Path, destination: Path):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("還原目的目錄必須尚不存在；保留現有帳本與 WAL")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        if (
            original.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or original.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("來源完整性檢查失敗")
        if original.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("來源 schema 不相容")
        # mkdir without exist_ok arbitrates concurrent attempts before creating any DB.
        destination.mkdir(parents=True, exist_ok=False)
        guard = RuntimeLock(destination / "instance.lock")
        guard.acquire()
        try:
            target = destination / "trading.sqlite3"
            with sqlite3.connect(target) as restored:
                original.backup(restored)
                if (
                    restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or restored.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise ValueError("還原後完整性檢查失敗；保留失敗目錄供查核")
        finally:
            guard.release()
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("new_runtime", type=Path)
    args = parser.parse_args()
    restored = restore(args.backup, args.new_runtime)
    print(f"已還原至 {restored}；下次啟動保持暫停，仍須重新對帳。")
