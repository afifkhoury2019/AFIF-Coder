"""The agent loop: talks to the built-in AFIF Engine and runs tools (background thread)."""
from __future__ import annotations

import json
import platform
import threading
from datetime import date
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

from .tools import Toolbox, ToolError

SYSTEM_PROMPT = """You are AFIF Coder, a coding assistant made by AFIF-TECH, running entirely on the user's own computer.
You work inside one project folder: {root}
Operating system: {os}. Today is {today}.

How to work:
- Use the tools to look before you change anything: list_dir, find_files, search_text, read_file.
- Make changes with edit_file (small, exact replacements) or write_file (new files or full rewrites).
- Use run_command to run builds, tests or scripts. Commands run in the project folder.
- Keep going with tools until the task is done, then reply with a short summary of what changed.
- If a tool returns an error, read it, fix your call, and try again.
- Never invent file contents you have not read. Paths are relative to the project folder.
- Answer in the language the user writes in.
"""


class EngineError(Exception):
    pass


class Agent(QObject):
    text_delta = Signal(str)
    turn_started = Signal()
    tool_started = Signal(str, str, str)        # call_id, tool name, summary
    tool_finished = Signal(str, bool, str)      # call_id, ok, output
    approval_needed = Signal(str, str, dict)    # call_id, tool name, args
    status = Signal(str)
    finished = Signal(str)                      # "" on success, else message

    def __init__(self, settings: dict, engine_url):
        super().__init__()
        self.s = settings
        self.engine_url = engine_url            # callable -> base URL of the running engine
        self.messages: list[dict] = []
        self.toolbox: Toolbox | None = None
        self._stop = threading.Event()
        self._approval = threading.Event()
        self._approved = False
        self.auto_approve = False
        self._resp = None

    def reset(self, root: str):
        self.toolbox = Toolbox(root)
        prompt = SYSTEM_PROMPT.format(root=root, os=f"{platform.system()} {platform.release()}",
                                      today=date.today().isoformat())
        notes = Path(root) / "CODER.md"
        if notes.is_file():
            prompt += "\nProject instructions from CODER.md:\n" + notes.read_text(encoding="utf-8", errors="replace")[:8000]
        self.messages = [{"role": "system", "content": prompt}]

    def stop(self):
        self._stop.set()
        self._approved = False
        self._approval.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass

    def answer_approval(self, ok: bool, always: bool = False):
        if always:
            self.auto_approve = True
        self._approved = ok
        self._approval.set()

    # ---------------------------------------------------------------- loop
    def run(self, user_text: str):
        self._stop.clear()
        err = ""
        try:
            self.messages.append({"role": "user", "content": user_text})
            for _ in range(int(self.s.get("max_steps", 40))):
                if self._stop.is_set():
                    break
                msg = self._chat_once()
                if msg is None:
                    break
                self.messages.append(msg)
                calls = msg.get("tool_calls") or []
                if not calls:
                    break
                for call in calls:
                    if self._stop.is_set():
                        break
                    self._do_tool(call)
            else:
                err = "Stopped after the maximum number of steps. Send a message to continue."
        except requests.ConnectionError:
            err = "The AFIF Engine isn't running. Pick a model at the top, then try again."
        except EngineError as e:
            err = str(e)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if self._stop.is_set() and not err:
            err = "Stopped."
        self.finished.emit(err)

    def _chat_once(self) -> dict | None:
        body = {
            "model": "afif-local",
            "messages": self.messages,
            "tools": Toolbox.schemas(),
            "stream": True,
            "temperature": float(self.s.get("temperature", 0.2)),
        }
        self.turn_started.emit()
        self.status.emit("Thinking…")
        content: list[str] = []
        calls: dict[int, dict] = {}
        self._resp = requests.post(f"{self.engine_url()}/v1/chat/completions", json=body,
                                   stream=True, timeout=(5, 900))
        try:
            if self._resp.status_code != 200:
                raise EngineError(_explain(self._resp))
            for raw in self._resp.iter_lines():
                if self._stop.is_set():
                    return None
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise EngineError(str(chunk["error"].get("message", chunk["error"])))
                for ch in chunk.get("choices", []):
                    d = ch.get("delta") or {}
                    if d.get("content"):
                        content.append(d["content"])
                        self.text_delta.emit(d["content"])
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)),
                                                {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        f = tc.get("function") or {}
                        if f.get("name"):
                            slot["name"] += f["name"]
                        if f.get("arguments"):
                            slot["arguments"] += f["arguments"]
        except (requests.exceptions.ChunkedEncodingError, AttributeError):
            if self._stop.is_set():
                return None
            raise
        finally:
            if self._resp is not None:
                self._resp.close()
            self._resp = None
        msg = {"role": "assistant", "content": "".join(content)}
        if calls:
            n = len(self.messages)
            msg["tool_calls"] = [
                {"id": c["id"] or f"call_{n}_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for i, c in sorted(calls.items())]
        return msg

    def _do_tool(self, call: dict):
        fn = call["function"]
        name, call_id = fn["name"], call["id"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            self.tool_started.emit(call_id, name, "")
            out = "Error: arguments were not valid JSON. Send them again as a JSON object."
            self.tool_finished.emit(call_id, False, out)
            self._reply(call_id, out)
            return
        self.tool_started.emit(call_id, name, self.toolbox.summary(name, args))

        if name in Toolbox.NEEDS_APPROVAL and not self.auto_approve:
            self.status.emit("Waiting for your approval")
            self._approval.clear()
            self.approval_needed.emit(call_id, name, args)
            self._approval.wait()
            if not self._approved:
                self.tool_finished.emit(call_id, False, "Declined")
                self._reply(call_id, "The user declined this action. Ask what they would like instead.")
                return

        self.status.emit(f"Running {name}")
        try:
            out, ok = self.toolbox.run(name, args), True
        except ToolError as e:
            out, ok = f"Error: {e}", False
        except Exception as e:
            out, ok = f"Error: {type(e).__name__}: {e}", False
        self.tool_finished.emit(call_id, ok, out)
        self._reply(call_id, out)

    def _reply(self, call_id: str, content: str):
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})


def _explain(resp) -> str:
    try:
        j = resp.json()
        msg = j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else j.get("error")
        msg = msg or resp.text
    except ValueError:
        msg = resp.text
    if "context" in msg.lower() and ("exceed" in msg.lower() or "too long" in msg.lower()):
        return ("The conversation no longer fits in the model's memory. Start a new chat, "
                "or raise the context length in Settings.")
    if "tool" in msg.lower() and "support" in msg.lower():
        return "This model can't use tools, so it can't read or edit files. Pick a coding model instead."
    return f"The engine returned an error: {msg}"
