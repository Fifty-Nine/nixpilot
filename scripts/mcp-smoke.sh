#!/usr/bin/env bash
# nixpilot-mcp (gamepad profile) protocol smoke gate. Delegates to
# scripts/gamepad_smoke.py; requires the deployed launch path
# (ssh-hosted nixpilot-mcp) and python3 on the invoking machine.
#
# The gate injects real input on the connected target: keep that screen
# in view. Environment: NIXPILOT_SMOKE_HOST, NIXPILOT_SMOKE_NODE.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/gamepad_smoke.py" "$@"
