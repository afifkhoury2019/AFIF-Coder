"""The agent loop: streams turns from the selected model backend and runs tools (background thread)."""
from __future__ import annotations

import json
import platform
import re
import threading
from datetime import date
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

from .backends import Backend, BackendError
from .tools import Toolbox, ToolError

SYSTEM_PROMPT = """You are AFIF Coder, a coding assistant made by AFIF-TECH. Your tools run on the user's own computer.
You work inside one project folder: {root}
Operating system: {os}. Today is {today}.

How to work:
- Use the tools to look before you change anything: list_dir, find_files, search_text, read_file.
- Make changes with edit_file (small, exact replacements) or write_file (new files or full rewrites).
- Use run_command to run builds, tests or scripts. Commands run in the project folder.
- Keep going with tools until the task is done, then reply with a short summary of what changed.
- If a tool returns an error, read it, fix your call, and try again.
- Never invent file contents you have not read. Paths are relative to the project folder.
- You can only change files or run commands by calling a tool. Never say you edited, created,
  ran or fixed something unless you called the tool and saw its result in this conversation.
- Files the user attaches appear in their message. Large ones are also saved in the project's
  .afif-attachments folder; read them there with read_file.
- Answer in the language the user writes in.
"""

NUDGE = ("You described a change but did not call any tool, so nothing changed on disk. "
         "Call the needed tool now (for example read_file, then edit_file).")
CLAIM = re.compile(r"\b(i|i've|i have)\s+(used|edited|updated|fixed|changed|created|wrote|written|"
                   r"modified|replaced|ran|run)\b|\b(edit_file|write_file|run_command)\b", re.I)
TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


TOOL_NAMES = {t["function"]["name"] for t in Toolbox.schemas()}
FENCE = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.S)


def extract_text_calls(text: str) -> tuple[list[tuple[str, dict]], str]:
    """Find tool calls a model wrote as text: <tool_call>{..}</tool_call>, ```json {..}```, or bare JSON.

    Returns the calls and the text with those calls removed.
    """
    dec = json.JSONDecoder()
    calls, spans = [], []
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("name") in TOOL_NAMES:
            args = obj.get("arguments", obj.get("parameters", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            if isinstance(args, dict):
                calls.append((obj["name"], args))
                spans.append((i, end))
        i = end if end > i else i + 1
    if not calls:
        return [], text
    for a, b in reversed(spans):
        text = text[:a] + text[b:]
    text = TEXT_CALL.sub("", text)
    text = re.sub(r"</?tool_call>", "", text)
    text = FENCE.sub(lambda m: "" if not m.group(1).strip() else m.group(0), text)
    return calls, text.strip()


EngineError = BackendError    # older name


class Agent(QObject):
    text_delta = Signal(str)
    turn_started = Signal()
    tool_started = Signal(str, str, str)        # call_id, tool name, summary
    tool_finished = Signal(str, bool, str)      # call_id, ok, output
    approval_needed = Signal(str, str, dict)    # call_id, tool name, args
    status = Signal(str)
    usage = Signal(int, int)                    # tokens in, tokens out for the last message (cloud)
    finished = Signal(str)                      # "" on success, else message

    def __init__(self, settings: dict, get_backend):
        super().__init__()
        self.s = settings
        self.get_backend = get_backend          # callable -> Backend for the selected model
        self._backend: Backend | None = None
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
        if self._backend is not None:
            self._backend.close()

    def answer_approval(self, ok: bool, always: bool = False):
        if always:
            self.auto_approve = True
        self._approved = ok
        self._approval.set()

    # ---------------------------------------------------------------- loop
    def run(self, user_content):
        """user_content: plain text, or a list of OpenAI-style parts when files are attached."""
        self._stop.clear()
        err = ""
        self._tin = self._tout = 0
        try:
            self._backend = self.get_backend()
            self.messages.append({"role": "user", "content": user_content})
            nudged, used_tools = False, False
            for _ in range(int(self.s.get("max_steps", 40))):
                if self._stop.is_set():
                    break
                msg = self._chat_once()
                if msg is None:
                    break
                self.messages.append(msg)
                calls = msg.get("tool_calls") or []
                if not calls:
                    # weak models sometimes claim an action without calling the tool
                    if not nudged and not used_tools and CLAIM.search(msg.get("content") or ""):
                        nudged = True
                        self.messages.append({"role": "user", "content": NUDGE})
                        continue
                    break
                used_tools = True
                for call in calls:
                    if self._stop.is_set():
                        break
                    self._do_tool(call)
            else:
                err = "Stopped after the maximum number of steps. Send a message to continue."
        except requests.ConnectionError:
            err = "Lost the connection to the model. Check the engine or your internet, then try again."
        except BackendError as e:
            err = str(e)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if self._stop.is_set() and not err:
            err = "Stopped."
        if self._tin or self._tout:
            self.usage.emit(self._tin, self._tout)
        self.finished.emit(err)

    def _chat_once(self) -> dict | None:
        self.turn_started.emit()
        self.status.emit("Thinking…")
        turn = self._backend.stream(self.messages, Toolbox.schemas(), float(self.s.get("temperature", 0.2)),
                                    self.text_delta.emit, self._stop.is_set)
        if turn is None:
            return None
        self._tin += turn.tokens_in
        self._tout += turn.tokens_out
        msg = turn.msg
        if not msg.get("tool_calls"):            # model wrote the call as text instead
            found, text = extract_text_calls(msg.get("content") or "")
            if found:
                msg["content"] = text
                msg["tool_calls"] = [{"id": "", "type": "function",
                                      "function": {"name": n, "arguments": json.dumps(a)}} for n, a in found]
                msg.pop("_anthropic", None)
        n = len(self.messages)
        for i, c in enumerate(msg.get("tool_calls") or []):
            if not c.get("id"):
                c["id"] = f"call_{n}_{i}"
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

