"""Per-project state kept in <project>/.afif: shared progress notes, pinned skills, saved chats.

PROGRESS.md is meant to travel with the project (any model, any AI tool can read it);
chats and attachments stay private (git-ignored).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PROGRESS_TEMPLATE = """# Project progress

## Goal

## Status

## Decisions

## Repository and builds

## Next steps
"""


class Project:
    def __init__(self, root: str):
        self.root = Path(root)
        self.dir = self.root / ".afif"

    def _ensure(self):
        self.dir.mkdir(exist_ok=True)
        gi = self.dir / ".gitignore"
        if not gi.exists():
            gi.write_text("chats/\nattachments/\n", encoding="utf-8")

    # ---------- progress ----------
    @property
    def progress_file(self) -> Path:
        return self.dir / "PROGRESS.md"

    def progress(self) -> str:
        f = self.progress_file
        return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""

    def write_progress(self, text: str):
        self._ensure()
        self.progress_file.write_text(text.strip() + "\n", encoding="utf-8")

    # ---------- settings ----------
    def _cfg(self) -> dict:
        f = self.dir / "project.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cfg(self, cfg: dict):
        self._ensure()
        (self.dir / "project.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def pinned(self) -> list[str]:
        return list(self._cfg().get("skills", []))

    def set_pinned(self, names: list[str]):
        cfg = self._cfg()
        cfg["skills"] = names
        self._save_cfg(cfg)

    # ---------- chats ----------
    @property
    def chat_file(self) -> Path:
        return self.dir / "chats" / "current.json"

    def save_chat(self, messages: list, model: str, loaded: list):
        if len(messages) <= 1:
            return
        self._ensure()
        self.chat_file.parent.mkdir(exist_ok=True)
        data = {"saved": time.time(), "model": model, "loaded_skills": loaded, "messages": messages[1:]}
        tmp = self.chat_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.chat_file)

    def load_chat(self) -> dict | None:
        try:
            return json.loads(self.chat_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def archive_chat(self):
        if self.chat_file.is_file():
            self.chat_file.replace(self.chat_file.with_name(time.strftime("chat-%Y%m%d-%H%M%S.json")))
