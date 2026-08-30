"""uStreamer frame capture for the screen profile.

`fetch_frame()` performs one /state + /snapshot round-trip and returns a
Frame; the same entry point serves any future frame-analysis profile
(ui_scan): the exact JPEG bytes, dimensions from a marker walk (no decoder
dependency), a content sha256 for cross-referencing, and the capture-pipeline
snapshot at fetch time. All failures raise CaptureError with a message safe
to surface as an MCP tool error.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import struct
import time
import urllib.error
import urllib.request

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger("nixpilot.screen.capture")

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

_SNAPSHOT_HEADERS = {"Accept": "image/jpeg, */*"}


class CaptureError(RuntimeError):
    """uStreamer is unreachable, unhealthy, or did not return a JPEG frame."""


class _Resolution(BaseModel):
    model_config = ConfigDict(extra="ignore")

    width: int | None = None
    height: int | None = None


class _Source(BaseModel):
    model_config = ConfigDict(extra="ignore")

    online: bool | None = None
    captured_fps: float | None = None
    resolution: _Resolution | None = None


class _Encoder(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str | None = None


class _Stream(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clients: int | None = None


class _StateResult(BaseModel):
    """Validated mirror of the /state fields this server consumes; extra keys
    (clients_stat, headers, …) are ignored."""

    model_config = ConfigDict(extra="ignore")

    source: _Source | None = None
    encoder: _Encoder | None = None
    stream: _Stream | None = None


@dataclasses.dataclass(frozen=True)
class StreamState:
    """Advisory capture-pipeline snapshot from /state (None = unavailable)."""

    online: bool | None = None
    width: int | None = None
    height: int | None = None
    captured_fps: float | None = None
    encoder: str | None = None
    clients: int | None = None


@dataclasses.dataclass(frozen=True)
class Frame:
    """One captured frame plus everything a consumer needs to reason about it."""

    jpeg: bytes
    width: int
    height: int
    sha256: str
    fetched_url: str
    fetched_at_s: float
    source: StreamState

    def metadata(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "bytes": len(self.jpeg),
            "sha256": self.sha256,
            "source_online": self.source.online,
            "captured_fps": self.source.captured_fps,
            "fetched_url": self.fetched_url,
        }


def fetch_frame(base_url: str, timeout_s: float) -> Frame:
    """Capture the latest frame from the uStreamer at `base_url`."""
    state = fetch_stream_state(base_url, timeout_s)
    url = f"{base_url.rstrip('/')}/snapshot"
    jpeg = _get(url, timeout_s, headers=_SNAPSHOT_HEADERS)
    validate_jpeg(jpeg)
    width, height = parse_jpeg_size(jpeg)
    frame = Frame(
        jpeg=jpeg,
        width=width,
        height=height,
        sha256=hashlib.sha256(jpeg).hexdigest(),
        fetched_url=url,
        fetched_at_s=time.time(),
        source=state,
    )
    log.info(
        "captured frame: %dx%d, %d bytes (source_online=%s)",
        width,
        height,
        len(jpeg),
        state.online,
    )
    return frame


def fetch_stream_state(base_url: str, timeout_s: float) -> StreamState:
    """Advisory /state reader; any failure (unreachable, non-JSON, unexpected
    shape) degrades to an all-None StreamState so screenshots keep working
    without the metadata."""
    try:
        payload = json.loads(_get(f"{base_url.rstrip('/')}/state", timeout_s))
    except (CaptureError, ValueError) as exc:
        log.warning("/state unavailable, continuing without advisories: %s", exc)
        return StreamState()
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    try:
        parsed = _StateResult.model_validate(payload)
    except ValidationError as exc:
        log.warning(
            "/state shaped unexpectedly, continuing without advisories: %s", exc
        )
        return StreamState()
    resolution = parsed.source.resolution if parsed.source else None
    return StreamState(
        online=parsed.source.online if parsed.source else None,
        width=resolution.width if resolution else None,
        height=resolution.height if resolution else None,
        captured_fps=parsed.source.captured_fps if parsed.source else None,
        encoder=parsed.encoder.type if parsed.encoder else None,
        clients=parsed.stream.clients if parsed.stream else None,
    )


def _get(url: str, timeout_s: float, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            if response.status != 200:
                # urlopen raises HTTPError for >=400; kept as a guard.
                raise CaptureError(f"{url} returned HTTP {response.status}")
            return response.read()
    except urllib.error.HTTPError as exc:
        raise CaptureError(
            f"{url} returned HTTP {exc.code} — the capture source may be"
            " offline (ustreamer --persistent keeps the service up, but no"
            " frame exists until the target feeds the HDMI input)"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CaptureError(f"{url} unreachable: {exc}") from exc


def validate_jpeg(body: bytes) -> None:
    """Reject anything that is not a complete JPEG stream (e.g. an HTML error
    page delivered with a 200, or a truncated transfer)."""
    if not body.startswith(JPEG_SOI):
        head = body[:16]
        raise CaptureError(
            f"upstream body is not a JPEG (starts with {head!r});"
            " the endpoint may be erroring despite HTTP 200"
        )
    if not body.endswith(JPEG_EOI):
        raise CaptureError(
            f"JPEG stream truncated ({len(body)} bytes, missing EOI marker)"
        )
    if len(body) < len(JPEG_SOI) + len(JPEG_EOI):
        raise CaptureError("JPEG stream too short to contain a frame header")


def parse_jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """Walk the JPEG marker stream to the first SOF segment and return
    (width, height). Pure stdlib; entropy-coded data (after SOS) is never
    entered, so the walk is independent of encoder internals."""
    pos = len(JPEG_SOI)
    end = len(jpeg) - len(JPEG_EOI)
    while pos + 1 < end:
        if jpeg[pos] != 0xFF:
            raise CaptureError(f"malformed JPEG marker stream at byte {pos}")
        marker = jpeg[pos + 1]
        if marker == 0xFF:  # fill byte before the real marker
            pos += 1
            continue
        # Standalone markers: TEM, RST0..7, stray SOI.
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            pos += 2
            continue
        if marker == 0xD9:  # EOI before any SOF
            raise CaptureError("JPEG ended before a SOF frame header")
        if pos + 4 > end:
            raise CaptureError("JPEG truncated inside a segment header")
        (segment_len,) = struct.unpack(">H", jpeg[pos + 2 : pos + 4])
        if segment_len < 2:
            raise CaptureError(f"JPEG segment length {segment_len} at byte {pos}")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > end:
                raise CaptureError("JPEG truncated inside the SOF segment")
            height, width = struct.unpack(">HH", jpeg[pos + 5 : pos + 9])
            return width, height
        pos += 2 + segment_len
    raise CaptureError("JPEG carries no SOF frame header")
