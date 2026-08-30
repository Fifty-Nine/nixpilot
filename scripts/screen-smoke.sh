#!/usr/bin/env bash
# nixpilot-screen-mcp protocol smoke gate. Delegates to
# scripts/screen_smoke.py; the frame assertions need a live video source
# on the device (the error path runs either way). Read-only: nothing is
# injected on the target.
# Environment: NIXPILOT_SMOKE_HOST, NIXPILOT_SMOKE_NODE.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/screen_smoke.py" "$@"
