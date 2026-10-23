"""The agent loop: streams turns from the selected model backend and runs tools (background thread)."""
from __future__ import annotations

import json
import platform
import re
import threading
import time
from datetime import date
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

from . import skills as skillmod
from .project import Project
from .backends import Backend, BackendError
from .mcp import MCPError, MCPManager
from .tools import Ctx, Toolbox, ToolError, ToolResult, drive_connected

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
- Bundled command-line tools are on PATH for run_command: git, gh (GitHub CLI), node/npm/npx, python/pip,
  java, rclone. {connections}
- When a task matches a skill below, call use_skill first and follow it. Skills shown in full below are
  already loaded. When you solve a new pitfall or the user corrects you, save it with record_lesson.
- Keep the project's shared notes current with update_progress (status, decisions, repo, builds, next
  steps). Another model may continue this work and relies on them.
- Files the user attaches appear in their message. Large ones are also saved in the project's
  .afif-attachments folder; read them there with read_file.
- Answer in the language the user writes in.
"""

SYSTEM_PROMPT_LEAN = """You are AFIF Coder (AFIF-TECH), a coding assistant. Project folder: {root}. {os}. Today {today}.
Look before changing: list_dir, find_files, search_text, read_file. Change files with edit_file or write_file.
run_command runs commands in the project (git, gh, node, python, java, rclone are available). {connections}
Only say you changed or ran something after calling the tool. Keep going until done, then summarise briefly.
Keep shared notes current with update_progress. Load a matching skill with use_skill. Reply in the user's language.
"""

NUDGE = ("You described a change but did not call any tool, so nothing changed on disk. "
         "Call the needed tool now (for example read_file, then edit_file).")
CLAIM = re.compile(r"\b(i|i've|i have)\s+(used|edited|updated|fixed|changed|created|wrote|written|"
                   r"modified|replaced|ran|run)\b|\b(edit_file|write_file|run_command)\b", re.I)
TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


TOOL_NAMES = {t["function"]["name"] for t in Toolbox.schemas(drive=True)}
READ_ONLY_MCP = re.compile(r"^(inspect|search|list|trace|get|read|describe|compare|find|explain|summari[sz]e|query|"
                           r"analy[sz]e|check|show|diagnose|doctor|open|status|lookup|resolve)", re.I)
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


def _sig(call: dict) -> str:
    f = call.get("function", {})
    try:
        args = json.dumps(json.loads(f.get("arguments") or "{}"), sort_keys=True)
    except ValueError:
        args = f.get("arguments", "")
    return f"{f.get('name')}:{args}"


class Agent(QObject):
    text_delta = Signal(str)
    turn_started = Signal()
    tool_started = Signal(str, str, str)        # call_id, tool name, summary
    tool_finished = Signal(str, bool, str)      # call_id, ok, output
    tool_progress = Signal(str, str)            # call_id, live output text
    skill_loaded = Signal(str, str)             # skill name, how ("matched" | "pinned" | "requested")
    approval_needed = Signal(str, str, dict)    # call_id, tool name, args
    status = Signal(str)
    usage = Signal(int, int)                    # tokens in, tokens out for the last message (cloud)
    thinking_delta = Signal(str)                # live reasoning text
    timing = Signal(float, float)               # seconds to first output, tokens per second
    progress = Signal(int, int)                 # local engine: prompt tokens read, total
    finished = Signal(str)                      # "" on success, else message

    def __init__(self, settings: dict, get_backend):
        super().__init__()
        self.s = settings
        self.get_backend = get_backend          # callable -> Backend for the selected model
        self._backend: Backend | None = None
        self.mcp = MCPManager(settings)
        self.active_mcp: set[str] = set()
        self.skills: dict = {}
        self.loaded: list[str] = []
        self.project: Project | None = None
        self._lean = False
        self.messages: list[dict] = []
        self.toolbox: Toolbox | None = None
        self._stop = threading.Event()
        self._approval = threading.Event()
        self._approved = False
        self.auto_approve = False
        self._resp = None

    def reset(self, root: str):
        self.skills = skillmod.discover()
        self.toolbox = Toolbox(root, self.skills, self.s)
        self.project = Project(root)
        self.active_mcp = set()
        self.loaded = []
        self.messages = [{"role": "system", "content": self.system_prompt()}]

    def system_prompt(self, lean: bool | None = None) -> str:
        lean = self._lean if lean is None else lean
        root = str(self.toolbox.root)
        conn = ["GitHub is connected (gh and git push work)." if self.s.get("github_user")
                else "GitHub isn't connected yet; if you need it, ask the user to connect it in Connections.",
                "Google Drive is connected (drive_* tools)." if drive_connected() else "Google Drive isn't connected."]
        prompt = (SYSTEM_PROMPT_LEAN if lean else SYSTEM_PROMPT).format(
            root=root, os=f"{platform.system()} {platform.release()}", today=date.today().isoformat(),
            connections=" ".join(conn))
        notes = Path(root) / "CODER.md"
        if notes.is_file():
            prompt += "\nProject instructions from CODER.md:\n" + notes.read_text(encoding="utf-8", errors="replace")[:30000]
        prog = self.project.progress() if self.project else ""
        prompt += ("\n\nShared progress notes for this project (.afif/PROGRESS.md), written by whoever worked on it "
                   "before. Continue from them:\n" + prog[:12000]) if prog.strip() else \
            "\n\nThis project has no progress notes yet. Once you understand the task, create them with update_progress."
        pinned = [n for n in (self.project.pinned() if self.project else []) if n in self.skills]
        for n in pinned:
            if lean:
                prompt += f"\nPinned skill for this project: {n}. Call use_skill(\"{n}\") before starting work it covers."
            else:
                prompt += f"\n\n<skill name=\"{n}\" pinned=\"true\">\n{self.skills[n].full_text()}\n</skill>"
        others = {k: v for k, v in self.skills.items() if k not in pinned}
        prompt += skillmod.catalog_text(others, short=lean)
        return prompt

    def warm_up(self, backend):
        """Local engine: read the instructions and tool list ahead of time (runs in a thread)."""
        if not getattr(backend, "local", False) or self.toolbox is None:
            return
        if not self._lean:
            self._lean = True
            self.messages[0]["content"] = self.system_prompt(True)
        backend.speed = self.s.get("speed", "balanced")
        backend.warm_up([self.messages[0], {"role": "user", "content": "Hi"}], self._schemas())

    def resume(self, data: dict) -> list:
        """Load a saved chat for this project; returns its messages for the UI to redraw."""
        self.messages = [{"role": "system", "content": self.system_prompt()}] + list(data.get("messages", []))
        self.loaded = [n for n in data.get("loaded_skills", []) if n in self.skills]
        return self.messages[1:]

    def save(self):
        if self.project:
            try:
                self.project.save_chat(self.messages, self.s.get("model", ""), self.loaded)
            except OSError:
                pass

    def new_chat(self):
        if self.project:
            self.project.archive_chat()
        self.reset(str(self.toolbox.root))

    def _skill_pointer(self, name: str) -> str:
        return (f"[AFIF Coder: the skill \"{name}\" matches this request. If you are going to do that work, "
                f"call use_skill(\"{name}\") first and follow it. For a simple question, just answer.]")

    def _shrink_skill_notes(self):
        """Local models re-read the whole chat on a slow CPU: replace full skill texts loaded earlier
        with a one-line pointer (they can load the skill again with use_skill when they need it)."""
        for m in self.messages[1:]:
            c = m.get("content")
            if m["role"] == "user" and isinstance(c, str) and c.startswith("[AFIF Coder loaded the skill") and len(c) > 600:
                name = c.split('"')[1] if '"' in c else "skill"
                m["content"] = self._skill_pointer(name)

    def _auto_skills(self, text: str):
        """Load matching skills for the model, so even models that never call use_skill follow them."""
        pinned = self.project.pinned() if self.project else []
        hits = [sk for n, sk in self.skills.items()
                if n not in self.loaded and n not in pinned and sk.matches(text)]
        for sk in hits[:2]:
            self.loaded.append(sk.name)
            if self._lean:
                # A local model on a CPU would spend minutes reading a long skill before answering
                # even "hi". Point at it instead; it loads the skill with use_skill when it starts the work.
                self.messages.append({"role": "user", "content": self._skill_pointer(sk.name)})
                self.skill_loaded.emit(sk.name, "suggested")
                continue
            note = (f"[AFIF Coder loaded the skill \"{sk.name}\" because it matches this request. "
                    f"Follow it for this task.]\n<skill name=\"{sk.name}\">\n{sk.full_text()}\n</skill>")
            if sk.mcp:
                note += self._connect_mcp(sk.mcp)
            self.messages.append({"role": "user", "content": note})
            self.skill_loaded.emit(sk.name, "matched")

    def stop(self):
        self._stop.set()
        self._approved = False
        self._approval.set()
        if self._backend is not None:
            self._backend.abort()               # never blocks the window

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
            self._backend.speed = self.s.get("speed", "balanced")
            self._backend.on_thinking = self._on_think
            self._backend.on_progress = lambda done, total: self.progress.emit(done, total)
            lean = bool(getattr(self._backend, "local", False))
            if lean != self._lean:                    # small local models get a short prompt: much faster to read
                self._lean = lean
                self.messages[0]["content"] = self.system_prompt(lean)
            if lean:
                self._shrink_skill_notes()
            self._t0, self._first = time.time(), 0.0
            self._tps = 0.0
            text = user_content if isinstance(user_content, str) else " ".join(
                p.get("text", "") for p in user_content if p.get("type") == "text")
            self._auto_skills(text)
            self.messages.append({"role": "user", "content": user_content})
            nudged, used_tools = False, False
            self._seen: dict[str, list] = {}         # call signature -> [times, last_ok, last_output]
            last_batch, repeats = None, 0
            for _ in range(int(self.s.get("max_steps", 40))):
                if self._stop.is_set():
                    break
                self._fit_context()
                try:
                    msg = self._chat_once()
                except BackendError as e:
                    if "no longer fits" not in str(e) or not getattr(self._backend, "context_tokens", 0):
                        raise
                    self._fit_context(0.45, keep=1)       # trim harder and try once more
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
                batch = [_sig(c) for c in calls]
                repeats = repeats + 1 if batch == last_batch else 0
                last_batch = batch
                if repeats >= 2:
                    err = ("The model kept repeating the same steps, so it was stopped. Try rephrasing the "
                           "request, or pick a stronger model.")
                    for c in calls:                      # keep the history valid for the next message
                        self._reply(c["id"], "Not run: repeated step.")
                    break
                skipped = calls[3:] if msg.get("_from_text") else []   # text-style batches: a few steps at a time
                for call in calls[:len(calls) - len(skipped)]:
                    if self._stop.is_set():
                        break
                    self._do_tool(call)
                for c in skipped:
                    self._reply(c["id"], "Not run yet: do one step at a time and check each result.")
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
        if getattr(self, "_first", 0):
            self.timing.emit(self._first, getattr(self, "_tps", 0.0))
        self.save()
        self.finished.emit(err)

    def _chat_once(self) -> dict | None:
        self.turn_started.emit()
        self.status.emit("Reading your message…" if self._lean else "Waiting for the model…")
        turn = self._backend.stream(self.messages, self._schemas(), float(self.s.get("temperature", 0.2)),
                                    self._on_text, self._stop.is_set)
        if turn is None:
            return None
        self._tin += turn.tokens_in
        self._tout += turn.tokens_out
        if turn.tokens_per_s:
            self._tps = turn.tokens_per_s
        self._mark_first()
        msg = turn.msg
        if not msg.get("tool_calls"):            # model wrote the call as text instead
            found, text = extract_text_calls(msg.get("content") or "")
            if found:
                msg["_from_text"] = True
                msg["content"] = text
                msg["tool_calls"] = [{"id": "", "type": "function",
                                      "function": {"name": n, "arguments": json.dumps(a)}} for n, a in found]
                msg.pop("_anthropic", None)
        n = len(self.messages)
        for i, c in enumerate(msg.get("tool_calls") or []):
            if not c.get("id"):
                c["id"] = f"call_{n}_{i}"
        return msg

    def _mark_first(self):
        if not self._first:
            self._first = time.time() - self._t0

    def _on_text(self, t: str):
        self._mark_first()
        self.text_delta.emit(t)

    def _on_think(self, t: str):
        self._mark_first()
        self.thinking_delta.emit(t)

    def _schemas(self) -> list[dict]:
        out = Toolbox.schemas(drive=drive_connected(), skills=bool(self.skills), compact=self._lean)
        for name in sorted(self.active_mcp):
            srv = self.mcp.running.get(name)
            if srv and srv.alive():
                out += srv.schemas(name)
        return out

    def _connect_mcp(self, name: str) -> str:
        if name in self.active_mcp and self.mcp.running.get(name) and self.mcp.running[name].alive():
            return f"\n\n[{name} tools are already connected.]"
        self.status.emit(f"Starting {name.upper()}…")
        try:
            srv = self.mcp.get(name, str(self.toolbox.root))
        except MCPError as e:
            return f"\n\n[Couldn't start the {name} tool server: {e}. Continue without it and tell the user.]"
        self.active_mcp.add(name)
        return (f"\n\n[{name.upper()} is connected: {len(srv.tools)} tools are now available, named "
                f"{name}__<tool>. AFIF Coder already installed and configured it (Node, JDK, JADX), so skip any "
                f"setup or doctor steps and go straight to the analysis.]")

    def _run_mcp(self, name: str, args: dict, call_id: str) -> tuple[bool, str, list]:
        server, _, tool = name.partition("__")
        srv = self.mcp.running.get(server)
        if not srv or not srv.alive():
            return False, f"The {server} tool server isn't running. Load its skill again to restart it.", []
        real = next((t["name"] for t in srv.tools if f"{server}__{t['name']}"[:64] == name), tool)
        try:
            text, images, is_err = srv.call(real, args, cancelled=self._stop.is_set)
        except MCPError as e:
            return False, f"Error: {e}", []
        return not is_err, (text if len(text) < 60_000 else text[:60_000] + "\n... [output cut at 60,000 characters]"), images

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
        sig = _sig(call)
        seen = self._seen.setdefault(sig, [0, True, ""])
        if seen[0] >= 1 and not seen[1] and name != "run_command":
            out = (f"Not run: this exact call already failed with: {seen[2][:300]} "
                   "Change something first (for example read the file again and copy the text exactly).")
            seen[0] += 1
            self.tool_finished.emit(call_id, False, out)
            self._reply(call_id, out)
            return

        is_mcp = "__" in name and name.split("__")[0] in self.active_mcp
        needs = name in Toolbox.NEEDS_APPROVAL or (is_mcp and not READ_ONLY_MCP.match(name.split("__", 1)[1]))
        if needs and not self.auto_approve:
            self.status.emit("Waiting for your approval")
            self._approval.clear()
            self.approval_needed.emit(call_id, name, args)
            self._approval.wait()
            if not self._approved:
                self.tool_finished.emit(call_id, False, "Declined")
                self._reply(call_id, "The user declined this action. Ask what they would like instead.")
                return

        self.status.emit(f"Running {name}")
        images: list[str] = []
        if is_mcp:
            ok, out, images = self._run_mcp(name, args, call_id)
        else:
            ctx = Ctx(on_output=lambda t, cid=call_id: self.tool_progress.emit(cid, t), cancelled=self._stop.is_set)
            try:
                res = self.toolbox.run(name, args, ctx)
                if isinstance(res, ToolResult):
                    out, images = res.text, res.images
                else:
                    out = res
                ok = True
            except ToolError as e:
                out, ok = f"Error: {e}", False
            except Exception as e:
                out, ok = f"Error: {type(e).__name__}: {e}", False
            if ok and name == "use_skill":
                sk = self.skills.get(args.get("name")) or next(
                    (v for k, v in self.skills.items() if k.lower() == str(args.get("name", "")).lower()), None)
                if sk and sk.name not in self.loaded:
                    self.loaded.append(sk.name)
                    self.skill_loaded.emit(sk.name, "requested")
                if sk and sk.mcp:
                    out += self._connect_mcp(sk.mcp)
        seen[0] += 1
        seen[1], seen[2] = ok, out
        if ok and name in ("write_file", "edit_file"):
            self._seen = {k: v for k, v in self._seen.items() if v[1]}   # files changed: failures may now work
        self.tool_finished.emit(call_id, ok, out)
        self._reply(call_id, out, images)

    def _fit_context(self, share: float = 0.7, keep: int = 2):
        """Local models have a small memory: trim old tool output when the chat gets near the limit."""
        limit = getattr(self._backend, "context_tokens", 0)
        if not limit:
            return

        def size():
            return sum(len(m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
                       + len(json.dumps(m.get("tool_calls", ""))) for m in self.messages) / 3.2

        budget = limit * share
        if size() <= budget:
            return
        tool_idx = [i for i, m in enumerate(self.messages) if m["role"] == "tool"]
        for i in tool_idx[:-keep]:                       # keep the latest results intact
            m = self.messages[i]
            if len(m["content"]) > 200:
                m["content"] = m["content"][:160] + "\n[older output trimmed to save memory]"
            if size() <= budget:
                return
        for m in self.messages[1:-1]:                    # then long replies and attachments, oldest first
            c = m.get("content")
            if isinstance(c, str) and len(c) > 1500:
                m["content"] = c[:1200] + "\n[trimmed to save memory]"
            if size() <= budget:
                return
        self.status.emit("This chat is near the model's memory limit. Consider starting a new chat.")

    def _reply(self, call_id: str, content: str, images: list | None = None):
        m = {"role": "tool", "tool_call_id": call_id, "content": content}
        if images:
            m["_images"] = images
        self.messages.append(m)

