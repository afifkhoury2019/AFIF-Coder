"""Claude Code, free on this PC: run Anthropic's Claude Code command-line agent against the AFIF Engine.

The AFIF Engine (llama.cpp's server) answers Anthropic's Messages API at /v1/messages, so Claude Code
can use a local model instead of an Anthropic account, the same idea as `ollama launch claude`, with
nothing else to install. Claude Code itself is downloaded from npm on first use (with the bundled
Node.js) into AFIF Coder's data folder, so it's always Anthropic's own current release.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import tooling
from .paths import data_dir

WIN = sys.platform == "win32"
PACKAGE = "@anthropic-ai/claude-code"
MIN_CONTEXT = 32768          # Claude Code's own instructions and tools are about 20,000 tokens


def home() -> Path:
    d = data_dir() / "claude-code"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exe() -> Path | None:
    b = home() / "node_modules" / ".bin"
    for name in (("claude.cmd", "claude.exe", "claude") if WIN else ("claude",)):
        if (b / name).is_file():
            return b / name
    return None


def installed() -> bool:
    return exe() is not None


def install(on_line) -> tuple[bool, str]:
    """npm install Claude Code into the data folder. on_line(text) receives progress."""
    npm = tooling.which("npm.cmd" if WIN else "npm") or tooling.which("npm")
    if not npm:
        return False, "Node.js isn't installed. Reinstall AFIF Coder with the Full option."
    args = [npm, "install", "--prefix", str(home()), PACKAGE, "--no-fund", "--no-audit", "--loglevel", "error"]
    flags = subprocess.CREATE_NO_WINDOW if WIN else 0
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=tooling.env(),
                             creationflags=flags, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"Couldn't run npm: {e}"
    for line in p.stdout:
        on_line(line.rstrip())
    p.wait()
    if p.returncode != 0 or not installed():
        return False, f"npm couldn't install Claude Code (exit {p.returncode})."
    return True, "Claude Code is installed."


def environment(engine_url: str) -> dict:
    """Point Claude Code at the AFIF Engine; every model slot uses the loaded local model."""
    e = tooling.env()
    e.update({
        "ANTHROPIC_BASE_URL": engine_url,
        "ANTHROPIC_AUTH_TOKEN": "afif-local",          # any value: the engine has no key; skips the login
        "ANTHROPIC_MODEL": "afif-local",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "afif-local",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "afif-local",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "afif-local",
        "ANTHROPIC_SMALL_FAST_MODEL": "afif-local",
        "CLAUDE_CODE_SUBAGENT_MODEL": "afif-local",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CONFIG_DIR": str(home() / "config"),    # separate from a real Claude Code setup
    })
    e.pop("ANTHROPIC_API_KEY", None)
    bash = tooling.tools_dir() / "git" / "usr" / "bin" / "bash.exe"
    if WIN and bash.is_file():
        e["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
    return e


def launch(project: str, engine_url: str) -> None:
    """Open Claude Code in its own console window, in the project folder."""
    cmd = exe()
    if cmd is None:
        raise RuntimeError("Claude Code isn't installed yet.")
    env = environment(engine_url)
    if WIN:
        # cmd strips the outer quotes of a /k string that holds more than two quotes
        subprocess.Popen(f'cmd.exe /k "title Claude Code - AFIF Engine & "{cmd}""', cwd=project, env=env,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        term = next((t for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm") if tooling.which(t)), None)
        if not term:
            raise RuntimeError(f"No terminal found. Run this yourself: ANTHROPIC_BASE_URL={engine_url} {cmd}")
        subprocess.Popen([term, "-e", str(cmd)], cwd=project, env=env)


def first_reply_minutes(prompt_tps: float) -> float:
    """Rough wait for Claude Code's first answer: it sends ~20,000 tokens of instructions first."""
    return 20000 / prompt_tps / 60 if prompt_tps else 0.0


# ---------------------------------------------------------------- Claude Code inside AFIF Coder's chat
# Claude Code's headless mode (`claude -p --output-format stream-json`) prints one JSON event per line:
# system/init, assistant messages (text, thinking, tool_use), user messages (tool_result), stream_event
# deltas (with --include-partial-messages) and a final result. AFIF Coder shows them in its own chat, so
# Claude Code runs embedded: no terminal, no Anthropic account, no API key, only the AFIF Engine.

TOOL_NAMES = {"Read": "read_file", "Write": "write_file", "Edit": "edit_file", "MultiEdit": "edit_file",
              "Bash": "run_command", "PowerShell": "run_command", "Glob": "find_files", "Grep": "search_text",
              "LS": "list_dir", "WebFetch": "web_fetch", "WebSearch": "web_search", "NotebookEdit": "edit_file"}

# A lighter setup for slow PCs: AFIF Coder's short instructions instead of Claude Code's ~20,000-token
# prompt, and only the tools a coding task needs. Falls back to the full setup if a flag isn't known.
LITE_PROMPT = ("You are Claude Code running on a local model inside AFIF Coder (AFIF-TECH). Work in the current "
               "folder. Look before changing: use Glob, Grep and Read. Change files with Edit or Write. Run builds "
               "and tests with Bash. Keep going until the task is done, then reply with a short summary. Only say "
               "you changed something after the tool succeeded. Reply in the user's language.")
LITE_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"


def summary(name: str, args: dict) -> str:
    for k in ("file_path", "path", "command", "pattern", "url", "query", "notebook_path"):
        if args.get(k):
            return str(args[k])[:200]
    return ""


def native_exe() -> Path | None:
    """The claude.exe the npm package downloads (runs without Node and without cmd quoting issues)."""
    root = home() / "node_modules" / "@anthropic-ai"
    if root.is_dir():
        for p in sorted(root.rglob("claude.exe" if WIN else "claude")):
            if p.is_file() and p.parent.name != ".bin":
                return p
    return None


def command() -> list[str]:
    exe_ = native_exe() or exe()
    if exe_ is None:
        raise RuntimeError("Claude Code isn't installed yet.")
    return [str(exe_)]


class Run:
    """One message handled by Claude Code. Calls back on_event(kind, data) from the reading thread:
    ("text", str), ("thinking", str), ("tool", (id, name, summary)), ("tool_done", (id, ok, output)),
    ("status", str). Returns the result dict."""

    def __init__(self, project: str, engine_url: str):
        self.project, self.engine_url = project, engine_url
        self.proc: subprocess.Popen | None = None

    def args(self, auto: bool, session: str, lite: bool, partial: bool = True, notes: str = "") -> list[str]:
        a = command() + ["-p", "--output-format", "stream-json", "--verbose", "--model", "afif-local",
                         "--permission-mode", "bypassPermissions" if auto else "acceptEdits"]
        if partial:
            a.append("--include-partial-messages")
        if session:
            a += ["--resume", session]
        if lite:
            a += ["--system-prompt", LITE_PROMPT + ("\n\n" + notes[:3000] if notes else ""), "--tools", LITE_TOOLS]
        elif notes:
            a += ["--append-system-prompt", notes]
        return a

    def stop(self):
        p = self.proc
        if p and p.poll() is None:
            if WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                p.kill()

    def run(self, prompt: str, auto: bool, session: str, lite: bool, notes: str, on_event, stopped) -> dict:
        tries = [(lite, True), (False, True), (False, False)] if lite else [(False, True), (False, False)]
        res: dict = {}
        for use_lite, partial in tries:
            res = self._once(prompt, auto, session, use_lite, partial, notes, on_event, stopped)
            if res.get("_unknown_flag") and not stopped():
                on_event("status", "This Claude Code version doesn't know a setting; trying the standard setup…")
                continue
            break
        res.pop("_unknown_flag", None)
        return res

    def _once(self, prompt, auto, session, lite, partial, notes, on_event, stopped) -> dict:
        import json
        import threading
        args = self.args(auto, session, lite, partial, notes)
        flags = subprocess.CREATE_NO_WINDOW if WIN else 0
        self.proc = subprocess.Popen(args, cwd=self.project, env=environment(self.engine_url),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1)
        err_lines: list[str] = []
        threading.Thread(target=lambda: err_lines.extend(self.proc.stderr), daemon=True).start()
        try:
            self.proc.stdin.write(prompt)          # the prompt goes in on stdin: no shell quoting of user text
            self.proc.stdin.close()
        except OSError:
            pass
        result: dict = {}
        streamed = False
        watcher = threading.Thread(target=self._watch, args=(stopped,), daemon=True)
        watcher.start()
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                result["session_id"] = ev.get("session_id", "")
                on_event("status", "Claude Code is reading its instructions on the local model…")
            elif t == "stream_event":
                e = ev.get("event") or {}
                if e.get("type") == "message_start":
                    streamed = False
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    streamed = True
                    on_event("text", d["text"])
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    on_event("thinking", d["thinking"])
            elif t == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "text" and c.get("text") and not streamed:
                        on_event("text", c["text"])
                    elif c.get("type") == "tool_use":
                        on_event("tool", (c.get("id", ""), c.get("name", ""), c.get("input") or {}))
                streamed = False
            elif t == "user":
                content = (ev.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if c.get("type") == "tool_result":
                        out = c.get("content")
                        if isinstance(out, list):
                            out = "\n".join(p.get("text", "") for p in out if isinstance(p, dict))
                        on_event("tool_done", (c.get("tool_use_id", ""), not c.get("is_error"), str(out or "")))
            elif t == "result":
                result.update(ev)
        self.proc.wait()
        tail = "".join(err_lines)[-1500:]
        if not result.get("type") and ("unknown option" in tail.lower() or "error: option" in tail.lower()):
            return {"_unknown_flag": True}
        if not result.get("type") and not stopped():
            result["is_error"] = True
            result["result"] = ("Claude Code stopped without an answer (exit "
                                f"{self.proc.returncode}). {tail.strip()[-600:]}")
        return result

    def _watch(self, stopped):
        import time
        while self.proc and self.proc.poll() is None:
            if stopped():
                self.stop()
                return
            time.sleep(0.2)


class Harness:
    """Stands in for a model backend: the agent hands each message to Claude Code instead."""
    harness = True
    local = True
    budget_tokens = 4000
    label = "Claude Code"
    model = "claude-code"
    speed = "balanced"
    timing: dict = {}
    on_thinking = on_progress = on_status = None

    def __init__(self, engine_url: str, lite: bool = True):
        self.engine_url, self.lite = engine_url, lite
        self.current: Run | None = None

    def run(self, project: str, text: str, auto: bool, session: str, notes: str, on_event, stopped) -> dict:
        import time
        self.current = Run(project, self.engine_url)
        t0 = time.time()
        res = self.current.run(text, auto, session, self.lite, notes, on_event, stopped)
        self.timing = {"host": self.engine_url, "harness": "claude-code", "lite": self.lite,
                       "seconds": round(time.time() - t0, 2), "turns": res.get("num_turns"),
                       "usage": res.get("usage"), "result": str(res.get("subtype") or "")}
        return res

    def abort(self):
        if self.current:
            self.current.stop()

    def warm_up(self, project: str, notes: str):
        """Have the engine read Claude Code's instructions once, ahead of the first real message."""
        r = Run(project, self.engine_url)
        try:
            r.run("Reply with the single word OK.", False, "", self.lite, notes, lambda *a: None, lambda: False)
        except Exception:
            pass
