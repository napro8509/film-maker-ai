"""Native macOS window for Film Maker (no browser, no server to start by hand).

The FastAPI backend runs inside this process on a random 127.0.0.1 port and the
UI is shown in a native WKWebView window via pywebview. Closing the window quits
the app.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "FilmMaker.log"


def _login_shell_path() -> None:
    """Apps opened from Finder get a bare PATH; borrow the user's shell PATH so
    gflow, ffmpeg and claude are found like in Terminal."""
    extra = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")]
    try:
        shell = os.environ.get("SHELL") or "/bin/zsh"
        out = subprocess.run([shell, "-lic", "printf %s \"$PATH\""], capture_output=True,
                             text=True, timeout=5, stdin=subprocess.DEVNULL).stdout.strip().splitlines()
        if out:
            extra = out[-1].split(":") + extra
    except Exception:
        pass
    parts = [p for p in extra + os.environ.get("PATH", "").split(":") if p]
    os.environ["PATH"] = ":".join(dict.fromkeys(parts))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _notify(title: str, message: str) -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.Popen(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _check_flow_driver_access() -> None:
    """Run the Flow chat driver once at startup (`--help`, no browser, no credit).

    It reads flow_ui.py from this checkout, so if the checkout is in ~/Desktop/~/Documents macOS asks
    for access now, in Film Maker's name, instead of failing mid-render with "Operation not permitted"."""
    from .gflow import Gflow, _tcc_direct

    cmd = Gflow().ui_command()
    if not cmd:
        return
    args, env = _tcc_direct([*cmd, "--help"])
    try:
        r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[quyền] không chạy được driver Flow: {exc}", flush=True)
        return
    if r.returncode == 0:
        print("[quyền] driver Flow đọc được mã nguồn: OK", flush=True)
        return
    err = (r.stderr or r.stdout or "").strip()[-300:]
    print(f"[quyền] driver Flow lỗi: {err}", flush=True)
    if "Operation not permitted" in err:
        _notify("Film Maker cần quyền truy cập thư mục",
                "Mở Cài đặt hệ thống › Quyền riêng tư & Bảo mật › Tệp và thư mục, bật cho Film Maker rồi mở lại app.")
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Bridge:
    """Functions the page can call as window.pywebview.api.* (things WKWebView
    can't do on its own: notifications and saving downloads)."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.window = None

    def notify(self, message: str) -> None:
        _notify("Film Maker", message)

    def save_download(self, url: str, filename: str) -> str | None:
        import webview

        src = urllib.parse.urljoin(self.base_url, url)
        if not src.startswith(self.base_url):
            return None
        picked = self.window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(Path.home() / "Downloads"), save_filename=filename or "")
        if not picked:
            return None
        dest = Path(picked if isinstance(picked, str) else picked[0])
        with urllib.request.urlopen(src) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return str(dest)


def main(workspace: Path | None = None) -> None:
    try:
        import uvicorn
        import webview
    except ImportError as exc:
        sys.exit(f"cần cài thêm: pip install -e '.[ui,app]' ({exc})")

    from .web.server import create_app

    if not sys.stdout or not sys.stdout.isatty():  # launched from Finder: keep a log file
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log
    _login_shell_path()

    workspace = workspace or Path(os.environ.get("FILMMAKER_WORKSPACE") or Path.home() / "FilmMaker")
    port = _free_port()
    app = create_app(workspace, port=port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None  # we're not on the main thread
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.025)

    threading.Thread(target=_check_flow_driver_access, daemon=True).start()

    base = f"http://127.0.0.1:{port}/"
    bridge = Bridge(base)
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        "Film Maker", base, js_api=bridge, width=1400, height=900, min_size=(900, 600),
        text_select=True, background_color="#111317")
    bridge.window = window

    def on_closing():
        job = app.state.jobs.current
        if not (job and job.status == "running"):
            return True
        import AppKit  # runs on the main thread, so show the alert directly

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Đang có việc chạy")
        alert.setInformativeText_(f"“{job.title}” đang chạy. Thoát bây giờ sẽ dừng ngay, "
                                  "cảnh đang render có thể bị ngắt. Vẫn thoát?")
        alert.addButtonWithTitle_("Ở lại")
        alert.addButtonWithTitle_("Thoát")
        return alert.runModal() != AppKit.NSAlertFirstButtonReturn

    window.events.closing += on_closing
    webview.start(private_mode=False, storage_path=str(Path.home() / "Library/Application Support/FilmMaker"))
    server.should_exit = True


if __name__ == "__main__":
    main()
