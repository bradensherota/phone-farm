"""Desktop entry point: starts the local server and opens it in a native macOS window.

    python -m farm.app              # native window
    python -m farm.app --server     # server only (use with `npm run dev` for the UI)
"""

from __future__ import annotations

import socket
import sys
import threading
import time

import uvicorn

from .config import HOST, PORT


def _wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def run_server() -> None:
    uvicorn.run("farm.main:app", host=HOST, port=PORT, log_level="info", access_log=False, reload=False)


def main() -> None:
    if "--server" in sys.argv:
        run_server()
        return

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    if not _wait_for_port(HOST, PORT):
        print("server failed to start", file=sys.stderr)
        sys.exit(1)

    import webview  # imported late: only needed for the window

    webview.create_window(
        "Phone Farm",
        f"http://{HOST}:{PORT}/",
        width=1480,
        height=940,
        min_size=(1100, 700),
        background_color="#0b0b0d",
    )
    webview.start()


if __name__ == "__main__":
    main()
