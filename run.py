"""Start one loopback-only process. No automatic brokerage login."""

import argparse
from pathlib import Path
import socket
import threading
import time
import webbrowser

import uvicorn
from app.server import create_app


class ServerFailure(RuntimeError):
    """Own structured failure evidence; never forward native SDK exception text."""

    def __init__(self, detail, cleanup_confirmed):
        super().__init__("工作台未完成啟動或收尾")
        self.detail = detail
        self.cleanup_confirmed = cleanup_confirmed


class WorkbenchServer(uvicorn.Server):
    """Let a closed browser disappear without leaving a logged-out actor running."""

    shutdown_deadline = None

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            self.config.app.state.service.audit.emit("server_ready")

    async def shutdown(self, sockets=None):
        audit = self.config.app.state.service.audit
        audit.emit(
            "server_shutdown_started",
            connections=len(self.server_state.connections),
            http_tasks=len(self.server_state.tasks),
            http_grace_seconds=self.config.timeout_graceful_shutdown,
        )
        try:
            await super().shutdown(sockets=sockets)
        finally:
            audit.emit(
                "server_shutdown_finished",
                connections=len(self.server_state.connections),
                http_tasks=len(self.server_state.tasks),
            )

    async def on_tick(self, counter):
        if await super().on_tick(counter):
            return True
        service = self.config.app.state.service
        if service.cleanup_complete.is_set() and service.get_state().get("closing"):
            if self.shutdown_deadline is None:
                self.shutdown_deadline = time.monotonic() + 5
            if time.monotonic() >= self.shutdown_deadline:
                service.audit.emit("shutdown_page_ack_timeout", wait_seconds=5)
                self.should_exit = True
                return True
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_app(args.runtime)
    server = WorkbenchServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            workers=1,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=3,
        )
    )
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    finished = threading.Event()
    stage = "listen"
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", args.port))
        sock.listen(128)
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        print(f"KGI US Grid Web 0.1.3: {url}", flush=True)

        def open_browser():
            deadline = time.monotonic() + app.state.service.startup_timeout + 15
            while time.monotonic() < deadline and not server.should_exit and not finished.is_set():
                if server.started:
                    try:
                        opened = webbrowser.open(url)
                        app.state.service.audit.emit("browser_opened", opened=bool(opened))
                    except Exception as exc:
                        app.state.service.audit.emit("browser_open_failed", error_type=type(exc).__name__)
                    return
                finished.wait(0.1)

        if not args.no_browser:
            threading.Thread(target=open_browser, daemon=True).start()
        app.state.service.audit.emit("server_starting", host="127.0.0.1", port=args.port)
        stage = "server_startup"
        server.run(sockets=[sock])
        stage = "shutdown"
        if (
            not app.state.service.cleanup_complete.is_set()
            or app.state.service.thread.is_alive()
            or app.state.service.error is not None
        ):
            raise RuntimeError("背景清理尚未完成；不可宣稱程式已正常結束")
    except BaseException as exc:
        service = app.state.service
        detail = dict(service.startup_failure or {"stage": stage, "reason": "server_error"})
        detail["error_type"] = type(exc).__name__ if "error_type" not in detail else detail["error_type"]
        if isinstance(exc, SystemExit) and isinstance(exc.code, int):
            detail["server_exit_code"] = exc.code
        if isinstance(exc, OSError):
            detail["errno"] = exc.errno
            detail["winerror"] = getattr(exc, "winerror", None)
        cleanup_confirmed = service.thread.ident is None or (
            service.cleanup_complete.is_set() and not service.thread.is_alive()
        )
        service.audit.emit("server_failed", **detail, cleanup_confirmed=cleanup_confirmed)
        raise ServerFailure(detail, cleanup_confirmed) from exc
    finally:
        finished.set()
        sock.close()
        app.state.service.audit.emit(
            "server_stopped",
            actor_alive=app.state.service.thread.is_alive(),
            cleanup_complete=app.state.service.cleanup_complete.is_set(),
        )


if __name__ == "__main__":
    main()
