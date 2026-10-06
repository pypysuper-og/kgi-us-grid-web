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
    run_module = None
    stage = "environment_lock"
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

        stage = "environment_check"
        check()
        stage = "server_import"
        import run as run_module

        run_module.main()
        bootstrap("launcher_finished", outcome="normal")
        return 0
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            return 0
        detail = {"stage": stage, "reason": "launcher_error"}
        cleanup_confirmed = run_module is None
        if run_module is not None and isinstance(exc, run_module.ServerFailure):
            detail = exc.detail
            cleanup_confirmed = exc.cleanup_confirmed
        elif isinstance(exc, SystemExit) and isinstance(exc.code, int):
            detail["server_exit_code"] = exc.code
        # A dismissed error dialog is not part of DB/SDK custody. Release only
        # after the server attests to cleanup, before showing this modal dialog.
        if cleanup_confirmed and environment_guard:
            environment_guard.close()
            environment_guard = None
        try:
            bootstrap(
                "launcher_failed",
                error_type=type(exc).__name__,
                detail=detail,
                cleanup_confirmed=cleanup_confirmed,
            )
        finally:
            if "--no-browser" not in sys.argv:
                import ctypes

                reason = (
                    "工作台載入帳本／市場日曆逾時。"
                    if detail["reason"] == "timeout"
                    else "工作台啟動或背景收尾未完成。"
                )
                cleanup = (
                    "背景清理已完成，可以重新執行 start。"
                    if cleanup_confirmed
                    else "背景清理尚未確認；請關閉此提示並保留紀錄，勿刪除鎖檔或重送交易。"
                )
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"{reason}\n階段：{detail['stage']}；原因：{detail['reason']}。\n"
                    f"{cleanup}\n請查看 logs/bootstrap.jsonl 與 runtime/logs 稽核紀錄。",
                    "KGI 美股網格工作台",
                    0x10,
                )
        return 1
    finally:
        if environment_guard:
            environment_guard.close()


if __name__ == "__main__":
    outcome = main()
    if outcome == 0:
        # run.py has verified actor/DB cleanup; main() has released the environment.
        # Native SDK helpers must not keep a successfully stopped workbench alive.
        os._exit(0)
    raise SystemExit(outcome)
