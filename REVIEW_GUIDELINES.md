# REVIEW_GUIDELINES.md

Project-specific review criteria for the nixpilot repository. The `pi-review`
pre-commit hook injects this file into the review prompt, overriding its
default criteria.

## Repository context

A flake-packaged collection of MCP servers (binary `nixpilot-mcp`) for the
NixOS TinyPilot KVM, served on-device over client-launched SSH sessions. The
gamepad profile drives the KVM target's `/dev/hidg3`; the contracts live in
`README.md`, the device facts in the sibling `../tinypilot/tinypilot-docs/`
workspace (`usb-gadget.md`, `m5-gate.md`).

## Blocking criteria — reject the change

### 1. Contract drift

- Any change to the 8-byte report wire format, enum tables (buttons, hat),
  or axis encoding (byte `0x7F` center, +1 → 255, −1 → 0) that does not match
  `nixpilot_mcp/report.py` and the documented profile is rejected; the codec
  and the docs must move together.
- Tool/resource names, argument shapes, bounds (hold ≤ 10 s, sequence ≤ 100
  steps / 30 s), or the delivery-caveat contract drifting from `README.md`
  without a README edit in the same change.
- Selftest vectors not updated when codec behavior changes (`nixpilot_mcp/selftest.py`
  must keep proving the format).

### 2. Protocol correctness

- Anything writing to stdout besides the MCP frame stream (logs to stderr
  only); anything that breaks the initialize/dispatch loop.
- Silent failure: swallowing exceptions around device writes, clamping
  instead of rejecting invalid input, or returning success without a write
  when one was contracted (fail-fast philosophy).

### 3. Safety regression

- Removing or weakening: idle-on-start, watchdog expiry (idle revert), idle
  on shutdown, flock single-writer arbitration, sequence pre-flight
  duration checks, hold/delay bounds.
- New blocking IO on the MCP event loop (tool functions must stay compatible
  with the threading model already in place — sync FastMCP tools run in a
  worker thread and the process-level lock must cover all writes).

### 4. Packaging / guardrail violations

- Breaks `nix flake check`, adds a second nixpkgs pin into the package
  (python env must stay single-source), or leaks secrets into the closure.
- Device access from import-time or check-time code paths (the offline
  selftest must never open HID nodes); repo code performing SSH calls to
  the host at runtime outside the documented smoke script.
- AGENTS.md guidance made false, or scope dilution (line cap enforced).
