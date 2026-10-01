"""Windowless entry. Keep native output out of support logs; retain sanitized audit."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


def bootstrap(event, **fields):
    directory = Path(
        os.environ.get(
            "KGI_GRID_RUNTIME", Path(os.environ["LOCALAPPDATA"]) / "KGI_US_Grid_Trading_Web" / "runtime"
        )
    )
    if "--runtime" in sys.argv:
        directory = Path(sys.argv[sys.argv.index("--runtime") + 1])
    logs = directory.resolve().parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "bootstrap.jsonl").open("a", encoding="utf-8") as output:
        output.write(
            json.dumps(
                {"at": datetime.now(timezone.utc).isoformat(), "event": event, "pid": os.getpid(), **fields}
            )
            + "\n"
        )


def main():
    # pythonw has no console streams; libraries still expect writable stdout/stderr.
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    os.chdir(Path(__file__).resolve().parent)
    environment_guard = None
    try:
        import ctypes
        import msvcrt

        # Keep this environment in use until the server stops; install must not mutate it.
        directory = Path(__file__).resolve().parent / ".bootstrap"
        directory.mkdir(exist_ok=True)
        environment_guard = (directory / "install.lock").open("a+b")
        environment_guard.seek(0, 2)
        if not environment_guard.tell():
            environment_guard.write(b"0")
            environment_guard.flush()
        environment_guard.seek(0)
        msvcrt.locking(environment_guard.fileno(), msvcrt.LK_NBLCK, 1)

        bootstrap(
            "launcher_started",
            windowless=Path(sys.executable).name.lower() == "pythonw.exe",
            console_attached=bool(ctypes.windll.kernel32.GetConsoleWindow()),
        )
        from tools.check_environment import check

        check()
        from run import main as run_server

        run_server()
        bootstrap("launcher_finished", outcome="normal")
        return 0
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            return 0
        try:
            bootstrap("launcher_failed", error_type=type(exc).__name__)
        finally:
            if "--no-browser" not in sys.argv:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    None,
                    "啟動失敗或程式異常結束。請查看 logs/bootstrap.jsonl 與稽核紀錄；不要重送交易。",
                    "KGI 美股網格工作台",
                    0x10,
                )
        return 1
    finally:
        if environment_guard:
            environment_guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
