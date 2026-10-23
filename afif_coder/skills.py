"""Skills: folders with a SKILL.md (YAML front matter: name, description) plus any
reference files or scripts. The model sees each skill's name and description and loads
the full instructions with the use_skill tool when a task matches.

Bundled skills live in <app>/skills; your own go in %LOCALAPPDATA%\\AFIF-Coder\\skills
(a skill there with the same name replaces the bundled one).
An optional afif.json next to SKILL.md can name an MCP server to connect when the
skill is loaded, e.g. {"mcp": "rea"}.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import app_base, data_dir


@dataclass
class Skill:
    name: str
    description: str
    path: Path            # folder
    builtin: bool
    mcp: str = ""
    triggers: tuple = ()

    def lessons(self) -> str:
        f = lessons_file(self.name)
        return f.read_text(encoding="utf-8", errors="replace").strip() if f.is_file() else ""

    def full_text(self) -> str:
        """Instructions plus everything models have learned while using this skill."""
        text = self.body()
        les = self.lessons()
        if les:
            text += ("\n\n## Lessons learned while using this skill (recorded by AFIF Coder models and the owner)\n"
                     + les)
        return text

    def matches(self, message: str) -> bool:
        low = message.lower()
        for t in self.triggers:
            t = t.lower()
            if t.isascii():
                if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
                    return True
            elif t in low:
                return True
        return False

    def body(self) -> str:
        return (self.path / "SKILL.md").read_text(encoding="utf-8", errors="replace")

    def files(self) -> list[str]:
        out = []
        for f in sorted(self.path.rglob("*")):
            if f.is_file() and f.name not in ("SKILL.md", "afif.json") and "__pycache__" not in f.parts:
                out.append(f.relative_to(self.path).as_posix())
        return out[:200]


def lessons_file(name: str) -> Path:
    d = data_dir() / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^\w.-]", "_", name) + ".md")


def record_lesson(name: str, lesson: str) -> int:
    from datetime import date
    f = lessons_file(name)
    lesson = " ".join(lesson.split())[:800]
    old = f.read_text(encoding="utf-8") if f.is_file() else ""
    if lesson.lower() in old.lower():
        return old.count("\n- ")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"- {date.today().isoformat()}: {lesson}\n")
    return old.count("\n- ") + 1


def user_skills_dir() -> Path:
    d = data_dir() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_skills_dir() -> Path:
    return app_base() / "skills"


def _front_matter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv and not line.startswith(" "):
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = val.strip("\"'") if val not in ("|", ">", ">-", "|-") else ""
        elif key and line.startswith(" ") and key in ("description",):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def discover() -> dict[str, Skill]:
    found: dict[str, Skill] = {}
    for root, builtin in ((bundled_skills_dir(), True), (user_skills_dir(), False)):
        if not root.is_dir():
            continue
        for md in sorted(root.glob("*/SKILL.md")):
            try:
                fm = _front_matter(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            name = fm.get("name") or md.parent.name
            extra = {}
            side = md.parent / "afif.json"
            if side.is_file():
                try:
                    extra = json.loads(side.read_text(encoding="utf-8"))
                except ValueError:
                    pass
            found[name] = Skill(name, fm.get("description", "")[:600], md.parent, builtin, extra.get("mcp", ""),
                                tuple(extra.get("triggers", [])))
    return found


def catalog_text(skills: dict[str, Skill], short: bool = False) -> str:
    if not skills:
        return ""
    lines = ["\nSkills (load the full instructions with use_skill before starting a matching task):"]
    for s in skills.values():
        d = s.description
        if short:
            d = d.split(". ")[0][:110]
        lines.append(f"- {s.name}: {d}")
    return "\n".join(lines)
