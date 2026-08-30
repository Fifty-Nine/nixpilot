# AGENTS.md

Guidance for AI agents working in this repository.

## Project

`nixpilot` — MCP servers for the NixOS-converted TinyPilot KVM, served by the
tinypilot host. The flake packages the profile servers `nixpilot-mcp`
(gamepad), `nixpilot-keyboard-mcp` (keyboard, input) and
`nixpilot-screen-mcp` (screen, read-only) — Python, official `mcp` SDK; the aedificium-nixos homelab flake imports this repo and installs
the packages on the tinypilot host. The code is the spec; design contracts
live in `README.md` and git commit messages, not here.

For device/profile context, the sibling migration workspace at
`../tinypilot/` holds the authoritative docs (`tinypilot-docs/`) and the
extracted vendor rootfs. Working there (or in `../aedificium-nixos/`) requires
obeying those repos' own AGENTS.md files.

## Guardrails

- **Local commits only. Never push.** The repo is consumed by aedificium via
  a local `git+file:` flake input while unpublished; switch to a remote only
  on explicit user instruction.
- **Never touch the devices.** No `ssh` writes to the tinypilot host, no
  rebuilds, no system changes — the tinypilot host is owned by
  `../aedificium-nixos/` and its AGENTS.md deployment rules (rebuilds are the
  user's). Smoke-test device access is initiated by the user unless the user
  has explicitly scripted/asked for it.
- **Do not edit aedificium or the migration workspace from here** without an
  explicit user instruction; cross-repo changes are separate review units.
- `nix flake check` is the standard validation and always permitted.

## Development workflow

All tooling comes from the dev shell (`nix develop`); register hooks once
with `make install-hooks`.

1. Edit files.
2. `git add -A` — flakes ignore untracked files.
3. `nix flake check` — must pass.
4. `make fmt`, keep commits small and focused, then `git commit` (hooks run).

The pi-review pre-commit hook reviews every commit and may take up to
600 seconds; **never commit past a review timeout** — rerun, don't bypass.

## Conventions

- `flake.lock` is committed; refresh with `nix flake update`.
- Nix style follows the aedificium guidelines (module signatures, let/in,
  option types, `throw` for user-facing errors, no bare types attr sets).
- Python: std library-first, strict runtime validation (fail fast), logging
  to stderr only — stdout is reserved for the MCP protocol stream.
- Do not add plaintext secrets at all; this repo so far requires none.
- Keep comments synchronized with the code; no historical narration — git
  history is the record.
