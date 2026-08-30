"""Device-side keyboard profile: the HID node writer, sticky-state machine,
watchdog, single-writer session arbitration, and the LED output-report
readback (the keyboard interface is the one gadget function the host sends
reports to — lock state arrives via SET_REPORT and is surfaced in status).

Lifecycle contracts mirror the gamepad profile (see mcp/README.md): take the
session file lock, open the node, write the idle report, then serve.
type_text is transient — it clears any asserted state, types, and leaves
modifiers/keys empty when the call returns.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
import time

from pydantic import BaseModel, Field, model_validator

from . import keys
from .keys import KEY_SLOTS, Key, KeyboardState, Modifier, StateUpdate, parse_raw_hex

log = logging.getLogger("nixpilot.keyboard.gadget")


class GadgetError(RuntimeError):
    """Gadget access failure or a contract-violating request."""


HOLD_MAX_MS = 10_000
DELAY_MAX_MS = 2_000
SEQUENCE_MAX_STEPS = 100
SEQUENCE_MAX_SECONDS = 30.0
TYPE_MAX_CHARS = 200
TYPE_MAX_TOTAL_MS = 20_000
WATCHDOG_POLL_S = 0.25
LOCK_GRACE_S = 5.0


class PressRequest(BaseModel):
    modifiers: list[Modifier] = Field(default_factory=list)
    keys: list[Key] = Field(min_length=1, max_length=KEY_SLOTS)
    hold_ms: int = Field(default=150, ge=0, le=HOLD_MAX_MS)


class TypeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=TYPE_MAX_CHARS)
    per_key_ms: int = Field(default=12, ge=4, le=100)


class SequenceStep(BaseModel):
    """Exactly one of press / set_state / type_text / delay_ms."""

    press: PressRequest | None = None
    set_state: keys.StateUpdate | None = None
    type_text: TypeRequest | None = None
    delay_ms: int | None = Field(default=None, ge=0, le=DELAY_MAX_MS)

    @model_validator(mode="after")
    def _exactly_one(self) -> SequenceStep:
        chosen = sum(
            choice is not None
            for choice in (self.press, self.set_state, self.type_text, self.delay_ms)
        )
        if chosen != 1:
            raise ValueError(
                "sequence step must set exactly one of press / set_state /"
                " type_text / delay_ms"
            )
        return self


class Gadget:
    def __init__(self, *, node: str, lockfile: str, watchdog_ttl: float):
        self._node = node
        self._lockfile = lockfile
        self._ttl = float(watchdog_ttl)
        self._owner_pid = os.getpid()
        self._deadline: float | None = None
        self._closed = False
        self._expiry_count = 0
        self._stop = threading.Event()
        self._reql = threading.RLock()
        self._lock_fd: int | None = None
        self._fd: int | None = None
        self._led_fd: int | None = None
        self._acquire_session_lock()
        try:
            # Writes block like the gamepad (the host must poll the IN
            # endpoint); the LED fd is a separate nonblocking read handle for
            # the SET_REPORT output report (None = host hasn't sent any yet).
            self._fd = os.open(node, os.O_WRONLY)
            self._led_fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            self._release_session_lock()
            raise GadgetError(
                f"cannot open {node}: {exc} (gadget service must be up; the"
                " uid needs the usb-gadget group)"
            ) from exc
        # Idle on start: a fresh session always begins from a known state.
        self._state = KeyboardState.idle()
        self._commit(self._state)
        threading.Thread(
            target=self._watchdog_loop, name="nixpilot-kb-watchdog", daemon=True
        ).start()
        log.info(
            "keyboard profile ready: node=%s watchdog_ttl=%.1fs lock=%s pid=%d",
            node,
            self._ttl,
            lockfile,
            self._owner_pid,
        )

    # -- tool-facing operations ---------------------------------------------

    def set_state(self, update: StateUpdate) -> KeyboardState:
        with self._reql:
            self._check_open()
            self._state = self._state.merged(update)
            self._commit(self._state)
            return self._state

    def press(self, press: PressRequest) -> dict:
        with self._reql:
            self._check_open()
            previous = self._state
            pressed = previous.with_press(
                frozenset(press.modifiers), frozenset(press.keys)
            )
            if len(pressed.keys) > KEY_SLOTS:
                raise GadgetError(
                    f"press would hold {len(pressed.keys)} keys; the boot"
                    f" keyboard has {keys.KEY_SLOTS} slots (reset or release"
                    " sticky keys first)"
                )
            self._commit(pressed)
            time.sleep(press.hold_ms / 1000.0)
            self._state = previous
            # Re-asserting the pre-call state also refreshes the watchdog.
            self._commit(previous)
            return {
                "pressed": {
                    "modifiers": sorted(m.value for m in press.modifiers),
                    "keys": sorted(k.value for k in press.keys),
                },
                "hold_ms": press.hold_ms,
                "state": previous.as_dict(),
            }

    def type_text(self, request: TypeRequest) -> dict:
        """Pre-flight the full mapping, release the sticky state, then type
        per character (down, hold, up). Transient by contract: leaves the
        keyboard idle — chaining modifiers with typing must use sequence."""
        try:
            plan = keys.type_plan(request.text)
        except keys.ReportError as exc:
            raise GadgetError(f"type_text rejected before any write: {exc}") from exc
        total_ms = len(plan) * request.per_key_ms
        if total_ms > TYPE_MAX_TOTAL_MS:
            raise GadgetError(
                f"typing duration {total_ms} ms exceeds {TYPE_MAX_TOTAL_MS} ms"
                " (pre-flight check; reduce text or per_key_ms)"
            )
        with self._reql:
            self._check_open()
            if self._state != KeyboardState.idle():
                self._state = KeyboardState.idle()
                self._commit(self._state)
            started = time.monotonic()
            for key, shift in plan:
                state = KeyboardState(
                    modifiers=frozenset({Modifier.LSHIFT}) if shift else frozenset(),
                    keys=frozenset({key}),
                )
                self._commit(state)
                time.sleep(request.per_key_ms / 1000.0)
                self._state = KeyboardState.idle()
                self._commit(self._state)
            log.info(
                "typed %d characters in %.0f ms",
                len(plan),
                (time.monotonic() - started) * 1000,
            )
            return {
                "chars": len(plan),
                "per_key_ms": request.per_key_ms,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "state": self._state.as_dict(),
            }

    def sequence(self, steps: list[SequenceStep]) -> dict:
        if not 1 <= len(steps) <= SEQUENCE_MAX_STEPS:
            raise GadgetError(
                f"sequence requires 1..{SEQUENCE_MAX_STEPS} steps, got {len(steps)}"
            )
        total = 0
        for step in steps:
            total += step.delay_ms or 0
            if step.press is not None:
                total += step.press.hold_ms
            if step.type_text is not None:
                try:
                    keys.type_plan(step.type_text.text)  # pre-flight validity
                except keys.ReportError as exc:
                    raise GadgetError(
                        f"sequence step rejected before any write: {exc}"
                    ) from exc
                total += len(step.type_text.text) * step.type_text.per_key_ms
        if total / 1000.0 > SEQUENCE_MAX_SECONDS:
            raise GadgetError(
                f"sequence total duration {total} ms exceeds"
                f" {int(SEQUENCE_MAX_SECONDS * 1000)} ms (pre-flight check)"
            )
        with self._reql:
            self._check_open()
            started = time.monotonic()
            results: list[dict] = []
            for pos, step in enumerate(steps, 1):
                if step.press is not None:
                    results.append({"step": pos, **self.press(step.press)})
                elif step.set_state is not None:
                    self._state = self._state.merged(step.set_state)
                    self._commit(self._state)
                    results.append({"step": pos, "set_state": self._state.as_dict()})
                elif step.type_text is not None:
                    results.append({"step": pos, **self.type_text(step.type_text)})
                elif step.delay_ms is not None:
                    time.sleep(step.delay_ms / 1000.0)
                    results.append({"step": pos, "delay_ms": step.delay_ms})
            return {
                "steps": results,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }

    def reset(self) -> KeyboardState:
        with self._reql:
            self._check_open()
            self._state = KeyboardState.idle()
            self._commit(self._state)
            return self._state

    def send_raw(self, hex_report: str) -> KeyboardState:
        try:
            data = parse_raw_hex(hex_report)
            state = KeyboardState.from_report(data)
        except keys.ReportError as exc:
            raise GadgetError(f"invalid raw report: {exc}") from exc
        with self._reql:
            self._check_open()
            # Commit the exact bytes requested (parity with the workspace
            # hidg-smoke vectors); state tracking follows the decoded report.
            self._commit_raw(data, state)
            return state

    def _read_leds(self) -> dict | None:
        """Last host-driven LED output report (SET_REPORT/SETUP transport);
        None while nothing has been received since boot (or the last read)."""
        if self._led_fd is None:
            return None
        try:
            data = os.read(self._led_fd, 1)
        except BlockingIOError:
            return None
        except OSError as exc:
            log.error("LED readback failed: %s", exc)
            return None
        if not data:
            return None
        return {name: bool(data[0] & bit) for name, bit in keys.LED_BITS.items()} | {
            "raw": data[0]
        }

    def status(self) -> dict:
        with self._reql:
            remaining = (
                None
                if self._closed or self._deadline is None
                else round(max(0.0, self._deadline - time.monotonic()), 3)
            )
            state = self._state.as_dict()
        udc = None
        try:
            with open("/sys/kernel/config/usb_gadget/g1/UDC", encoding="ascii") as fh:
                if bound := fh.read().strip():
                    udc = bound
        except OSError:
            pass
        return {
            "node": self._node,
            "open": not self._closed,
            "udc": udc,
            "state": state,
            "host_leds": self._read_leds(),
            "watchdog_ttl_s": self._ttl if self._ttl > 0 else None,
            "watchdog_expires_in_s": remaining,
            "watchdog_fired_count": self._expiry_count,
            "single_writer": {"lockfile": self._lockfile, "pid": self._owner_pid},
            "note": (
                "state shown is the server-asserted state; host_leds reflect"
                " the target's keyboard lock lights (the device's only"
                " readback channel)"
            ),
        }

    # -- lifecycle ------------------------------------------------------------

    def shutdown(self) -> None:
        with self._reql:
            if self._closed:
                return
            self._stop.set()
            try:
                if self._fd is not None:
                    self._state = KeyboardState.idle()
                    os.write(self._fd, self._state.encode())
            except OSError as exc:
                log.error("shutdown idle write failed: %s", exc)
            finally:
                self._closed = True
                for fd_attr in ("_fd", "_led_fd"):
                    if (fd := getattr(self, fd_attr)) is not None:
                        os.close(fd)
                        setattr(self, fd_attr, None)
                self._release_session_lock()
                log.info("keyboard profile shut down cleanly")

    # -- internals --------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed or self._fd is None:
            raise GadgetError("keyboard session is shut down")

    def _commit(self, state: KeyboardState) -> None:
        self._commit_raw(state.encode(), state)

    def _commit_raw(self, raw: bytes, state: KeyboardState) -> None:
        if len(raw) != keys.REPORT_LEN:  # codec invariant, unreachable
            raise GadgetError("internal: report size invariant violated")
        try:
            os.write(self._fd, raw)
        except OSError as exc:
            raise GadgetError(
                f"write to {self._node} failed: {exc} — did the gadget go"
                " down? (status/reset probe)"
            ) from exc
        if state == KeyboardState.idle() or self._ttl <= 0:
            self._deadline = None
        else:
            self._deadline = time.monotonic() + self._ttl
        self._state = state

    def _watchdog_loop(self) -> None:
        while not self._stop.wait(WATCHDOG_POLL_S):
            with self._reql:
                if self._closed or self._deadline is None:
                    continue
                if time.monotonic() < self._deadline:
                    continue
                self._expiry_count += 1
                log.warning("watchdog expired — reverting to idle")
                self._state = KeyboardState.idle()
                try:
                    os.write(self._fd, self._state.encode())
                except OSError as exc:
                    log.error("watchdog idle write failed: %s", exc)
                self._deadline = None

    def _acquire_session_lock(self) -> None:
        fd = os.open(self._lockfile, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + LOCK_GRACE_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise GadgetError(
                        f"another nixpilot-keyboard-mcp session holds"
                        f" {self._lockfile} (single-writer contract across"
                        " MCP client sessions)"
                    )
                time.sleep(0.2)

    def _release_session_lock(self) -> None:
        if (fd := self._lock_fd) is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            self._lock_fd = None
