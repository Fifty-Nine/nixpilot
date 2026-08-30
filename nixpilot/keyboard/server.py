"""FastMCP wiring for the keyboard profile. Tool contracts live in the repo
README; every write result carries the delivery caveat: success means the
USB gadget accepted the report, and keystrokes land in whatever window the
target currently has focused."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .gadget import Gadget, GadgetError, PressRequest, SequenceStep, TypeRequest
from .keys import KEY_USAGES, Key, Modifier, StateUpdate

log = logging.getLogger("nixpilot.keyboard.server")

DELIVERY_NOTE = (
    "delivery note: success means the USB gadget accepted the report;"
    " keystrokes are delivered to the target's focused window and must be"
    " verified on the target"
)

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)

_LAYOUT = {
    "profile": "keyboard",
    "report_length": 8,
    "bytes": {
        "0": "modifier bitmap: bits 0..7 = LCTRL LSHIFT LALT LGUI RCTRL RSHIFT RALT RGUI",
        "1": "constant zero (reserved)",
        "2-7": "six key usage slots (ordered set; 0x00 = empty)",
    },
    "modifiers": sorted(m.value for m in Modifier),
    "keys": {name: usage for name, usage in sorted(KEY_USAGES.items())},
    "key_usage_range": "0x00..0x91 descriptor-legal; named keys above are the supported subset",
    "text_layout": "US QWERTY subset; typing maps A-Z/a-z, digits, common punctuation, ENTER/Tab",
    "descriptor_base64": (
        "BQEJBqEBBQgZASkDFQAlAXUBlQORAglLlQGRApUEkQEFBxngKeeVCIECdQiVAYEBGQAp"
        "kSb/AJUGgQDA"
    ),
    "host_leds": "output report readback: num_lock/caps_lock/scroll_lock/indicator",
    "device_docs": "tinypilot workspace: tinypilot-docs/usb-gadget.md, tinypilot-docs/m5-gate.md",
}


def build_server(keyboard: Gadget) -> FastMCP:
    mcp = FastMCP(
        "nixpilot-keyboard-mcp",
        instructions=(
            "Input tools for the TinyPilot USB gadget's keyboard endpoint."
            " Keystrokes land in the target's focused window — use reset"
            " when done and avoid typing into unknown foreground state."
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
    def set_state(
        modifiers: list[Modifier] | None = None,
        keys: list[Key] | None = None,
    ) -> dict:
        """Assert the keyboard state (partial merge: fields you provide
        replace current values, absent fields keep theirs). Writes exactly
        one boot-protocol report; state is sticky until the next write,
        reset, or watchdog expiry. Max 6 keys alongside 8 modifiers."""

        return _run(
            lambda: {
                "state": keyboard.set_state(
                    StateUpdate(modifiers=modifiers, keys=keys)
                ).as_dict()
            }
        )

    @mcp.tool(annotations=_WRITE)
    def press(
        keys: list[Key],
        modifiers: list[Modifier] | None = None,
        hold_ms: int = 150,
    ) -> dict:
        """Momentarily press a chord: modifiers (default none) + keys,
        held hold_ms (0..10000) server-side, then the pre-call state is
        re-asserted. Atomic single-writer round-trip. e.g. press(modifiers=
        ["LCTRL"], keys=["C"]) is Ctrl+C; press(keys=["F13"]) is inert."""

        return _run(
            lambda: keyboard.press(
                PressRequest(modifiers=modifiers or [], keys=keys, hold_ms=hold_ms)
            )
        )

    @mcp.tool(annotations=_WRITE)
    def type_text(text: str, per_key_ms: int = 12) -> dict:
        """Type text on the target (US QWERTY subset: letters, digits, common
        punctuation, Enter, Tab). Clears sticky state, then types per
        character (down, per_key_ms, up) and leaves the keyboard idle.
        Non-US characters fail before any write. Total duration
        len(text) x per_key_ms is pre-flight capped at 20 s."""

        return _run(
            lambda: keyboard.type_text(TypeRequest(text=text, per_key_ms=per_key_ms))
        )

    @mcp.tool(annotations=_WRITE)
    def sequence(steps: list[SequenceStep]) -> dict:
        """Timed macro: 1..100 steps of press / set_state / type_text /
        delay_ms. Total duration (holds + typing + delays, capped at 30 s)
        is pre-flight checked before any report is written; executed under
        one lock acquisition."""

        return _run(lambda: keyboard.sequence(steps))

    @mcp.tool(annotations=_WRITE)
    def reset() -> dict:
        """Full idle: release all modifiers and keys, disarm the watchdog.
        The recommended recovery verb."""

        return _run(lambda: {"state": keyboard.reset().as_dict()})

    @mcp.tool(annotations=_READ)
    def status() -> dict:
        """Probe the keyboard gadget: node, UDC binding, asserted state, the
        target's LED lock state (host_leds), watchdog, single-writer lock.
        Recommended first call of a session."""

        return _run(keyboard.status)

    @mcp.tool(annotations=_WRITE)
    def send_raw(hex: str) -> dict:
        """Write a raw 8-byte boot-protocol report (16 hex characters) for
        protocol parity with the tinypilot workspace's hidg-smoke.sh vectors
        (e.g. "04002b0000000000" = LAlt+Tab). Byte 1 (reserved) must be 00;
        key slots are a set — duplicates rejected; the report is decoded back
        into tracked state."""

        return _run(lambda: {"state": keyboard.send_raw(hex).as_dict()})

    @mcp.resource("gadget://keyboard/layout")
    def layout() -> str:
        """Static keyboard profile layout (wire format + key usage map +
        text-typing coverage)."""
        return json.dumps(_LAYOUT, indent=2)

    @mcp.resource("gadget://keyboard/state")
    def state() -> str:
        """Last-asserted keyboard state, host LED readback, watchdog
        deadline, writer lock."""
        return json.dumps(keyboard.status(), indent=2)

    return mcp
