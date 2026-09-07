# nixpilot

Reusable NixOS flake for TinyPilot KVM appliances with optional MCP servers.

## Architecture & Features Overview

The `nixpilot` flake provides a modular, declarative NixOS configuration for building a TinyPilot KVM appliance. The architecture is composed of a unified default module and several sub-modules that can be consumed or overridden:

- **`services.nixpilot.backend`**: Core TinyPilot backend service and dependencies.
- **`services.nixpilot.ustreamer`**: Video capture pipeline via uStreamer.
- **`services.nixpilot.usb-gadget`**: Composite USB gadget configuration (keyboard, mouse, gamepad).
- **`services.nixpilot.mcp`**: Model Context Protocol (MCP) servers (gamepad, keyboard, screen observation).
- **`services.nixpilot.caddy`**: Reverse proxy configuration utilizing Caddy for unbuffered streaming and web access.

## Usage Instructions

### Building a bootable SD image (Raspberry Pi 4)

You can build a bootable SD card image for a Raspberry Pi 4 directly from this flake:

```bash
nix build .#sdImage
```

### Importing the Flake in a downstream NixOS system

You can consume the modules in your own NixOS configuration. First, add the flake to your `flake.nix` inputs:

```nix
{
  inputs.nixpilot.url = "https://flakehub.com/f/Fifty-Nine/nixpilot/*";
  # ...
}
```

Then, import the default module in your system configuration:

```nix
{
  imports = [
    inputs.nixpilot.nixosModules.default
  ];

  services.nixpilot = {
    enable = true;
    # Customize sub-modules as needed
  };
}
```

## Testing & Development

Code formatting, linting, and Nix evaluation are validated using `nix flake check`. To verify your changes before submitting:

```bash
make fmt
nix flake check
```

## MCP Servers

MCP servers for the NixOS-converted TinyPilot KVM, **served by the host**. Three profiles ship today: the M5 USB-OTG **gamepad** (input, `nixpilot-mcp`), the **keyboard** (input, `nixpilot-keyboard-mcp`), and read-only **screen** observation (`nixpilot-screen-mcp`). Profile device facts: `../tinypilot-docs/usb-gadget.md` + `m5-gate.md` (gadget), `video-pipeline.md` + `m3-gate.md` (capture).

## Scope
- **Gamepad profile (in scope):** the M5 device persona — 16 named buttons
  (13 exposed; the rest unrendered legacy codes), left stick X/Y and right
  stick Rx/Ry, analog triggers Z/Rz, an 8-way hat switch, 10-byte reports,
  write-only (no readback — `no_out_endpoint=1`).
- **Keyboard profile (in scope):** the boot-protocol keyboard endpoint
  (6-slot usage array, 8 modifiers, descriptor-legal usages through 0x91,
  tracked set = 109 named keys, max 6 keys/sticky state), US QWERTY
  `type_text`, and the host LED output report as the only readback channel
  (`host_leds`).
- **Screen profile (in scope):** read-only frame capture from the device's
  uStreamer instance (loopback HTTP): a single `screenshot` tool returning
  the latest JPEG frame plus metadata, and a `screen://state` resource.
  Statelessness is a contract, not an oversight: concurrent client sessions
  are safe for a read-only profile.
- **Deferred (not precluded by design):** `ui_scan` — scan a captured frame
  for UI elements and report bounding boxes; coordinates will be normalized
  to `[0,1]` floats with frame dimensions attached (see the screen profile's
  metadata contract). Also deferred: mouse profile, virtual media,
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
| Packaging | Flake `packages.<system>.{nixpilot-mcp, nixpilot-keyboard-mcp, nixpilot-screen-mcp}`; PATH binaries in the host closure |
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
- **Gamepad and keyboard sessions:** one session = one process = one
  flock holder (independent lockfiles, so gamepad + keyboard sessions may
  coexist); simultaneous sessions on the same profile are rejected with a
  clear error. **Screen sessions:** no locks by design; any number of
  concurrent sessions may coexist with input sessions.
- Client config (pi-mcp-adapter and other stdio clients): `command: ssh`,
  `args: [tinypilot, nixpilot-mcp]`, `toolPrefix: tinypilot`; likewise for
  the other two binaries with their own prefixes.

## Profile: gamepad (`nixpilot-mcp`)

Server name `nixpilot-mcp`; capabilities `tools` + `resources`. Validation is
strict (fail-fast, no clamping); errors are MCP tool errors with a
machine-readable shape. Every write result carries the delivery caveat
(success means the USB gadget accepted the report — the target's
interpretation must be verified on-screen).

**`set_state`** — authoritative partial merge; one 10-byte report per call.

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
`{ hex: <20 hex> }`; byte 9 (const padding) must be `00`; decoded back into
state so tracking stays authoritative (parity with the tinypilot workspace's
`scripts/gamepad-smoke.sh` vectors).

Resources: `gadget://gamepad/layout` (static report schema, button name →
byte/bit map with the Linux input codes verified in `m5-gate.md`, base64
descriptor) and `gadget://gamepad/state` (live asserted state + watchdog
deadline).

### Encoding

| Field | Domain | Wire |
|---|---|---|
| Buttons | `A B X Y LB RB BACK START GUIDE L3 R3` | DirectInput bit order: `[0]` bits 0–7 = A B ·(C) X Y ·(Z) LB RB, `[1]` bits 0–7 = ·(TL2) ·(TR2) BACK START GUIDE L3 R3 ·(unused). Reserved bits 2, 5 and 15 (`BTN_C`, `BTN_Z`, 0x13f) are never set — the host input stack does not render them. |
| Triggers | `LT` / `RT` buttons | analog axes `[6]` (Z) and `[7]` (Rz): asserted → `0xFF`, deasserted → `0x00` |
| Sticks | float −1.0..1.0 | bytes `[2..5]` (X Y Rx Ry), center `0x7F`; +1.0 → 255; −1.0 → 0 |
| Hat | `N NE E SE S SW W NW NEUTRAL` | low nibble of `[8]`: 0..7, neutral `0xF` |

## Profile: keyboard (`nixpilot-keyboard-mcp`)

Server name `nixpilot-keyboard-mcp`; capabilities `tools` + `resources`.
Lifecycle machinery matches the gamepad (flock single-writer on
`/tmp/nixpilot-keyboard.lock`, 60 s sticky-state watchdog `NIXPILOT_WATCHDOG_TTL`,
idle on start/exit); every write result carries the delivery caveat —
keystrokes land in the target's currently focused window.

Report contract: `[0]` modifier bitmap (LCTRL LSHIFT LALT LGUI RCTRL RSHIFT
RALT RGUI), `[1]` reserved 0x00, `[2..7]` six usage slots (usage order is
canonical; duplicates rejected). The tracked key table is 109 named keys
covering letters, digits, F1–F24, control/navigation/arrow keys, and the
keypad; `send_raw` validates against the descriptor's 0x91 logical maximum.

**`set_state`** — partial merge, sticky:

```jsonc
{
  "modifiers": ["LCTRL"],      // optional; [] clears; max 8
  "keys": ["A", "TAB"]         // optional; [] clears; max 6
}
```

**`press`** — `{keys, modifiers = [], hold_ms = 150 (0..10000)}`: atomic chord
(keys required, modifiers optional)
down + hold + re-assert. **`type_text`** — `{text, per_key_ms = 12}`: types
US QWERTY text per character (down/hold/up, shift auto-composed), transient
by contract (leaves the keyboard idle; no sticky remnants), pre-flight
capped at 200 chars / 20 s. **`sequence`** — steps of press / set_state /
type_text / delay_ms (≤ 100 steps, ≤ 30 s, one lock acquisition) for flows
like Ctrl+T then a URL. **`reset`**, **`status`** (adds `host_leds`), and
**`send_raw`** mirror the gamepad shapes; raw vectors are parity with the
tinypilot workspace's `scripts/hidg-smoke.sh` (e.g. `04002b0000000000` =
LAlt+Tab).

`host_leds` — the keyboard is the only HID function the host writes to:
`status` reads the node's single LED output byte nonblockingly (num_lock,
caps_lock, scroll_lock, indicator, raw) — consuming it, so `host_leds` is
`null` after each successful read until the host sends another output
report (lock-state changes).

Resources: `gadget://keyboard/layout` (usage map, text coverage, wire
format, descriptor) and `gadget://keyboard/state` (asserted state + LED
readback + watchdog + lock).

Environment: `NIXPILOT_KEYBOARD_NODE` (default `/dev/hidg0`), shared
`NIXPILOT_WATCHDOG_TTL`/`NIXPILOT_LOCKFILE`/`NIXPILOT_LOG_LEVEL`.

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
- **Writes:** one long-lived `open("/dev/hidg3")`, single atomic 10-byte write
  per report; a successful write proves the gadget accepted the report —
  nothing about the target's interpretation.

Screen: read-only; every upstream call is timeout-bounded; capture failures
map to tool errors with cause; the target machine is never driven.

## Repository layout (target state)

```
flake.nix + flake.lock    packages: nixpilot-mcp, nixpilot-screen-mcp
nixpilot/common/          shared runtime (logging, env parsing)
nixpilot/gamepad/         gamepad profile (report codec, gadget, server, selftest)
nixpilot/keyboard/        keyboard profile (keys codec, gadget, server, selftest)
nixpilot/screen/          screen profile (capture, server, selftest)
scripts/lib/mcp_session.py  shared JSON-RPC-over-stdio session transport
scripts/mcp-smoke.sh        gamepad protocol gate (gamepad input lands on the target)
scripts/keyboard-smoke.sh   keyboard protocol gate (default keys inert; typing opt-in)
scripts/screen-smoke.sh     screen protocol gate (live video required)
Makefile                  install-hooks / fmt / check entry points
.pre-commit-config.yaml   aedificium-style rig (nix hooks + pi-review)
```

## Increment plan

1. **Gamepad (done):** flake + gate + aedificium wire-up + pi client smoke.
2. **Screen (done):** the `screenshot` profile, offline selftest,
   aedificium package wire-up, `screen-smoke.sh` gate, client entry.
3. **Keyboard (this increment):** the typing/chord profile, offline
   selftest, aedificium package wire-up, `keyboard-smoke.sh` gate, client
   entry.
4. **Deferred:** `ui_scan` (bbox contract), mouse profile, systemd/HTTP
   deployment.
