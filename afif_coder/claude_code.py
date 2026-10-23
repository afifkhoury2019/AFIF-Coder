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
