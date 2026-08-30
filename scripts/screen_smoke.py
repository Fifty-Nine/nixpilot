"""nixpilot-screen-mcp protocol smoke gate. Drives the screen profile's stdio
transport over the deployed launch path (default:
`ssh tinypilot nixpilot-screen-mcp`).

Read-only profile: no target-side input; the gate needs a live video source
on the /snapshot endpoint for the frame assertions (the error path always
runs and must degrade to a tool error).

Environment:
    NIXPILOT_SMOKE_HOST  ssh destination (default the tinypilot KVM host)
    NIXPILOT_SMOKE_NODE  device binary (default nixpilot-screen-mcp)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

from lib.mcp_session import Fail, ssh_session, step

TOOLS = ["screenshot"]
RESOURCES = ["screen://state"]

HOST = os.environ.get("NIXPILOT_SMOKE_HOST", "princet@tinypilot.home.trprince.com")
NODE_BIN = os.environ.get("NIXPILOT_SMOKE_NODE", "nixpilot-screen-mcp")


def main() -> None:
    print(f"nixpilot-screen-mcp smoke gate against {HOST}")
    s = ssh_session("gate", HOST, NODE_BIN)
    info = s.initialize()
    step("initialize", info.get("name") == "nixpilot-screen-mcp", str(info))

    names = sorted(t["name"] for t in s.call("tools/list", {}, 20).get("tools", []))
    step("tools/list", names == TOOLS, str(names))
    uris = sorted(
        r["uri"] for r in s.call("resources/list", {}, 20).get("resources", [])
    )
    step("resources/list", uris == RESOURCES, str(uris))

    state = json.loads(
        s.call("resources/read", {"uri": "screen://state"})["contents"][0]["text"]
    )
    step("resource state", state.get("url", "").startswith("http://"), str(state)[:200])

    shot = s.tool("screenshot", timeout=60)
    content = shot["content"]
    step(
        "screenshot blocks",
        len(content) == 2
        and content[0].get("type") == "image"
        and content[0].get("mimeType") == "image/jpeg"
        and content[1].get("type") == "text",
        str([c.get("type") for c in content]),
    )
    jpeg = base64.b64decode(content[0]["data"])
    step(
        "jpeg framing",
        jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9") and len(jpeg) > 0,
        f"{len(jpeg)} bytes",
    )
    meta = json.loads(content[1]["text"])
    step(
        "metadata integrity",
        meta.get("bytes") == len(jpeg)
        and meta.get("sha256") == hashlib.sha256(jpeg).hexdigest(),
        json.dumps(meta),
    )
    if state.get("width") is not None:
        step(
            "dims match capture source",
            meta.get("width") == state.get("width")
            and meta.get("height") == state.get("height"),
            json.dumps(meta),
        )
    else:
        step("dims match capture source", True, "(source offline; skipped)")

    # Read-only concurrency: a second simultaneous session must work (contrast
    # with the gamepad profile's single-writer contract).
    second = ssh_session("second-reader", HOST, NODE_BIN)
    second.initialize()
    second_shot = second.tool("screenshot", timeout=60)
    step("concurrent session", len(second_shot["content"]) == 2, "")
    second.close()

    # Error path: a dead upstream must surface as a tool error, not a crash.
    bad = ssh_session(
        "errpath",
        HOST,
        NODE_BIN,
        env={"NIXPILOT_USTREAMER_URL": "http://127.0.0.1:1"},
    )
    bad.initialize()
    msg = bad.call("tools/call", {"name": "screenshot", "arguments": {}}, timeout=45)
    text = str(msg.get("content"))
    step(
        "error path isError",
        msg.get("isError") is True and "unreachable" in text,
        text[:200],
    )
    bad.close()

    step("clean exit", s.close() == 0)
    print(
        "nixpilot-screen-mcp smoke gate: PASS (screenshot, resource, concurrency, error path)"
    )


if __name__ == "__main__":
    try:
        main()
    except (Fail, TimeoutError, EOFError, AssertionError) as exc:
        print(f"nixpilot-screen-mcp smoke gate: FAIL — {exc}", file=sys.stderr)
        sys.exit(1)
