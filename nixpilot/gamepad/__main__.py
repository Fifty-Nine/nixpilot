"""Entrypoint: python -m nixpilot.gamepad (wrapped as the nixpilot-mcp binary).

Environment:
    NIXPILOT_GAMEPAD_NODE  HID node (default /dev/hidg3)
    NIXPILOT_WATCHDOG_TTL  sticky-state TTL in seconds, 0 disables (default 60)
    NIXPILOT_LOCKFILE      session flock path (default /tmp/nixpilot-gamepad.lock)
    NIXPILOT_LOG_LEVEL     stderr log level (default INFO)
Exit codes: 0 clean run, 2 gadget access failure, 3 bad configuration.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal

from ..common.runtime import env_number, setup_logging
from .gadget import Gadget, GadgetError
from .server import build_server

log = logging.getLogger("nixpilot.gamepad.main")


def main() -> None:
    setup_logging()
    try:
        gadget = Gadget(
            node=os.environ.get("NIXPILOT_GAMEPAD_NODE", "/dev/hidg3"),
            lockfile=os.environ.get("NIXPILOT_LOCKFILE", "/tmp/nixpilot-gamepad.lock"),
            watchdog_ttl=env_number("NIXPILOT_WATCHDOG_TTL", 60.0),
        )
    except GadgetError as exc:
        log.error("%s", exc)
        raise SystemExit(2) from None
    atexit.register(gadget.shutdown)

    def _terminate(_signum, _frame):
        gadget.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    # Runs until the client closes stdin; atexit/`_terminate` restore idle.
    build_server(gadget).run()


if __name__ == "__main__":
    main()
