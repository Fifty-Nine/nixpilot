# nixpilot

MCP servers for the NixOS-converted TinyPilot KVM ("nixpilot"), **served by
the tinypilot host**. The initial profile exposes the M5 USB-OTG HID gamepad
as agent-usable tool calls; further gadget profiles join the same repo and
binary later. Profile device facts: `../tinypilot-docs/usb-gadget.md`
(gadget) and `../tinypilot-docs/m5-gate.md` (verified behavior).

The shipped flake package is **`nixpilot-mcp`**; the tinypilot host config
imports this repo as a flake input and installs the package, which puts the
`nixpilot-mcp` binary in the device's `PATH` inside the system closure.

## Scope

- **In scope:** a gamepad-only MCP profile over the device persona from M5:
  16 named buttons, two 8-bit analog sticks (left X/Y, right Z/Rz), an 8-way
  hat switch, 8-byte reports, write-only (no readback — `no_out_endpoint=1`).
- **Out of scope:** keyboard/mouse/absolute-mouse profiles, video/audio
  observability, virtual media, frontend/backend integration, multi-profile
  arbitration. Target-screen feedback does not exist at this layer; tool
  results carry the caveat that delivery is verified visually on the target.
- **Milestone status:** tracked in this repo, not in `../tinypilot/milestones.md`.
  Host-side deployment beyond the package import (systemd service, HTTP
  transport) is an explicit later decision.

## Design decisions

| Decision | Resolution |
|---|---|
| Tool surface | Stateful core + macro + raw escape hatch; layout/state as resources |
| Transport | MCP stdio, launched through SSH by the client (per-session process); HTTP deferred |
| Stack | Python + official `mcp` SDK, packaged via this flake (`python3.withPackages`) |
| Safety | Auto-release on `press`, sticky-hold watchdog, idle write at start/exit, flock single-writer |
| Packaging | This repo's flake `packages.<system>.nixpilot-mcp`; binary wrapper in the host closure |
| Host wire-up | aedificium `flake.lock` input + `environment.systemPackages` on the tinypilot host |

## Transport contract

MCP stdio: the client launches the server over SSH; each client session gets
its own process on the device, with SSH providing identity, confidentiality,
and idle-start state.

- **Packaged path (post wire-up + rebuild):** the client command is simply
  `ssh princet@tinypilot.home.trprince.com nixpilot-mcp` — the binary lives in
  the system closure (`systemPackages`), so no interpreter provisioning, no
  rsync, and no GC eviction concerns.
- **Dev loop (before the next host rebuild):** rsync this repo to
  `tinypilot:~/nixpilot/` and launch
  `ssh ... 'nix run ~/nixpilot#default'` — the package rebuilds on-device from
  the synced tree on each run.
- One session = one process = one flock holder; simultaneous sessions are
  rejected with a clear error. Client config (pi-mcp-adapter and other stdio
  clients): `command: ssh`, `args: [tinypilot, nixpilot-mcp]`,
  `toolPrefix: nixpilot`.

## Tool contracts

Server name `nixpilot-mcp`; capabilities `tools` + `resources`. Validation is
strict (fail-fast, no clamping); errors are MCP tool errors with a machine-
readable `code`.

**`set_state`** — authoritative partial merge; one 8-byte report per call.

```jsonc
{
  "buttons": ["A", "START"],        // optional; [] clears
  "left":  { "x": 0.0, "y": 0.0 },  // -1.0..1.0, strict bounds
  "right": { "x": 0.0, "y": 0.0 },
  "hat":   "NEUTRAL"                // N|NE|E|SE|S|SW|W|NW|NEUTRAL
}
```

**`press`** — `{ buttons, hold_ms = 200 (0..10000) }`: atomic fused write +
sleep + re-assert previous state. **`sequence`** — ≤ 100 steps of
`press` / `set_state` / `delay_ms`, ≤ 30 s total, pre-checked, one flock
acquisition. **`reset`** — idle report. **`status`** — node writability, UDC
binding, asserted state, watchdog deadline, lock holder. **`send_raw`** —
`{ hex: <16 hex> }`; byte 7 (const padding) must be `00`; decoded back into
state so tracking stays authoritative (parity with the tinypilot workspace's
`scripts/gamepad-smoke.sh` vectors).

Resources: `gadget://gamepad/layout` (static report schema, button name →
byte/bit map with the Linux input codes verified in `m5-gate.md`, base64
descriptor) and `gadget://gamepad/state` (live asserted state + watchdog
deadline).

### Encoding

| Field | Domain | Wire |
|---|---|---|
| Buttons | `A B X Y LB RB LT RT BACK START L3 R3 GUIDE BTN14 BTN15 BTN16` | `[0]` bits 0–7 = buttons 1–8, `[1]` bits 0–7 = 9–16 |
| Sticks | float −1.0..1.0 | byte center `0x7F`; +1.0 → 255; −1.0 → 0 |
| Hat | `N NE E SE S SW W NW NEUTRAL` | low nibble of `[6]`: 0..7, neutral `0xF` |

## Safety and lifecycle

- **Idle on start** (known state even after a crashed predecessor) and
  best-effort idle on exit (watchdog is the backstop).
- **Watchdog:** any non-idle state carries a TTL (default 60 s, override
  `NIXPILOT_WATCHDOG_TTL`); expiry writes idle and logs to stderr (stdout is
  protocol-only).
- **Arbitration:** exclusive `flock` on the session lockfile; timed phases
  never interleave with tool calls.
- **Writes:** one long-lived `open("/dev/hidg3")`, single atomic 8-byte write
  per report; a successful write proves the gadget accepted the report —
  nothing about the target's interpretation.

## Repository layout (target state)

```
flake.nix + flake.lock   package: nixpilot-mcp wrapper (python env + server)
server.py (nixpilot_mcp) profile-based MCP server; gamepad profile first
scripts/mcp-smoke.sh     protocol-level validation gate (JSON-RPC over stdio)
Makefile                 install-hooks / fmt / check entry points
.pre-commit-config.yaml  aedificium-style rig (nix hooks + pi-review)
```

## Increment plan

1. **This increment:** flake + `server.py` + smoke gate + aedificium wire-up
   (input, `environment.systemPackages`). You run `make rebuild-tinypilot`
   once; the client command becomes `ssh tinypilot nixpilot-mcp`.
2. **Client integration:** pi `.mcp.json` entry + agent-driven smoke pass.
3. **Deferred:** systemd/HTTP deployment (transport decision behind the
   existing Caddy), additional profiles (keyboard/mouse), multi-writer
   arbitration.
