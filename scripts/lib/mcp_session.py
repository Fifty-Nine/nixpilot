"""Shared MCP stdio transport + assertion helpers for the nixpilot smoke
gates. Client-agnostic: raw newline-delimited JSON-RPC frames, no MCP SDK
dependency. Two spawn styles:

- ssh_session(): the deployed launch path (`ssh host nixpilot-mcp`), with
  per-session env via an `env K=V` prefix to the remote command.
- local_session(): a direct argv (dev loop against the source tree), env
  exported through the subprocess environment.

stdout is the protocol stream; each session drains stderr for diagnostics.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import subprocess
import threading
import time

PROTOCOL_VERSION = "2025-06-18"


class Fail(Exception):
    pass


def step(name: str, condition: bool, detail: str = "") -> None:
    print(f"  pass: {name}" if condition else f"  FAIL: {name} {detail}", flush=True)
    if not condition:
        raise Fail(f"{name} {detail}")


class Session:
    """One MCP stdio session."""

    def __init__(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str] | None = None,
        env_mode: str = "remote-env",
    ):
        """`env_mode="remote-env"` inserts `env K=V...` after argv[0] (the ssh
        launcher form); "subprocess" exports env to the child process."""
        self.name = name
        self.buf = b""
        self.err: list[str] = []
        if env and env_mode == "remote-env":
            argv = argv[:1] + ["env", *[f"{k}={v}" for k, v in env.items()]] + argv[1:]
        popen_env = {**os.environ, **env} if env and env_mode == "subprocess" else None
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=popen_env,
        )
        threading.Thread(target=self._drain, daemon=True).start()
        self._nid = 0

    def _drain(self) -> None:
        for raw in iter(self.proc.stderr.readline, b""):
            self.err.append(raw.decode(errors="replace").rstrip())

    def send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _read_msg(self, timeout: float) -> dict:
        end = time.monotonic() + timeout
        while b"\n" not in self.buf:
            remain = end - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"{self.name}: no frame within {timeout}s")
            ready, _, _ = select.select([self.proc.stdout], [], [], remain)
            if not ready:
                if self.proc.poll() is not None:
                    raise EOFError(
                        f"{self.name}: stream closed; stderr: {self.err[-3:]}"
                    )
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError(f"{self.name}: stream closed; stderr: {self.err[-3:]}")
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line)

    def call(self, method: str, params: dict | None = None, timeout: int = 30) -> dict:
        self._nid += 1
        rid = self._nid
        self.send(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        )
        end = time.monotonic() + timeout
        while True:
            msg = self._read_msg(max(0.5, end - time.monotonic()))
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise Fail(
                        f"{self.name}: transport error for {method}: {msg['error']}"
                    )
                return msg["result"]
            # Non-matching frames are server log/progress notifications; ignore.

    def initialize(self) -> dict:
        result = self.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "nixpilot-smoke", "version": "0"},
            },
            timeout=20,
        )
        self.notify("notifications/initialized")
        return result.get("serverInfo", {})

    def tool(self, name: str, arguments: dict | None = None, timeout: int = 45) -> dict:
        result = self.call(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout
        )
        if result.get("isError"):
            raise Fail(f"{self.name}: {name} tool error: {result.get('content')}")
        return result

    def tool_json(
        self, name: str, arguments: dict | None = None, timeout: int = 45
    ) -> dict:
        result = self.tool(name, arguments, timeout)
        if "structuredContent" in result:
            return result["structuredContent"]
        text = result.get("content", [{}])[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self, timeout: int = 5) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return -1


def ssh_session(
    name: str, host: str, node_cmd: str, env: dict[str, str] | None = None
) -> Session:
    """A session over the deployed SSH launch path."""
    return Session(
        name,
        [
            "ssh",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "BatchMode=yes",
            host,
            *shlex.split(node_cmd),
        ],
        env=env,
        env_mode="remote-env",
    )


def local_session(
    name: str, argv: list[str], env: dict[str, str] | None = None
) -> Session:
    """A session against a local source tree (dev loop)."""
    return Session(name, argv, env=env, env_mode="subprocess")
