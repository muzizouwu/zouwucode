"""Desktop GUI — the local Web UI hosted in a native window.

The GUI is deliberately the SAME frontend the browser gets (``webui/static``):
one design, one API contract, no duplicate UI to keep in sync. Two launch
paths:

- ``pywebview`` installed (optional extra ``zouwucode[gui]``) → real desktop
  window with native chrome.
- not installed → the default browser opens the same URL, so ``--gui`` never
  dead-ends. The hint tells the user how to get the native window.

The HTTP server runs in a daemon thread with its own event loop; the GUI runs
on the main thread (a hard requirement of every desktop toolkit).
"""

import asyncio
import logging
import socket
import sys
import threading
import time
import webbrowser
from typing import Optional

logger = logging.getLogger("zouwucode.gui")

GUI_URL_TEMPLATE = "http://127.0.0.1:{port}/"
_GUI_TITLE = "ZOUWUCODE"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    """True when something is already accepting connections on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        time.sleep(0.15)
    return False


def _open_window(url: str) -> bool:
    """Open a native window when pywebview is available. False otherwise."""
    try:
        import webview  # noqa: PLC0415 — optional dependency
    except Exception as exc:  # noqa: BLE001 — missing or broken install
        logger.info("pywebview unavailable (%s) — falling back to the browser", exc)
        return False
    try:
        webview.create_window(
            _GUI_TITLE, url, width=1280, height=860, min_size=(960, 600),
        )
        webview.start()
        return True
    except Exception as exc:  # noqa: BLE001 — toolkit backend missing
        logger.warning("native window unavailable: %s", exc)
        return False


def launch_gui(app, port: int = 8080, *, block: bool = True) -> int:
    """Serve the UI and present it in a window (or the browser).

    ``app`` only needs ``run_web(port=...)`` — the same object ``--web`` uses,
    so CLI overrides (model/mode/reasoning/compression) apply identically.
    Returns a process exit code.
    """
    url = GUI_URL_TEMPLATE.format(port=port)

    if port_open(port):
        print(f"  Port {port} is already in use — open another one with --port.",
              file=sys.stderr)
        return 2

    def _serve() -> None:
        try:
            asyncio.run(app.run_web(port=port))
        except Exception as exc:  # noqa: BLE001 — background thread boundary
            logger.error("GUI server stopped: %s", exc)

    threading.Thread(target=_serve, name="zouwucode-gui-server",
                     daemon=True).start()
    if not _wait_for_server(port):
        print(f"  GUI server did not start on {url} within 30s.", file=sys.stderr)
        return 1

    print(f"  ZOUWUCODE GUI → {url}")
    if _open_window(url):
        print("  Native window opened (pywebview) — close it to stop the server.")
        return 0

    opened = webbrowser.open(url, new=2)
    if not opened:
        print(f"  Could not launch a browser automatically. Open {url} manually.")
    print("  Native window unavailable; install it with: "
          "pip install \"zouwucode[gui]\"  (Ctrl+C to stop)")
    if block:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n  Goodbye!")
    return 0
