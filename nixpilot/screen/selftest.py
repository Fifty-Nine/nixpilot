"""Offline selftest for the screen profile: JPEG validation and marker-walk
vectors with no network or device access. Run:
python3 -m nixpilot.screen.selftest"""

from __future__ import annotations

import base64
import hashlib
import sys

from .capture import CaptureError, Frame, StreamState, parse_jpeg_size, validate_jpeg

_CASES = 0

#: ImageMagick-rendered 32x18 grayscale JPEG (full marker stream, JFIF APP0).
TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoM"
    "DAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/wAALCAASACABAREA/8QAFQAB"
    "AQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/AKpgAAA/"
    "/9k="
)


def check(name: str, condition: bool) -> None:
    global _CASES
    _CASES += 1
    if not condition:
        print(f"nixpilot screen selftest: FAIL at {name}", file=sys.stderr)
        raise SystemExit(1)


def expect_error(name: str, action) -> None:
    global _CASES
    _CASES += 1
    try:
        action()
    except CaptureError:
        return
    print(
        f"nixpilot screen selftest: FAIL at {name} (no CaptureError)",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _segment(marker: int, payload: bytes) -> bytes:
    # Segment length includes the two length bytes themselves.
    return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload


def _sof(marker: int, precision: int, height: int, width: int) -> bytes:
    return _segment(
        marker,
        bytes((precision,))
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x02\x11\x00",
    )


def _sof0(height: int, width: int) -> bytes:
    return _sof(0xC0, 8, height, width)


def main() -> None:
    tiny = base64.b64decode(TINY_JPEG_B64)
    validate_jpeg(tiny)
    check("tiny validation passes", True)
    check("tiny dims", parse_jpeg_size(tiny) == (32, 18))

    # Hand-crafted stream: EXIF APP1 preamble, 0xFFFF fill padding, DQT
    # segment, SOF0 frame header (640x480), SOS with a stuffed scan byte,
    # EOI. The walk must stop at the SOF and ignore everything after it.
    crafted = (
        b"\xff\xd8"
        + _segment(0xE1, b"Exif\x00\x00" + b"\xff\xff\x01\x02")
        + b"\xff\xff"  # fill padding between markers
        + _segment(0xDB, b"\x00" * 65)
        + _sof0(480, 640)
        + _segment(0xDA, b"\x01\x11\x00")  # SOS; entropy data not walked
        + b"\xff\x00\x00\x11"
        + b"\xff\xd9"
    )
    check("crafted parse (exif + fills + sof0)", parse_jpeg_size(crafted) == (640, 480))

    # Progressive JPEG (SOF2) also yields dimensions.
    prog = (
        b"\xff\xd8"
        + _segment(0xC4, b"\x00" * 16)  # DHT that a decoder would need
        + _sof(0xC2, 8, 30, 40)
        + b"\xff\xd9"
    )
    check("sof2 dims", parse_jpeg_size(prog) == (40, 30))

    # RST markers are standalone and skipped.
    check(
        "rst skipped",
        parse_jpeg_size(b"\xff\xd8" + _sof0(10, 20) + b"\xff\xd0" + b"\xff\xd9")
        == (20, 10),
    )

    expect_error("html body", lambda: validate_jpeg(b"<html>503</html>"))
    expect_error("empty body", lambda: validate_jpeg(b""))
    expect_error("truncated jpeg", lambda: validate_jpeg(b"\xff\xd8\xff\xe0\x00\x02"))
    expect_error("missing eoi", lambda: validate_jpeg(tiny[:-2] + b"\x00\x00"))
    expect_error("html parsed as jpeg", lambda: parse_jpeg_size(b"<html>"))
    expect_error(
        "no sof",
        lambda: parse_jpeg_size(b"\xff\xd8" + _segment(0xFE, b"comment") + b"\xff\xd9"),
    )
    expect_error(
        "short segment len",
        lambda: parse_jpeg_size(b"\xff\xd8\xff\xfe\x00\x01\xff\xd9"),
    )

    frame = Frame(
        jpeg=tiny,
        width=32,
        height=18,
        sha256=hashlib.sha256(tiny).hexdigest(),
        fetched_url="http://127.0.0.1:48001/snapshot",
        fetched_at_s=123.0,
        source=StreamState(
            online=True,
            width=32,
            height=18,
            captured_fps=48.0,
            encoder="M2M-IMAGE",
            clients=3,
        ),
    )
    meta = frame.metadata()
    check(
        "metadata keys",
        set(meta)
        == {
            "width",
            "height",
            "bytes",
            "sha256",
            "source_online",
            "captured_fps",
            "fetched_url",
        },
    )
    check(
        "metadata values",
        meta["width"] == 32
        and meta["bytes"] == len(tiny)
        and meta["source_online"] is True
        and meta["captured_fps"] == 48.0,
    )

    print(f"nixpilot screen selftest: PASS ({_CASES} cases)")


if __name__ == "__main__":
    main()
