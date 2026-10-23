"""AFIF Coder main window."""
from __future__ import annotations

import difflib
import json
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import __version__
from .agent import Agent, extract_text_calls
from .engine import Engine
from .models import Downloader, ModelsDialog, display_name, installed_models
from .paths import data_dir
from .theme import DARK, LIGHT, reply_css, stylesheet

KIND = {  # tool name -> label shown in the transcript
    "list_dir": "List", "read_file": "Read", "write_file": "Write", "edit_file": "Edit",
    "find_files": "Find", "search_text": "Search", "run_command": "Run",
}

DEFAULTS = {
    "model": "", "num_ctx": 16384, "temperature": 0.2, "use_gpu": True,
    "max_steps": 40, "theme": "system", "project": "", "recent": [], "extra_models": [],
}
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
    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("userMsg")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 2, 0, 2)
        lay.setSpacing(2)
        who = QLabel("You")
        who.setObjectName("who")
        body = QLabel(text)
        body.setObjectName("userText")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(who)
        lay.addWidget(body)


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
        self.kind = QLabel(KIND.get(name, name))
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
        if not ok and output != "Declined":
            self._toggle()   # show errors straight away

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
        self.agent = Agent(self.s, lambda: self.engine.url)
        self.busy = False
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
        if not installed_models(self.s["extra_models"]):
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
        self.project_btn = QPushButton("Open folder…", objectName="project")
        self.project_btn.setToolTip("Change project folder")
        self.model = QComboBox()
        self.model.setMinimumWidth(220)
        self.model.setToolTip("Model running on this PC")
        self.dot = QLabel("●", objectName="statusDot")
        self.conn = QLabel("", objectName="muted")
        self.new_btn = QPushButton("New chat")
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
        c = QHBoxLayout(comp)
        c.setContentsMargins(10, 8, 8, 8)
        self.input = Composer(objectName="input")
        self.input.setPlaceholderText("Describe what to build or fix…  (Enter to send, Shift+Enter for a new line)")
        self.input.setFixedHeight(68)
        self.send = QPushButton("Send", objectName="primary")
        self.send.setFixedWidth(84)
        c.addWidget(self.input, 1)
        c.addWidget(self.send, 0, Qt.AlignBottom)
        b.addWidget(comp)
        foot = QHBoxLayout()
        self.auto = QCheckBox("Allow edits and commands without asking")
        self.status = QLabel("", objectName="muted")
        foot.addWidget(self.auto)
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
        self.auto.toggled.connect(lambda on: setattr(self.agent, "auto_approve", on))
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_chat)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_project)
        QShortcut(QKeySequence("Esc"), self, activated=lambda: self.busy and self.agent.stop())

        a = self.agent
        a.turn_started.connect(self._on_turn)
        a.text_delta.connect(self._on_delta)
        a.tool_started.connect(self._on_tool_start)
        a.tool_finished.connect(self._on_tool_done)
        a.approval_needed.connect(self._on_approval)
        a.status.connect(self.status.setText)
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
        self.new_chat()

    def new_chat(self):
        if self.busy or not self.s["project"]:
            return
        self.agent.reset(self.s["project"])
        while self.log.count() > 1:
            item = self.log.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self.tools.clear()
        self.reply = None
        hint = "CODER.md found and loaded. " if (Path(self.s["project"]) / "CODER.md").is_file() else ""
        self.status.setText(hint + f"Working in {self.s['project']}")
        self.input.setFocus()

    # ---------- models ----------
    def refresh_models(self, start: bool = False):
        paths = installed_models(self.s["extra_models"])
        want = self.s.get("model", "")
        self.model.blockSignals(True)
        self.model.clear()
        for p in paths:
            self.model.addItem(display_name(p), str(p))
        self.model.addItem(MANAGE, "")
        strs = [str(p) for p in paths]
        if want not in strs and strs:
            want = strs[-1] if len(strs) == 1 else strs[0]
        if want in strs:
            self.model.setCurrentIndex(strs.index(want))
        self.model.blockSignals(False)
        if want in strs and (start or want != self.engine.model or self.engine.current in ("off", "failed")):
            self._load(want)
        elif not strs:
            self.engine.stop()
            self.s["model"] = ""

    def _model_changed(self, _name: str):
        path = self.model.currentData()
        if not path:                      # "Manage models…"
            self.refresh_models()
            self.open_models()
            return
        self._load(path)

    def _load(self, path: str):
        self.s["model"] = path
        save_settings(self.s)
        if self.engine.model == path and self.engine.current in ("loading", "ready"):
            return
        self.engine.model = path
        self.engine.start(path, int(self.s["num_ctx"]), bool(self.s["use_gpu"]))

    def _on_engine(self, state: str, detail: str):
        colours = {"ready": "true", "loading": "wait", "failed": "false", "off": "false"}
        self.dot.setProperty("on", colours[state])
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)
        text = {"ready": "Ready", "loading": "Loading model…", "failed": "Engine error",
                "off": "No model loaded"}[state]
        self.conn.setText(text)
        self.conn.setToolTip(detail)
        self.dot.setToolTip(detail)
        if state == "failed":
            self.engine.model = ""
            self.status.setText(detail)
        elif state == "ready":
            self.status.setText(f"Model ready: {detail}")
        elif state == "loading":
            self.status.setText(detail)

    def open_models(self):
        dlg = ModelsDialog(self.dl, self.s, lambda p: str(p) == self.engine.model, self)
        dlg.changed.connect(lambda: (save_settings(self.s), self.refresh_models()))
        dlg.exec()
        self.refresh_models()

    def open_settings(self):
        old = (self.s["use_gpu"], self.s["num_ctx"])
        dlg = SettingsDialog(self.s, self)
        if dlg.exec():
            self.s.update(dlg.values())
            save_settings(self.s)
            self.apply_theme()
            if old != (self.s["use_gpu"], self.s["num_ctx"]) and self.s.get("model"):
                self.engine.model = ""
                self._load(self.s["model"])

    # ---------- sending ----------
    def _send_or_stop(self):
        if self.busy:
            self.agent.stop()
            self.status.setText("Stopping…")
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self.engine.current != "ready":
            self.status.setText({"loading": "The model is still loading. Try again in a moment.",
                                 "failed": "The engine couldn't start. " + self.conn.toolTip()}.get(
                self.engine.current, "Download or choose a model first (Manage models…)."))
            if self.engine.current == "off":
                self.open_models()
            return
        self.input.clear()
        self._add(UserBlock(text))
        self._set_busy(True)
        threading.Thread(target=self.agent.run, args=(text,), daemon=True).start()

    def _set_busy(self, on: bool):
        self.busy = on
        self.send.setText("Stop" if on else "Send")
        self.send.setObjectName("danger" if on else "primary")
        self.send.style().unpolish(self.send)
        self.send.style().polish(self.send)
        for w in (self.model, self.new_btn, self.project_btn, self.set_btn):
            w.setEnabled(not on)

    def _add(self, w: QWidget):
        self.log.addWidget(w)

    def _stick_bottom(self, _min, mx):
        self.scroll.verticalScrollBar().setValue(mx)

    # ---------- agent signals ----------
    def _on_turn(self):
        self._flush_reply()
        self.reply, self.reply_buf = None, ""

    def _on_delta(self, text: str):
        if self.reply is None:
            self.reply = ReplyBlock(self.css)
            self._add(self.reply)
        self.reply_buf += text
        if not self.flush.isActive():
            self.flush.start()

    def _flush_reply(self):
        self.flush.stop()
        if self.reply is not None:
            shown = extract_text_calls(self.reply_buf)[1]
            if "<tool_call>" in shown:                    # still streaming a text-style call
                shown = shown[: shown.index("<tool_call>")]
            if self.reply.raw != shown:
                self.reply.set_text(shown)

    def _on_tool_start(self, call_id: str, name: str, summary: str):
        self._flush_reply()
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
        self._set_busy(False)
        self.status.setText(err or "Done")
        if err and err != "Stopped.":
            note = ReplyBlock(self.css)
            note.set_text(f"**Couldn't finish.** {err}")
            self._add(note)
        self.input.setFocus()

    def closeEvent(self, e):
        if self.busy:
            self.agent.stop()
        for mid in list(self.dl.cancel_flags):
            self.dl.cancel(mid)            # partial downloads resume next time
        self.engine.stop()
        save_settings(self.s)
        super().closeEvent(e)
