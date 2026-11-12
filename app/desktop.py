"""Native WebView2 host. Window closure follows the existing actor-owned shutdown."""

from pathlib import Path
import json
import sqlite3
import sys
import threading
import uuid


class DesktopAPI:
    def __init__(self, host):
        self._host = host

    def close_completed_window(self):
        if not self._host.allow_close or not self._host.finished.is_set():
            raise ValueError("尚未確認後端完成收尾")
        self._host.window.destroy()
        return {"closed": True}


def webview2_version():
    if sys.platform != "win32":
        raise ValueError("desktop_requires_windows")
    import winreg

    product = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, path in (
        (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + product),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + product),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + product),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
                if isinstance(version, str) and version and version != "0.0.0.0":
                    return version
        except OSError:
            continue
    raise ValueError("webview2_runtime_missing")


def saved_presentation_mode(database: Path):
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            row = db.execute("SELECT presentation_mode FROM application_preferences WHERE id=1").fetchone()
            if row and row[0] in {"desktop", "browser"}:
                return row[0]
    except sqlite3.Error:
        pass
    return "desktop"


class DesktopHost:
    def __init__(self, app, finished):
        self.app = app
        self.service = app.state.service
        self.finished = finished
        self.window = None
        self.loaded = threading.Event()
        self.allow_close = False
        self.minimized = False
        self.close_lock = threading.Lock()

    def activate(self):
        if not self.window or not self.loaded.is_set():
            raise ValueError("桌面視窗仍在載入，請稍後再開啟")
        self.window.show()
        self.window.restore()
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(int(self.window.native.Handle.ToInt64()))
        return {"activated": True}

    def on_loaded(self):
        self.loaded.set()
        self.app.state.desktop_ready = True
        self.service.audit.emit("desktop_ready", renderer="edgechromium")
        self.window.run_js("window.__GRID_DESKTOP__=true")
        self.window.run_js("window.setWorkbenchHidden && window.setWorkbenchHidden(" + json.dumps(self.minimized) + ")")

    def on_visibility(self, hidden):
        self.minimized = hidden
        if self.loaded.is_set():
            self.window.run_js("window.setWorkbenchHidden && window.setWorkbenchHidden(" + json.dumps(hidden) + ")")

    def on_closing(self):
        if self.allow_close:
            return True
        if self.close_lock.acquire(blocking=False):
            threading.Thread(target=self.request_close, daemon=True).start()
        return False

    def request_close(self):
        try:
            self.service.audit.emit("desktop_close_requested")
            if self.loaded.is_set():
                self.window.run_js("window.requestDesktopClose && window.requestDesktopClose()")
            elif self.window.create_confirmation_dialog(
                "確認結束程式", "畫面仍在載入。要停止工作台並結束嗎？既有委託會保留；取消可繼續使用。"
            ):
                self.service.submit("shutdown", {"cancel": False}, uuid.uuid4().hex)
        except Exception as exc:
            self.service.audit.emit("desktop_close_failed", error_type=type(exc).__name__)
        finally:
            self.close_lock.release()

    def watch_backend(self):
        self.finished.wait()
        clean = self.service.cleanup_complete.is_set() and not self.service.thread.is_alive()
        if clean and self.service.error is None and self.service.get_state().get("closing"):
            self.allow_close = True
            if self.window:
                with self.service.lock:
                    completed = [r for r in self.service.results.values() if r.get("result", {}).get("shutdown")]
                    unresolved = completed[-1]["result"].get("unresolved_orders", []) if completed else []
                if unresolved:
                    self.window.run_js("window.completeDesktopShutdown(" + json.dumps(unresolved) + ")")
                else:
                    self.window.destroy()
        elif self.window and self.loaded.is_set():
            self.service.audit.emit("desktop_backend_unconfirmed", cleanup_complete=clean)
            self.window.run_js("document.getElementById('notice').textContent='後端已停止或清理未確認，請保留紀錄並查核。'")

    def run(self, url):
        webview2_version()
        import webview

        if sys.platform == "win32":
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("USGrid.Workbench")
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        self.window = webview.create_window(
            "美股網格工作台", url=url, width=1440, height=900,
            min_size=(900, 650), background_color="#101010", text_select=True, zoomable=True,
            js_api=DesktopAPI(self),
        )
        self.window.events.loaded += self.on_loaded
        self.window.events.closing += self.on_closing
        self.window.events.minimized += lambda: self.on_visibility(True)
        self.window.events.restored += lambda: self.on_visibility(False)
        self.app.state.activate_callback = self.activate
        threading.Thread(target=self.watch_backend, daemon=True).start()
        webview.start(gui="edgechromium", private_mode=True, debug=False,
                      icon=str(Path(__file__).parent / "static/workbench.ico"))

