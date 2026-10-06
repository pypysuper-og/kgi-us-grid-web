"""Start one loopback-only process. No automatic brokerage login."""

import argparse
from pathlib import Path
import socket
import threading
import time
import webbrowser

import uvicorn
from app.server import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_app(args.runtime)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=args.port, workers=1, access_log=False, log_level="warning"
        )
    )
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", args.port))
        sock.listen(128)
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        print(f"KGI US Grid Web 0.1.2: {url}", flush=True)

        def open_browser():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not server.should_exit:
                if server.started:
                    webbrowser.open(url)
                    return
                time.sleep(0.1)

        if not args.no_browser:
            threading.Thread(target=open_browser, daemon=True).start()
        app.state.service.audit.emit("server_starting", host="127.0.0.1", port=args.port)
        server.run(sockets=[sock])
    finally:
        sock.close()
        app.state.service.audit.emit("server_stopped", actor_alive=app.state.service.thread.is_alive())


if __name__ == "__main__":
    main()
