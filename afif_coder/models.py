"""Model catalog, downloads and the Models window."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from .backends import PROVIDERS, BackendError, list_models, protect, unprotect
from .paths import bundled_models_dir, models_dir, total_ram_gb

HF = "https://huggingface.co"

# Local models. Sizes are the Q4 download in GB; "ram" is roughly what the PC needs.
# "sources" are Hugging Face paths tried in order; the file is saved under "file".
# "tools": "good" = reliable tool use, "fair" = works, with AFIF Coder's recovery for text-style calls.
CATALOG = [
    {"id": "qwen3-0.6b", "maker": "Qwen", "name": "Qwen3 0.6B (tiny)", "size": 0.48, "ram": 2, "tools": "fair",
     "about": "The lightest model: half a gigabyte, answers fast on any PC. Good for chat and tiny edits only.",
     "file": "Qwen3-0.6B-Q4_K_M.gguf",
     "sources": ["unsloth/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q4_K_M.gguf",
                 "bartowski/Qwen_Qwen3-0.6B-GGUF/Qwen_Qwen3-0.6B-Q4_K_M.gguf"]},
    {"id": "llama-3.2-1b", "maker": "Meta", "name": "Llama 3.2 1B", "size": 0.81, "ram": 3, "tools": "fair",
     "about": "Meta's smallest model. Very quick on a laptop processor; simple tasks.",
     "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Llama-3.2-1B-Instruct-GGUF/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                 "unsloth/Llama-3.2-1B-Instruct-GGUF/Llama-3.2-1B-Instruct-Q4_K_M.gguf"]},
    {"id": "gemma-3-1b", "maker": "Google", "name": "Gemma 3 1B", "size": 0.81, "ram": 3, "tools": "fair",
     "about": "Google's smallest open model. Quick, good at explaining; weak with tools.",
     "file": "gemma-3-1b-it-Q4_K_M.gguf",
     "sources": ["unsloth/gemma-3-1b-it-GGUF/gemma-3-1b-it-Q4_K_M.gguf",
                 "bartowski/google_gemma-3-1b-it-GGUF/google_gemma-3-1b-it-Q4_K_M.gguf"]},
    {"id": "qwen3-1.7b", "maker": "Qwen", "name": "Qwen3 1.7B (AFIF Lite)", "size": 1.1, "ram": 4, "tools": "good",
     "about": "Built into the installer. The best tool user among the light models; runs on a laptop processor.",
     "file": "Qwen3-1.7B-Q4_K_M.gguf",
     "sources": ["unsloth/Qwen3-1.7B-GGUF/Qwen3-1.7B-Q4_K_M.gguf",
                 "bartowski/Qwen_Qwen3-1.7B-GGUF/Qwen_Qwen3-1.7B-Q4_K_M.gguf"]},
    {"id": "llama-3.2-3b", "maker": "Meta", "name": "Llama 3.2 3B", "size": 2.0, "ram": 6, "tools": "good",
     "about": "Light, with dependable tool use. A good step up from the 1-2B models.",
     "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Llama-3.2-3B-Instruct-GGUF/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                 "unsloth/Llama-3.2-3B-Instruct-GGUF/Llama-3.2-3B-Instruct-Q4_K_M.gguf"]},
    {"id": "granite-4-micro", "maker": "IBM", "name": "Granite 4.0 Micro (3B)", "size": 2.1, "ram": 6, "tools": "good",
     "about": "IBM's small model made for tool calling and agents. Light and reliable.",
     "file": "granite-4.0-micro-Q4_K_M.gguf",
     "sources": ["unsloth/granite-4.0-micro-GGUF/granite-4.0-micro-Q4_K_M.gguf",
                 "ibm-granite/granite-4.0-micro-GGUF/granite-4.0-micro-Q4_K_M.gguf"]},
    {"id": "qwen3-4b", "maker": "Qwen", "name": "Qwen3 4B Instruct", "size": 2.5, "ram": 8, "tools": "good",
     "about": "The strongest light model: answers directly (no long thinking) and uses tools well.",
     "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
     "sources": ["unsloth/Qwen3-4B-Instruct-2507-GGUF/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
                 "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"]},
    {"id": "qwen2.5-coder-1.5b", "maker": "Qwen", "name": "Qwen2.5 Coder 1.5B", "size": 0.92, "ram": 4, "tools": "fair",
     "about": "Very light. Runs on almost any PC, but only handles simple tasks.",
     "file": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF/Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf"]},
    {"id": "qwen2.5-coder-3b", "maker": "Qwen", "name": "Qwen2.5 Coder 3B", "size": 1.8, "ram": 8, "tools": "fair",
     "about": "Light. Fine for small scripts and single-file fixes.",
     "file": "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Qwen2.5-Coder-3B-Instruct-GGUF/Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf"]},
    {"id": "gemma-3-4b", "maker": "Google", "name": "Gemma 3 4B", "size": 2.3, "ram": 8, "tools": "fair",
     "about": "Google's small open model, from the same research as Gemini. Good at writing and explaining.",
     "file": "gemma-3-4b-it-Q4_K_M.gguf",
     "sources": ["bartowski/google_gemma-3-4b-it-GGUF/google_gemma-3-4b-it-Q4_K_M.gguf",
                 "unsloth/gemma-3-4b-it-GGUF/gemma-3-4b-it-Q4_K_M.gguf"]},
    {"id": "qwen2.5-coder-7b", "maker": "Qwen", "name": "Qwen2.5 Coder 7B", "size": 4.4, "ram": 16, "tools": "fair",
     "about": "Good balance of speed and quality for everyday coding.",
     "file": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"]},
    {"id": "llama-3.1-8b", "maker": "Meta", "name": "Llama 3.1 8B", "size": 4.6, "ram": 16, "tools": "good",
     "about": "Meta's all-rounder with dependable tool use.",
     "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
                 "lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"]},
    {"id": "qwen3-8b", "maker": "Qwen", "name": "Qwen3 8B", "size": 4.7, "ram": 16, "tools": "good",
     "about": "Thinks before answering, and uses tools well. Slower per reply, smarter per step.",
     "file": "Qwen3-8B-Q4_K_M.gguf",
     "sources": ["unsloth/Qwen3-8B-GGUF/Qwen3-8B-Q4_K_M.gguf", "Qwen/Qwen3-8B-GGUF/Qwen3-8B-Q4_K_M.gguf"]},
    {"id": "deepseek-r1-qwen3-8b", "maker": "DeepSeek", "name": "DeepSeek R1 (8B)", "size": 4.7, "ram": 16, "tools": "fair",
     "about": "DeepSeek's reasoning model distilled to 8B. Strong step-by-step problem solving.",
     "file": "DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf",
     "sources": ["unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF/DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf",
                 "bartowski/deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-GGUF/deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf"]},
    {"id": "gemma-3-12b", "maker": "Google", "name": "Gemma 3 12B", "size": 6.8, "ram": 16, "tools": "fair",
     "about": "Google's mid-size open model. Clear explanations and solid general coding.",
     "file": "gemma-3-12b-it-Q4_K_M.gguf",
     "sources": ["bartowski/google_gemma-3-12b-it-GGUF/google_gemma-3-12b-it-Q4_K_M.gguf",
                 "unsloth/gemma-3-12b-it-GGUF/gemma-3-12b-it-Q4_K_M.gguf"]},
    {"id": "qwen2.5-coder-14b", "maker": "Qwen", "name": "Qwen2.5 Coder 14B", "size": 8.4, "ram": 24, "tools": "fair",
     "about": "Stronger reasoning across several files. Needs a good GPU or plenty of RAM.",
     "file": "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"]},
    {"id": "deepseek-coder-v2-lite", "maker": "DeepSeek", "name": "DeepSeek Coder V2 Lite", "size": 9.7, "ram": 16, "tools": "fair",
     "about": "DeepSeek's coding model. Mixture-of-experts: big knowledge, but fast because only part runs at once.",
     "file": "DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf",
     "sources": ["bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF/DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf"]},
    {"id": "gpt-oss-20b", "maker": "OpenAI", "name": "gpt-oss 20B", "size": 10.8, "ram": 16, "tools": "good",
     "about": "OpenAI's open model. Excellent tool use and reasoning; mixture-of-experts, so it's quick.",
     "file": "gpt-oss-20b-Q4_K_M.gguf",
     "sources": ["unsloth/gpt-oss-20b-GGUF/gpt-oss-20b-Q4_K_M.gguf", "ggml-org/gpt-oss-20b-GGUF/gpt-oss-20b-MXFP4.gguf"]},
    {"id": "devstral-small", "maker": "Mistral", "name": "Devstral Small 24B", "size": 13.3, "ram": 24, "tools": "good",
     "about": "Mistral's model built for coding agents like this one. Very good at multi-step edits.",
     "file": "Devstral-Small-2507-Q4_K_M.gguf",
     "sources": ["unsloth/Devstral-Small-2507-GGUF/Devstral-Small-2507-Q4_K_M.gguf",
                 "bartowski/mistralai_Devstral-Small-2507-GGUF/mistralai_Devstral-Small-2507-Q4_K_M.gguf"]},
    {"id": "qwen3-coder-30b", "maker": "Qwen", "name": "Qwen3 Coder 30B-A3B", "size": 17.3, "ram": 32, "tools": "good",
     "about": "The strongest local coder here. Mixture-of-experts, so it's quicker than its size suggests.",
     "file": "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
     "sources": ["unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
                 "lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"]},
]
RECOMMEND = ["qwen3-1.7b", "qwen3-4b", "qwen2.5-coder-7b"]   # by RAM, smallest first


def urls_of(m: dict) -> list[str]:
    out = []
    for src in m.get("sources") or [f"{m['repo']}/{m['file']}"]:
        repo, _, file = src.rpartition("/")
        out.append(f"{HF}/{repo}/resolve/main/{file}")
    return out


def url_of(m: dict) -> str:
    return urls_of(m)[0]


def parse_hf_link(url: str) -> dict | None:
    """Turn a Hugging Face file link into a download entry, or None if it isn't one."""
    import re
    m = re.match(r"https?://huggingface\.co/([^/]+/[^/]+)/(?:blob|resolve)/([^/]+)/(.+\.gguf)(?:\?.*)?$", url.strip())
    if not m:
        return None
    repo, rev, file = m.groups()
    name = Path(file).name
    return {"id": "hf:" + name, "maker": repo.split("/")[0], "name": Path(name).stem, "size": 0.0, "ram": 0,
            "tools": "fair", "about": f"From {repo}", "file": name, "url": f"{HF}/{repo}/resolve/{rev}/{file}"}


def installed_models(extra: list[str]) -> list[Path]:
    """Built-in model(s) first, then downloaded ones, then files the user added."""
    built = sorted(bundled_models_dir().glob("*.gguf"), key=lambda p: p.name.lower()) \
        if bundled_models_dir().is_dir() else []
    have = {p.name for p in built}
    found = built + [p for p in sorted(models_dir().glob("*.gguf"), key=lambda p: p.name.lower())
                     if p.name not in have]
    for e in extra:
        p = Path(e)
        if p.is_file() and p not in found:
            found.append(p)
    return found


def catalog_entry(p: Path) -> dict | None:
    return next((m for m in CATALOG if m["file"] == p.name), None)


def display_name(p: Path) -> str:
    for m in CATALOG:
        if m["file"] == p.name:
            return m["name"]
    return p.stem


def recommended() -> dict:
    ram = total_ram_gb()
    by_id = {m["id"]: m for m in CATALOG}
    best = by_id[RECOMMEND[0]]
    import os
    picks = RECOMMEND[:1] if (os.cpu_count() or 4) <= 4 else RECOMMEND   # 2-core laptops: keep it light
    for mid in picks:                  # never auto-recommend the very large ones
        if ram >= by_id[mid]["ram"] - 0.6:     # "16 GB" PCs report ~15.4
            best = by_id[mid]
    return best


class Downloader(QObject):
    # object, not int: Qt's int is 32-bit, and byte counts above 2 GB overflowed it (OverflowError every
    # progress update while downloading a large model, seen as hundreds of crashes in the report)
    progress = Signal(str, object, object, float)    # id, done bytes, total bytes, bytes/s
    done = Signal(str, str)                    # id, error ("" = ok)

    def __init__(self):
        super().__init__()
        self.cancel_flags: dict[str, threading.Event] = {}

    def active(self, mid: str) -> bool:
        return mid in self.cancel_flags

    def start(self, m: dict):
        if m["id"] in self.cancel_flags:
            return
        flag = threading.Event()
        self.cancel_flags[m["id"]] = flag
        threading.Thread(target=self._run, args=(m, flag), daemon=True).start()

    def cancel(self, mid: str):
        f = self.cancel_flags.get(mid)
        if f:
            f.set()

    def _run(self, m: dict, flag: threading.Event):
        dest = models_dir() / m["file"]
        part = dest.with_suffix(".gguf.part")
        err = ""
        try:
            need = max(m["size"], 0.5) * 1.05 * 1024 ** 3 - (part.stat().st_size if part.exists() else 0)
            if shutil.disk_usage(models_dir()).free < need:
                raise RuntimeError(f"Not enough free disk space. {m['name']} needs about {m['size']:.1f} GB.")
            for attempt in range(6):          # resume after network drops
                try:
                    self._fetch(m, part, flag)
                    break
                except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                    if flag.is_set() or attempt == 5:
                        raise
                    time.sleep(3 * (attempt + 1))
            if flag.is_set():
                err = "cancelled"
            else:
                with open(part, "rb") as fh:
                    if fh.read(4) != b"GGUF":
                        part.unlink(missing_ok=True)
                        raise RuntimeError("The downloaded file isn't a valid model. Try again.")
                part.replace(dest)
        except requests.ConnectionError:
            err = "No internet connection to Hugging Face. Check your connection and try again; the download resumes where it stopped."
        except Exception as e:
            err = str(e)
        self.cancel_flags.pop(m["id"], None)
        from . import diag
        diag.event("download", level="error" if err and err != "cancelled" else "info", model=m.get("name"),
                   file=m.get("file"), result=err or "done")
        self.done.emit(m["id"], err)

    def _fetch(self, m, part: Path, flag):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        for url in ([m["url"]] if m.get("url") else urls_of(m)):
            r = requests.get(url, headers=headers, stream=True, timeout=(10, 60))
            if r.status_code not in (401, 403, 404):
                break
            r.close()
        with r:
            if r.status_code == 416:          # already complete
                return
            if r.status_code not in (200, 206):
                raise RuntimeError(f"Download failed (HTTP {r.status_code}).")
            if r.status_code == 200:
                have = 0
            total = have + int(r.headers.get("Content-Length", 0))
            t0, mark, last = time.time(), have, 0.0
            with open(part, "ab" if have else "wb") as fh:
                for block in r.iter_content(1024 * 1024):
                    if flag.is_set():
                        return
                    fh.write(block)
                    have += len(block)
                    now = time.time()
                    if now - last > 0.3:
                        speed = (have - mark) / max(now - t0, 0.001)
                        self.progress.emit(m["id"], have, total, speed)
                        last = now


# ---------------------------------------------------------------- window
class ModelRow(QFrame):
    def __init__(self, m: dict, dlg: "ModelsDialog"):
        super().__init__()
        self.m, self.dlg = m, dlg
        self.setObjectName("tool")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel(m["name"])
        title.setObjectName("toolKind")
        tools = "reliable tool use" if m.get("tools") == "good" else "tool use: fair"
        meta = f"{m['maker']}, {m['size']:.1f} GB, needs about {m['ram']} GB RAM, {tools}" if m.get("size") \
            else m.get("about", "")
        self.meta = QLabel(meta)
        self.meta.setProperty("muted", True)
        self.btn = QPushButton()
        self.btn.clicked.connect(self._click)
        top.addWidget(title)
        top.addSpacing(8)
        top.addWidget(self.meta, 1)
        top.addWidget(self.btn)
        v.addLayout(top)
        about = QLabel(m["about"])
        about.setWordWrap(True)
        v.addWidget(about)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setVisible(False)
        self.info = QLabel()
        self.info.setProperty("muted", True)
        self.info.setVisible(False)
        v.addWidget(self.bar)
        v.addWidget(self.info)
        self.refresh()

    def path(self) -> Path:
        built_in = bundled_models_dir() / self.m["file"]
        return built_in if built_in.is_file() else models_dir() / self.m["file"]

    def built_in(self) -> bool:
        return self.path().parent == bundled_models_dir()

    def refresh(self):
        busy = self.dlg.dl.active(self.m["id"])
        if busy:
            self.btn.setText("Cancel")
            self.btn.setObjectName("danger")
        elif self.path().exists():
            self.btn.setText("Built in" if self.built_in() else "Delete")
            self.btn.setEnabled(not self.built_in())
            self.btn.setObjectName("flat")
            self.info.setText("Included with AFIF Coder: works with no download" if self.built_in() else "Installed")
            self.info.setVisible(True)
            self.bar.setVisible(False)
        else:
            part = self.path().with_suffix(".gguf.part")
            self.btn.setText("Resume download" if part.exists() else "Download")
            self.btn.setObjectName("primary" if self.m["id"] == recommended()["id"] else "")
            if self.m["id"] == recommended()["id"] and not part.exists():
                self.info.setText("Recommended for this PC")
                self.info.setVisible(True)
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)

    def _click(self):
        if self.dlg.dl.active(self.m["id"]):
            self.dlg.dl.cancel(self.m["id"])
        elif self.path().exists():
            if self.dlg.in_use(self.path()):
                QMessageBox.information(self, "Model in use", "Switch to another model before deleting this one.")
                return
            if QMessageBox.question(self, "Delete model", f"Delete {self.m['name']} "
                                    f"({self.m['size']:.1f} GB) from this PC?") == QMessageBox.Yes:
                self.path().unlink(missing_ok=True)
                self.dlg.changed.emit()
        else:
            self.dlg.dl.start(self.m)
            self.bar.setVisible(True)
            self.bar.setRange(0, 0)
            self.info.setText("Connecting…")
            self.info.setVisible(True)
        self.refresh()

    def on_progress(self, done: int, total: int, speed: float):
        self.bar.setVisible(True)
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
        left = (total - done) / speed if speed > 0 and total else 0
        eta = f", about {int(left // 60)} min {int(left % 60)} s left" if left else ""
        self.info.setText(f"{done / 1024 ** 3:.2f} of {total / 1024 ** 3:.2f} GB at {speed / 1024 ** 2:.1f} MB/s{eta}")
        self.info.setVisible(True)

    def on_done(self, err: str):
        self.bar.setVisible(False)
        if err == "cancelled":
            self.info.setText("Paused. Download again to resume.")
        elif err:
            self.info.setText(err)
        self.refresh()


class CloudCard(QFrame):
    """API key and status for one cloud provider."""

    def __init__(self, pid: str, dlg: "ModelsDialog"):
        super().__init__()
        self.pid, self.dlg = pid, dlg
        self.p = PROVIDERS[pid]
        self.cfg = dlg.s.setdefault("cloud", {}).setdefault(pid, {})
        self.setObjectName("tool")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel((self.p.label if not self.p.custom else "Other OpenAI-compatible service")
                       + ("  ·  FREE" if self.p.free else ""))
        title.setObjectName("toolKind")
        top.addWidget(title)
        top.addStretch(1)
        if self.p.key_url:
            link = QLabel(f'<a href="{self.p.key_url}">Get an API key</a>')
            link.setOpenExternalLinks(True)
            top.addWidget(link)
        v.addLayout(top)
        note = QLabel(self.p.note)
        note.setWordWrap(True)
        note.setProperty("muted", True)
        v.addWidget(note)
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        if self.p.custom:
            self.base = QLineEdit(self.cfg.get("base_url", ""))
            self.base.setPlaceholderText("https://openrouter.ai/api/v1")
            form.addRow("Address", self.base)
        self.key = QLineEdit(unprotect(self.cfg.get("key", "")))
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Optional for local servers" if self.p.custom else
                                    "Not needed: uses your GitHub sign-in" if self.p.keyless else "Paste your key")
        if not self.p.keyless:
            form.addRow("API key", self.key)
        if self.p.custom:
            self.names = QLineEdit(self.cfg.get("pinned", ""))
            self.names.setPlaceholderText("Model names, separated by commas (blank = list from the service)")
            form.addRow("Models", self.names)
            self.vision = QCheckBox("These models can see images")
            self.vision.setChecked(bool(self.cfg.get("vision", False)))
            form.addRow("", self.vision)
        v.addLayout(form)
        row = QHBoxLayout()
        self.status = QLabel(self._summary())
        self.status.setProperty("muted", True)
        self.status.setWordWrap(True)
        self.save = QPushButton("Check models" if self.p.keyless else "Save and connect")
        self.save.clicked.connect(self._save)
        self.remove = QPushButton("Remove")
        self.remove.setObjectName("flat")
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.status, 1)
        row.addWidget(self.remove)
        row.addWidget(self.save)
        v.addLayout(row)
        dlg.cloud_result.connect(self._result)

    def _summary(self) -> str:
        n = len(self.cfg.get("models") or [])
        if self.p.keyless:
            if not self.dlg.s.get("github_user"):
                return "Sign in to GitHub in Connections, then click Check models."
            return f"Uses GitHub sign-in {self.dlg.s['github_user']}; {n or len(self.p.defaults)} models."
        if self.cfg.get("key") or (self.p.custom and self.cfg.get("base_url")):
            return f"Connected, {n} model{'s' if n != 1 else ''} available." if n else "Saved."
        return "Not set up."

    def _save(self):
        if self.p.custom:
            self.cfg["base_url"] = self.base.text().strip().rstrip("/")
            self.cfg["pinned"] = self.names.text().strip()
            self.cfg["vision"] = self.vision.isChecked()
        self.cfg["key"] = protect(self.key.text().strip())
        if not self.cfg["key"] and not self.p.custom and not self.p.keyless:
            self.status.setText("Paste a key first.")
            return
        self.status.setText("Checking…")
        self.save.setEnabled(False)
        cfg = dict(self.cfg, key=self.key.text().strip())
        threading.Thread(target=self._check, args=(cfg,), daemon=True).start()

    def _check(self, cfg):
        try:
            models = list_models(self.pid, cfg)
            self.dlg.cloud_result.emit(self.pid, models, "")
        except BackendError as e:
            self.dlg.cloud_result.emit(self.pid, [], str(e))
        except Exception as e:
            self.dlg.cloud_result.emit(self.pid, [], f"{type(e).__name__}: {e}")

    def _result(self, pid, models, err):
        if pid != self.pid:
            return
        self.save.setEnabled(True)
        if err:
            self.status.setText(err)
            if not self.cfg.get("models"):
                self.cfg["models"] = list(self.p.defaults)
        else:
            self.cfg["models"] = models or list(self.p.defaults)
            self.status.setText(self._summary())
        self.dlg.changed.emit()

    def _remove(self):
        self.dlg.s["cloud"].pop(self.pid, None)
        self.cfg = self.dlg.s["cloud"].setdefault(self.pid, {})
        self.key.clear()
        self.status.setText("Not set up.")
        self.dlg.changed.emit()


class ModelsDialog(QDialog):
    changed = Signal()
    cloud_result = Signal(str, list, str)

    def __init__(self, dl: Downloader, settings: dict, in_use, parent=None, tab: int = 0):
        super().__init__(parent)
        self.dl, self.s, self.in_use = dl, settings, in_use
        self.setWindowTitle("Models")
        self.resize(700, 680)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        head = QLabel("Models")
        head.setObjectName("emptyTitle")
        v.addWidget(head)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        # --- on this PC
        local = QWidget()
        lv = QVBoxLayout(local)
        lv.setContentsMargins(0, 8, 0, 0)
        ram = total_ram_gb()
        sub = QLabel(f"These run on this PC ({ram:.0f} GB RAM detected): free, private and offline once "
                     f"downloaded. Saved in {models_dir()}")
        sub.setWordWrap(True)
        sub.setProperty("muted", True)
        lv.addWidget(sub)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget(objectName="root")
        self.col = QVBoxLayout(inner)
        self.col.setSpacing(8)
        self.rows = {}
        for m in CATALOG:
            self._add_row(m)
        self.col.addStretch(1)
        area.setWidget(inner)
        lv.addWidget(area, 1)
        foot = QHBoxLayout()
        add = QPushButton("Add a .gguf file from this PC…")
        add.clicked.connect(self._add_file)
        link = QPushButton("Download from a Hugging Face link…")
        link.clicked.connect(self._add_link)
        foot.addWidget(add)
        foot.addWidget(link)
        foot.addStretch(1)
        lv.addLayout(foot)
        self.tabs.addTab(local, "On this PC")

        # --- cloud
        cloud = QWidget()
        cv = QVBoxLayout(cloud)
        cv.setContentsMargins(0, 8, 0, 0)
        csub = QLabel("Cloud models run on the provider's servers with your own API key. The FREE ones at the top "
                      "cost nothing (each has daily limits); connect two or three and pick \"Free Auto\" in the model "
                      "list: it switches to the next one whenever one is busy or out of quota. Claude and DeepSeek "
                      "are billed per use. Your files only leave this PC when the model reads them. Keys are "
                      "stored encrypted for your Windows account.")
        csub.setWordWrap(True)
        csub.setProperty("muted", True)
        cv.addWidget(csub)
        carea = QScrollArea()
        carea.setWidgetResizable(True)
        cinner = QWidget(objectName="root")
        ccol = QVBoxLayout(cinner)
        ccol.setSpacing(8)
        for pid in sorted(PROVIDERS, key=lambda k: (not PROVIDERS[k].free, PROVIDERS[k].custom)):
            ccol.addWidget(CloudCard(pid, self))
        ccol.addStretch(1)
        carea.setWidget(cinner)
        cv.addWidget(carea, 1)
        self.tabs.addTab(cloud, "Cloud (Claude, Gemini, DeepSeek…)")
        self.tabs.setCurrentIndex(tab)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        v.addLayout(bottom)
        dl.progress.connect(self._prog)
        dl.done.connect(self._done)

    def _add_row(self, m: dict, at: int | None = None):
        r = ModelRow(m, self)
        self.rows[m["id"]] = r
        if at is None:
            self.col.addWidget(r)
        else:
            self.col.insertWidget(at, r)
        return r

    def _prog(self, mid, done, total, speed):
        if mid in self.rows:
            self.rows[mid].on_progress(done, total, speed)

    def _done(self, mid, err):
        if mid in self.rows:
            self.rows[mid].on_done(err)
        if not err:
            self.changed.emit()

    def _add_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Add model file", str(Path.home()), "GGUF model (*.gguf)")
        if f:
            extra = self.s.setdefault("extra_models", [])
            if f not in extra:
                extra.append(f)
            self.changed.emit()
            QMessageBox.information(self, "Model added", f"{Path(f).name} is now in the model list.")

    def _add_link(self):
        url, ok = QInputDialog.getText(self, "Download from Hugging Face",
                                       "Paste the link to a .gguf file on huggingface.co\n"
                                       "(open the file's page and copy the address):")
        if not ok or not url.strip():
            return
        m = parse_hf_link(url)
        if not m:
            QMessageBox.warning(self, "Not a model link", "That isn't a link to a .gguf file on huggingface.co. "
                                "It should look like huggingface.co/<owner>/<repo>/blob/main/<name>.gguf")
            return
        row = self.rows.get(m["id"]) or self._add_row(m, at=0)
        if not row.path().exists():
            row._click()
