"""FastMCP wiring for the gamepad profile. Tool contracts live in the repo
README; every write result carries the delivery caveat because the HID
function has no readback channel."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import report
from .gamepad import Gadget, GadgetError, PressRequest, SequenceStep

log = logging.getLogger("nixpilot.server")

DELIVERY_NOTE = (
    "delivery note: success means the USB gadget accepted the report; the"
    " target machine's interpretation must be verified visually (no readback)"
)

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)

_LAYOUT = {
    "profile": "gamepad",
    "report_length": 8,
    "bytes": {
        "0": "buttons 1-8 (bit i = button i+1)",
        "1": "buttons 9-16",
        "2": "left.x (X)",
        "3": "left.y (Y)",
        "4": "right.x (Z)",
        "5": "right.y (Rz)",
        "6": "hat switch, low nibble: 0=N..7=NW, 0xF neutral",
        "7": "constant zero padding",
    },
    "buttons": {name: pos for pos, name in enumerate(report.BUTTON_NAMES)},
    "linux_input_codes": {
        name: 0x130 + pos for pos, name in enumerate(report.BUTTON_NAMES)
    },
    "hat": dict(report.HAT_WIRE),
    "axes": "tool domain -1.0..1.0; byte 0x7F = center; +1.0 -> 255; -1.0 -> 0",
    "descriptor_base64": (
        "BQEJBaEBBQkZASkQFQAlAXUBlRCBAgUBCTAJMQkyCTUVACb/AHUIlQSBAgk5FQAlBzUA"
        "RjsBZQB1BJUBgUJ1BJUBgQN1CJUBgQPA"
    ),
    "device_docs": "tinypilot workspace: tinypilot-docs/usb-gadget.md, tinypilot-docs/m5-gate.md",
}


def build_server(gadget: Gadget) -> FastMCP:
    mcp = FastMCP(
        "nixpilot-mcp",
        instructions=(
            "Input tools for the TinyPilot USB gadget. The gamepad profile"
            " drives the connected target machine; use reset when done."
        ),
    )

    def _run(func, /, **kwargs):
        try:
            data = func()
        except GadgetError as exc:
            log.error("tool failure: %s", exc)
            raise ValueError(str(exc)) from exc
        data.setdefault("note", DELIVERY_NOTE)
        return data

    @mcp.tool(annotations=_WRITE)
    def set_state(update: report.StateUpdate) -> dict:
        """Assert the gamepad state (partial merge: fields you provide replace
        current values, absent fields keep theirs). Writes exactly one HID
        report; state is sticky until the next write or watchdog expiry."""

        return _run(lambda: {"state": gadget.set_state(update).as_dict()})

    @mcp.tool(annotations=_WRITE)
    def press(buttons: list[report.Button], hold_ms: int = 200) -> dict:
        """Momentarily assert buttons: writes the merged report, waits
        hold_ms server-side (0..10000), then re-asserts the pre-call state.
        Atomic single-writer round-trip — no follow-up release call needed."""

        return _run(
            lambda: gadget.press(PressRequest(buttons=buttons, hold_ms=hold_ms))
        )

    @mcp.tool(annotations=_WRITE)
    def sequence(steps: list[SequenceStep]) -> dict:
        """Timed macro: 1..100 steps of press / set_state / delay_ms. The
        total duration (holds + delays, capped at 30 s) is pre-flight checked
        before any report is written. Executed under one lock acquisition."""

        return _run(lambda: gadget.sequence(steps))

    @mcp.tool(annotations=_WRITE)
    def reset() -> dict:
        """Full idle: clear buttons, center both sticks, hat neutral, disarm
        the watchdog. The recommended recovery verb."""

        return _run(lambda: {"state": gadget.reset().as_dict()})

    @mcp.tool(annotations=_READ)
    def status() -> dict:
        """Probe the gamepad gadget: node, UDC binding, asserted state,
        watchdog, single-writer lock. Recommended first call of a session."""

        return _run(gadget.status)

    @mcp.tool(annotations=_WRITE)
    def send_raw(hex: str) -> dict:
        """Write a raw 8-byte HID report (16 hex characters) for protocol
        parity with the tinypilot workspace's gamepad-smoke vectors. Byte 7
        (constant padding) must be 00; the report is decoded back into the
        tracked state."""

        return _run(lambda: {"state": gadget.send_raw(hex).as_dict()})

    @mcp.resource("gadget://gamepad/layout")
    def layout() -> str:
        """Static gamepad profile layout (wire format + mappings)."""
        return json.dumps(_LAYOUT, indent=2)

    @mcp.resource("gadget://gamepad/state")
    def state() -> str:
        """Last-asserted gamepad state, watchdog deadline, writer lock."""
        return json.dumps(gadget.status(), indent=2)

    return mcp
