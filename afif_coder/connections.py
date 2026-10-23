"""Connections (GitHub, Google Drive, REA, bundled tools) and Skills windows."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from . import skills as skillmod
from . import tooling
from .tools import DRIVE, drive_connected


def _clean_env() -> dict:
    e = tooling.env()
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
        e.pop(k, None)
    return e


def _popen(args: list[str]) -> subprocess.Popen:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    exe = tooling.which(args[0]) or args[0]
    return subprocess.Popen([exe, *args[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=_clean_env(), creationflags=flags, text=True, encoding="utf-8", errors="replace")


class Worker(QObject):
    """Runs sign-in and other slow jobs off the UI thread."""
    line = Signal(str, str)        # job, text
    done = Signal(str, bool, str)  # job, ok, message


def github_user() -> str:
    code, out = tooling.run(["gh", "api", "user", "--jq", ".login"], timeout=20)
    return out.strip() if code == 0 and re.fullmatch(r"[A-Za-z0-9-]+", out.strip() or "-") else ""


class Card(QFrame):
    def __init__(self, title: str, about: str):
        super().__init__()
        self.setObjectName("tool")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(14, 12, 14, 12)
        self.v.setSpacing(6)
        top = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("toolKind")
        self.state = QLabel("")
        self.state.setProperty("muted", True)
        top.addWidget(t)
        top.addStretch(1)
        top.addWidget(self.state)
        self.v.addLayout(top)
        a = QLabel(about)
        a.setWordWrap(True)
        a.setProperty("muted", True)
        self.v.addWidget(a)
        self.row = QHBoxLayout()
        self.row.addStretch(1)
        self.v.addLayout(self.row)

    def button(self, text: str, fn, primary=False) -> QPushButton:
        b = QPushButton(text)
        if primary:
            b.setObjectName("primary")
        b.clicked.connect(fn)
        self.row.addWidget(b)
        return b


class ConnectionsDialog(QDialog):
    open_project = Signal(str)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.s = settings
        self.w = Worker()
        self.w.line.connect(self._line)
        self.w.done.connect(self._done)
        self.setWindowTitle("Connections")
        self.resize(680, 720)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        head = QLabel("Connections")
        head.setObjectName("emptyTitle")
        v.addWidget(head)
        sub = QLabel("Sign in once; every model in AFIF Coder can then use GitHub and Google Drive for you. "
                     "Sign-ins happen in your browser, and AFIF Coder never sees your passwords.")
        sub.setWordWrap(True)
        sub.setProperty("muted", True)
        v.addWidget(sub)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget(objectName="root")
        col = QVBoxLayout(inner)
        col.setSpacing(10)

        self.gh = Card("GitHub", "Clone, create and push repositories, and follow GitHub Actions builds. "
                       "Uses the official GitHub CLI's browser sign-in.")
        self.gh_in = self.gh.button("Sign in to GitHub", self._gh_login, primary=True)
        self.gh_clone = self.gh.button("Open a repository…", self._gh_clone)
        self.gh_out = self.gh.button("Sign out", self._gh_logout)
        col.addWidget(self.gh)

        self.dr = Card("Google Drive", "Let models list, download and upload your Drive files (brand kits, assets, "
                       "builds). Uses rclone's Google sign-in; you approve access in your browser.")
        self.dr_in = self.dr.button("Connect Google Drive", self._drive_login, primary=True)
        self.dr_out = self.dr.button("Disconnect", self._drive_logout)
        col.addWidget(self.dr)

        self.rea = Card("REA (Reverse Engineer Anything)", "Evidence-based analysis of your own APKs, Windows "
                        "programs and Electron apps. Connects automatically when a model loads the REA skill.")
        self.rea.button("Test REA", self._rea_test)
        col.addWidget(self.rea)

        tools = Card("Bundled tools", "Installed with AFIF Coder and available to every model's commands.")
        self.tool_list = QLabel()
        self.tool_list.setWordWrap(True)
        self.tool_list.setTextFormat(Qt.RichText)
        tools.v.insertWidget(2, self.tool_list)
        col.addWidget(tools)

        web = Card("Web search", "Models search with DuckDuckGo by default. For steadier results, add a free "
                   "Brave Search API key (optional).")
        self.brave = QLineEdit(self.s.get("brave_key", ""))
        self.brave.setEchoMode(QLineEdit.Password)
        self.brave.setPlaceholderText("Brave Search API key (optional)")
        self.brave.editingFinished.connect(lambda: self.s.__setitem__("brave_key", self.brave.text().strip()))
        web.v.insertWidget(2, self.brave)
        col.addWidget(web)

        col.addStretch(1)
        area.setWidget(inner)
        v.addWidget(area, 1)
        self.log = QPlainTextEdit()
        self.log.setObjectName("toolOut")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setVisible(False)
        v.addWidget(self.log)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        v.addLayout(bottom)
        self.refresh()

    # ---------- state ----------
    def refresh(self):
        have_gh = bool(tooling.which("gh"))
        user = self.s.get("github_user", "")
        self.gh.state.setText(f"Signed in as {user}" if user else ("Not signed in" if have_gh else "GitHub CLI not installed"))
        self.gh_in.setVisible(have_gh and not user)
        self.gh_clone.setVisible(bool(user))
        self.gh_out.setVisible(bool(user))
        have_rc = bool(tooling.which("rclone"))
        con = drive_connected()
        self.dr.state.setText("Connected" if con else ("Not connected" if have_rc else "rclone not installed"))
        self.dr_in.setVisible(have_rc and not con)
        self.dr_out.setVisible(con)
        self.rea.state.setText("Installed" if tooling.rea_launcher() else "Not installed")
        rows = []
        for name, use, ok in tooling.inventory():
            mark = "✓" if ok else "–"
            rows.append(f"{mark} <b>{name}</b>: {use}{'' if ok else ' (not installed)'}")
        self.tool_list.setText("<br>".join(rows))

    def _line(self, job, text):
        self.log.setVisible(True)
        self.log.appendPlainText(text.rstrip())

    def _done(self, job, ok, msg):
        from . import diag
        diag.event("connection", level="info" if ok else "error", job=job, ok=ok, message=msg[:600],
                   log=self.log.toPlainText()[-2000:])
        self.log.setVisible(True)
        self.log.appendPlainText(msg)
        for b in (self.gh_in, self.dr_in):
            b.setEnabled(True)
        if job == "clone" and ok:
            self.open_project.emit(msg.split("\n")[-1])
            self.accept()
            return
        self.refresh()
        if not ok:
            QMessageBox.warning(self, "Didn't work", msg)

    # ---------- GitHub ----------
    def _gh_login(self):
        self.gh_in.setEnabled(False)
        self.log.clear()
        threading.Thread(target=self._gh_login_job, daemon=True).start()

    def _gh_login_job(self):
        p = _popen(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web",
                    "--scopes", "repo,workflow,read:org,delete_repo"])
        opened = False
        for line in p.stdout:
            self.w.line.emit("gh", line)
            m = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", line)
            if m and not opened:
                opened = True
                self.w.line.emit("gh", f"Your one-time code is {m.group(1)} (copied). Paste it on the GitHub page "
                                       "that just opened, then approve.")
                self._copy(m.group(1))
                webbrowser.open("https://github.com/login/device")
                try:
                    p.stdin.write("\n")
                    p.stdin.flush()
                except OSError:
                    pass
        code = p.wait()
        if code != 0:
            self.w.done.emit("gh", False, "GitHub sign-in didn't finish. Try again.")
            return
        tooling.run(["gh", "auth", "setup-git"], timeout=30)
        user = github_user()
        self.s["github_user"] = user
        _ensure_git_identity(user)
        self.w.done.emit("gh", True, f"Signed in to GitHub as {user}. Git pushes now use this account.")

    def _copy(self, text):
        from PySide6.QtCore import QMetaObject, Q_ARG
        cb = QGuiApplication.clipboard()
        QMetaObject.invokeMethod(cb, "setText", Qt.QueuedConnection, Q_ARG(str, text))

    def _gh_logout(self):
        tooling.run(["gh", "auth", "logout", "--hostname", "github.com"], timeout=30, input="Y\n")
        self.s["github_user"] = ""
        self.refresh()

    def _gh_clone(self):
        code, out = tooling.run(["gh", "repo", "list", "--limit", "200", "--json",
                                 "nameWithOwner,description,isPrivate,updatedAt"], timeout=60)
        try:
            repos = json.loads(out) if code == 0 else []
        except ValueError:
            repos = []
        dlg = RepoPicker(repos, self)
        if not dlg.exec() or not dlg.choice:
            return
        name = dlg.choice
        base = self.s.get("projects_dir") or str(Path.home() / "AFIF-Projects")
        parent = QFileDialog.getExistingDirectory(self, "Where should the repository go?", base)
        if not parent:
            return
        self.s["projects_dir"] = parent
        dest = str(Path(parent) / name.split("/")[-1])
        if Path(dest).exists():
            self.open_project.emit(dest)
            self.accept()
            return
        self.log.clear()

        def job():
            p = _popen(["gh", "repo", "clone", name, dest])
            for line in p.stdout:
                self.w.line.emit("clone", line)
            ok = p.wait() == 0
            self.w.done.emit("clone", ok, f"Cloned {name} into\n{dest}" if ok else f"Couldn't clone {name}.")
        threading.Thread(target=job, daemon=True).start()

    # ---------- Google Drive ----------
    def _drive_login(self):
        self.dr_in.setEnabled(False)
        self.log.clear()
        self._line("drive", "Opening Google sign-in in your browser. Choose your account and allow access…")
        threading.Thread(target=self._drive_job, daemon=True).start()

    def _drive_job(self):
        p = _popen(["rclone", "authorize", "drive"])
        out = []
        for line in p.stdout:
            out.append(line)
            if "http" in line and "auth" in line:
                self.w.line.emit("drive", line)
        p.wait()
        text = "".join(out)
        m = re.search(r"--->\s*(\{.*?\})\s*<---", text, re.S) or re.search(r"(\{\"access_token\".*?\})", text, re.S)
        if p.returncode != 0 or not m:
            self.w.done.emit("drive", False, "Google Drive sign-in didn't finish. Try again.\n" + text[-400:])
            return
        token = m.group(1).strip()
        code, msg = tooling.run(["rclone", "config", "create", DRIVE, "drive", "scope=drive", f"token={token}",
                                 "--non-interactive"], timeout=60)
        if code != 0:
            self.w.done.emit("drive", False, "Couldn't save the Drive connection: " + msg[-400:])
            return
        code, about = tooling.run(["rclone", "about", f"{DRIVE}:", "--json"], timeout=60)
        self.w.done.emit("drive", code == 0, "Google Drive connected." if code == 0 else about[-400:])

    def _drive_logout(self):
        tooling.run(["rclone", "config", "delete", DRIVE], timeout=30)
        self.refresh()

    # ---------- REA ----------
    def _rea_test(self):
        self.log.clear()
        self._line("rea", "Starting REA…")

        def job():
            from .mcp import MCPError, MCPServer
            cmd = tooling.rea_launcher()
            if not cmd:
                self.w.done.emit("rea", False, "REA isn't installed. Reinstall AFIF Coder with the REA toolkit.")
                return
            try:
                srv = MCPServer("rea", cmd, {"REA_MCP_INPUT_SCHEMA_PROFILE": "compact"}).start()
                n = len(srv.tools)
                android = [t["name"] for t in srv.tools if "android" in t["name"]]
                srv.stop()
                self.w.done.emit("rea", True, f"REA is working: {n} tools, including {len(android)} for Android "
                                              f"(JADX {'found' if tooling.jadx_jar() else 'missing'}).")
            except MCPError as e:
                self.w.done.emit("rea", False, str(e))
        threading.Thread(target=job, daemon=True).start()


def _ensure_git_identity(user: str):
    code, name = tooling.run(["git", "config", "--global", "user.name"], timeout=10)
    if not name.strip() and user:
        tooling.run(["git", "config", "--global", "user.name", user], timeout=10)
    code, mail = tooling.run(["git", "config", "--global", "user.email"], timeout=10)
    if not mail.strip() and user:
        c, uid = tooling.run(["gh", "api", "user", "--jq", ".id"], timeout=20)
        if c == 0 and uid.strip().isdigit():
            tooling.run(["git", "config", "--global", "user.email",
                         f"{uid.strip()}+{user}@users.noreply.github.com"], timeout=10)


class RepoPicker(QDialog):
    def __init__(self, repos: list, parent=None):
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Open a repository")
        self.resize(520, 520)
        v = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter, or type owner/name of any repository")
        v.addWidget(self.filter)
        self.list = QListWidget()
        for r in repos:
            it = QListWidgetItem(f"{r['nameWithOwner']}{'  (private)' if r.get('isPrivate') else ''}"
                                 + (f"\n    {r['description']}" if r.get("description") else ""))
            it.setData(Qt.UserRole, r["nameWithOwner"])
            self.list.addItem(it)
        v.addWidget(self.list, 1)
        self.filter.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda it: self._pick(it.data(Qt.UserRole)))
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Open", objectName="primary")
        ok.clicked.connect(self._ok)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

    def _filter(self, text):
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    def _ok(self):
        it = self.list.currentItem()
        if it and not it.isHidden():
            self._pick(it.data(Qt.UserRole))
        elif re.fullmatch(r"[\w.-]+/[\w.-]+", self.filter.text().strip()):
            self._pick(self.filter.text().strip())

    def _pick(self, name):
        self.choice = name
        self.accept()


class SkillsDialog(QDialog):
    """See skills, pin them to the current project, review what models have learned."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Skills")
        self.resize(720, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        head = QLabel("Skills")
        head.setObjectName("emptyTitle")
        v.addWidget(head)
        sub = QLabel("Every model gets these. A skill loads automatically when your message matches it; pinned skills "
                     "are given to every chat in this project. Lessons are what models (and you) recorded while using "
                     "a skill, shared by all models.")
        sub.setWordWrap(True)
        sub.setProperty("muted", True)
        v.addWidget(sub)
        tabs = QTabWidget()
        v.addWidget(tabs, 1)
        self.skills = skillmod.discover()
        pinned = set(project.pinned()) if project else set()

        lst = QWidget()
        lv = QVBoxLayout(lst)
        lv.setContentsMargins(0, 8, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget(objectName="root")
        col = QVBoxLayout(inner)
        col.setSpacing(8)
        self.pins = {}
        for sk in self.skills.values():
            card = QFrame(objectName="tool")
            c = QVBoxLayout(card)
            c.setContentsMargins(12, 10, 12, 10)
            top = QHBoxLayout()
            t = QLabel(sk.name)
            t.setObjectName("toolKind")
            top.addWidget(t)
            n = len([l for l in sk.lessons().splitlines() if l.startswith("- ")])
            meta = QLabel(("built in" if sk.builtin else "your skill") + (f", {n} lesson{'s' * (n != 1)}" if n else ""))
            meta.setProperty("muted", True)
            top.addWidget(meta)
            top.addStretch(1)
            pin = QCheckBox("Always use in this project")
            pin.setChecked(sk.name in pinned)
            pin.setEnabled(project is not None)
            self.pins[sk.name] = pin
            top.addWidget(pin)
            c.addLayout(top)
            d = QLabel(sk.description)
            d.setWordWrap(True)
            c.addWidget(d)
            col.addWidget(card)
        col.addStretch(1)
        area.setWidget(inner)
        lv.addWidget(area, 1)
        row = QHBoxLayout()
        b1 = QPushButton("Open my skills folder")
        b1.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(skillmod.user_skills_dir()))))
        b2 = QPushButton("Add a skill folder…")
        b2.clicked.connect(self._add)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        lv.addLayout(row)
        tabs.addTab(lst, "Skills")

        les = QWidget()
        lsv = QVBoxLayout(les)
        lsv.setContentsMargins(0, 8, 0, 0)
        self.les_list = QListWidget()
        for sk in self.skills.values():
            if sk.lessons():
                self.les_list.addItem(sk.name)
        self.les_text = QPlainTextEdit()
        self.les_text.setPlaceholderText("Pick a skill to see and edit its lessons. One lesson per line, starting with '- '.")
        self.les_list.currentTextChanged.connect(self._show_lessons)
        lsv.addWidget(self.les_list, 1)
        lsv.addWidget(self.les_text, 2)
        save = QPushButton("Save lessons")
        save.clicked.connect(self._save_lessons)
        lsv.addWidget(save, 0, Qt.AlignRight)
        tabs.addTab(les, "Lessons learned")

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        v.addLayout(bottom)

    def accept(self):
        if self.project:
            self.project.set_pinned([n for n, cb in self.pins.items() if cb.isChecked()])
        super().accept()

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a skill folder (it must contain SKILL.md)")
        if not d:
            return
        src = Path(d)
        if not (src / "SKILL.md").is_file():
            QMessageBox.warning(self, "Not a skill", "That folder has no SKILL.md.")
            return
        import shutil
        dest = skillmod.user_skills_dir() / src.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        QMessageBox.information(self, "Skill added", f"{src.name} was added. It's available in new chats.")

    def _show_lessons(self, name):
        sk = self.skills.get(name)
        self.les_text.setPlainText(sk.lessons() if sk else "")

    def _save_lessons(self):
        it = self.les_list.currentItem()
        if it:
            skillmod.lessons_file(it.text()).write_text(self.les_text.toPlainText().strip() + "\n", encoding="utf-8")
