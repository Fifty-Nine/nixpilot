"""Keyboard HID report codec for the nixpilot keyboard profile.

Wire contract (8 bytes), device-verified in the tinypilot workspace's
`tinypilot-docs/m5-gate.md`; descriptor semantics from the vendor-parity
keyboard report descriptor in aedificium's `tinypilot-usb-gadget.nix`:

    [0] modifier keys, bits 0..7 = LCTRL LSHIFT LALT LGUI RCTRL RSHIFT RALT RGUI
    [1] constant zero (reserved, Input(DConst) in the descriptor)
    [2..7] six key-array slots, usages 0x00 (none) .. 0x91 (LogicalMax)

Unlike the vendor-parity gamepad descriptor (LogicalMax 0x65), the keyboard
slots accept usages through 0x91; anything beyond is malformed. Slots are an
unordered set — duplicates are non-compliant and rejected. The LED output
report (1 byte: NumLock, CapsLock, ScrollLock, GenericIndicator, 4 pad bits)
is the only host→gadget feedback channel and is read passively via a
nonblocking second fd.

`type_text` maps text through the US QWERTY subset of the table below; any
character outside the map (including C0 control codes other than \\n and
\\t) fails the call before any write.
"""

from __future__ import annotations

import enum
import re

from pydantic import BaseModel, ConfigDict, Field

REPORT_LEN = 8
KEY_SLOTS = 6
USAGE_MAX = 0x91
MODIFIER_BITMASK = {
    "LCTRL": 0x01,
    "LSHIFT": 0x02,
    "LALT": 0x04,
    "LGUI": 0x08,
    "RCTRL": 0x10,
    "RSHIFT": 0x20,
    "RALT": 0x40,
    "RGUI": 0x80,
}


class KeyboardError(ValueError):
    """Malformed raw report or request-contract violation."""


class Modifier(enum.StrEnum):
    LCTRL = "LCTRL"
    LSHIFT = "LSHIFT"
    LALT = "LALT"
    LGUI = "LGUI"
    RCTRL = "RCTRL"
    RSHIFT = "RSHIFT"
    RALT = "RALT"
    RGUI = "RGUI"


_MODIFIER_BITS: dict[Modifier, int] = {
    Modifier(name): bit for name, bit in MODIFIER_BITMASK.items()
}

#: Key name -> HID usage code (descriptor-legal through 0x91).
KEY_USAGES: dict[str, int] = {
    **{name: 0x04 + i for i, name in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
    **{
        name: 0x1E + i
        for i, name in enumerate(["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"])
    },
    **{f"F{i}": 0x3A + i - 1 for i in range(1, 13)},  # F1..F12
    **{f"F{i}": 0x68 + i - 13 for i in range(13, 25)},  # F13..F24
    "ENTER": 0x28,
    "ESC": 0x29,
    "BACKSPACE": 0x2A,
    "TAB": 0x2B,
    "SPACE": 0x2C,
    "MINUS": 0x2D,
    "EQUAL": 0x2E,
    "BRACKET_LEFT": 0x2F,
    "BRACKET_RIGHT": 0x30,
    "BACKSLASH": 0x31,
    "SEMICOLON": 0x33,
    "QUOTE": 0x34,
    "BACKTICK": 0x35,
    "COMMA": 0x36,
    "PERIOD": 0x37,
    "SLASH": 0x38,
    "CAPSLOCK": 0x39,
    "PRINTSCREEN": 0x46,
    "SCROLLLOCK": 0x47,
    "PAUSE": 0x48,
    "INSERT": 0x49,
    "HOME": 0x4A,
    "PAGEUP": 0x4B,
    "DELETE": 0x4C,
    "END": 0x4D,
    "PAGEDOWN": 0x4E,
    "RIGHT": 0x4F,
    "LEFT": 0x50,
    "DOWN": 0x51,
    "UP": 0x52,
    "NUMLOCK": 0x53,
    "KP_DIVIDE": 0x54,
    "KP_MULTIPLY": 0x55,
    "KP_SUBTRACT": 0x56,
    "KP_ADD": 0x57,
    "KP_ENTER": 0x58,
    **{f"KP{i}": 0x58 + i for i in range(1, 10)},  # KP1..KP9
    "KP0": 0x62,
    "KP_PERIOD": 0x63,
    "INTL_BACKSLASH": 0x64,
    "MENU": 0x65,
}

_KEY_FROM_USAGE: dict[int, str] = {usage: name for name, usage in KEY_USAGES.items()}

Key = enum.StrEnum("Key", {name: name for name in KEY_USAGES})
ModifierSet = frozenset[Modifier]

#: LED output report bitmask (single byte read back from the gadget node).
LED_BITS = {"num_lock": 0x01, "caps_lock": 0x02, "scroll_lock": 0x04, "indicator": 0x08}

#: US QWERTY text layout: character -> (key name, shift held).
_TEXT_LAYOUT: dict[str, tuple[Key, bool]] = {}


def _register_text(chars: str, key: str, shift: bool) -> None:
    try:
        entry = (Key(key), shift)
    except ValueError:
        return
    for char in chars:
        _TEXT_LAYOUT[char] = entry


_register_text(" ", "SPACE", False)
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _register_text(_c, _c.upper(), False)
    _register_text(_c.upper(), _c.upper(), True)
for _i, _pair in enumerate("1234567890"):
    _register_text(_pair, _pair, False)
for _char, _base in [
    ("!", "1"),
    ("@", "2"),
    ("#", "3"),
    ("$", "4"),
    ("%", "5"),
    ("^", "6"),
    ("&", "7"),
    ("*", "8"),
    ("(", "9"),
    (")", "0"),
]:
    _register_text(_char, _base, True)
for _base, _shifted, _name in [
    ("-", "_", "MINUS"),
    ("=", "+", "EQUAL"),
    ("[", "{", "BRACKET_LEFT"),
    ("]", "}", "BRACKET_RIGHT"),
    ("\\", "|", "BACKSLASH"),
    (";", ":", "SEMICOLON"),
    ("'", '"', "QUOTE"),
    ("`", "~", "BACKTICK"),
    (",", "<", "COMMA"),
    (".", ">", "PERIOD"),
    ("/", "?", "SLASH"),
]:
    _register_text(_base, _name, False)
    _register_text(_shifted, _name, True)
_register_text("\n", "ENTER", False)
_register_text("\t", "TAB", False)

_RAW_HEX = re.compile(r"[0-9a-fA-F]{16}")


class ReportError(ValueError):
    """Malformed raw report or request-contract violation."""


def type_plan(text: str) -> list[tuple[Key, bool]]:
    """Map text to (key, shift) sequences; raises on the first unmapped
    character. Pure; used by type_text and the offline selftest."""
    plan: list[tuple[Key, bool]] = []
    for pos, char in enumerate(text):
        entry = _TEXT_LAYOUT.get(char)
        if entry is None:
            raise ReportError(
                f"untypeable character {char!r} (U+{ord(char):04X}) at index"
                f" {pos}: the keyboard profile ships a US QWERTY subset"
            )
        plan.append(entry)
    return plan


class StateUpdate(BaseModel):
    """Partial keyboard update; absent fields keep their asserted value."""

    model_config = ConfigDict(extra="forbid")

    modifiers: list[Modifier] | None = None
    keys: list[Key] | None = Field(default=None, max_length=KEY_SLOTS)


class KeyboardState(BaseModel):
    model_config = ConfigDict(frozen=True)

    modifiers: frozenset[Modifier] = frozenset()
    keys: frozenset[Key] = frozenset()

    @classmethod
    def idle(cls) -> KeyboardState:
        return cls()

    def as_dict(self) -> dict:
        """Authoritative state in tool-result form (canonical usage order)."""
        return {
            "modifiers": sorted(m.value for m in self.modifiers),
            "keys": [
                name
                for name, _ in sorted(KEY_USAGES.items(), key=lambda kv: kv[1])
                if Key(name) in self.keys
            ],
        }

    def merged(self, update: StateUpdate) -> KeyboardState:
        changes: dict[str, object] = {}
        if update.modifiers is not None:
            changes["modifiers"] = frozenset(update.modifiers)
        if update.keys is not None:
            changes["keys"] = frozenset(update.keys)
        return self.model_copy(update=changes)

    def with_press(
        self, modifiers: frozenset[Modifier], keys: frozenset[Key]
    ) -> KeyboardState:
        return self.model_copy(
            update={
                "modifiers": self.modifiers | modifiers,
                "keys": self.keys | keys,
            }
        )

    def encode(self) -> bytes:
        raw = bytearray(REPORT_LEN)
        for modifier in self.modifiers:
            raw[0] |= _MODIFIER_BITS[modifier]
        raw[1] = 0
        if len(self.keys) > KEY_SLOTS:
            raise ReportError(f"keyboard state exceeds {KEY_SLOTS} key slots")
        for pos, key in enumerate(sorted(self.keys, key=lambda k: KEY_USAGES[k.value])):
            raw[2 + pos] = KEY_USAGES[key.value]
        return bytes(raw)

    @classmethod
    def from_report(cls, raw: bytes) -> KeyboardState:
        if len(raw) != REPORT_LEN:
            raise ReportError(f"report must be {REPORT_LEN} bytes, got {len(raw)}")
        if raw[1] != 0:
            raise ReportError("byte 1 (reserved) must be 00")
        modifiers = frozenset(
            modifier for modifier, bit in _MODIFIER_BITS.items() if raw[0] & bit
        )
        usages = [usage for usage in raw[2:] if usage != 0]  # 0 = no key
        if len(set(usages)) != len(usages):
            raise ReportError("duplicate key usage in a report (non-set array)")
        try:
            keys = frozenset(Key(_KEY_FROM_USAGE[usage]) for usage in usages)
        except KeyError as exc:
            raise ReportError(
                f"untracked keyboard usage 0x{usages[0]:02x} (descriptor-legal"
                f" through 0x{USAGE_MAX:02x}; the profile tracks named keys"
                " only)"
            ) from exc
        return cls(modifiers=modifiers, keys=keys)


def parse_raw_hex(raw: str) -> bytes:
    """Validate and decode the send_raw hex literal into 8 report bytes."""
    if not isinstance(raw, str) or _RAW_HEX.fullmatch(raw) is None:
        raise ReportError("raw report must be exactly 16 hex characters")
    return bytes.fromhex(raw)
