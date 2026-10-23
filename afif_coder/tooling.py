"""Helper programs bundled with AFIF Coder (installed under <app>/tools) and the
environment they need. Tools the installer didn't include are simply absent."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import app_base, data_dir

WIN = sys.platform == "win32"


def tools_dir() -> Path:
    return Path(os.environ.get("AFIF_TOOLS") or app_base() / "tools")


def _first(*cands: Path) -> Path | None:
    return next((c for c in cands if c and c.exists()), None)


def tool_paths() -> dict[str, Path]:
    """Folders to put on PATH, by tool name, for the tools that are installed."""
    t = tools_dir()
    out = {}
    for name, rel in (("git", "git/cmd"), ("git-usr", "git/usr/bin"), ("git-mingw", "git/mingw64/bin"),
                      ("gh", "gh/bin"), ("node", "node"), ("python", "python"), ("python-scripts", "python/Scripts"),
                      ("java", "jdk/bin"), ("rclone", "rclone"), ("rea", "rea/node_modules/.bin")):
        p = t / rel
        if p.is_dir():
            out[name] = p
    return out


def jadx_jar() -> Path | None:
    return _first(tools_dir() / "jadx" / "jadx-headless-mcp-0.7.1-all.jar")


def rclone_config() -> Path:
    return data_dir() / "rclone.conf"


def env() -> dict:
    """Environment for commands, MCP servers and sign-in helpers."""
    e = dict(os.environ)
    paths = [str(p) for p in tool_paths().values()]
    e["PATH"] = os.pathsep.join(paths + [e.get("PATH", "")])
    t = tools_dir()
    if (t / "jdk").is_dir():
        e["JAVA_HOME"] = str(t / "jdk")
    if jadx_jar():
        e["REA_JADX_MCP_JAR"] = str(jadx_jar())
    if (t / "ms-playwright").is_dir():
        e["PLAYWRIGHT_BROWSERS_PATH"] = str(t / "ms-playwright")
    e["RCLONE_CONFIG"] = str(rclone_config())
    e.setdefault("GIT_TERMINAL_PROMPT", "0")       # never hang waiting for a password
    e["PYTHONIOENCODING"] = "utf-8"
    e.setdefault("NO_COLOR", "1")
    return e


def which(name: str) -> str | None:
    return shutil.which(name, path=env()["PATH"])


def run(args: list[str], timeout: int = 60, input: str | None = None) -> tuple[int, str]:
    """Run a helper quietly and return (exit code, combined output)."""
    flags = subprocess.CREATE_NO_WINDOW if WIN else 0
    exe = which(args[0]) or args[0]
    try:
        r = subprocess.run([exe, *args[1:]], capture_output=True, timeout=timeout, env=env(), input=input,
                           text=True, encoding="utf-8", errors="replace", creationflags=flags)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, f"{args[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{args[0]} timed out"


def rea_launcher() -> list[str] | None:
    """Command that starts REA's MCP server, if REA is bundled (or on PATH)."""
    script = tools_dir() / "rea" / "node_modules" / "rea-agents" / "scripts" / "rea.mjs"
    node = which("node")
    if script.is_file() and node:
        return [node, str(script), "mcp"]
    rea = which("rea-agents") or which("rea")
    return [rea, "mcp"] if rea else None


def inventory() -> list[tuple[str, str, bool]]:
    """(tool, what it's for, installed?) for the Connections window."""
    have = tool_paths()
    return [
        ("Git", "clone, commit and push code", "git" in have or bool(which("git"))),
        ("GitHub CLI", "sign in, create repos, follow Actions builds", "gh" in have or bool(which("gh"))),
        ("Node.js", "JavaScript tools, REA", "node" in have or bool(which("node"))),
        ("Python + document and test libraries", "Word/Excel/PowerPoint/PDF, Playwright UI tests",
         "python" in have),
        ("Chromium for Playwright", "headless screenshots of web UIs", (tools_dir() / "ms-playwright").is_dir()),
        ("Java JDK", "REA Android analysis (JADX)", "java" in have or bool(which("java"))),
        ("JADX engine", "decompiling APKs through REA", bool(jadx_jar())),
        ("REA", "reverse-engineering MCP server", bool(rea_launcher())),
        ("rclone", "Google Drive", "rclone" in have or bool(which("rclone"))),
    ]
