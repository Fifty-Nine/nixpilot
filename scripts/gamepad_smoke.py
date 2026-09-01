"""nixpilot-mcp (gamepad profile) protocol smoke gate. Drives the server's
stdio transport with raw JSON-RPC frames over the deployed launch path
(default: `ssh tinypilot nixpilot-mcp`, once the package is installed).

The press/set_state/sequence steps inject real input on the connected
target: keep that screen in view. The gate only proves the gadget accepted
the reports; target-side outcomes are verified visually.

Environment:
    NIXPILOT_SMOKE_HOST  ssh destination (default the tinypilot KVM host)
    NIXPILOT_SMOKE_NODE  device binary (default nixpilot-mcp)
"""

from __future__ import annotations

import json
import os
import sys
import time

from lib.mcp_session import Fail, ssh_session, step

TOOLS = ["press", "reset", "send_raw", "sequence", "set_state", "status"]
RESOURCES = ["gadget://gamepad/layout", "gadget://gamepad/state"]
A_HEX = "01007f7f7f7f00000f00"

HOST = os.environ.get("NIXPILOT_SMOKE_HOST", "princet@tinypilot.home.trprince.com")
NODE_BIN = os.environ.get("NIXPILOT_SMOKE_NODE", "nixpilot-mcp")


def main() -> None:
    print(f"nixpilot-mcp smoke gate against {HOST}")
    s = ssh_session("gate", HOST, NODE_BIN)
    info = s.initialize()
    step("initialize", info.get("name") == "nixpilot-mcp", str(info))

    names = sorted(t["name"] for t in s.call("tools/list", {}, 20).get("tools", []))
    step("tools/list", names == TOOLS, str(names))
    uris = sorted(
        r["uri"] for r in s.call("resources/list", {}, 20).get("resources", [])
    )
    step("resources/list", uris == RESOURCES, str(uris))

    st = s.tool_json("status")
    step(
        "status reachable",
        st.get("open") is True and st.get("state", {}).get("buttons") == [],
        json.dumps(st),
    )
    step("udc bound", st.get("udc") == "fe980000.usb", str(st.get("udc")))

    sent = s.tool_json("send_raw", {"hex": A_HEX})
    step("send_raw A", sent.get("state", {}).get("buttons") == ["A"], json.dumps(sent))

    state = s.tool_json(
        "set_state", {"buttons": ["B"], "left": {"x": 1.0, "y": 0.0}, "hat": "E"}
    ).get("state", {})
    step(
        "set_state full",
        state.get("buttons") == ["B"]
        and state.get("left", {}).get("x") == 1.0
        and state.get("hat") == "E",
        json.dumps(state),
    )

    state = s.tool_json("set_state", {"hat": "NEUTRAL"}).get("state", {})
    step(
        "partial merge",
        state.get("buttons") == ["B"]
        and state.get("hat") == "NEUTRAL"
        and state.get("left", {}).get("x") == 1.0,
        json.dumps(state),
    )

    state = s.tool_json("send_raw", {"hex": "03007f7f7f7f00ff0600"}).get("state", {})
    step(
        "send_raw combo",
        set(state.get("buttons", [])) == {"A", "B", "RT"} and state.get("hat") == "W",
        json.dumps(state),
    )

    state = s.tool_json("reset").get("state", {})
    step(
        "reset idle",
        state.get("buttons") == [] and state.get("hat") == "NEUTRAL",
        json.dumps(state),
    )

    seq = s.tool_json(
        "sequence",
        {
            "steps": [
                {"press": {"buttons": ["A"], "hold_ms": 120}},
                {"delay_ms": 80},
                {"press": {"buttons": ["B"], "hold_ms": 120}},
            ]
        },
    )
    step(
        "sequence",
        len(seq.get("steps", [])) == 3 and seq.get("elapsed_ms", 10**9) < 30000,
        json.dumps(seq),
    )

    for name, args in [
        (
            "reject out-of-range axis",
            {"name": "set_state", "arguments": {"left": {"x": 2.0, "y": 0.0}}},
        ),
        (
            "reject nonzero padding",
            {"name": "send_raw", "arguments": {"hex": "01007f7f7f7f00000f01"}},
        ),
        (
            "reject empty press",
            {"name": "press", "arguments": {"buttons": [], "hold_ms": 100}},
        ),
    ]:
        msg = s.call("tools/call", args)
        step(name, msg.get("isError") is True, str(msg)[:200])

    # Watchdog: separate lockfile+TTL; a held state must self-revert to idle.
    w = ssh_session(
        "watchdog",
        HOST,
        NODE_BIN,
        env={
            "NIXPILOT_WATCHDOG_TTL": "1",
            "NIXPILOT_LOCKFILE": "/tmp/nixpilot-smoke-wd.lock",
        },
    )
    w.initialize()
    held = w.tool_json("set_state", {"buttons": ["A"]})
    step(
        "watchdog armed",
        held.get("state", {}).get("buttons") == ["A"],
        json.dumps(held),
    )
    time.sleep(2.2)
    after = w.tool_json("status")
    fired = any("watchdog expired" in line for line in w.err)
    step(
        "watchdog fired + reverted",
        fired and after.get("state", {}).get("buttons") == [],
        json.dumps(after),
    )
    w.close()

    # Single-writer: the gate session holds the default lock; a second
    # session on the same lockfile must reject at startup, not interleave.
    two = ssh_session("second-writer", HOST, NODE_BIN)
    rc = two.close(timeout=12)
    step(
        "single-writer reject",
        rc != 0 and any("single-writer" in line for line in two.err),
        f"rc={rc} stderr={two.err[-2:]}",
    )

    step("clean exit", s.close() == 0)
    print(f"nixpilot-mcp smoke gate: PASS ({len(TOOLS)} tools, watchdog verified)")


if __name__ == "__main__":
    try:
        main()
    except (Fail, TimeoutError, EOFError, AssertionError) as exc:
        print(f"nixpilot-mcp smoke gate: FAIL — {exc}", file=sys.stderr)
        sys.exit(1)
