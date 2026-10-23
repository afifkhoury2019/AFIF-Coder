"""Model catalog, downloads and the Models window."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from .paths import models_dir, total_ram_gb

HF = "https://huggingface.co"

# Coding models that support tool calling. Sizes are the Q4_K_M download in GB.
CATALOG = [
    {"id": "qwen2.5-coder-1.5b", "name": "Qwen2.5 Coder 1.5B", "size": 1.1, "ram": 4,
     "about": "Very light. Runs on almost any PC, but only handles simple tasks.",
     "file": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
     "repo": "bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF"},
    {"id": "qwen2.5-coder-3b", "name": "Qwen2.5 Coder 3B", "size": 1.9, "ram": 8,
     "about": "Light. Fine for small scripts and single-file fixes.",
     "file": "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
     "repo": "bartowski/Qwen2.5-Coder-3B-Instruct-GGUF"},
    {"id": "qwen2.5-coder-7b", "name": "Qwen2.5 Coder 7B", "size": 4.7, "ram": 16,
     "about": "Good balance of speed and quality for everyday coding.",
     "file": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
     "repo": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF"},
    {"id": "qwen2.5-coder-14b", "name": "Qwen2.5 Coder 14B", "size": 9.0, "ram": 24,
     "about": "Stronger reasoning across several files. Needs a good GPU or plenty of RAM.",
     "file": "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf",
     "repo": "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF"},
    {"id": "qwen3-coder-30b", "name": "Qwen3 Coder 30B-A3B", "size": 18.6, "ram": 32,
     "about": "The strongest option. Mixture-of-experts, so it's quicker than its size suggests.",
     "file": "Qwen_Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
     "repo": "bartowski/Qwen_Qwen3-Coder-30B-A3B-Instruct-GGUF"},
]


def url_of(m: dict) -> str:
    return f"{HF}/{m['repo']}/resolve/main/{m['file']}"


def installed_models(extra: list[str]) -> list[Path]:
    found = sorted(models_dir().glob("*.gguf"), key=lambda p: p.name.lower())
    for e in extra:
        p = Path(e)
        if p.is_file() and p not in found:
            found.append(p)
    return found


def display_name(p: Path) -> str:
    for m in CATALOG:
        if m["file"] == p.name:
            return m["name"]
    return p.stem


def recommended() -> dict:
    ram = total_ram_gb()
    best = CATALOG[0]
    for m in CATALOG[:3]:              # never auto-recommend the very large ones
        if ram >= m["ram"] - 0.6:     # "16 GB" PCs report ~15.4
            best = m
    return best


class Downloader(QObject):
    progress = Signal(str, int, int, float)    # id, done bytes, total bytes, bytes/s
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
            need = m["size"] * 1.05 * 1024 ** 3 - (part.stat().st_size if part.exists() else 0)
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
        self.done.emit(m["id"], err)

    def _fetch(self, m, part: Path, flag):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with requests.get(url_of(m), headers=headers, stream=True, timeout=(10, 60)) as r:
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
        self.meta = QLabel(f"{m['size']:.1f} GB download, needs about {m['ram']} GB RAM")
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
        return models_dir() / self.m["file"]

    def refresh(self):
        busy = self.dlg.dl.active(self.m["id"])
        if busy:
            self.btn.setText("Cancel")
            self.btn.setObjectName("danger")
        elif self.path().exists():
            self.btn.setText("Delete")
            self.btn.setObjectName("flat")
            self.info.setText("Installed")
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


class ModelsDialog(QDialog):
    changed = Signal()

    def __init__(self, dl: Downloader, settings: dict, in_use, parent=None):
        super().__init__(parent)
        self.dl, self.s, self.in_use = dl, settings, in_use
        self.setWindowTitle("Models")
        self.resize(640, 620)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        head = QLabel("Models")
        head.setObjectName("emptyTitle")
        ram = total_ram_gb()
        sub = QLabel(f"Models run on this PC ({ram:.0f} GB RAM detected). Download once; after that, "
                     "AFIF Coder works offline. Files are saved in " + str(models_dir()))
        sub.setWordWrap(True)
        sub.setProperty("muted", True)
        v.addWidget(head)
        v.addWidget(sub)
        v.addSpacing(6)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget(objectName="root")
        col = QVBoxLayout(inner)
        col.setSpacing(8)
        self.rows = {}
        for m in CATALOG:
            r = ModelRow(m, self)
            self.rows[m["id"]] = r
            col.addWidget(r)
        col.addStretch(1)
        area.setWidget(inner)
        v.addWidget(area, 1)
        foot = QHBoxLayout()
        add = QPushButton("Add a .gguf file from this PC…")
        add.clicked.connect(self._add_file)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot.addWidget(add)
        foot.addStretch(1)
        foot.addWidget(close)
        v.addLayout(foot)
        dl.progress.connect(self._prog)
        dl.done.connect(self._done)

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
