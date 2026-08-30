#!/usr/bin/env bash
# nixpilot-keyboard-mcp protocol smoke gate. Delegates to
# scripts/keyboard_smoke.py; requires the deployed launch path and python3
# on the invoking machine.
#
# The gate injects REAL keystrokes on the target (deliberately inert keys by
# default: F13/F14, modifier holds; type_text only with
# NIXPILOT_KB_GATE_TYPE=1 once the target screen is in a safe state).
# Environment: NIXPILOT_SMOKE_HOST, NIXPILOT_SMOKE_NODE, NIXPILOT_KB_GATE_TYPE.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/keyboard_smoke.py" "$@"
