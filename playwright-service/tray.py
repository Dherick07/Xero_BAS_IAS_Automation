"""
tray.py — Xero Reports Automation system tray launcher.

Double-click (or run via desktop shortcut) to start the FastAPI server
in the background and display a system tray icon.

Usage:
    pythonw tray.py     ← no console window (recommended)
    python tray.py      ← with console window (debugging)
"""

import threading
import webbrowser
import time
import os
import sys
import subprocess

import uvicorn
import pystray
from PIL import Image, ImageDraw

APP_URL = "http://localhost:8000"
PORT = 8000
HOST = "0.0.0.0"

# Path helpers
_BASE = os.path.dirname(os.path.abspath(__file__))
_ICON_PATH = os.path.join(_BASE, "assets", "icon.ico")

# Chrome remote debugging — Playwright connects to this port for bot-detection bypass
CHROME_DEBUG_PORT = 9222
CHROME_PROFILE_DIR = os.path.join(_BASE, ".xero-chrome-profile")

_chrome_process = None


def _find_chrome() -> str | None:
    """Find Chrome executable on Windows via common paths."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _launch_chrome() -> None:
    """Launch Chrome with remote debugging so Playwright can connect to it later."""
    global _chrome_process
    chrome_exe = _find_chrome()
    if not chrome_exe:
        webbrowser.open(APP_URL)
        return
    _chrome_process = subprocess.Popen([
        chrome_exe,
        f"--remote-debugging-port={CHROME_DEBUG_PORT}",
        f"--user-data-dir={CHROME_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",  # Ensures outerWidth == screen width, avoiding Akamai viewport mismatch
        APP_URL,
    ])


def _make_icon_image() -> Image.Image:
    """Generate a simple green circle icon programmatically."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Green circle background
    draw.ellipse([2, 2, size - 2, size - 2], fill=(66, 153, 225, 255))
    # White "X" letter (simplified as two rectangles)
    m = size // 4
    draw.rectangle([m, m, m + 4, size - m], fill="white")
    draw.rectangle([m, m, size - m, m + 4], fill="white")
    return img


def _load_icon() -> Image.Image:
    """Load icon from file, or fall back to generated icon."""
    if os.path.exists(_ICON_PATH):
        return Image.open(_ICON_PATH)
    return _make_icon_image()


def _start_server():
    """Start uvicorn in this thread. Blocking."""
    # Ensure the app directory is on the path
    sys.path.insert(0, _BASE)
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="warning",  # Suppress info logs when running in tray mode
    )


def _open_ui(icon, item):
    webbrowser.open(APP_URL)


def _quit_app(icon, item):
    global _chrome_process
    if _chrome_process:
        _chrome_process.terminate()
    icon.stop()
    # uvicorn doesn't have a clean shutdown from outside thread;
    # os._exit is acceptable for a tray app
    os._exit(0)


def main():
    # Start FastAPI server in background thread (daemon so it dies with main thread)
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()

    # Wait for server to be ready (poll health endpoint)
    print("Starting Xero Reports service...")
    for _ in range(20):
        time.sleep(0.5)
        try:
            import urllib.request
            urllib.request.urlopen(f"{APP_URL}/api/health", timeout=1)
            break
        except Exception:
            pass

    # Auto-open browser on first start (with remote debugging port for Playwright)
    _launch_chrome()

    # Build tray icon
    icon_image = _load_icon()
    menu = pystray.Menu(
        pystray.MenuItem("Open Xero Reports", _open_ui, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit_app),
    )
    icon = pystray.Icon(
        name="XeroReports",
        icon=icon_image,
        title="Xero Reports Automation",
        menu=menu,
    )
    icon.run()


if __name__ == "__main__":
    main()
