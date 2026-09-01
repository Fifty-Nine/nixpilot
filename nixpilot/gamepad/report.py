"""Gamepad HID report codec for the nixpilot gamepad profile.

Wire contract (8 bytes), device-verified in the tinypilot workspace's
`tinypilot-docs/m5-gate.md`:

    [0] buttons 1-8   (bit i = button i+1)
    [1] buttons 9-16
    [2] X   left stick horizontal
    [3] Y   left stick vertical
    [4] Z   right stick horizontal
    [5] Rz  right stick vertical
    [6] hat switch in the low nibble (0=N .. 7=NW, 0xF neutral)
    [7] constant zero padding

Tool-schema axes are normalized floats in -1.0..1.0 (+y = up) with strict
bounds; the 8-bit report grid is asymmetric around the center byte 0x7F and
the rounding below keeps both endpoints exact and the center byte-identical
with the idle report.
"""

from __future__ import annotations

import enum
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

REPORT_LEN = 8
AXIS_CENTER = 0x7F
HAT_NEUTRAL = 0x0F

#: Button order is the descriptor's bit order: HID button usages 1..16,
#: which Linux hid-input maps to BTN_GAMEPAD + (usage-1) = 0x130..0x13f.
#: Only the codes the host input stack renders are exposed as API buttons;
#: the legacy DirectInput slots BTN_C (bit 2), BTN_Z (bit 5) and 0x13f
#: (bit 15) are unrendered by Steam/SDL testers and remain permanently idle.
BUTTON_NAMES: tuple[str, ...] = (
    "A",
    "B",
    "X",
    "Y",
    "LB",
    "RB",
    "LT",
    "RT",
    "BACK",
    "START",
    "GUIDE",
    "L3",
    "R3",
)

#: Report bits with no rendered host meaning; never set by the codec.
RESERVED_BITS: frozenset[int] = frozenset({2, 5, 15})

#: Hat names, clockwise from north, as they appear in tool arguments.
HAT_NAMES: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "NEUTRAL")

HAT_WIRE: dict[str, int] = {
    "N": 0,
    "NE": 1,
    "E": 2,
    "SE": 3,
    "S": 4,
    "SW": 5,
    "W": 6,
    "NW": 7,
    "NEUTRAL": HAT_NEUTRAL,
}
_HAT_FROM_WIRE: dict[int, str] = {wire: name for name, wire in HAT_WIRE.items()}

HatName = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW", "NEUTRAL"]

#: The idle report every session and reset returns to.
IDLE_REPORT = bytes(
    (0, 0, AXIS_CENTER, AXIS_CENTER, AXIS_CENTER, AXIS_CENTER, HAT_NEUTRAL, 0)
)

_RAW_HEX = re.compile(r"[0-9a-fA-F]{16}")


class ReportError(ValueError):
    """Malformed raw report."""


class Button(enum.StrEnum):
    A = "A"
    B = "B"
    X = "X"
    Y = "Y"
    LB = "LB"
    RB = "RB"
    LT = "LT"
    RT = "RT"
    BACK = "BACK"
    START = "START"
    L3 = "L3"
    R3 = "R3"
    GUIDE = "GUIDE"


#: Wire bit for each button in the DirectInput descriptor order; gaps are the
#: unrendered legacy slots (see RESERVED_BITS).
BUTTON_BITS: dict[str, int] = {
    "A": 0,
    "B": 1,
    "X": 3,
    "Y": 4,
    "LB": 6,
    "RB": 7,
    "LT": 8,
    "RT": 9,
    "BACK": 10,
    "START": 11,
    "GUIDE": 12,
    "L3": 13,
    "R3": 14,
}


def axis_to_byte(value: float) -> int:
    """Map the -1.0..1.0 tool domain onto the 8-bit report domain."""
    if value >= 0.0:
        return AXIS_CENTER + round(value * 128)
    return AXIS_CENTER + math.ceil(value * 127.5)


def byte_to_axis(value: int) -> float:
    """Inverse of axis_to_byte; display/decode only, quantization is lossy."""
    if value == AXIS_CENTER:
        return 0.0
    if value > AXIS_CENTER:
        return (value - AXIS_CENTER) / 128.0
    return (value - AXIS_CENTER) / 127.5


def parse_raw_hex(raw: str) -> bytes:
    """Validate and decode the send_raw hex literal into 8 report bytes."""
    if not isinstance(raw, str) or _RAW_HEX.fullmatch(raw) is None:
        raise ReportError("raw report must be exactly 16 hex characters")
    return bytes.fromhex(raw)


class Stick(BaseModel):
    """An analog stick in the normalized tool domain, +y = up."""

    model_config = ConfigDict(frozen=True)

    x: float = Field(ge=-1.0, le=1.0)
    y: float = Field(ge=-1.0, le=1.0)


CENTER: Stick = Stick(x=0.0, y=0.0)


class StateUpdate(BaseModel):
    """Partial gamepad update; absent fields keep their asserted value."""

    buttons: list[Button] | None = None
    left: Stick | None = None
    right: Stick | None = None
    hat: HatName | None = None


class GamepadState(BaseModel):
    model_config = ConfigDict(frozen=True)

    buttons: frozenset[Button] = frozenset()
    left: Stick = CENTER
    right: Stick = CENTER
    hat: HatName = "NEUTRAL"

    @classmethod
    def idle(cls) -> GamepadState:
        return cls()

    def as_dict(self) -> dict:
        """Authoritative state in tool-result form (button bit order)."""
        return {
            "buttons": [name for name in BUTTON_NAMES if Button(name) in self.buttons],
            "left": {"x": round(self.left.x, 4), "y": round(self.left.y, 4)},
            "right": {"x": round(self.right.x, 4), "y": round(self.right.y, 4)},
            "hat": self.hat,
        }

    def merged(self, update: StateUpdate) -> GamepadState:
        changes: dict[str, object] = {}
        if update.buttons is not None:
            changes["buttons"] = frozenset(update.buttons)
        if update.left is not None:
            changes["left"] = update.left
        if update.right is not None:
            changes["right"] = update.right
        if update.hat is not None:
            changes["hat"] = update.hat
        return self.model_copy(update=changes)

    def with_buttons(self, buttons: frozenset[Button]) -> GamepadState:
        return self.model_copy(update={"buttons": self.buttons | buttons})

    def encode(self) -> bytes:
        raw = bytearray(REPORT_LEN)
        for name in self.buttons:
            bit = BUTTON_BITS[name]
            raw[bit // 8] |= 1 << (bit % 8)
        raw[2] = axis_to_byte(self.left.x)
        raw[3] = axis_to_byte(self.left.y)
        raw[4] = axis_to_byte(self.right.x)
        raw[5] = axis_to_byte(self.right.y)
        raw[6] = HAT_WIRE[self.hat]
        raw[7] = 0
        return bytes(raw)

    @classmethod
    def from_report(cls, raw: bytes) -> GamepadState:
        """Decode a raw report; used to keep state honest after send_raw."""
        if len(raw) != REPORT_LEN:
            raise ReportError(f"report must be {REPORT_LEN} bytes, got {len(raw)}")
        if raw[7] != 0:
            raise ReportError("byte 7 (constant padding) must be 00")
        if raw[6] & 0xF0:
            raise ReportError("byte 6 carries undefined high-nibble bits")
        names = [
            name
            for name, bit in BUTTON_BITS.items()
            if (raw[bit // 8] >> (bit % 8)) & 1
        ]
        reserved = [bit for bit in RESERVED_BITS if (raw[bit // 8] >> (bit % 8)) & 1]
        if reserved:
            raise ReportError(
                f"unrendered reserved button bits set: {sorted(reserved)}"
            )
        try:
            hat = _HAT_FROM_WIRE[raw[6] & 0x0F]
        except KeyError as exc:
            raise ReportError(f"undefined hat nibble {raw[6] & 0x0F:#x}") from exc
        return cls(
            buttons=frozenset(Button(name) for name in names),
            left=Stick(x=byte_to_axis(raw[2]), y=byte_to_axis(raw[3])),
            right=Stick(x=byte_to_axis(raw[4]), y=byte_to_axis(raw[5])),
            hat=hat,
        )
