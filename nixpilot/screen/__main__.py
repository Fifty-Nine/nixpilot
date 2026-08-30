"""Entrypoint: python -m nixpilot.screen (wrapped as the nixpilot-screen-mcp
binary).

Environment:
    NIXPILOT_USTREAMER_URL    uStreamer base URL (default http://127.0.0.1:48001;
                              the service binds loopback only)
    NIXPILOT_SCREEN_TIMEOUT_S per-request HTTP timeout (default 10)
    NIXPILOT_LOG_LEVEL        stderr log level (default INFO)

Read-only profile: no device state, no session lock, no watchdog — concurrent
client sessions are safe. Exit codes: 0 clean run, 3 bad configuration.
"""

from __future__ import annotations

import logging
import os
import urllib.parse

from ..common.runtime import env_number, setup_logging
from .server import build_server

log = logging.getLogger("nixpilot.screen.main")


def main() -> None:
    setup_logging()
    raw_url = os.environ.get("NIXPILOT_USTREAMER_URL", "http://127.0.0.1:48001")
    try:
        parsed = urllib.parse.urlsplit(raw_url)
    except ValueError:
        parsed = None
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.netloc:
        log.error("NIXPILOT_USTREAMER_URL must be an http(s) URL, got %r", raw_url)
        raise SystemExit(3)
    timeout_s = env_number("NIXPILOT_SCREEN_TIMEOUT_S", 10.0)
    if timeout_s <= 0:
        log.error("NIXPILOT_SCREEN_TIMEOUT_S must be positive, got %s", timeout_s)
        raise SystemExit(3)
    # Runs until the client closes stdin.
    build_server(base_url=raw_url.rstrip("/"), timeout_s=timeout_s).run()


if __name__ == "__main__":
    main()
