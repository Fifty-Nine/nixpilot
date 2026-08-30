"""Offline codec gate for `nix flake check`: keyboard report encode/decode and
text-layout vectors, no device access. Run:
python3 -m nixpilot.keyboard.selftest"""

from __future__ import annotations

import sys

from pydantic import ValidationError

from .keys import (
    Key,
    KeyboardState,
    Modifier,
    ReportError,
    parse_raw_hex,
    type_plan,
)

_CASES = 0


def check(name: str, condition: bool) -> None:
    global _CASES
    _CASES += 1
    if not condition:
        print(f"nixpilot keyboard selftest: FAIL at {name}", file=sys.stderr)
        raise SystemExit(1)


def expect_error(name: str, action) -> None:
    global _CASES
    _CASES += 1
    try:
        action()
    except (ReportError, ValidationError):
        return
    print(
        f"nixpilot keyboard selftest: FAIL at {name} (no error raised)",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> None:
    idle = KeyboardState.idle().encode()
    check("idle encode", idle == bytes(8))

    ctrl_c = KeyboardState(
        modifiers=frozenset({Modifier.LCTRL}), keys=frozenset({Key("C")})
    ).encode()
    check("ctrl+c", ctrl_c == bytes.fromhex("0100060000000000"))

    alt_tab = KeyboardState(
        modifiers=frozenset({Modifier.LALT}), keys=frozenset({Key("TAB")})
    ).encode()
    check(
        "alt+tab (hidg-smoke.sh parity)", alt_tab == bytes.fromhex("04002b0000000000")
    )
    check(
        "alt+tab send_raw roundtrip",
        KeyboardState.from_report(bytes.fromhex("04002b0000000000")).encode()
        == bytes.fromhex("04002b0000000000"),
    )

    shift_e = KeyboardState.from_report(bytes.fromhex("0200080000000000"))
    check(
        "shift+e decode",
        sorted(m.value for m in shift_e.modifiers) == ["LSHIFT"]
        and [k.value for k in shift_e.keys] == ["E"],
    )

    # Slot ordering is canonical (usage-sorted), independent of set order.
    combo = KeyboardState(keys=frozenset({Key("Z"), Key("A"), Key("F13")})).encode()
    check("slot order sorted", combo[2:5] == bytes([0x04, 0x1D, 0x68]))
    check("slot trailing zeros", combo[5:] == bytes(3))
    check(
        "f24 boundary", KeyboardState(keys=frozenset({Key("F24")})).encode()[2] == 0x73
    )

    # The vendor descriptor's key-array LogicalMax is 0x91 (unlike the
    # gamepad's 0x65). Untracked-but-legal usages and out-of-range values
    # are both rejected by the tracked-state contract.
    expect_error(
        "untracked usage 0x86",
        lambda: KeyboardState.from_report(bytes.fromhex("0000860000000000")),
    )
    expect_error(
        "usage beyond logical max",
        lambda: KeyboardState.from_report(bytes.fromhex("0000920000000000")),
    )
    expect_error(
        "reserved byte nonzero",
        lambda: KeyboardState.from_report(bytes.fromhex("0000010000000000")),
    )
    expect_error(
        "duplicate key slots",
        lambda: KeyboardState.from_report(bytes.fromhex("0000040400000000")),
    )
    expect_error("short report", lambda: KeyboardState.from_report(b"\x00" * 7))
    expect_error("bad hex literal", lambda: parse_raw_hex("zz00"))
    expect_error("overflow hex", lambda: parse_raw_hex("00" * 9))

    # Seven keys cannot fit six slots (encoder-side slot bound).
    seven = ["A", "B", "C", "D", "E", "F", "G"]
    expect_error(
        "state exceeds slots",
        lambda: KeyboardState(keys=frozenset(Key(k) for k in seven)).encode(),
    )

    # Text layout (US QWERTY subset).
    check("type lowercase", type_plan("ab") == [(Key("A"), False), (Key("B"), False)])
    check("type uppercase shift", type_plan("A") == [(Key("A"), True)])
    check("type digit", type_plan("9") == [(Key("9"), False)])
    check("type shifted symbol", type_plan("@") == [(Key("2"), True)])
    check("type punctuation", type_plan("-") == [(Key("MINUS"), False)])
    check(
        "type enter/tab",
        type_plan("\n\t") == [(Key("ENTER"), False), (Key("TAB"), False)],
    )
    expect_error("type unicode", lambda: type_plan("é"))
    expect_error("type control char", lambda: type_plan("\r"))

    print(f"nixpilot keyboard selftest: PASS ({_CASES} cases)")


if __name__ == "__main__":
    main()
