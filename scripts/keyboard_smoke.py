"""nixpilot-keyboard-mcp protocol smoke gate. Drives the keyboard profile's
stdio transport over the deployed launch path (default:
`ssh tinypilot nixpilot-keyboard-mcp`).

Injection phases run REAL keystrokes on the connected target: they land in
the target's focused window. Default key set is deliberately inert on a
desktop or game menu (F13/F14 are essentially never bound; the set_state
phase holds only modifiers); set NIXPILOT_KB_GATE_TYPE=1 to additionally
type a short inert string — do this only with the target screen in a safe
state and in view.

Environment:
    NIXPILOT_SMOKE_HOST   ssh destination (default the tinypilot KVM host)
    NIXPILOT_SMOKE_NODE   device binary (default nixpilot-keyboard-mcp)
    NIXPILOT_KB_GATE_TYPE 1 = include the type_text phase
"""

from __future__ import annotations

import json
import os
import sys
import time

from lib.mcp_session import Fail, ssh_session, step

TOOLS = ["press", "reset", "send_raw", "sequence", "set_state", "status", "type_text"]
RESOURCES = ["gadget://keyboard/layout", "gadget://keyboard/state"]

HOST = os.environ.get("NIXPILOT_SMOKE_HOST", "princet@tinypilot.home.trprince.com")
NODE_BIN = os.environ.get("NIXPILOT_SMOKE_NODE", "nixpilot-keyboard-mcp")
GATE_TYPE = os.environ.get("NIXPILOT_KB_GATE_TYPE", "") == "1"


def main() -> None:
    print(f"nixpilot-keyboard-mcp smoke gate against {HOST}")
    s = ssh_session("gate", HOST, NODE_BIN)
    info = s.initialize()
    step("initialize", info.get("name") == "nixpilot-keyboard-mcp", str(info))

    names = sorted(t["name"] for t in s.call("tools/list", {}, 20).get("tools", []))
    step("tools/list", names == TOOLS, str(names))
    uris = sorted(
        r["uri"] for r in s.call("resources/list", {}, 20).get("resources", [])
    )
    step("resources/list", uris == RESOURCES, str(uris))

    st = s.tool_json("status")
    step(
        "status reachable",
        st.get("open") is True
        and st.get("state", {}).get("keys") == []
        and "host_leds" in st,
        json.dumps(st),
    )
    step("udc bound", st.get("udc") == "fe980000.usb", str(st.get("udc")))

    # Inert-key injection: F13/F14 have no bindings on stock desktops and
    # game menus; a bare LSHIFT hold produces no keystrokes at all.
    pressed = s.tool_json("press", {"keys": ["F13"]})
    step(
        "press F13",
        pressed.get("pressed", {}).get("keys") == ["F13"],
        json.dumps(pressed),
    )
    pressed = s.tool_json("press", {"keys": ["F14"]})
    step(
        "press F14",
        pressed.get("pressed", {}).get("keys") == ["F14"],
        json.dumps(pressed),
    )

    state = s.tool_json("set_state", {"modifiers": ["LSHIFT"]}).get("state", {})
    step(
        "set_state sticky modifier",
        state.get("modifiers") == ["LSHIFT"] and state.get("keys") == [],
        json.dumps(state),
    )
    state = s.tool_json("reset").get("state", {})
    step(
        "reset idle",
        state.get("modifiers") == [] and state.get("keys") == [],
        json.dumps(state),
    )

    seq = s.tool_json(
        "sequence",
        {
            "steps": [
                {"delay_ms": 40},
                {"press": {"keys": ["F14"], "hold_ms": 60}},
            ]
        },
    )
    step(
        "sequence",
        len(seq.get("steps", [])) == 2 and seq.get("elapsed_ms", 10**9) < 30000,
        json.dumps(seq),
    )

    # Typing runs BEFORE any raw-protocol report: the raw section below
    # intentionally stays free of focus-affecting vectors (a literal alt+tab
    # report switches the target's focused window and the compositor eats
    # keystrokes during the transfer — observed first-hand; verify typed
    # text visually when NIXPILOT_KB_GATE_TYPE=1).
    if GATE_TYPE:
        typed = s.tool_json("type_text", {"text": "nixpilot-gate", "per_key_ms": 12})
        step("type_text", typed.get("chars") == 13, json.dumps(typed))
    else:
        print(
            "  skipped: type_text (set NIXPILOT_KB_GATE_TYPE=1 and confirm the target screen)"
        )

    for label, args in [
        (
            "reject untypeable",
            {"name": "type_text", "arguments": {"text": "caf\u00e9"}},
        ),
        (
            "reject 7 keys",
            {"name": "set_state", "arguments": {"keys": list("ABCDEFG")}},
        ),
        (
            "reject nonzero reserved",
            {"name": "send_raw", "arguments": {"hex": "0100010000000000"}},
        ),
        ("reject no keys", {"name": "press", "arguments": {"modifiers": ["LCTRL"]}}),
        ("reject unknown key", {"name": "press", "arguments": {"keys": ["NOTAKEY"]}}),
    ]:
        msg = s.call("tools/call", args, timeout=45)
        step(label, msg.get("isError") is True, str(msg)[:160])

    # Watchdog: separate lockfile+TTL; a held SHIFT must self-revert (a
    # modifier hold produces no keystrokes, so this is injection-safe).
    w = ssh_session(
        "watchdog",
        HOST,
        NODE_BIN,
        env={
            "NIXPILOT_WATCHDOG_TTL": "1",
            "NIXPILOT_LOCKFILE": "/tmp/nixpilot-kb-smoke-wd.lock",
        },
    )
    w.initialize()
    w.tool_json("set_state", {"modifiers": ["LSHIFT"]})
    time.sleep(2.2)
    after = w.tool_json("status")
    fired = any("watchdog expired" in line for line in w.err)
    step(
        "watchdog fired + reverted",
        fired and after.get("state", {}).get("modifiers") == [],
        json.dumps(after),
    )
    w.close()

    # Single-writer: the gate session holds the default lock; a second
    # session must reject at startup.
    two = ssh_session("second-writer", HOST, NODE_BIN)
    rc = two.close(timeout=12)
    step(
        "single-writer reject",
        rc != 0 and any("single-writer" in line for line in two.err),
        f"rc={rc} stderr={two.err[-2:]}",
    )

    # Raw-protocol parity: a modifier-only hold (LALT, no key) is inert —
    # the alt+tab raw vector from the workspace's hidg-smoke.sh deliberately
    # is NOT fired here: it switches the target's focused window.
    raw = s.tool_json("send_raw", {"hex": "0400000000000000"})
    step(
        "send_raw modifier-only",
        raw.get("state", {}).get("modifiers") == ["LALT"],
        json.dumps(raw),
    )
    state = s.tool_json("reset").get("state", {})
    step("post-raw idle", state.get("modifiers") == [], json.dumps(state))

    step("clean exit", s.close() == 0)
    print(
        "nixpilot-keyboard-mcp smoke gate: PASS (chords, modifier hold, watchdog, lock)"
    )


if __name__ == "__main__":
    try:
        main()
    except (Fail, TimeoutError, EOFError, AssertionError) as exc:
        print(f"nixpilot-keyboard-mcp smoke gate: FAIL — {exc}", file=sys.stderr)
        sys.exit(1)
