#!/usr/bin/env bash
# nixpilot-mcp protocol smoke gate. Drives the server's stdio transport with
# raw JSON-RPC frames over the deployed launch path (default:
# `ssh tinypilot nixpilot-mcp`, once `make test-tinypilot` in aedificium has
# installed the package). Requires python3 on the invoking machine.
#
# The press/set_state steps inject real input on the connected target: keep
# that screen in view. The gate only proves the gadget accepted the reports;
# target-side outcomes are verified visually.
#
# Environment:
#   NIXPILOT_SMOKE_HOST  ssh destination (default the tinypilot KVM host)
#   NIXPILOT_SMOKE_NODE  device binary (default nixpilot-mcp)
set -euo pipefail

HOST="${NIXPILOT_SMOKE_HOST:-princet@tinypilot.home.trprince.com}"
NODE_BIN="${NIXPILOT_SMOKE_NODE:-nixpilot-mcp}"

exec python3 - "$HOST" "$NODE_BIN" <<'DRIVER'
"""Raw JSON-RPC driver: no MCP SDK dependency, so the gate is client-agnostic."""
import json
import os
import select
import shlex
import subprocess
import sys
import threading
import time

HOST, NODE_BIN = sys.argv[1], sys.argv[2]

TOOLS = ["press", "reset", "send_raw", "sequence", "set_state", "status"]
RESOURCES = ["gadget://gamepad/layout", "gadget://gamepad/state"]
IDLE_HEX = "00007f7f7f7f0f00"
A_HEX = "01007f7f7f7f0f00"


class Fail(Exception):
    pass


class Session:
    """One MCP stdio session; newline-delimited JSON-RPC frames over ssh."""

    def __init__(self, name, env=None):
        self.name = name
        self.buf = b""
        self.err = []
        argv = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", HOST]
        if env:
            argv += ["env"] + [f"{k}={v}" for k, v in env.items()]
        argv += shlex.split(NODE_BIN)
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        threading.Thread(target=self._drain, daemon=True).start()
        self._nid = 0

    def _drain(self):
        for raw in iter(self.proc.stderr.readline, b""):
            self.err.append(raw.decode(errors="replace").rstrip())

    def send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _read_msg(self, timeout):
        end = time.monotonic() + timeout
        while b"\n" not in self.buf:
            remain = end - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"{self.name}: no frame within {timeout}s")
            ready, _, _ = select.select([self.proc.stdout], [], [], remain)
            if not ready:
                if self.proc.poll() is not None:
                    raise EOFError(f"{self.name}: stream closed; stderr: {self.err[-3:]}")
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError(f"{self.name}: stream closed; stderr: {self.err[-3:]}")
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line)

    def call(self, method, params=None, timeout=30):
        self._nid += 1
        rid = self._nid
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        end = time.monotonic() + timeout
        while True:
            msg = self._read_msg(max(0.5, end - time.monotonic()))
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise Fail(f"{self.name}: transport error for {method}: {msg['error']}")
                return msg["result"]
            self.notify_seen = msg  # ignored: server log/progress notifications

    def initialize(self):
        result = self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nixpilot-smoke", "version": "0"},
            },
            timeout=20,
        )
        self.notify("notifications/initialized")
        return result.get("serverInfo", {})

    def tool(self, name, arguments=None, timeout=45):
        result = self.call("tools/call", {"name": name, "arguments": arguments or {}}, timeout)
        if result.get("isError"):
            raise Fail(f"{self.name}: {name} tool error: {result.get('content')}")
        return result

    def tool_json(self, name, arguments=None, timeout=45):
        result = self.tool(name, arguments, timeout)
        if "structuredContent" in result:
            return result["structuredContent"]
        text = result.get("content", [{}])[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self, timeout=5):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return -1


def step(name, condition, detail=""):
    print(f"  pass: {name}" if condition else f"  FAIL: {name} {detail}", flush=True)
    if not condition:
        raise Fail(f"{name} {detail}")


def main():
    print(f"nixpilot-mcp smoke gate against {HOST}")
    s = Session("gate")
    info = s.initialize()
    step("initialize", info.get("name") == "nixpilot-mcp", str(info))

    names = sorted(t["name"] for t in s.call("tools/list", {}, 20).get("tools", []))
    step("tools/list", names == TOOLS, str(names))
    uris = sorted(r["uri"] for r in
                  s.call("resources/list", {}, 20).get("resources", []))
    step("resources/list", uris == RESOURCES, str(uris))

    st = s.tool_json("status")
    step("status reachable", st.get("open") is True and st.get("state", {}).get("buttons") == [],
         json.dumps(st))
    step("udc bound", st.get("udc") == "fe980000.usb", str(st.get("udc")))

    sent = s.tool_json("send_raw", {"hex": A_HEX})
    step("send_raw A", sent.get("state", {}).get("buttons") == ["A"], json.dumps(sent))

    state = s.tool_json("set_state", {
        "buttons": ["B"], "left": {"x": 1.0, "y": 0.0}, "hat": "E",
    }).get("state", {})
    step("set_state full", state.get("buttons") == ["B"]
         and state.get("left", {}).get("x") == 1.0 and state.get("hat") == "E",
         json.dumps(state))

    state = s.tool_json("set_state", {"hat": "NEUTRAL"}).get("state", {})
    step("partial merge", state.get("buttons") == ["B"]
         and state.get("hat") == "NEUTRAL"
         and state.get("left", {}).get("x") == 1.0,
         json.dumps(state))

    state = s.tool_json("send_raw", {"hex": "030000ff7f7f0600"}).get("state", {})
    step("send_raw combo", set(state.get("buttons", [])) == {"A", "B"}
         and state.get("hat") == "W", json.dumps(state))

    state = s.tool_json("reset").get("state", {})
    step("reset idle", state.get("buttons") == [] and state.get("hat") == "NEUTRAL", json.dumps(state))

    seq = s.tool_json("sequence", {"steps": [
        {"press": {"buttons": ["A"], "hold_ms": 120}},
        {"delay_ms": 80},
        {"press": {"buttons": ["B"], "hold_ms": 120}},
    ]})
    step("sequence", len(seq.get("steps", [])) == 3 and seq.get("elapsed_ms", 10 ** 9) < 30000,
         json.dumps(seq))

    msg = s.call("tools/call", {"name": "set_state", "arguments": {"left": {"x": 2.0, "y": 0.0}}})
    step("reject out-of-range axis", msg.get("isError") is True, str(msg)[:200])
    msg = s.call("tools/call", {"name": "send_raw", "arguments": {"hex": "01007f7f7f7f0f01"}})
    step("reject nonzero padding", msg.get("isError") is True, str(msg)[:200])
    msg = s.call("tools/call", {"name": "press", "arguments": {"buttons": [], "hold_ms": 100}})
    step("reject empty press", msg.get("isError") is True, str(msg)[:200])

    # Watchdog: separate lockfile+TTL; a held state must self-revert to idle.
    w = Session("watchdog", env={
        "NIXPILOT_WATCHDOG_TTL": "1",
        "NIXPILOT_LOCKFILE": "/tmp/nixpilot-smoke-wd.lock",
    })
    w.initialize()
    held = w.tool_json("set_state", {"buttons": ["A"]})
    step("watchdog armed", held.get("state", {}).get("buttons") == ["A"], json.dumps(held))
    time.sleep(2.2)
    after = w.tool_json("status")
    fired = any("watchdog expired" in line for line in w.err)
    step("watchdog fired + reverted",
         fired and after.get("state", {}).get("buttons") == [], json.dumps(after))
    w.close()

    # Single-writer: the gate session holds the default lock; a second
    # session on the same lockfile must reject at startup, not interleave.
    two = Session("second-writer")
    rc = two.close(timeout=12)
    step("single-writer reject", rc != 0 and
         any("single-writer" in line for line in two.err),
         f"rc={rc} stderr={two.err[-2:]}")

    step("clean exit", s.close() == 0)
    print(f"nixpilot-mcp smoke gate: PASS ({len(TOOLS)} tools, watchdog verified)")


try:
    main()
except (Fail, TimeoutError, EOFError, AssertionError) as exc:
    print(f"nixpilot-mcp smoke gate: FAIL — {exc}", file=sys.stderr)
    sys.exit(1)
DRIVER
