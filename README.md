# nixpilot

MCP servers for the NixOS-converted TinyPilot KVM ("nixpilot"), **served by
the tinypilot host**. Two profiles ship today: the M5 USB-OTG **gamepad**
(input injection, binary `nixpilot-mcp`) and read-only **screen** observation
(binary `nixpilot-screen-mcp`); further profiles join the same repo. Profile
device facts: `../tinypilot-docs/usb-gadget.md` + `m5-gate.md` (gadget),
`video-pipeline.md` + `m3-gate.md` (capture).

The tinypilot host config imports this repo as a flake input and installs
both packages, putting both binaries in the device's `PATH`.

## Scope

- **Gamepad profile (in scope):** the M5 device persona — 16 named buttons,
  two 8-bit analog sticks (left X/Y, right Z/Rz), an 8-way hat switch, 8-byte
  reports, write-only (no readback — `no_out_endpoint=1`).
- **Screen profile (in scope):** read-only frame capture from the device's
  uStreamer instance (loopback HTTP): a single `screenshot` tool returning
  the latest JPEG frame plus metadata, and a `screen://state` resource.
  Statelessness is a contract, not an oversight: concurrent client sessions
  are safe for a read-only profile.
- **Deferred (not precluded by design):** `ui_scan` — scan a captured frame
  for UI elements and report bounding boxes; coordinates will be normalized
  to `[0,1]` floats with frame dimensions attached (see the screen profile's
  metadata contract). Also deferred: keyboard/mouse profiles, virtual media,
  frontend/backend integration, systemd/HTTP transport behind Caddy, audio.
- **Milestone status:** tracked in this repo, not in `../tinypilot/milestones.md`.
  Host-side deployment beyond the package import (systemd service, HTTP
  transport) is an explicit later decision.

## Design decisions

| Decision | Resolution |
|---|---|
| Profiles | One tool domain per server binary: gamepad (input), screen (observation); shared runtime helpers in `nixpilot/common` |
| Transport | MCP stdio, launched through SSH by the client (per-session process); HTTP deferred |
| Stack | Python + official `mcp` SDK, packaged via this flake (`python3.withPackages`) |
| Gamepad safety | Auto-release on `press`, sticky-hold watchdog, idle write at start/exit, flock single-writer |
| Screen safety | Read-only, stateless; validated JPEG (magic + EOI) so error pages can't masquerade as frames; bounded HTTP timeouts |
| Packaging | Flake `packages.<system>.nixpilot-mcp` and `.nixpilot-screen-mcp`; PATH binaries in the host closure |
| Host wire-up | aedificium `flake.lock` input + `environment.systemPackages` on the tinypilot host |

## Transport contract

MCP stdio: the client launches the server over SSH; each client session gets
its own process on the device, with SSH providing identity, confidentiality,
and idle-start state.

- **Packaged path (post wire-up + rebuild):** the client commands are
  `ssh princet@tinypilot.home.trprince.com nixpilot-mcp` and
  `ssh … nixpilot-screen-mcp` — both binaries live in the system closure
  (`systemPackages`), so no interpreter provisioning, no rsync, and no GC
  eviction concerns.
- **Dev loop (before the next host rebuild):** rsync this repo to
  `tinypilot:~/nixpilot/` and launch
  `ssh … 'NIXPILOT_USTREAMER_URL=http://127.0.0.1:48001 nix run ~/nixpilot#nixpilot-screen-mcp'`
  — the package rebuilds on-device from the synced tree on each run.
- **Gamepad sessions:** one session = one process = one flock holder;
  simultaneous sessions are rejected with a clear error. **Screen sessions:**
  no locks by design; any number of concurrent sessions may coexist with
  gamepad sessions.
- Client config (pi-mcp-adapter and other stdio clients): `command: ssh`,
  `args: [tinypilot, nixpilot-mcp]`, `toolPrefix: tinypilot`; likewise the
  screen binary with its own prefix.

## Profile: gamepad (`nixpilot-mcp`)

Server name `nixpilot-mcp`; capabilities `tools` + `resources`. Validation is
strict (fail-fast, no clamping); errors are MCP tool errors with a
machine-readable shape. Every write result carries the delivery caveat
(success means the USB gadget accepted the report — the target's
interpretation must be verified on-screen).

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

## Profile: screen (`nixpilot-screen-mcp`)

Server name `nixpilot-screen-mcp`; capabilities `tools` + `resources`.
Read-only and stateless: no device-state writes, no locks, no watchdog;
failure is always a structured MCP tool error, never a partial success.

**`screenshot`** — no arguments. Fetches uStreamer's `/snapshot` (the most
recent JPEG frame; `--drop-same-frames 30` makes static screens cheap) plus
`/state` for advisories, validates the frame, and returns two content blocks:

1. `image` content (`image/jpeg`, base64) — the pi-mcp-adapter maps MCP image
   blocks natively, so vision-capable models receive the real image.
2. `text` content — JSON metadata:

```jsonc
{
  "width": 1920, "height": 1080,     // frame dims (from the JPEG SOF header)
  "bytes": 107866, "sha256": "…",    // content hash for cross-referencing
  "source_online": true, "captured_fps": 48.0,
  "fetched_url": "http://127.0.0.1:48001/snapshot"
}
```

The frame dimensions ride along so consumers can reason in frame-relative
coordinates without a second call; the deferred `ui_scan` will report
bounding boxes as `[x0, y0, x1, y1]` floats on `[0,1]` together with the
same metadata shape, making results scale-free.

**`screen://state`** resource — capture-pipeline snapshot (source online,
resolution, captured fps, encoder, client count, observed URL). Screenshots
degrade gracefully when `/state` misbehaves; a failed `/snapshot`
(connection refused, HTTP ≥ 400 — e.g. 503 with no live input signal, HTML
error body, truncated transfer) is a tool error naming the cause.

Environment: `NIXPILOT_USTREAMER_URL` (default `http://127.0.0.1:48001` —
uStreamer binds loopback only, so the transport needs no credentials),
`NIXPILOT_SCREEN_TIMEOUT_S` (default 10), `NIXPILOT_LOG_LEVEL`.

## Safety and lifecycle

Gamepad:

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

Screen: read-only; every upstream call is timeout-bounded; capture failures
map to tool errors with cause; the target machine is never driven.

## Repository layout (target state)

```
flake.nix + flake.lock    packages: nixpilot-mcp, nixpilot-screen-mcp
nixpilot/common/          shared runtime (logging, env parsing)
nixpilot/gamepad/         gamepad profile (report codec, gadget, server, selftest)
nixpilot/screen/          screen profile (capture, server, selftest)
scripts/lib/mcp_session.py  shared JSON-RPC-over-stdio session transport
scripts/mcp-smoke.sh      gamepad protocol gate (device required)
scripts/screen-smoke.sh   screen protocol gate (live video required)
Makefile                  install-hooks / fmt / check entry points
.pre-commit-config.yaml   aedificium-style rig (nix hooks + pi-review)
```

## Increment plan

1. **Gamepad (done):** flake + gate + aedificium wire-up + pi client smoke.
2. **Screen (this increment):** the `screenshot` profile, offline selftest,
   aedificium package wire-up, `screen-smoke.sh` gate, client entry.
3. **Deferred:** `ui_scan` (bbox contract above), keyboard/mouse profiles,
   systemd/HTTP deployment, multi-writer arbitration.
