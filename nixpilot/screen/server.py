"""FastMCP wiring for the screen profile: one read-only tool (`screenshot`)
and the screen://state resource. Tool contracts live in the repo README;
results carry the frame metadata (dimensions, sha256, source state) so
follow-up profile tools — the planned ui_scan among them — can cross-
reference the exact bytes and return frame-relative coordinates."""

from __future__ import annotations

import dataclasses
import json
import logging

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

from . import capture
from .capture import CaptureError, Frame

log = logging.getLogger("nixpilot.screen.server")

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False)


def build_server(*, base_url: str, timeout_s: float) -> FastMCP:
    mcp = FastMCP(
        "nixpilot-screen-mcp",
        instructions=(
            "Read-only screen observation for the TinyPilot KVM. screenshot"
            " returns the most recent captured frame from the device's"
            " uStreamer instance as a JPEG image block plus metadata; the"
            " target machine is never driven. Call status via the"
            " screen://state resource to check the capture pipeline."
        ),
    )

    def _run(func):
        try:
            return func()
        except CaptureError as exc:
            log.error("tool failure: %s", exc)
            raise ValueError(str(exc)) from exc

    @mcp.tool(annotations=_READ)
    def screenshot() -> list:
        """Capture the target machine's current screen: the most recent frame
        from uStreamer as a JPEG image block, with metadata (dimensions, byte
        size, sha256, capture-source state) in a second text block. Read-only:
        uStreamer serves its internal frame queue; the target is not driven."""

        frame: Frame = _run(lambda: capture.fetch_frame(base_url, timeout_s))
        return [
            Image(data=frame.jpeg, format="jpeg"),
            json.dumps(frame.metadata(), indent=2),
        ]

    @mcp.resource("screen://state")
    def stream_state() -> str:
        """Capture pipeline state: source online/resolution/fps, encoder,
        client count, and the uStreamer base URL being observed."""
        state = _run(lambda: capture.fetch_stream_state(base_url, timeout_s))
        payload = {"url": base_url, **dataclasses.asdict(state)}
        return json.dumps(payload, indent=2)

    return mcp
