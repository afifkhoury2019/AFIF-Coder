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

from . import diag
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


def _safe_json(text) -> dict:
    try:
        v = json.loads(text or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


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
        self._cuts = {}

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
        with diag.span("warm_up", prompt_chars=len(self.messages[0]["content"])):
            backend.warm_up([self.messages[0], {"role": "user", "content": "Hi"}], self._schemas())

    def resume(self, data: dict) -> list:
        """Load a saved chat for this project; returns its messages for the UI to redraw."""
        self.messages = [{"role": "system", "content": self.system_prompt()}] + list(data.get("messages", []))
        self._cuts = {}
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
                diag.event("skill", name=sk.name, how="suggested (local model)")
                continue
            note = (f"[AFIF Coder loaded the skill \"{sk.name}\" because it matches this request. "
                    f"Follow it for this task.]\n<skill name=\"{sk.name}\">\n{sk.full_text()}\n</skill>")
            if sk.mcp:
                note += self._connect_mcp(sk.mcp)
            self.messages.append({"role": "user", "content": note})
            self.skill_loaded.emit(sk.name, "matched")
            diag.event("skill", name=sk.name, how="matched", chars=len(note))

    def stop(self):
        diag.event("stop", backend=type(self._backend).__name__ if self._backend else "")
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
            self._backend.on_status = self.status.emit
            self._sys_cache, self._budget_scale = {}, 1.0
            router = bool(getattr(self._backend, "router", False))
            if router:
                self._backend.payload = self._payload
            # Local models and services with small limits get the short prompt: much faster to read,
            # and it fits free tiers (Groq, GitHub Models) that allow only a few thousand tokens.
            lean = router or bool(getattr(self._backend, "local", False)) or self._backend.budget_tokens < 12000
            if lean != self._lean:
                self._lean = lean
                self.messages[0]["content"] = self.system_prompt(lean)
            if lean:
                self._shrink_skill_notes()
            self._t0, self._first = time.time(), 0.0
            self._tps = 0.0
            self._turns, self._tools_run = 0, 0
            sel = self.s.get("model", "")
            name = getattr(self._backend, "model", "") or ""
            if lean or name == "afif-local":
                name = Path(sel[6:] if sel.startswith("local:") else sel).stem or "local model"
            self._msg_info = {"model": name, "backend": type(self._backend).__name__,
                              "speed": self._backend.speed, "local_prompt": lean,
                              "history_messages": len(self.messages)}
            diag.event("message_start", **self._msg_info,
                       text=(user_content if isinstance(user_content, str) else "[with attachments]")[:200])
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
                try:
                    msg = self._chat_once()
                except BackendError as e:
                    if "no longer fits" not in str(e) and "too large" not in str(e):
                        raise
                    self._budget_scale = 0.5              # send less history and try once more
                    self.status.emit("The chat was too long for this model; sending a shorter part of it…")
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
        except requests.ConnectionError as e:
            err = "Lost the connection to the model. Check the engine or your internet, then try again."
            diag.error("message_error", e, **getattr(self, "_msg_info", {}),
                       request=getattr(self._backend, "timing", {}))
        except BackendError as e:
            err = str(e)
            diag.event("message_error", level="error", error=err, **getattr(self, "_msg_info", {}),
                       request=getattr(self._backend, "timing", {}))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            diag.error("message_error", e, **getattr(self, "_msg_info", {}),
                       request=getattr(self._backend, "timing", {}))
        if self._stop.is_set() and not err:
            err = "Stopped."
        if self._tin or self._tout:
            self.usage.emit(self._tin, self._tout)
        if getattr(self, "_first", 0):
            self.timing.emit(self._first, getattr(self, "_tps", 0.0))
        total = round(time.time() - getattr(self, "_t0", time.time()), 3)
        first = round(getattr(self, "_first", 0.0), 3)
        slow = total >= diag.SLOW["message"] or first >= diag.SLOW["first_words"]
        diag.event("message", level="error" if err and err != "Stopped." else ("slow" if slow else "info"),
                   seconds=total, first_words_s=first, tokens_per_s=round(getattr(self, "_tps", 0.0), 1),
                   tokens_in=self._tin, tokens_out=self._tout, turns=getattr(self, "_turns", 0),
                   tools=getattr(self, "_tools_run", 0), result=err or "done", **getattr(self, "_msg_info", {}))
        self.save()
        self.finished.emit(err)

    def _chat_once(self) -> dict | None:
        t0 = time.time()
        try:
            msg = self._chat_once_inner()
        except Exception as e:
            diag.event("turn", level="error", seconds=round(time.time() - t0, 3), error=f"{type(e).__name__}: {e}",
                       request=getattr(self._backend, "timing", {}))
            raise
        self._turns = getattr(self, "_turns", 0) + 1
        secs = round(time.time() - t0, 3)
        diag.event("turn", level="slow" if secs >= diag.SLOW["turn"] else "info", seconds=secs,
                   stopped=msg is None, text_chars=len((msg or {}).get("content") or ""),
                   tool_calls=[c["function"]["name"] for c in (msg or {}).get("tool_calls") or []],
                   calls_from_text=bool((msg or {}).get("_from_text")),
                   request=getattr(self._backend, "timing", {}))
        return msg

    def _chat_once_inner(self) -> dict | None:
        self.turn_started.emit()
        self.status.emit("Reading your message…" if self._lean else "Waiting for the model…")
        if getattr(self._backend, "router", False):
            msgs, tools = None, None              # Free Auto sizes the prompt for each service it tries
        else:
            msgs, tools = self._payload(self._backend.budget_tokens, self._lean)
        turn = self._backend.stream(msgs, tools, float(self.s.get("temperature", 0.2)),
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

    def _schemas(self, lean: bool | None = None) -> list[dict]:
        lean = self._lean if lean is None else lean
        out = Toolbox.schemas(drive=drive_connected(), skills=bool(self.skills), compact=lean)
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
            with diag.span("mcp_start", server=name):
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
        t0 = time.time()
        before = len(self.messages)
        try:
            self._do_tool_inner(call)
        except Exception as e:
            diag.error("tool", e, tool=call.get("function", {}).get("name"), seconds=round(time.time() - t0, 3))
            raise
        self._tools_run = getattr(self, "_tools_run", 0) + 1
        res = self.messages[-1] if len(self.messages) > before else {}
        out = str(res.get("content", ""))
        ok = not out.startswith(("Error", "Not run", "The user declined"))
        secs = round(time.time() - t0, 3)
        fn = call.get("function", {})
        diag.event("tool", level="error" if not ok else ("slow" if secs >= diag.SLOW["tool"] else "info"),
                   tool=fn.get("name"), seconds=secs, ok=ok, output_chars=len(out),
                   detail=self.toolbox.summary(fn.get("name", ""), _safe_json(fn.get("arguments")))[:200] if self.toolbox else "",
                   error=out[:500] if not ok else "")

    def _do_tool_inner(self, call: dict):
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

    # ---------------------------------------------------------------- what is sent each turn
    @staticmethod
    def _tokens(m: dict) -> float:
        """Rough token count of one message (pictures count as about 1,000 tokens)."""
        c = m.get("content")
        if isinstance(c, list):
            n = sum(len(p.get("text", "")) if p.get("type") == "text" else 3200 for p in c)
        else:
            n = len(c or "")
        if m.get("tool_calls"):
            n += len(json.dumps(m["tool_calls"]))
        n += 3200 * len(m.get("_images") or [])
        return n / 3.2 + 4

    def _payload(self, budget: float, lean: bool) -> tuple[list, list]:
        """The prompt for one turn: instructions, tools and as much recent chat as fits `budget` tokens.
        The full chat stays in memory and on disk; only this copy is shortened. Sending the whole
        history every turn made each Gemini step re-send ~45,000 tokens (slow, and it used up the free
        quota in a few minutes); a long saved chat took a slow PC minutes to re-read."""
        cache = getattr(self, "_sys_cache", None)
        if cache is None:
            cache = self._sys_cache = {}
        if lean not in cache:
            cache[lean] = self.system_prompt(lean)
        system = {"role": "system", "content": cache[lean]}
        tools = self._schemas(lean)
        budget = budget * getattr(self, "_budget_scale", 1.0)
        avail = max(1200.0, budget - self._tokens(system) - len(json.dumps(tools)) / 3.5)
        return [system] + self._view(avail, lean), tools

    def _view(self, avail: float, lean: bool) -> list[dict]:
        msgs = self.messages[1:]
        keep, cap = (3, 500) if lean else (6, 2000)
        tool_idx = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
        old_tools = set(tool_idx[:-keep])
        out = []
        for i, m in enumerate(msgs):
            c = m.get("content")
            if i in old_tools and isinstance(c, str) and len(c) > cap:
                m = dict(m, content=c[:int(cap * 0.8)] + "\n[older output shortened to save tokens; run the tool "
                                                          "again if you need it]")
                m.pop("_images", None)
            elif i < len(msgs) - 4 and isinstance(c, str) and len(c) > cap * 3 and m["role"] != "tool":
                m = dict(m, content=c[:cap * 2] + "\n[shortened to save tokens]")
            out.append(m)
        sizes = [self._tokens(m) for m in out]
        total = sum(sizes)
        cuts = getattr(self, "_cuts", None)
        if cuts is None:
            cuts = self._cuts = {}
        key = (int(avail) // 500, lean)        # one cut point per service size (Free Auto mixes them)
        if total <= avail:
            cuts.pop(key, None)
            return out
        # Leave out the oldest part. Cut only where a user or assistant message starts, so a tool result
        # never loses the call it answers. Keep the cut point while it still fits: the engine's and
        # Claude's prompt caches then stay valid from one turn to the next.
        starts = [i for i, m in enumerate(out) if i > 0 and m["role"] in ("user", "assistant")]
        cut = cuts.get(key, 0)
        if not (cut in starts and sum(sizes[cut:]) <= avail):
            target = avail * 0.65
            cut = next((i for i in starts if sum(sizes[i:]) <= target), starts[-1] if starts else 0)
            cuts[key] = cut
        request = next((m.get("content") for m in reversed(out) if m["role"] == "user"
                        and isinstance(m.get("content"), str) and not m["content"].startswith("[AFIF Coder")), "")
        note = ("[AFIF Coder: older messages of this chat were left out to save tokens. The progress notes in "
                "your instructions summarise earlier work."
                + (f" The user's latest request: \u00ab{request[:600]}\u00bb" if request else "") + "]")
        rest = out[cut:]
        if rest and rest[0]["role"] == "user" and isinstance(rest[0].get("content"), str):
            rest[0] = dict(rest[0], content=note + "\n\n" + rest[0]["content"])
        else:
            rest.insert(0, {"role": "user", "content": note})
        return rest

    def _reply(self, call_id: str, content: str, images: list | None = None):
        m = {"role": "tool", "tool_call_id": call_id, "content": content}
        if images:
            m["_images"] = images
        self.messages.append(m)

