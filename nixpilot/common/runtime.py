"""Runtime bootstrap shared by every nixpilot profile entrypoint: stderr
logging (stdout is the MCP protocol stream) and environment parsing with
fail-fast exit codes (3 = bad configuration)."""

from __future__ import annotations

import logging
import os
import sys


def setup_logging() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("NIXPILOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def env_number(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logging.getLogger("nixpilot.runtime").error(
            "%s must be a number, got %r", name, raw
        )
        raise SystemExit(3) from None
