"""Tools the local model can call. Every path is confined to the project folder."""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_OUT = 20_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vs"}


class ToolError(Exception):
    pass


def _clip(text: str, limit: int = MAX_OUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


class Toolbox:
    """Holds the project root and runs tool calls against it."""

    # Tools that change something need the user's approval (unless auto-approve is on)
    NEEDS_APPROVAL = {"write_file", "edit_file", "run_command"}

    def __init__(self, root: str):
        self.root = Path(root).resolve()

    # ---------- path safety ----------
    def _path(self, rel: str) -> Path:
        if not rel:
            raise ToolError("path is empty")
        p = Path(rel)
        p = (p if p.is_absolute() else self.root / p).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError(f"'{rel}' is outside the project folder")
        return p

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root)).replace("\\", "/") or "."
        except ValueError:
            return str(p)

    # ---------- schemas sent to Ollama ----------
    @staticmethod
    def schemas() -> list[dict]:
        def fn(name, desc, props, required):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required}}}
        s = {"type": "string"}
        return [
            fn("list_dir", "List files and folders in a directory of the project.",
               {"path": {**s, "description": "Directory relative to project root, '.' for root"}}, ["path"]),
            fn("read_file", "Read a text file. Returns numbered lines.",
               {"path": {**s, "description": "File path relative to project root"},
                "start_line": {"type": "integer", "description": "Optional first line (1-based)"},
                "end_line": {"type": "integer", "description": "Optional last line"}}, ["path"]),
            fn("write_file", "Create or overwrite a file with the full content given.",
               {"path": s, "content": {**s, "description": "Complete file content"}}, ["path", "content"]),
            fn("edit_file", "Replace one exact occurrence of old_text with new_text in a file.",
               {"path": s, "old_text": {**s, "description": "Exact text to find; must be unique in the file"},
                "new_text": s}, ["path", "old_text", "new_text"]),
            fn("find_files", "Find files whose path matches a glob pattern, e.g. '**/*.py'.",
               {"pattern": s}, ["pattern"]),
            fn("search_text", "Search file contents with a regular expression. Returns matching lines.",
               {"pattern": s, "glob": {**s, "description": "Optional file filter, e.g. '*.py'"}}, ["pattern"]),
            fn("run_command", "Run a shell command in the project folder and return its output.",
               {"command": s}, ["command"]),
        ]

    # ---------- dispatch ----------
    def run(self, name: str, args: dict) -> str:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ToolError(f"unknown tool '{name}'")
        try:
            return fn(**(args or {}))
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}") from e

    def summary(self, name: str, args: dict) -> str:
        """One-line label for the transcript."""
        a = args or {}
        if name == "run_command":
            return a.get("command", "")
        if name in ("find_files", "search_text"):
            return a.get("pattern", "")
        return a.get("path", "")

    # ---------- tools ----------
    def _walk(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                yield Path(dirpath) / f

    def t_list_dir(self, path: str = "."):
        p = self._path(path)
        if not p.is_dir():
            raise ToolError(f"'{path}' is not a directory")
        rows = []
        for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if c.name in SKIP_DIRS:
                continue
            rows.append(c.name + "/" if c.is_dir() else f"{c.name}  ({c.stat().st_size:,} bytes)")
        return "\n".join(rows) or "(empty)"

    def t_read_file(self, path: str, start_line: int | None = None, end_line: int | None = None):
        p = self._path(path)
        if not p.is_file():
            raise ToolError(f"'{path}' does not exist")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        a = max(1, int(start_line or 1))
        b = min(len(lines), int(end_line or len(lines)))
        out = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(a, b + 1))
        return _clip(out or "(empty file)")

    def t_write_file(self, path: str, content: str):
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"{'Overwrote' if existed else 'Created'} {self.rel(p)} ({len(content.splitlines())} lines)"

    def t_edit_file(self, path: str, old_text: str, new_text: str):
        p = self._path(path)
        if not p.is_file():
            raise ToolError(f"'{path}' does not exist")
        text = p.read_text(encoding="utf-8", errors="replace")
        n = text.count(old_text)
        if n == 0:
            raise ToolError("old_text not found; read the file again and copy it exactly")
        if n > 1:
            raise ToolError(f"old_text appears {n} times; include more surrounding lines")
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {self.rel(p)}"

    def t_find_files(self, pattern: str):
        pat = pattern.replace("\\", "/")
        hits = []
        for f in self._walk():
            r = self.rel(f)
            if fnmatch.fnmatch(r, pat) or fnmatch.fnmatch(f.name, pat) or \
               (pat.startswith("**/") and fnmatch.fnmatch(r, pat[3:])):
                hits.append(r)
            if len(hits) >= 300:
                break
        return "\n".join(sorted(hits)) or "No files matched."

    def t_search_text(self, pattern: str, glob: str | None = None):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"bad regular expression: {e}") from e
        out = []
        for f in self._walk():
            if glob and not fnmatch.fnmatch(f.name, glob):
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                with open(f, encoding="utf-8", errors="strict") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            out.append(f"{self.rel(f)}:{i}: {line.rstrip()[:240]}")
                            if len(out) >= 200:
                                return "\n".join(out) + "\n... [first 200 matches]"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(out) or "No matches."

    def t_run_command(self, command: str):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            r = subprocess.run(command, shell=True, cwd=self.root, capture_output=True,
                               timeout=180, creationflags=flags, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ToolError("command timed out after 180 seconds")
        enc = "utf-8"
        out = r.stdout.decode(enc, errors="replace") + r.stderr.decode(enc, errors="replace")
        return _clip(f"[exit code {r.returncode}]\n{out.strip()}")
