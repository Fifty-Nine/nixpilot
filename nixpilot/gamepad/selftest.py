"""Offline codec gate for `nix flake check`: pure report encode/decode
vectors, no device access. Run: python3 -m nixpilot.gamepad.selftest"""

from __future__ import annotations

import sys

from pydantic import ValidationError

from .report import (
    IDLE_REPORT,
    Button,
    GamepadState,
    ReportError,
    StateUpdate,
    Stick,
    parse_raw_hex,
)

_CASES = 0


def check(name: str, condition: bool) -> None:
    global _CASES
    _CASES += 1
    if not condition:
        print(f"nixpilot selftest: FAIL at {name}", file=sys.stderr)
        raise SystemExit(1)


def expect_error(name: str, action) -> None:
    global _CASES
    _CASES += 1
    try:
        action()
    except (ReportError, ValidationError):
        return
    print(f"nixpilot selftest: FAIL at {name} (no error raised)", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    check("idle encode", GamepadState.idle().encode() == IDLE_REPORT)

    mixed = GamepadState(
        buttons=frozenset({Button.A, Button.RT, Button.START})
    ).encode()
    check("button bits", mixed[0] == 0x81 and mixed[1] == 0x02)
    check(
        "all 16 buttons",
        GamepadState(buttons=frozenset(Button)).encode()[:2] == b"\xff\xff",
    )

    full_plus = GamepadState(left=Stick(x=1.0, y=1.0)).encode()
    full_minus = GamepadState(left=Stick(x=-1.0, y=-1.0)).encode()
    check("axis +1", full_plus[2:4] == b"\xff\xff")
    check("axis -1", full_minus[2:4] == b"\x00\x00")
    check("axis center byte", IDLE_REPORT[2:6] == bytes((127,) * 4))

    check("hat NE", GamepadState(hat="NE").encode()[6] == 0x01)
    check("hat S", GamepadState(hat="S").encode()[6] == 0x04)

    raw_a = bytes.fromhex("01007f7f7f7f0f00")
    check("raw A decode", GamepadState.from_report(raw_a).as_dict()["buttons"] == ["A"])
    check("raw A roundtrip", GamepadState.from_report(raw_a).encode() == raw_a)

    combo = bytes.fromhex("030000ff7f7f0600")
    state = GamepadState.from_report(combo)
    check("raw combo buttons", set(state.as_dict()["buttons"]) == {"A", "B"})
    check("raw combo hat", state.as_dict()["hat"] == "W")
    check("combo roundtrip", state.encode() == combo)

    expect_error(
        "padding enforced",
        lambda: GamepadState.from_report(bytes.fromhex("01007f7f7f7f0f01")),
    )
    expect_error(
        "undefined hat nibble",
        lambda: GamepadState.from_report(bytes.fromhex("01007f7f7f7f0800")),
    )
    expect_error("short report", lambda: GamepadState.from_report(b"\x00" * 7))
    expect_error("bad hex literal", lambda: parse_raw_hex("zz00"))
    expect_error("overflow hex", lambda: parse_raw_hex("00" * 9))
    expect_error("axis out of range", lambda: Stick(x=1.5, y=0.0))

    update = StateUpdate(buttons=[Button.B], left=Stick(x=1.0, y=0.0))
    merged = GamepadState(buttons=frozenset({Button.A})).merged(update)
    payload = merged.as_dict()
    check("merge replaces buttons", payload["buttons"] == ["B"])
    check("merge carries stick", payload["left"]["x"] == 1.0)
    check(
        "merge retains others",
        payload["right"] == {"x": 0.0, "y": 0.0} and payload["hat"] == "NEUTRAL",
    )

    print(f"nixpilot selftest: PASS ({_CASES} cases)")


if __name__ == "__main__":
    main()
