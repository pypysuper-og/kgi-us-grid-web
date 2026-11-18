"""Tauri window child; Python retains exclusive ownership of trading and cleanup."""

import json
from pathlib import Path
import subprocess
import threading
import uuid
import webbrowser

from .desktop import DesktopHost, webview2_version


def executable_path():
    executable = Path(__file__).resolve().parents[1] / "desktop" / "bin" / "us-grid-desktop.exe"
    if not executable.is_file():
        raise ValueError("tauri_desktop_missing")
    return executable


class NativeWindow:
    def __init__(self, host):
        self.host = host

    def run_js(self, script):
        self.host.send(action="eval", script=script)

    def destroy(self):
        self.host.send(action="complete", orders=[])


class TauriHost(DesktopHost):
    """Reuse the existing shutdown owner, replacing only native window operations."""

    def __init__(self, app, finished):
        super().__init__(app, finished)
        self.child = None
        self.write_lock = threading.Lock()

    def send(self, **message):
        with self.write_lock:
            if not self.child or self.child.poll() is not None:
                raise ValueError("桌面視窗已退出")
            self.child.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.child.stdin.flush()

    def activate(self):
        if not self.loaded.is_set():
            raise ValueError("桌面視窗仍在載入，請稍後再開啟")
        self.send(action="activate")
        return {"activated": True}

    def on_loaded(self):
        self.loaded.set()
        self.app.state.desktop_ready = True
        self.service.audit.emit("desktop_ready", renderer="tauri-webview2")
        self.window.run_js("window.setWorkbenchHidden && window.setWorkbenchHidden(" + json.dumps(self.minimized) + ")")

    def request_close(self):
        try:
            self.service.audit.emit("desktop_close_requested")
            # Tauri injects its desktop flag before page scripts. Repeated requests
            # are handled by the maintained frontend confirmation flow.
            if self.loaded.is_set():
                self.window.run_js("window.requestDesktopClose && window.requestDesktopClose()")
            else:
                self.send(action="confirm_loading_close")
        except (OSError, ValueError) as exc:
            self.service.audit.emit("desktop_close_failed", error_type=type(exc).__name__)
        finally:
            self.close_lock.release()

    def watch_backend(self):
        self.finished.wait()
        clean = self.service.cleanup_complete.is_set() and not self.service.thread.is_alive()
        try:
            if clean and self.service.error is None and self.service.get_state().get("closing"):
                self.allow_close = True
                with self.service.lock:
                    completed = [r for r in self.service.results.values() if r.get("result", {}).get("shutdown")]
                    orders = completed[-1]["result"].get("unresolved_orders", []) if completed else []
                self.send(action="complete", orders=orders)
            else:
                self.window.run_js("document.getElementById('notice').textContent='後端已停止或清理未確認，請保留紀錄並查核。'")
        except (OSError, ValueError):
            pass  # An exited native child cannot acknowledge a cleanup result.

    def run(self, url):
        webview2_version()
        self.child = subprocess.Popen(
            [str(executable_path()), url, str(self.service.path.parent / "tauri-webview")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.window = NativeWindow(self)
        self.app.state.activate_callback = self.activate
        threading.Thread(target=self.watch_backend, daemon=True).start()
        try:
            for line in self.child.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                match event.get("event"):
                    case "loaded":
                        self.on_loaded()
                    case "closing":
                        self.on_closing()
                    case "visibility":
                        self.on_visibility(bool(event.get("hidden")))
                    case "loading_close_confirmed":
                        self.service.submit("shutdown", {"cancel": False}, uuid.uuid4().hex)
                    case "external_link":
                        url = event.get("url", "")
                        if isinstance(url, str) and url.startswith(("https://", "http://")):
                            try:
                                webbrowser.open(url)
                            except Exception as exc:
                                self.service.audit.emit("external_link_failed", error_type=type(exc).__name__)
                    case "download_finished":
                        self.service.audit.emit("desktop_download_finished", success=bool(event.get("success")))
            code = self.child.wait()
            self.service.audit.emit("desktop_exited", exit_code=code, loaded=self.loaded.is_set())
            if code or not self.allow_close:
                raise RuntimeError("Tauri 桌面視窗意外退出；請保留紀錄並查核")
        finally:
            self.app.state.desktop_ready = False
            self.child.stdin.close()
            self.child.stdout.close()

