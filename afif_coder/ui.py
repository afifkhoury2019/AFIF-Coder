"""AFIF Coder main window."""
from __future__ import annotations

import difflib
import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import __version__
from .agent import Agent, extract_text_calls
from .attachments import Attachment, build_message, from_image_bytes, load as load_attachment
from .backends import PROVIDERS, BackendError, LocalBackend, RouterBackend, make_backend, unprotect
from .connections import ConnectionsDialog, SkillsDialog
from .report import ReportDialog
from .engine import Engine
from .models import Downloader, ModelsDialog, display_name, installed_models
from .paths import data_dir
from .theme import DARK, LIGHT, reply_css, stylesheet

KIND = {  # tool name -> label shown in the transcript
    "list_dir": "List", "read_file": "Read", "write_file": "Write", "edit_file": "Edit",
    "find_files": "Find", "search_text": "Search", "run_command": "Run", "web_search": "Web",
    "web_fetch": "Fetch", "view_image": "Look", "use_skill": "Skill", "read_skill_file": "Skill",
    "record_lesson": "Learn", "update_progress": "Progress", "drive_list": "Drive", "drive_search": "Drive",
    "drive_download": "Drive", "drive_upload": "Drive",
}

DEFAULTS = {
    "model": "", "num_ctx": 16384, "temperature": 0.2, "use_gpu": True,
    "max_steps": 40, "theme": "system", "project": "", "recent": [], "extra_models": [], "cloud": {},
}


def pretty_model(pid: str, model: str) -> str:
    """claude-sonnet-5-5 -> Claude Sonnet 5.5, gemini-3.8-flash -> Gemini 3.8 Flash."""
    if pid not in ("anthropic", "gemini", "deepseek"):
        return model.removesuffix(":free")
    words, out, i = model.split("-"), [], 0
    brand = {"claude": "Claude", "gemini": "Gemini", "deepseek": "DeepSeek"}
    while i < len(words):
        w = words[i]
        if w.isdigit() and len(w) == 8:                       # date suffix
            i += 1
            continue
        if w.isdigit() and i + 1 < len(words) and words[i + 1].isdigit() and len(words[i + 1]) <= 2:
            out.append(f"{w}.{words[i + 1]}")
            i += 2
            continue
        out.append(brand.get(w, w if any(c.isdigit() for c in w) else w.capitalize()))
        i += 1
    return " ".join(out)
MANAGE = "Manage models…"


# ---------------------------------------------------------------- settings
def config_dir() -> Path:
    return data_dir()


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        s.update(json.loads((config_dir() / "settings.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict):
    try:
        (config_dir() / "settings.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- transcript blocks
class UserBlock(QFrame):
    def __init__(self, text: str, files: list | None = None):
        super().__init__()
        self.setObjectName("userMsg")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 2, 0, 2)
        lay.setSpacing(2)
        who = QLabel("You")
        who.setObjectName("who")
        lay.addWidget(who)
        if text:
            body = QLabel(text)
            body.setObjectName("userText")
            body.setWordWrap(True)
            body.setTextFormat(Qt.PlainText)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(body)
        if files:
            att = QLabel("Attached: " + ", ".join(f"{a.name} ({a.note})" if a.note else a.name for a in files))
            att.setObjectName("muted")
            att.setWordWrap(True)
            lay.addWidget(att)


class ReplyBlock(QTextBrowser):
    """Assistant markdown that grows to fit its content."""

    def __init__(self, css: str):
        super().__init__()
        self.setObjectName("reply")
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().setDefaultStyleSheet(css)
        self.document().setDocumentMargin(2)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.raw = ""
        self.document().contentsChanged.connect(self._fit)

    def set_text(self, text: str):
        self.raw = text
        self.setMarkdown(text)
        self.setVisible(bool(text.strip()))

    def _fit(self):
        self.document().setTextWidth(self.viewport().width())
        self.setFixedHeight(int(self.document().size().height()) + 6)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


class ToolBlock(QFrame):
    decided = Signal(str, bool, bool)   # call_id, approved, always

    def __init__(self, call_id: str, name: str, summary: str):
        super().__init__()
        self.call_id = call_id
        self.setObjectName("tool")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        h = QHBoxLayout(head)
        h.setContentsMargins(10, 6, 8, 6)
        h.setSpacing(10)
        self.kind = QLabel(KIND.get(name) or (name.split("__")[0].upper() if "__" in name else name))
        if "__" in name:
            summary = name.split("__", 1)[1] + ("  " + summary if summary else "")
        self.kind.setObjectName("toolKind")
        self.summ = QLabel()
        self.summ.setObjectName("toolSummary")
        self.summ.setText(self._elide(summary))
        self.summ.setToolTip(summary)
        self.summ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.state = QLabel("running")
        self.state.setObjectName("toolState")
        self.state.setProperty("state", "run")
        self.toggle = QPushButton("Show output")
        self.toggle.setObjectName("flat")
        self.toggle.setVisible(False)
        self.toggle.clicked.connect(self._toggle)
        for w in (self.kind, self.summ, self.state, self.toggle):
            h.addWidget(w)
        lay.addWidget(head)

        self.preview = QPlainTextEdit()          # what the action will do (shown while asking)
        self.preview.setObjectName("toolOut")
        self.preview.setReadOnly(True)
        self.preview.setVisible(False)
        self.preview.setMaximumHeight(220)
        lay.addWidget(self.preview)

        self.ask = QWidget()
        self.ask.setObjectName("askBar")
        a = QHBoxLayout(self.ask)
        a.setContentsMargins(10, 6, 8, 6)
        q = QLabel("Allow this change?")
        allow = QPushButton("Allow")
        allow.setObjectName("primary")
        always = QPushButton("Allow all this session")
        deny = QPushButton("Deny")
        deny.setObjectName("danger")
        allow.clicked.connect(lambda: self._decide(True, False))
        always.clicked.connect(lambda: self._decide(True, True))
        deny.clicked.connect(lambda: self._decide(False, False))
        a.addWidget(q)
        a.addStretch(1)
        for b in (deny, always, allow):
            a.addWidget(b)
        self.ask.setVisible(False)
        lay.addWidget(self.ask)
        self._allow = allow

        self.out = QPlainTextEdit()
        self.out.setObjectName("toolOut")
        self.out.setReadOnly(True)
        self.out.setVisible(False)
        self.out.setMaximumHeight(260)
        lay.addWidget(self.out)

    @staticmethod
    def _elide(s: str, n: int = 110) -> str:
        s = s.replace("\n", " ⏎ ")
        return s if len(s) <= n else s[: n - 1] + "…"

    def _set_state(self, text: str, state: str):
        self.state.setText(text)
        self.state.setProperty("state", state)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def ask_approval(self, preview: str):
        self._set_state("needs approval", "ask")
        if preview:
            self.preview.setPlainText(preview)
            lh = self.preview.fontMetrics().lineSpacing()
            self.preview.setFixedHeight(min(220, (preview.count("\n") + 1) * lh + 20))
            self.preview.setVisible(True)
        self.ask.setVisible(True)
        self._allow.setFocus()

    def _decide(self, ok: bool, always: bool):
        self.ask.setVisible(False)
        self.preview.setVisible(False)
        self._set_state("running" if ok else "declined", "run" if ok else "bad")
        self.decided.emit(self.call_id, ok, always)

    def finish(self, ok: bool, output: str):
        self.ask.setVisible(False)
        self.preview.setVisible(False)
        self._set_state("done" if ok else ("declined" if output == "Declined" else "failed"), "ok" if ok else "bad")
        self.out.setPlainText(output)
        self.toggle.setVisible(bool(output))
        if self.out.isVisible() and ok:
            self._toggle()                     # collapse after a live run finishes
        if not ok and output != "Declined":
            self._toggle()   # show errors straight away

    def append_output(self, text: str):
        """Live output from a running command."""
        if not self.out.isVisible():
            self.out.setVisible(True)
            self.toggle.setVisible(True)
            self.toggle.setText("Hide output")
        self.out.moveCursor(self.out.textCursor().MoveOperation.End)
        self.out.insertPlainText(text)
        doc = self.out.document()
        if doc.characterCount() > 200_000:
            self.out.setPlainText(self.out.toPlainText()[-150_000:])
        self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())

    def _toggle(self):
        vis = not self.out.isVisible()
        self.out.setVisible(vis)
        self.toggle.setText("Hide output" if vis else "Show output")


# ---------------------------------------------------------------- settings dialog
class SettingsDialog(QDialog):
    def __init__(self, s: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        f = QFormLayout(self)
        self.gpu = QCheckBox("Use the graphics card when available (much faster)")
        self.gpu.setChecked(bool(s.get("use_gpu", True)))
        self.ctx = QSpinBox()
        self.ctx.setRange(2048, 262144)
        self.ctx.setSingleStep(4096)
        self.ctx.setValue(int(s["num_ctx"]))
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0, 2)
        self.temp.setSingleStep(0.1)
        self.temp.setValue(float(s["temperature"]))
        self.steps = QSpinBox()
        self.steps.setRange(5, 200)
        self.steps.setValue(int(s["max_steps"]))
        self.theme = QComboBox()
        self.theme.addItems(["system", "light", "dark"])
        self.theme.setCurrentText(s["theme"])
        f.addRow("Graphics card", self.gpu)
        f.addRow("Context length (tokens)", self.ctx)
        f.addRow("Temperature", self.temp)
        f.addRow("Max tool steps per message", self.steps)
        f.addRow("Theme", self.theme)
        hint = QLabel("A larger context lets the model hold more files, but uses more memory. "
                      f"Changes to the graphics card or context reload the model.\n\nAFIF Coder {__version__} by AFIF-TECH. "
                      "Inference by the built-in AFIF Engine (based on llama.cpp, MIT licence).")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        f.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def values(self) -> dict:
        return {"use_gpu": self.gpu.isChecked(),
                "num_ctx": self.ctx.value(), "temperature": self.temp.value(),
                "max_steps": self.steps.value(), "theme": self.theme.currentText()}


# ---------------------------------------------------------------- composer
class Composer(QPlainTextEdit):
    submit = Signal()
    files_added = Signal(list)          # local file paths
    image_pasted = Signal(bytes)        # PNG bytes

    def canInsertFromMimeData(self, md):
        return md.hasImage() or md.hasUrls() or super().canInsertFromMimeData(md)

    def insertFromMimeData(self, md):
        if md.hasImage():
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            img = md.imageData()
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            self.image_pasted.emit(bytes(ba))
            return
        files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        if files:
            self.files_added.emit(files)
            return
        super().insertFromMimeData(md)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.s = load_settings()
        self.engine = Engine()
        self.dl = Downloader()
        self.agent = Agent(self.s, self._backend)
        self.busy = False
        self.sel = ""                     # "local:<path>" or "cloud:<provider>:<model>"
        self.pending: list[Attachment] = []
        self.last_usage = (0, 0)
        self.last_timing = (0.0, 0.0)
        self.think = None
        self.think_buf, self.think_t0 = "", 0.0
        self.setAcceptDrops(True)
        self.reply: ReplyBlock | None = None
        self.reply_buf = ""
        self.tools: dict[str, ToolBlock] = {}
        self.setWindowTitle("AFIF Coder")
        self.resize(1040, 760)
        self.setMinimumSize(640, 480)

        self._build()
        self._wire()
        self.apply_theme()
        self.flush = QTimer(self, interval=70, timeout=self._flush_reply)
        self.engine.state.connect(self._on_engine)
        self.dl.done.connect(lambda mid, err: self.refresh_models() if not err else None)
        self._on_engine("off", "")
        self.refresh_models(start=True)
        if self.s["project"] and Path(self.s["project"]).is_dir():
            self.open_project(self.s["project"])
        else:
            self.stack.setCurrentIndex(0)
        if not installed_models(self.s["extra_models"]) and not self._cloud_choices():
            QTimer.singleShot(400, self.open_models)    # first run: get a model

    # ---------- layout ----------
    def _build(self):
        root = QWidget(objectName="root")
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # header
        hdr = QFrame(objectName="header")
        h = QHBoxLayout(hdr)
        h.setContentsMargins(16, 8, 12, 8)
        h.setSpacing(8)
        brand = QLabel("AFIF Coder", objectName="brand")
        sub = QLabel("by AFIF-TECH", objectName="brandSub")
        self.brand_sub = sub
        self.project_btn = QPushButton("Open folder…", objectName="project")
        self.project_btn.setToolTip("Change project folder")
        self.model = QComboBox()
        self.model.setMinimumWidth(150)
        self.model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(16)
        self.model.setToolTip("Model running on this PC")
        self.dot = QLabel("●", objectName="statusDot")
        self.conn = QLabel("", objectName="muted")
        self.new_btn = QPushButton("New chat")
        self.skills_btn = QPushButton("Skills", objectName="flat")
        self.conn_btn = QPushButton("Connections", objectName="flat")
        self.report_btn = QPushButton("Report", objectName="flat")
        self.report_btn.setToolTip("Debug report: timings and errors")
        self.set_btn = QPushButton("Settings", objectName="flat")
        h.addWidget(brand)
        h.addWidget(sub)
        h.addSpacing(14)
        h.addWidget(self.project_btn)
        h.addStretch(1)
        h.addWidget(self.dot)
        h.addWidget(self.conn)
        h.addWidget(self.model)
        h.addWidget(self.new_btn)
        h.addWidget(self.skills_btn)
        h.addWidget(self.conn_btn)
        h.addWidget(self.report_btn)
        h.addWidget(self.set_btn)
        v.addWidget(hdr)

        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        # empty state
        empty = QWidget(objectName="empty")
        e = QVBoxLayout(empty)
        e.addStretch(2)
        t = QLabel("Open a project folder to start", objectName="emptyTitle")
        d = QLabel("AFIF Coder reads, edits and runs code inside one folder, using a model on this "
                   "computer. Nothing leaves your machine and there's no usage limit.")
        d.setWordWrap(True)
        d.setMaximumWidth(520)
        d.setProperty("muted", True)
        ob = QPushButton("Open folder", objectName="primary")
        ob.clicked.connect(self.choose_project)
        for w in (t, d):
            e.addWidget(w, 0, Qt.AlignHCenter)
        e.addSpacing(12)
        e.addWidget(ob, 0, Qt.AlignHCenter)
        e.addStretch(3)
        self.stack.addWidget(empty)

        # work view
        work = QWidget()
        w = QVBoxLayout(work)
        w.setContentsMargins(0, 0, 0, 0)
        w.setSpacing(0)
        self.scroll = QScrollArea(objectName="transcript")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="root")
        outer = QHBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)
        col = QWidget()
        col.setMaximumWidth(860)
        self.log = QVBoxLayout(col)
        self.log.setContentsMargins(24, 20, 24, 20)
        self.log.setSpacing(12)
        self.log.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(col, 100)
        outer.addStretch(1)
        self.scroll.setWidget(holder)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._stick_bottom)
        w.addWidget(self.scroll, 1)

        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bcol = QWidget()
        bcol.setMaximumWidth(860)
        b = QVBoxLayout(bcol)
        b.setContentsMargins(24, 4, 24, 14)
        b.setSpacing(6)
        comp = QFrame(objectName="composer")
        cv = QVBoxLayout(comp)
        cv.setContentsMargins(10, 8, 8, 8)
        cv.setSpacing(4)
        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 0)
        self.chips_lay.setSpacing(6)
        self.chips.setVisible(False)
        cv.addWidget(self.chips)
        self.input = Composer(objectName="input")
        self.input.setPlaceholderText("Describe what to build or fix…  (Enter to send, Shift+Enter for a new line)")
        self.input.setFixedHeight(64)
        cv.addWidget(self.input)
        row = QHBoxLayout()
        self.attach = QPushButton("Attach files", objectName="flat")
        self.attach.setToolTip("Attach files, or drag them onto the window, or paste an image")
        self.send = QPushButton("Send", objectName="primary")
        self.send.setFixedWidth(84)
        row.addWidget(self.attach)
        row.addStretch(1)
        row.addWidget(self.send)
        cv.addLayout(row)
        b.addWidget(comp)
        foot = QHBoxLayout()
        self.auto = QCheckBox("Allow edits and commands without asking")
        self.status = QLabel("", objectName="muted")
        foot.addWidget(self.auto)
        foot.addSpacing(12)
        self.speed = QComboBox()
        self.speed.addItems(["Fast", "Balanced", "Thorough"])
        self.speed.setCurrentIndex({"fast": 0, "balanced": 1, "thorough": 2}.get(self.s.get("speed", "balanced"), 1))
        self.speed.setToolTip("Fast: models think less and answer sooner (good for chat and small edits).\n"
                              "Balanced: the default for building.\nThorough: deeper thinking, slower and costlier.")
        foot.addWidget(QLabel("Speed", objectName="muted"))
        foot.addWidget(self.speed)
        foot.addStretch(1)
        foot.addWidget(self.status)
        b.addLayout(foot)
        bl.addStretch(1)
        bl.addWidget(bcol, 100)
        bl.addStretch(1)
        w.addWidget(bottom)
        self.stack.addWidget(work)

        self.setCentralWidget(root)

    def _wire(self):
        self.project_btn.clicked.connect(self._project_menu)
        self.model.currentTextChanged.connect(self._model_changed)
        self.new_btn.clicked.connect(self.new_chat)
        self.set_btn.clicked.connect(self.open_settings)
        self.send.clicked.connect(self._send_or_stop)
        self.input.submit.connect(self._send_or_stop)
        self.input.files_added.connect(self.add_files)
        self.input.image_pasted.connect(lambda png: self._add_attachment(from_image_bytes(png)))
        self.attach.clicked.connect(self._pick_files)
        self.auto.toggled.connect(lambda on: setattr(self.agent, "auto_approve", on))
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_chat)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_project)
        QShortcut(QKeySequence("Esc"), self, activated=lambda: self.busy and self.agent.stop())

        a = self.agent
        a.turn_started.connect(self._on_turn)
        a.text_delta.connect(self._on_delta)
        a.tool_started.connect(self._on_tool_start)
        a.tool_finished.connect(self._on_tool_done)
        a.tool_progress.connect(lambda cid, t: self.tools.get(cid) and self.tools[cid].append_output(t))
        a.skill_loaded.connect(self._on_skill)
        self.skills_btn.clicked.connect(self.open_skills)
        self.conn_btn.clicked.connect(self.open_connections)
        self.report_btn.clicked.connect(self.open_report)
        a.approval_needed.connect(self._on_approval)
        a.status.connect(self.status.setText)
        a.usage.connect(lambda i, o: setattr(self, "last_usage", (i, o)))
        a.thinking_delta.connect(self._on_think)
        a.timing.connect(lambda first, tps: setattr(self, "last_timing", (first, tps)))
        a.progress.connect(self._on_progress)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        a.finished.connect(self._on_finished)

    # ---------- theme ----------
    def tokens(self) -> dict:
        mode = self.s.get("theme", "system")
        if mode == "system":
            try:
                dark = QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            except AttributeError:
                dark = False
            mode = "dark" if dark else "light"
        return DARK if mode == "dark" else LIGHT

    def apply_theme(self):
        t = self.tokens()
        QApplication.instance().setStyleSheet(stylesheet(t))
        self.css = reply_css(t)

    # ---------- project ----------
    def _project_menu(self):
        m = QMenu(self)
        m.addAction("Open folder…", self.choose_project)
        recent = [p for p in self.s.get("recent", []) if Path(p).is_dir() and p != self.s["project"]]
        if recent:
            m.addSeparator()
            for p in recent[:8]:
                m.addAction(p, lambda p=p: self.open_project(p))
        m.exec(self.project_btn.mapToGlobal(self.project_btn.rect().bottomLeft()))

    def choose_project(self):
        if self.busy:
            return
        d = QFileDialog.getExistingDirectory(self, "Open project folder", self.s["project"] or str(Path.home()))
        if d:
            self.open_project(d)

    def open_project(self, path: str):
        if self.busy:
            return
        path = str(Path(path).resolve())
        self.s["project"] = path
        self.s["recent"] = [path] + [p for p in self.s.get("recent", []) if p != path][:9]
        save_settings(self.s)
        self.project_btn.setText(Path(path).name or path)
        self.project_btn.setToolTip(path)
        self.stack.setCurrentIndex(1)
        self.agent.reset(path)
        self._clear_log()
        saved = self.agent.project.load_chat()
        if saved and saved.get("messages"):
            self._redraw(self.agent.resume(saved))
            n = sum(1 for m in saved["messages"] if m["role"] == "user" and not str(m.get("content", "")).startswith("[AFIF"))
            self.status.setText(f"Continuing your last chat in this project ({n} message{'s' * (n != 1)}). "
                                "New chat starts fresh; progress notes carry over.")
            self._warm_up()
        else:
            self._after_reset()

    def new_chat(self):
        if self.busy or not self.s["project"]:
            return
        self.agent.new_chat()
        self._clear_log()
        self._after_reset()

    def _clear_log(self):
        while self.log.count() > 1:
            item = self.log.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self.tools.clear()
        self.reply = None

    def _after_reset(self):
        self._warm_up()
        bits = []
        if (Path(self.s["project"]) / "CODER.md").is_file():
            bits.append("CODER.md loaded")
        if self.agent.project and self.agent.project.progress().strip():
            bits.append("progress notes loaded")
        pinned = self.agent.project.pinned() if self.agent.project else []
        if pinned:
            bits.append("pinned: " + ", ".join(pinned))
        self.status.setText((" · ".join(bits) + " · " if bits else "") + f"Working in {self.s['project']}")
        self.input.setFocus()

    def _redraw(self, messages: list):
        """Show a saved conversation compactly."""
        results = {m["tool_call_id"]: m for m in messages if m["role"] == "tool"}
        for m in messages:
            c = m.get("content")
            if m["role"] == "user":
                text = c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if p.get("type") == "text")
                if text.startswith("[AFIF Coder loaded the skill"):
                    name = text.split('"')[1] if '"' in text else "skill"
                    self._on_skill(name, "matched")
                elif not text.startswith("You described a change but did not call"):
                    self._add(UserBlock(text[:4000]))
            elif m["role"] == "assistant":
                if c and c.strip():
                    rb = ReplyBlock(self.css)
                    rb.set_text(c)
                    self._add(rb)
                for call in m.get("tool_calls") or []:
                    fn = call["function"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    blk = ToolBlock(call["id"], fn["name"], self.agent.toolbox.summary(fn["name"], args)
                                    if self.agent.toolbox else "")
                    res = results.get(call["id"])
                    out = (res or {}).get("content", "")
                    blk.finish(bool(res) and not str(out).startswith(("Error", "Not run", "The user declined")), out)
                    self._add(blk)
        sep = QLabel("Earlier in this chat (saved). Continue below.")
        sep.setObjectName("muted")
        self._add(sep)

    def _on_skill(self, name: str, how: str):
        why = {"matched": "matches your request", "pinned": "pinned to this project",
               "requested": "loaded by the model",
               "suggested": "suggested; the model loads it when it starts that work"}.get(how, how)
        lbl = QLabel(f"Skill: {name} ({why})")
        lbl.setObjectName("muted")
        self._add(lbl)

    def open_skills(self):
        if self.busy:
            return
        SkillsDialog(self.agent.project, self).exec()
        if self.s.get("project"):
            msgs = self.agent.messages[1:]
            self.agent.reset(self.s["project"])          # pick up pins, new skills and lessons
            self.agent.messages += msgs
            self._after_reset()

    def open_report(self):
        ReportDialog(self.s, self).exec()

    def open_connections(self):
        dlg = ConnectionsDialog(self.s, self)
        dlg.open_project.connect(self.open_project)
        dlg.claude_code.connect(self.start_claude_code)
        dlg.exec()
        save_settings(self.s)
        if self.s.get("project") and not self.busy:
            self.agent.messages[0]["content"] = self.agent.system_prompt()

    # ---------- Claude Code on the AFIF Engine ----------
    cc_line = Signal(str)
    cc_done = Signal(bool, str)

    def start_claude_code(self):
        from . import claude_code as cc
        from .engine import SPEED
        if not self.s.get("project"):
            QMessageBox.information(self, "Claude Code", "Open a project folder first; Claude Code works in it.")
            return
        if not self.sel.startswith("local:"):
            QMessageBox.information(self, "Claude Code", "Pick a model on this PC at the top first. Claude Code "
                                    "runs on it through the AFIF Engine (Qwen3 4B or bigger works best).")
            return
        if self.engine.current != "ready":
            QMessageBox.information(self, "Claude Code", "Wait until the model has finished loading, then try again.")
            return
        if (self.engine.ctx_effective or 0) < cc.MIN_CONTEXT:
            if QMessageBox.question(self, "Claude Code needs more memory", "Claude Code needs a context of at least "
                                    f"{cc.MIN_CONTEXT:,} tokens (now {self.engine.ctx_effective:,}). Reload the model "
                                    "with a 64K context? It uses more RAM.") != QMessageBox.Yes:
                return
            self.s["num_ctx"] = 65536
            save_settings(self.s)
            self._cc_after_load = True
            self._load(self.sel[6:], force=True)
            self.status.setText("Reloading the model with a larger context for Claude Code…")
            return
        mins = cc.first_reply_minutes(SPEED.get("prompt_tps") or 0)
        if mins > 3 and QMessageBox.question(
                self, "Claude Code will be slow here", f"On this PC the first answer will take about {mins:.0f} "
                "minutes (Claude Code sends ~20,000 tokens of instructions; later answers are quicker because "
                "they're cached). For quick work use AFIF Coder's own chat, which sends ~2,000. Start anyway?") \
                != QMessageBox.Yes:
            return
        if cc.installed():
            self._cc_launch()
            return
        self.status.setText("Downloading Claude Code from npm (first time only)…")
        if not getattr(self, "_cc_wired", False):
            self._cc_wired = True
            self.cc_line.connect(lambda t: self.status.setText("Installing Claude Code: " + t[-120:]))
            self.cc_done.connect(lambda ok, msg: self._cc_launch() if ok else
                                 QMessageBox.warning(self, "Claude Code", msg))
        threading.Thread(target=lambda: self.cc_done.emit(*cc.install(self.cc_line.emit)), daemon=True).start()

    def _cc_launch(self):
        from . import claude_code as cc
        from . import diag
        try:
            cc.launch(self.s["project"], self.engine.url)
            diag.event("claude_code", level="info", model=Path(self.engine.model).stem, context=self.engine.ctx_effective)
            self.status.setText("Claude Code started in its own window, running free on this PC's model. Keep "
                                "AFIF Coder open while you use it.")
        except Exception as e:
            diag.error("claude_code", e)
            QMessageBox.warning(self, "Claude Code", str(e))

    # ---------- models ----------
    def _connected(self, pid: str) -> bool:
        p = PROVIDERS.get(pid)
        cfg = (self.s.get("cloud") or {}).get(pid) or {}
        if not p:
            return False
        if p.keyless:
            return bool(self.s.get("github_user")) and cfg.get("enabled", True)
        return bool(cfg.get("key") or (p.custom and cfg.get("base_url")))

    def _models_of(self, pid: str) -> list[str]:
        p = PROVIDERS[pid]
        cfg = (self.s.get("cloud") or {}).get(pid) or {}
        if p.custom and cfg.get("pinned"):
            return [n.strip() for n in cfg["pinned"].split(",") if n.strip()]
        names = cfg.get("models") or list(p.defaults)
        if pid == "gemini":                    # hide special models saved by older versions
            from .backends import gemini_chat_model, gemini_rank
            names = sorted([n for n in names if gemini_chat_model(n)], key=gemini_rank) or list(p.defaults)
        from .backends import chat_model
        return [n for n in names if chat_model(n)] or list(p.defaults)

    def _cloud_choices(self) -> list[tuple[str, str, bool]]:
        out = []
        for pid in PROVIDERS:
            if not self._connected(pid):
                continue
            p = PROVIDERS[pid]
            for m in self._models_of(pid)[:60]:
                out.append((f"{pretty_model(pid, m)}  ·  {p.label if not p.custom else 'cloud'}", f"cloud:{pid}:{m}",
                            p.free))
        return out

    # Free Auto: which model of each free service to use, best first
    FREE_PICKS = {
        "gemini": lambda ids: [i for i in ids if "flash" in i and "lite" not in i][:2] + [i for i in ids if "lite" in i][:1],
        "groq": lambda ids: [i for i in ("openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b",
                                         "llama-3.3-70b-versatile") if i in ids][:2] or ids[:1],
        "cerebras": lambda ids: [i for i in ids if "coder" in i or "gpt-oss" in i][:2] or ids[:1],
        "mistral": lambda ids: [i for i in ids if "devstral" in i][:1] + [i for i in ids if "medium" in i][:1] or ids[:1],
        "github": lambda ids: [i for i in ("openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/gpt-4o") if i in ids][:2]
        or ids[:1],
        "openrouter": lambda ids: sorted(ids, key=lambda i: (not any(k in i for k in ("coder", "gpt-oss", "deepseek",
                                                                                         "qwen3", "kimi"))))[:3],
    }

    def _free_candidates(self) -> list:
        cands = []
        for pid in ("gemini", "groq", "cerebras", "mistral", "github", "openrouter"):
            if not self._connected(pid):
                continue
            for m in self.FREE_PICKS[pid](self._models_of(pid)):
                def factory(pid=pid, m=m):
                    cfg = dict((self.s.get("cloud") or {}).get(pid) or {})
                    cfg["key"] = unprotect(cfg.get("key", ""))
                    return make_backend(pid, m, cfg)
                cands.append((f"{pretty_model(pid, m)} ({PROVIDERS[pid].label})", factory))
        local = self._best_local()
        if local:
            cands.append((f"{display_name(local)} (this PC)", lambda: self._local_on_demand(local)))
        return cands

    def _best_local(self):
        paths = installed_models(self.s["extra_models"])
        if self.engine.model and Path(self.engine.model).is_file():
            return Path(self.engine.model)
        return min(paths, key=lambda p: p.stat().st_size) if paths else None

    def _local_on_demand(self, path):
        """Free Auto's last resort: load a model on this PC (runs on the agent's thread)."""
        if not (self.engine.current == "ready" and Path(self.engine.model) == Path(path)):
            self.engine.start(str(path), int(self.s["num_ctx"]), bool(self.s["use_gpu"]))
            self.engine.model = str(path)
            t0 = time.time()
            while self.engine.current != "ready" and time.time() - t0 < 300:
                if self.engine.current == "failed":
                    raise BackendError("The model on this PC couldn't be loaded.")
                time.sleep(0.3)
            if self.engine.current != "ready":
                raise BackendError("The model on this PC took too long to load.")
        return LocalBackend(self.engine.url, self.engine.ctx_effective or int(self.s.get("num_ctx", 16384)))

    def refresh_models(self, start: bool = False):
        paths = installed_models(self.s["extra_models"])
        clouds = self._cloud_choices()
        want = self.s.get("model", "")
        if want and not want.startswith(("local:", "cloud:", "auto:")):
            want = "local:" + want                       # settings from 1.0
        self.model.blockSignals(True)
        self.model.clear()

        def header(text):
            self.model.addItem(text, None)
            self.model.model().item(self.model.count() - 1).setEnabled(False)

        free = [c for c in clouds if c[2]]
        paid = [c for c in clouds if not c[2]]
        if free:
            header("Free Auto: switches between your free models")
            self.model.addItem("   Free Auto (recommended)", "auto:free")
        if paths:
            header("On this PC: free, offline")
            for p in paths:
                self.model.addItem("   " + display_name(p), "local:" + str(p))
        if free:
            header("Free cloud: daily limits")
            for label, data, _ in free:
                self.model.addItem("   " + label, data)
        if paid:
            header("Cloud: billed to your API key")
            for label, data, _ in paid:
                self.model.addItem("   " + label, data)
        self.model.addItem(MANAGE, "manage")
        choices = [self.model.itemData(i) for i in range(self.model.count())]
        choices = [d for d in choices if d and d != "manage"]
        if want not in choices:
            want = choices[0] if choices else ""
        if want:
            self.model.setCurrentIndex(self.model.findData(want))
        else:
            self.model.setCurrentIndex(self.model.count() - 1)
        self.model.blockSignals(False)
        if want:
            self._select(want, force=start)
        else:
            self.sel = ""
            self.engine.stop()
            self.s["model"] = ""
            self._on_engine("off", "")

    def _model_changed(self, _name: str):
        data = self.model.currentData()
        if data == "manage":
            self.refresh_models()
            self.open_models()
            return
        if data:
            self._select(data)

    def _select(self, data: str, force: bool = False):
        self.s["model"] = data
        save_settings(self.s)
        if data.startswith("local:"):
            self.sel = data
            self._load(data[6:], force)
            return
        if self.sel == data and not force:
            return
        self.sel = data
        self.engine.stop()                     # free the PC's memory while using the cloud
        if data == "auto:free":
            names = [n for n, _ in self._free_candidates()]
            self._set_dot("true", "Free Auto", "Uses, in order: " + ", ".join(names))
            self.status.setText("Free Auto: uses your free models one after another and switches when one is "
                                "busy or out of quota. First choice: " + (names[0] if names else "none"))
            return
        _, pid, model = data.split(":", 2)
        p = PROVIDERS[pid]
        self._set_dot("true", "Cloud", f"{pretty_model(pid, model)} via {p.label}. {p.note}")
        self.status.setText(f"Using {pretty_model(pid, model)} in the cloud. " + (
            f"{p.label} free tier: {p.note}" if p.free else f"Messages are billed to your {p.label} key."))

    def _load(self, path: str, force: bool = False):
        if not force and self.engine.model == path and self.engine.current in ("loading", "ready"):
            return
        self.engine.model = path
        self.engine.start(path, int(self.s["num_ctx"]), bool(self.s["use_gpu"]))

    def _set_dot(self, on: str, text: str, tip: str):
        self.dot.setProperty("on", on)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)
        self.conn.setText(text)
        self.conn.setToolTip(tip)
        self.dot.setToolTip(tip)

    def _on_engine(self, state: str, detail: str):
        if self.sel.startswith(("cloud:", "auto:")):
            return                             # the engine is idle while a cloud model is chosen
        colours = {"ready": "true", "loading": "wait", "failed": "false", "off": "false"}
        where = " (GPU)" if detail.endswith("on GPU") else (" (CPU)" if detail.endswith("on CPU") else "")
        text = {"ready": "Ready" + where, "loading": "Loading model…", "failed": "Engine error",
                "off": "No model loaded"}[state]
        self._set_dot(colours[state], text, detail)
        if state == "failed":
            self.engine.model = ""
            self.status.setText(detail)
        elif state == "ready" and getattr(self, "_cc_after_load", False):
            self._cc_after_load = False
            QTimer.singleShot(200, self.start_claude_code)
        if state == "ready":
            self.status.setText(f"Model ready: {detail}" + (
                ". It's running on the processor, so replies are slower than on a graphics card; "
                "the Fast speed and smaller models help." if detail.endswith("on CPU") else ""))
            self._warm_up()
        elif state == "loading":
            self.status.setText(detail)

    def _backend(self):
        """Called by the agent (worker thread) at the start of each message."""
        sel = self.sel
        if sel == "auto:free":
            cands = self._free_candidates()
            if not cands:
                raise BackendError("Connect a free service in Models → Cloud first (Gemini, Groq, GitHub…).")
            return RouterBackend(cands)
        if sel.startswith("cloud:"):
            _, pid, model = sel.split(":", 2)
            cfg = dict((self.s.get("cloud") or {}).get(pid) or {})
            cfg["key"] = unprotect(cfg.get("key", ""))
            return make_backend(pid, model, cfg)
        if self.engine.current != "ready":
            raise BackendError("The local model isn't loaded yet.")
        return LocalBackend(self.engine.url, self.engine.ctx_effective or int(self.s.get("num_ctx", 16384)))

    def _sees_images(self) -> bool:
        if not self.sel.startswith("cloud:"):
            return False
        pid = self.sel.split(":", 2)[1]
        cfg = (self.s.get("cloud") or {}).get(pid) or {}
        return bool(cfg.get("vision", PROVIDERS[pid].vision))

    def open_models(self, tab: int = 0):
        dlg = ModelsDialog(self.dl, self.s, lambda p: self.sel == "local:" + str(p), self, tab=tab)
        dlg.changed.connect(lambda: (save_settings(self.s), self.refresh_models()))
        dlg.exec()
        save_settings(self.s)
        self.refresh_models()

    def open_settings(self):
        old = (self.s["use_gpu"], self.s["num_ctx"])
        dlg = SettingsDialog(self.s, self)
        if dlg.exec():
            self.s.update(dlg.values())
            save_settings(self.s)
            self.apply_theme()
            if old != (self.s["use_gpu"], self.s["num_ctx"]) and self.sel.startswith("local:"):
                self._load(self.sel[6:], force=True)

    # ---------- attachments ----------
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Attach files", self.s.get("project") or str(Path.home()),
                                                "All files (*);;Documents (*.pdf *.docx *.xlsx *.txt *.md *.csv);;"
                                                "Images (*.png *.jpg *.jpeg *.gif *.webp)")
        self.add_files(files)

    def add_files(self, files: list):
        for f in files:
            if Path(f).is_file():
                self._add_attachment(load_attachment(f))
            elif Path(f).is_dir():
                self.status.setText(f"{Path(f).name} is a folder. To work on a folder, open it as the project.")

    def _add_attachment(self, a: Attachment):
        if len(self.pending) >= 12:
            self.status.setText("Up to 12 files per message.")
            return
        self.pending.append(a)
        self._render_chips()
        if a.kind == "error":
            self.status.setText(f"{a.name}: {a.note}")
        elif a.kind == "image" and not self._sees_images():
            self.status.setText("This model can't see images. Pick a cloud model like Claude or Gemini to send pictures.")

    def _render_chips(self):
        while self.chips_lay.count():
            w = self.chips_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for a in self.pending:
            chip = QPushButton(f"{a.name}  ×", objectName="chip")
            chip.setToolTip(f"{a.note}. Click to remove." if a.note else "Click to remove.")
            chip.clicked.connect(lambda _=False, a=a: (self.pending.remove(a), self._render_chips()))
            self.chips_lay.addWidget(chip)
        self.chips_lay.addStretch(1)
        self.chips.setVisible(bool(self.pending))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.stack.currentIndex() == 1:
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ---------- sending ----------
    def _send_or_stop(self):
        if self.busy:
            self.agent.stop()
            self.status.setText("Stopping…")
            return
        text = self.input.toPlainText().strip()
        if not text and not self.pending:
            return
        if not self.sel:
            self.status.setText("Choose a model first (Manage models…).")
            self.open_models()
            return
        if self.sel.startswith("local:") and self.engine.current != "ready":
            self.status.setText({"loading": "The model is still loading. Try again in a moment.",
                                 "failed": "The engine couldn't start. " + self.conn.toolTip()}.get(
                self.engine.current, "The local model isn't loaded."))
            return
        files, self.pending = self.pending, []
        self._render_chips()
        content = build_message(text, files, self.s["project"])
        self.input.clear()
        self._add(UserBlock(text, files))
        self.last_usage = (0, 0)
        self.last_timing = (0.0, 0.0)
        self._set_busy(True)
        if any(a.kind == "image" for a in files) and not self._sees_images():
            self.status.setText("Sent without the images: this model can't see pictures.")
        threading.Thread(target=self.agent.run, args=(content,), daemon=True).start()

    def _set_busy(self, on: bool):
        self.busy = on
        self.send.setText("Stop" if on else "Send")
        self.send.setObjectName("danger" if on else "primary")
        self.send.style().unpolish(self.send)
        self.send.style().polish(self.send)
        for w in (self.model, self.new_btn, self.project_btn, self.set_btn, self.attach, self.skills_btn, self.conn_btn):
            w.setEnabled(not on)

    def _add(self, w: QWidget):
        self.log.addWidget(w)

    def _stick_bottom(self, _min, mx):
        self.scroll.verticalScrollBar().setValue(mx)

    # ---------- agent signals ----------
    def _on_turn(self):
        self._flush_reply()
        self._end_think()
        self.reply, self.reply_buf = None, ""

    def _on_progress(self, done: int, total: int):
        if total > 0 and self.busy:
            pct = min(100, int(done * 100 / total))
            self.status.setText(f"Reading your message… {pct}% ({done:,} of {total:,} tokens)")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self.width()
        self.brand_sub.setVisible(w >= 1000)
        self.conn_btn.setText("Connections" if w >= 900 else "Connect")
        self.report_btn.setVisible(w >= 860)
        self.project_btn.setMaximumWidth(220 if w >= 1000 else 120)

    def _on_think(self, text: str):
        if self.think is None:
            self.think = QLabel(objectName="muted")
            self.think.setWordWrap(True)
            self.think_buf, self.think_t0 = "", time.time()
            self._add(self.think)
        self.think_buf += text
        if not self.flush.isActive():
            self.flush.start()

    def _end_think(self):
        if self.think is not None:
            secs = time.time() - self.think_t0
            self.think.setText(f"Thought for {secs:.0f} s")
            self.think.setToolTip(self.think_buf[-4000:])
            self.think = None

    def _speed_changed(self, i: int):
        self.s["speed"] = ("fast", "balanced", "thorough")[i]
        save_settings(self.s)
        self._warm_up()

    def _warm_up(self):
        """Local model: pre-read instructions and tools in the background so replies start sooner."""
        if self.busy or not self.sel.startswith("local:") or self.engine.current != "ready" \
                or not self.s.get("project") or self.agent.toolbox is None:
            return
        backend = LocalBackend(self.engine.url, self.engine.ctx_effective or int(self.s.get("num_ctx", 16384)))
        threading.Thread(target=self.agent.warm_up, args=(backend,), daemon=True).start()

    def _on_delta(self, text: str):
        self._end_think()
        if self.reply is None:
            self.reply = ReplyBlock(self.css)
            self._add(self.reply)
        self.reply_buf += text
        self.flush.setInterval(70 if len(self.reply_buf) < 4000 else 220)   # long replies: redraw less often
        if not self.flush.isActive():
            self.flush.start()

    def _flush_reply(self):
        self.flush.stop()
        if self.think is not None:
            tail = " ".join(self.think_buf.split())[-260:]
            self.think.setText(f"Thinking… {tail}")
        if self.reply is not None:
            buf = self.reply_buf
            shown = extract_text_calls(buf)[1] if ('"name"' in buf or "<tool_call>" in buf) else buf
            if "<tool_call>" in shown:                    # still streaming a text-style call
                shown = shown[: shown.index("<tool_call>")]
            if self.reply.raw != shown:
                self.reply.set_text(shown)

    def _on_tool_start(self, call_id: str, name: str, summary: str):
        self._flush_reply()
        self._end_think()
        self.reply, self.reply_buf = None, ""   # text after a tool goes in a new block
        blk = ToolBlock(call_id, name, summary)
        blk.decided.connect(lambda cid, ok, always: self._decided(ok, always))
        self.tools[call_id] = blk
        self._add(blk)

    def _on_approval(self, call_id: str, name: str, args: dict):
        blk = self.tools.get(call_id)
        if blk:
            blk.ask_approval(self._preview(name, args))
            QApplication.alert(self)

    def _decided(self, ok: bool, always: bool):
        if always:
            self.auto.setChecked(True)
        self.agent.answer_approval(ok, always)

    def _preview(self, name: str, args: dict) -> str:
        if name == "run_command":
            return "> " + args.get("command", "")
        if name == "write_file":
            return args.get("content", "")[:6000]
        if name == "edit_file":
            old = (args.get("old_text") or "").splitlines()
            new = (args.get("new_text") or "").splitlines()
            return "\n".join(difflib.unified_diff(old, new, "before", "after", lineterm=""))
        return ""

    def _on_tool_done(self, call_id: str, ok: bool, output: str):
        blk = self.tools.get(call_id)
        if blk:
            blk.finish(ok, output)

    def _on_finished(self, err: str):
        self._flush_reply()
        self._end_think()
        self._set_busy(False)
        tin, tout = self.last_usage
        used = f"  ·  {tin:,} tokens in, {tout:,} out" if (tin or tout) else ""
        first, tps = self.last_timing
        speed = (f"  ·  first words after {first:.1f} s" if first else "") + (f", {tps:.0f} words/s" if tps else "")
        self.status.setText((err or "Done") + speed + used)
        if err and err != "Stopped.":
            note = ReplyBlock(self.css)
            note.setOpenLinks(False)
            note.anchorClicked.connect(lambda url: self.open_report() if url.scheme() == "afif"
                                       else QDesktopServices.openUrl(url))
            note.set_text(f"**Couldn't finish.** {err}  [Details](afif:report)")
            self._add(note)
        self.input.setFocus()

    def closeEvent(self, e):
        if self.busy:
            self.agent.stop()
        self.agent.save()
        self.agent.mcp.stop_all()
        for mid in list(self.dl.cancel_flags):
            self.dl.cancel(mid)            # partial downloads resume next time
        self.engine.stop()
        save_settings(self.s)
        super().closeEvent(e)
