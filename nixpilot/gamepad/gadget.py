"""Device-side gamepad profile: the HID node writer, sticky-state machine,
watchdog, and single-writer session arbitration.

Lifecycle (see mcp/README.md safety contracts): take the session file lock
(process-lifetime, so a second MCP client session fails instead of
interleaving HID state), open the node, write the idle report, then serve
tool calls. Any non-idle write arms the watchdog; expiry reverts to idle so
a crashed client cannot leave a held button. Shutdown writes idle
best-effort from the exit/signal path.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
import time

from pydantic import BaseModel, Field, model_validator

from . import report
from .report import Button, GamepadState, ReportError, StateUpdate

log = logging.getLogger("nixpilot.gamepad")

HOLD_MAX_MS = 10_000
DELAY_MAX_MS = 2_000
SEQUENCE_MAX_STEPS = 100
SEQUENCE_MAX_SECONDS = 30.0
WATCHDOG_POLL_S = 0.25
LOCK_GRACE_S = 5.0


class GadgetError(RuntimeError):
    """Gadget access failure or a contract-violating request."""


class PressRequest(BaseModel):
    buttons: list[Button] = Field(min_length=1)
    hold_ms: int = Field(default=200, ge=0, le=HOLD_MAX_MS)


class SequenceStep(BaseModel):
    """Exactly one of press / set_state / delay_ms."""

    press: PressRequest | None = None
    set_state: StateUpdate | None = None
    delay_ms: int | None = Field(default=None, ge=0, le=DELAY_MAX_MS)

    @model_validator(mode="after")
    def _exactly_one(self) -> SequenceStep:
        chosen = sum(
            choice is not None for choice in (self.press, self.set_state, self.delay_ms)
        )
        if chosen != 1:
            raise ValueError(
                "sequence step must set exactly one of press / set_state / delay_ms"
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
        self._acquire_session_lock()
        try:
            self._fd = os.open(node, os.O_WRONLY)
        except OSError as exc:
            self._release_session_lock()
            raise GadgetError(
                f"cannot open {node}: {exc} (gadget service must be up; the"
                " uid needs the usb-gadget group)"
            ) from exc
        # Idle on start: a fresh session always begins from a known state,
        # even if a predecessor crashed holding a button.
        self._state = GamepadState.idle()
        self._commit(self._state)
        threading.Thread(
            target=self._watchdog_loop, name="nixpilot-watchdog", daemon=True
        ).start()
        log.info(
            "gamepad profile ready: node=%s watchdog_ttl=%.1fs lock=%s pid=%d",
            node,
            self._ttl,
            lockfile,
            self._owner_pid,
        )

    # -- tool-facing operations ---------------------------------------------

    def set_state(self, update: StateUpdate) -> GamepadState:
        with self._reql:
            self._check_open()
            self._state = self._state.merged(update)
            self._commit(self._state)
            return self._state

    def press(self, press: PressRequest) -> dict:
        with self._reql:
            self._check_open()
            previous = self._state
            self._commit(previous.with_buttons(frozenset(press.buttons)))
            time.sleep(press.hold_ms / 1000.0)
            self._state = previous
            # Re-asserting the pre-call state also refreshes the watchdog.
            self._commit(previous)
            return {
                "pressed": [b.value for b in press.buttons],
                "hold_ms": press.hold_ms,
                "state": previous.as_dict(),
            }

    def sequence(self, steps: list[SequenceStep]) -> dict:
        if not 1 <= len(steps) <= SEQUENCE_MAX_STEPS:
            raise GadgetError(
                f"sequence requires 1..{SEQUENCE_MAX_STEPS} steps, got {len(steps)}"
            )
        total = sum(
            (step.press.hold_ms if step.press else 0)
            + (step.delay_ms if step.delay_ms is not None else 0)
            for step in steps
        )
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
                elif step.delay_ms is not None:
                    time.sleep(step.delay_ms / 1000.0)
                    results.append({"step": pos, "delay_ms": step.delay_ms})
            return {
                "steps": results,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }

    def reset(self) -> GamepadState:
        with self._reql:
            self._check_open()
            self._state = GamepadState.idle()
            self._commit(self._state)
            return self._state

    def send_raw(self, hex_report: str) -> GamepadState:
        try:
            data = report.parse_raw_hex(hex_report)
            state = GamepadState.from_report(data)
        except ReportError as exc:
            raise GadgetError(f"invalid raw report: {exc}") from exc
        with self._reql:
            self._check_open()
            # Commit the exact bytes requested (parity with the workspace
            # smoke vectors); state tracking follows the decoded report.
            self._commit_raw(data, state)
            return state

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
            "watchdog_ttl_s": self._ttl if self._ttl > 0 else None,
            "watchdog_expires_in_s": remaining,
            "watchdog_fired_count": self._expiry_count,
            "single_writer": {"lockfile": self._lockfile, "pid": self._owner_pid},
            "note": "state shown is the server-asserted state; the HID function has no readback",
        }

    # -- lifecycle ------------------------------------------------------------

    def shutdown(self) -> None:
        with self._reql:
            if self._closed:
                return
            self._stop.set()
            try:
                if self._fd is not None:
                    self._state = GamepadState.idle()
                    os.write(self._fd, report.IDLE_REPORT)
            except OSError as exc:
                log.error("shutdown idle write failed: %s", exc)
            finally:
                self._closed = True
                os.close(self._fd)
                self._fd = None
                self._release_session_lock()
                log.info("gamepad profile shut down cleanly")

    # -- internals --------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed or self._fd is None:
            raise GadgetError("gadget session is shut down")

    def _commit(self, state: GamepadState) -> None:
        self._commit_raw(state.encode(), state)

    def _commit_raw(self, raw: bytes, state: GamepadState) -> None:
        if len(raw) != report.REPORT_LEN:  # codec invariant, unreachable
            raise GadgetError("internal: report size invariant violated")
        try:
            os.write(self._fd, raw)
        except OSError as exc:
            raise GadgetError(
                f"write to {self._node} failed: {exc} — did the gadget go down?"
                " (status/reset probe)"
            ) from exc
        if state == GamepadState.idle() or self._ttl <= 0:
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
                self._state = GamepadState.idle()
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
                        f"another nixpilot-mcp session holds {self._lockfile}"
                        " (single-writer contract across MCP client sessions)"
                    )
                time.sleep(0.2)

    def _release_session_lock(self) -> None:
        if (fd := self._lock_fd) is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            self._lock_fd = None
