"""Minimal MCP client over stdio (JSON-RPC 2.0, one JSON message per line).

Used for REA and any other MCP server the user adds. Each server runs as a child
process for the life of the app session.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__, tooling


class MCPError(Exception):
    pass


class MCPServer:
    def __init__(self, name: str, command: list[str], env: dict | None = None, cwd: str | None = None):
        self.name, self.command, self.extra_env, self.cwd = name, command, env or {}, cwd
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._id = 0
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._stderr: list[str] = []

    # ---------- lifecycle ----------
    def start(self, timeout: float = 120):
        e = tooling.env()
        e.update(self.extra_env)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, env=e, cwd=self.cwd, creationflags=flags)
        except OSError as ex:
            raise MCPError(f"Couldn't start {self.name}: {ex}")
        from .engine import _KillOnClose
        if not hasattr(MCPServer, "_job"):
            MCPServer._job = _KillOnClose()
        MCPServer._job.add(self.proc)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "AFIF Coder", "version": __version__}}, timeout)
        self.notify("notifications/initialized")
        tools, cursor = [], None
        while True:
            r = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout)
            tools += r.get("tools", [])
            cursor = r.get("nextCursor")
            if not cursor:
                break
        self.tools = tools
        return self

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(3)
            except Exception:
                self.proc.kill()

    # ---------- protocol ----------
    def _send(self, msg: dict):
        if not self.alive():
            raise MCPError(f"{self.name} isn't running. {self.last_error()}")
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None):
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict, timeout: float = 600, cancelled=None) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            slot = {"event": threading.Event(), "result": None, "error": None}
            self._pending[rid] = slot
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        waited = 0.0
        while not slot["event"].wait(0.5):
            waited += 0.5
            if cancelled and cancelled():
                self.notify("notifications/cancelled", {"requestId": rid, "reason": "Stopped by the user"})
                self._pending.pop(rid, None)
                raise MCPError("Stopped.")
            if waited >= timeout:
                self._pending.pop(rid, None)
                raise MCPError(f"{self.name} didn't answer within {int(timeout)} seconds.")
            if not self.alive():
                raise MCPError(f"{self.name} stopped unexpectedly. {self.last_error()}")
        if slot["error"]:
            e = slot["error"]
            raise MCPError(f"{self.name}: {e.get('message', e)}")
        return slot["result"] or {}

    def _read(self):
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                slot = self._pending.pop(msg["id"], None)
                if slot:
                    slot["result"], slot["error"] = msg.get("result"), msg.get("error")
                    slot["event"].set()
            elif "id" in msg and "method" in msg:          # server -> client request
                if msg["method"] == "ping":
                    reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                elif msg["method"] == "roots/list":
                    reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": (
                        [{"uri": Path(self.cwd).resolve().as_uri(), "name": "project"}] if self.cwd else [])}}
                else:
                    reply = {"jsonrpc": "2.0", "id": msg["id"],
                             "error": {"code": -32601, "message": f"{msg['method']} not supported by AFIF Coder"}}
                try:
                    self._send(reply)
                except Exception:
                    pass
        for slot in list(self._pending.values()):           # process ended: release waiters
            slot["error"] = {"message": "server closed"}
            slot["event"].set()

    def _read_err(self):
        for raw in self.proc.stderr:
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())
            del self._stderr[:-40]

    def last_error(self) -> str:
        return " | ".join(l for l in self._stderr[-4:] if l.strip())

    # ---------- tools ----------
    def call(self, tool: str, args: dict, timeout: float = 1800, cancelled=None) -> tuple[str, list[str], bool]:
        """Returns (text, image data URLs, is_error)."""
        r = self.request("tools/call", {"name": tool, "arguments": args}, timeout, cancelled)
        texts, images = [], []
        for c in r.get("content", []):
            if c.get("type") == "text":
                texts.append(c.get("text", ""))
            elif c.get("type") == "image" and c.get("data"):
                images.append(f"data:{c.get('mimeType', 'image/png')};base64,{c['data']}")
            elif c.get("type") == "resource":
                res = c.get("resource", {})
                texts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
        if not texts and r.get("structuredContent") is not None:
            texts.append(json.dumps(r["structuredContent"], ensure_ascii=False, indent=1))
        return "\n".join(texts), images, bool(r.get("isError"))

    def schemas(self, prefix: str) -> list[dict]:
        out = []
        for t in self.tools:
            params = t.get("inputSchema") or {"type": "object", "properties": {}}
            params = {k: v for k, v in params.items() if k != "$schema"}
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            out.append({"type": "function", "function": {
                "name": f"{prefix}__{t['name']}"[:64],
                "description": (t.get("description") or t.get("title") or t["name"])[:1024],
                "parameters": params}})
        return out


class MCPManager:
    """Servers configured for the app; started on first use."""

    def __init__(self, settings: dict):
        self.s = settings
        self.running: dict[str, MCPServer] = {}

    def configured(self) -> dict[str, dict]:
        servers = {}
        rea = tooling.rea_launcher()
        if rea:
            servers["rea"] = {"command": rea, "env": {"REA_MCP_INPUT_SCHEMA_PROFILE": "compact"}, "builtin": True}
        for name, cfg in (self.s.get("mcp_servers") or {}).items():   # user-added servers
            if cfg.get("command"):
                servers[name] = cfg
        return servers

    def get(self, name: str, cwd: str | None = None) -> MCPServer:
        srv = self.running.get(name)
        if srv and srv.alive():
            return srv
        cfg = self.configured().get(name)
        if not cfg:
            raise MCPError(f"No MCP server named {name} is set up.")
        cmd = cfg["command"] if isinstance(cfg["command"], list) else [cfg["command"], *cfg.get("args", [])]
        srv = MCPServer(name, cmd, cfg.get("env"), cwd).start()
        self.running[name] = srv
        return srv

    def stop_all(self):
        for s in self.running.values():
            s.stop()
        self.running.clear()
