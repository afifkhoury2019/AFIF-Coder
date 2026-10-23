"""Tools the model can call. File paths are confined to the project folder."""
from __future__ import annotations

import fnmatch
import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import requests

from . import tooling

MAX_OUT = 20_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vs", ".gradle"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
DRIVE = "gdrive"


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    text: str
    images: list[str] = field(default_factory=list)     # data URLs to show the model


@dataclass
class Ctx:
    on_output: object = None        # callable(str) for live command output
    cancelled: object = None        # callable() -> bool


def _clip(text: str, limit: int = MAX_OUT) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    return text[:head] + f"\n... [{len(text) - limit:,} characters cut] ...\n" + text[-(limit - head):]


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
I = {"type": "integer"}


class Toolbox:
    NEEDS_APPROVAL = {"write_file", "edit_file", "run_command", "drive_upload"}

    def __init__(self, root: str, skills: dict | None = None, settings: dict | None = None):
        self.root = Path(root).resolve()
        self.skills = skills or {}
        self.s = settings or {}

    # ---------- schemas ----------
    @staticmethod
    def schemas(drive: bool = False, skills: bool = True, compact: bool = False) -> list[dict]:
        t = [
            _fn("list_dir", "List files and folders in a directory of the project.",
                {"path": {**S, "description": "Directory relative to project root, '.' for root"}}, ["path"]),
            _fn("read_file", "Read a text file. Returns numbered lines.",
                {"path": {**S, "description": "File path relative to project root"},
                 "start_line": {**I, "description": "Optional first line (1-based)"},
                 "end_line": {**I, "description": "Optional last line"}}, ["path"]),
            _fn("write_file", "Create or overwrite a file with the full content given.",
                {"path": S, "content": {**S, "description": "Complete file content"}}, ["path", "content"]),
            _fn("edit_file", "Replace one exact occurrence of old_text with new_text in a file.",
                {"path": S, "old_text": {**S, "description": "Exact text to find; must be unique in the file"},
                 "new_text": S}, ["path", "old_text", "new_text"]),
            _fn("find_files", "Find files whose path matches a glob pattern, e.g. '**/*.py'.", {"pattern": S}, ["pattern"]),
            _fn("search_text", "Search file contents with a regular expression. Returns matching lines.",
                {"pattern": S, "glob": {**S, "description": "Optional file filter, e.g. '*.py'"}}, ["pattern"]),
            _fn("run_command",
                "Run a command in the project folder and return its output. Bundled tools are on PATH: git, gh "
                "(GitHub CLI, already signed in if the user connected GitHub), node/npm/npx, python/pip with "
                "python-docx, openpyxl, python-pptx, reportlab, pypdf, playwright (Chromium installed), java/JDK, rclone. "
                "Long jobs (builds, 'gh run watch') can run up to an hour.",
                {"command": S,
                 "shell": {"type": "string", "enum": ["cmd", "powershell", "bash"],
                           "description": "Windows: cmd (default) or powershell. bash only if available."},
                 "timeout_seconds": {**I, "description": "Default 300, maximum 3600"}}, ["command"]),
            _fn("web_search", "Search the web. Returns titles, links and snippets.", {"query": S}, ["query"]),
            _fn("web_fetch", "Download a web page or file from a URL and return its text (HTML is converted to text).",
                {"url": S, "max_chars": {**I, "description": "Default 40000"}}, ["url"]),
            _fn("view_image", "Look at an image file in the project (for example a screenshot you just made). "
                "Only models that can see images get the picture.", {"path": S}, ["path"]),
        ]
        if skills:
            t += [
                _fn("use_skill", "Load a skill's full instructions. Do this before starting a task the skill covers.",
                    {"name": S}, ["name"]),
                _fn("read_skill_file", "Read a reference file or script that belongs to a skill.",
                    {"skill": S, "path": S}, ["skill", "path"]),
                _fn("record_lesson", "Save a lesson to a skill so every model gets it next time: a pitfall you hit and "
                    "how you fixed it, or a correction from the user. One or two concrete sentences.",
                    {"skill": S, "lesson": S}, ["skill", "lesson"]),
            ]
        t.append(_fn("update_progress",
                     "Rewrite the project's shared progress notes (.afif/PROGRESS.md). Any model that continues this "
                     "project reads them first. Keep sections: Goal, Status, Decisions, Repository and builds, Next steps. "
                     "Update after each milestone and before you finish.",
                     {"content": {**S, "description": "The complete new PROGRESS.md (markdown)"}}, ["content"]))
        if drive:
            t += [
                _fn("drive_list", "List a folder in the user's Google Drive ('' for My Drive's top level).",
                    {"path": S}, ["path"]),
                _fn("drive_search", "Find files in Google Drive whose name contains the text.", {"name": S}, ["name"]),
                _fn("drive_download", "Copy a file or folder from Google Drive into the project. Google Docs/Sheets/"
                    "Slides arrive as .docx/.xlsx/.pptx.", {"drive_path": S, "to": {**S, "description":
                    "Destination path in the project"}}, ["drive_path", "to"]),
                _fn("drive_upload", "Copy a project file or folder to Google Drive.",
                    {"path": S, "drive_path": {**S, "description": "Destination path in Drive, e.g. 'AFIF/builds/app.apk'"}},
                    ["path", "drive_path"]),
            ]
        if compact:          # local models: short descriptions read much faster
            t = [f for f in t if f["function"]["name"] not in ("view_image", "read_skill_file")]
            for f in t:
                fn = f["function"]
                fn["description"] = fn["description"].split(". ")[0].rstrip(".") + "."
                for prop in fn["parameters"]["properties"].values():
                    prop.pop("description", None)
        return t

    # ---------- dispatch ----------
    def run(self, name: str, args: dict, ctx: Ctx | None = None):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ToolError(f"unknown tool '{name}'")
        self.ctx = ctx or Ctx()
        try:
            return fn(**(args or {}))
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}") from e

    def summary(self, name: str, args: dict) -> str:
        a = args or {}
        for k in ("command", "query", "url", "pattern", "drive_path", "name", "path"):
            if a.get(k):
                return str(a[k])
        return ""

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

    def _walk(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                yield Path(dirpath) / f

    # ---------- files ----------
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

    # ---------- commands ----------
    def t_run_command(self, command: str, shell: str = "cmd", timeout_seconds: int | None = None):
        timeout = max(5, min(int(timeout_seconds or 300), 3600))
        win = sys.platform == "win32"
        env = tooling.env()
        kw = dict(cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
        if win:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        if shell == "powershell" and win:
            args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
            proc = subprocess.Popen(args, **kw)
        elif shell == "bash" or not win:
            bash = tooling.which("bash") or ("/bin/bash" if not win else None)
            if not bash:
                raise ToolError("bash isn't available on this PC. Use shell 'cmd' or 'powershell'.")
            proc = subprocess.Popen([bash, "-lc", command] if win else command, shell=not win, **kw)
        else:
            proc = subprocess.Popen(f"chcp 65001>nul & {command}", shell=True, **kw)

        chunks: list[str] = []

        def pump():
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                chunks.append(line)
                if self.ctx.on_output:
                    self.ctx.on_output(line)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        t0, reason = time.time(), ""
        while proc.poll() is None:
            if self.ctx.cancelled and self.ctx.cancelled():
                reason = "stopped by the user"
            elif time.time() - t0 > timeout:
                reason = f"timed out after {timeout} seconds"
            if reason:
                _kill_tree(proc)
                break
            time.sleep(0.2)
        reader.join(5)
        out = "".join(chunks).strip()
        code = proc.poll()
        head = f"[{reason}]" if reason else f"[exit code {code}]"
        return _clip(f"{head}\n{out}")

    # ---------- web ----------
    def t_web_search(self, query: str):
        key = self.s.get("brave_key", "")
        try:
            if key:
                r = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 8},
                                 headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=20)
                r.raise_for_status()
                res = [(x.get("title", ""), x.get("url", ""), _strip_tags(x.get("description", "")))
                       for x in r.json().get("web", {}).get("results", [])]
            else:
                r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                                  headers={"User-Agent": UA}, timeout=20)
                r.raise_for_status()
                res = _parse_ddg(r.text)
        except requests.RequestException as e:
            raise ToolError(f"web search failed: {e}")
        if not res:
            raise ToolError("no results (the search service may be limiting requests; try again shortly or "
                            "add a Brave Search key in Settings)")
        return "\n\n".join(f"{i}. {t}\n{u}\n{s}" for i, (t, u, s) in enumerate(res[:8], 1))

    def t_web_fetch(self, url: str, max_chars: int | None = None):
        if not re.match(r"^https?://", url):
            raise ToolError("url must start with http:// or https://")
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en,ar;q=0.8"}, timeout=30)
        except requests.RequestException as e:
            raise ToolError(f"couldn't fetch {url}: {e}")
        ctype = r.headers.get("Content-Type", "")
        limit = max(1000, min(int(max_chars or 40_000), 200_000))
        if "html" in ctype or r.text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
            text = _html_to_text(r.text)
        elif any(t in ctype for t in ("json", "xml", "text", "javascript", "rss", "atom")):
            text = r.text
        else:
            return f"[HTTP {r.status_code}] {ctype or 'binary'} file, {len(r.content):,} bytes; not text."
        return f"[HTTP {r.status_code}] {r.url}\n\n" + _clip(text, limit)

    # ---------- images ----------
    def t_view_image(self, path: str):
        p = self._path(path)
        if not p.is_file():
            raise ToolError(f"'{path}' does not exist")
        from .attachments import _image_data_url
        url = _image_data_url(p)
        return ToolResult(f"Image {self.rel(p)} attached below.", [url])

    # ---------- skills ----------
    def t_use_skill(self, name: str):
        sk = self.skills.get(name) or next((s for k, s in self.skills.items() if k.lower() == name.lower()), None)
        if not sk:
            raise ToolError(f"no skill named '{name}'. Available: {', '.join(self.skills) or 'none'}")
        files = sk.files()
        extra = ("\n\nFiles in this skill (read with read_skill_file): " + ", ".join(files)) if files else ""
        return f"<skill name=\"{sk.name}\">\n{sk.full_text()}\n</skill>{extra}"

    def t_record_lesson(self, skill: str, lesson: str):
        from .skills import record_lesson
        if skill not in self.skills:
            raise ToolError(f"no skill named '{skill}'. Available: {', '.join(self.skills)}")
        if len(lesson.strip()) < 15:
            raise ToolError("write the lesson as a full sentence: what went wrong and what fixes it")
        n = record_lesson(skill, lesson)
        return f"Saved. {skill} now has {n} lesson(s); every model will see them when it uses this skill."

    def t_update_progress(self, content: str):
        from .project import Project
        if len(content.strip()) < 20:
            raise ToolError("progress notes are too short; include status and next steps")
        Project(str(self.root)).write_progress(content)
        return "Progress notes saved to .afif/PROGRESS.md."

    def t_read_skill_file(self, skill: str, path: str):
        sk = self.skills.get(skill)
        if not sk:
            raise ToolError(f"no skill named '{skill}'")
        p = (sk.path / path).resolve()
        if sk.path.resolve() not in p.parents or not p.is_file():
            raise ToolError(f"'{path}' isn't a file in skill {skill}")
        return _clip(p.read_text(encoding="utf-8", errors="replace"), 60_000)

    # ---------- Google Drive (rclone) ----------
    def _rclone(self, args: list[str], timeout: int = 600) -> str:
        if not drive_connected():
            raise ToolError("Google Drive isn't connected. Ask the user to connect it in Connections.")
        code, out = tooling.run(["rclone", *args], timeout=timeout)
        if code != 0:
            raise ToolError(out.strip()[-1500:] or f"rclone failed ({code})")
        return out

    def t_drive_list(self, path: str = ""):
        out = self._rclone(["lsjson", f"{DRIVE}:{path}", "--max-depth", "1"], 120)
        items = json.loads(out or "[]")
        rows = [f"{i['Name']}/" if i.get("IsDir") else f"{i['Name']}  ({i.get('Size', 0):,} bytes, {i.get('MimeType', '')})"
                for i in sorted(items, key=lambda x: (not x.get("IsDir"), x["Name"].lower()))]
        return "\n".join(rows) or "(empty folder)"

    def t_drive_search(self, name: str):
        pat = "*" + re.sub(r"[\[\]{}*?]", "", name) + "*"
        out = self._rclone(["lsjson", f"{DRIVE}:", "-R", "--files-only", "--ignore-case", "--include", pat,
                            "--fast-list"], 300)
        items = json.loads(out or "[]")
        return "\n".join(f"{i['Path']}  ({i.get('Size', 0):,} bytes)" for i in items[:100]) or "No files matched."

    def t_drive_download(self, drive_path: str, to: str):
        dest = self._path(to)
        dest.parent.mkdir(parents=True, exist_ok=True)
        info = json.loads(self._rclone(["lsjson", f"{DRIVE}:{drive_path}", "--stat"], 120) or "{}")
        verb = "copy" if info.get("IsDir") else "copyto"
        self._rclone([verb, f"{DRIVE}:{drive_path}", str(dest)], 3600)
        return f"Copied Drive:{drive_path} to {self.rel(dest)}"

    def t_drive_upload(self, path: str, drive_path: str):
        src = self._path(path)
        if not src.exists():
            raise ToolError(f"'{path}' does not exist")
        self._rclone(["copy" if src.is_dir() else "copyto", str(src), f"{DRIVE}:{drive_path}"], 3600)
        return f"Uploaded {self.rel(src)} to Drive:{drive_path}"


# ---------------------------------------------------------------- helpers
def drive_connected() -> bool:
    cfg = tooling.rclone_config()
    return cfg.is_file() and f"[{DRIVE}]" in cfg.read_text(encoding="utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen):
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _parse_ddg(page: str) -> list[tuple[str, str, str]]:
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                         page, re.S):
        href, title, rest = m.groups()
        if "uddg=" in href:
            href = urllib.parse.unquote(re.search(r"uddg=([^&]+)", href).group(1))
        if "duckduckgo.com/y.js" in href:          # ads
            continue
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        out.append((_strip_tags(title), href, _strip_tags(snip.group(1)) if snip else ""))
    return out


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
             "footer", "pre", "table", "ul", "ol", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith("http"):
                self.out.append(" [")
                self._href = href
        if tag in ("h1", "h2", "h3"):
            self.out.append("# ")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and getattr(self, "_href", None):
            self.out.append(f"]({self._href}) ")
            self._href = None
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def _html_to_text(page: str) -> str:
    p = _Text()
    try:
        p.feed(page)
    except Exception:
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return (f"Title: {p.title.strip()}\n\n" if p.title.strip() else "") + text
