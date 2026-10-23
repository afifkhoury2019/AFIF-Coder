"""Debug report: what happened, how long it took, what failed. Viewable in the app, exportable as a zip."""
from __future__ import annotations

import html
import json
import statistics
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import diag
from .paths import data_dir

LABEL = {"message": "Message", "turn": "Model reply", "tool": "Tool", "engine_load": "Model load",
         "warm_up": "Warm-up", "message_error": "Error", "crash": "Crash", "download": "Download",
         "connection": "Sign-in", "mcp_start": "REA start", "skill": "Skill", "stop": "Stop",
         "app_start": "App start", "message_start": "Message sent"}


def _med(xs):
    xs = [x for x in xs if isinstance(x, (int, float)) and x > 0]
    return round(statistics.median(xs), 1) if xs else None


def summarize(events: list[dict]) -> dict:
    msgs = [e for e in events if e["kind"] == "message"]
    by_model: dict[str, list] = {}
    for e in msgs:
        by_model.setdefault(e.get("model", "?").split(":")[-1].split("\\")[-1], []).append(e)
    models = []
    for m, es in by_model.items():
        models.append({"model": m, "messages": len(es), "errors": sum(1 for e in es if e.get("level") == "error"),
                       "first_words_s": _med(e.get("first_words_s") for e in es),
                       "total_s": _med(e.get("seconds") for e in es),
                       "tokens_per_s": _med(e.get("tokens_per_s") for e in es)})
    turns = [e for e in events if e["kind"] == "turn"]
    local = [e.get("request", {}) for e in turns if e.get("request", {}).get("engine_prompt_n") is not None]
    problems = [e for e in events if e.get("level") in ("error", "slow") and e["kind"] not in ("message", "turn")]
    return {
        "messages": len(msgs),
        "errors": sum(1 for e in events if e.get("level") == "error"
                      and e["kind"] in ("message", "crash", "engine_load", "download", "connection", "mcp_start")),
        "slow": sum(1 for e in events if e.get("level") == "slow"), "models": models,
        "engine_read_tps": _med(r.get("engine_prompt_tps") for r in local),
        "engine_cache_share": (round(100 * sum(r.get("engine_cache_n") or 0 for r in local) /
                                     max(1, sum((r.get("engine_cache_n") or 0) + (r.get("engine_prompt_n") or 0)
                                                for r in local))) if local else None),
        "problems": problems[-60:],
        "tools_slowest": sorted((e for e in events if e["kind"] == "tool"), key=lambda e: -e.get("seconds", 0))[:12],
        "loads": [e for e in events if e["kind"] == "engine_load"][-10:],
    }


def advice(sm: dict) -> list[str]:
    tips = []
    for m in sm["models"]:
        if m["first_words_s"] and m["first_words_s"] > 15:
            tips.append(f"{m['model']}: first words take about {m['first_words_s']} s. On a processor without a "
                        "graphics card, use a smaller model or Speed: Fast; for big jobs use a cloud model.")
        if m["tokens_per_s"] and m["tokens_per_s"] < 6:
            tips.append(f"{m['model']}: writes about {m['tokens_per_s']} words/s. A smaller model or the graphics "
                        "card (Settings) will be faster.")
    if sm["engine_read_tps"] and sm["engine_read_tps"] < 80:
        tips.append(f"The engine reads only about {sm['engine_read_tps']} tokens/s on this PC, so long prompts "
                    "(big files, long chats, skills) take a while. Start a new chat for new tasks.")
    if any(e.get("backend") == "cpu" for e in sm["loads"]) and any(e.get("backend") == "vulkan" and e.get("level") == "error"
                                                                  for e in sm["loads"]):
        tips.append("The graphics card failed to start, so the processor is used. Update the graphics driver.")
    errs = [e for e in sm["problems"] if e.get("level") == "error"]
    if any("credit" in str(e.get("error", "")).lower() for e in errs):
        tips.append("Claude: the API account needs credit (platform.claude.com → Billing).")
    if any("quota" in str(e.get("error", "")).lower() or "rate limit" in str(e.get("error", "")).lower() for e in errs):
        tips.append("A cloud provider hit its rate limit or quota. Wait, or switch model.")
    return tips


def _row(cells):
    return "<tr>" + "".join(f"<td>{html.escape(str(c if c is not None else '–'))}</td>" for c in cells) + "</tr>"


def render_html(events: list[dict], info: dict) -> str:
    sm = summarize(events)
    css = ("body{font-family:Segoe UI,Arial,sans-serif;color:#18232E;background:#F7F9FA;margin:24px;max-width:1100px}"
           "h1{color:#1E2D80;font-size:22px}h2{color:#1E2D80;font-size:17px;margin-top:28px}"
           "table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}"
           "td,th{border:1px solid #CFD6DC;padding:5px 7px;text-align:left;vertical-align:top}"
           "th{background:#1E2D80;color:#fff}.err{color:#A8322B}.slow{color:#9A6A00}"
           "pre{white-space:pre-wrap;font-size:12px;background:#EEF2F5;padding:8px;margin:4px 0}")
    out = [f"<!doctype html><meta charset=utf-8><title>AFIF Coder debug report</title><style>{css}</style>",
           f"<h1>AFIF Coder debug report</h1><p>{datetime.now():%Y-%m-%d %H:%M} · {html.escape(info.get('app', ''))} · "
           f"{sm['messages']} messages · <span class=err>{sm['errors']} errors</span> · "
           f"<span class=slow>{sm['slow']} slow steps</span></p>"]
    tips = advice(sm)
    if tips:
        out.append("<h2>What to do</h2><ul>" + "".join(f"<li>{html.escape(t)}</li>" for t in tips) + "</ul>")
    out.append("<h2>Speed by model</h2><table><tr><th>Model</th><th>Messages</th><th>Errors</th>"
               "<th>First words (median s)</th><th>Whole message (median s)</th><th>Words/s</th></tr>")
    for m in sm["models"]:
        out.append(_row([m["model"], m["messages"], m["errors"], m["first_words_s"], m["total_s"], m["tokens_per_s"]]))
    out.append("</table>")
    if sm["engine_read_tps"] is not None:
        out.append(f"<p>Local engine reads about <b>{sm['engine_read_tps']}</b> tokens/s; "
                   f"<b>{sm['engine_cache_share']}%</b> of prompts came from cache.</p>")
    out.append("<h2>Recent messages</h2><table><tr><th>Time</th><th>Model</th><th>Speed</th><th>Total s</th>"
               "<th>First words s</th><th>Replies</th><th>Tools</th><th>Tokens in/out</th><th>Result</th></tr>")
    for e in [e for e in events if e["kind"] == "message"][-40:]:
        out.append(_row([e["t"][5:19].replace("T", " "), str(e.get("model", "")).split("\\")[-1][-40:], e.get("speed"),
                         e.get("seconds"), e.get("first_words_s"), e.get("turns"), e.get("tools"),
                         f"{e.get('tokens_in', 0)}/{e.get('tokens_out', 0)}", e.get("result")]))
    out.append("</table><h2>Errors and slow steps</h2>")
    for e in reversed(sm["problems"]):
        cls = "err" if e.get("level") == "error" else "slow"
        head = f"{e['t'][5:19].replace('T', ' ')} · {LABEL.get(e['kind'], e['kind'])}"
        detail = {k: v for k, v in e.items() if k not in ("t", "session", "kind", "level", "traceback")}
        out.append(f"<p class={cls}><b>{html.escape(head)}</b> {html.escape(str(e.get('error') or ''))[:400]}</p>"
                   f"<pre>{html.escape(json.dumps(detail, ensure_ascii=False, indent=1)[:4000])}</pre>")
        if e.get("traceback"):
            out.append(f"<pre>{html.escape(e['traceback'][-3000:])}</pre>")
    out.append("<h2>Slowest tool steps</h2><table><tr><th>Time</th><th>Tool</th><th>Seconds</th><th>OK</th><th>Detail</th></tr>")
    for e in sm["tools_slowest"]:
        out.append(_row([e["t"][5:19].replace("T", " "), e.get("tool"), e.get("seconds"), e.get("ok"), e.get("detail")]))
    out.append("</table><h2>Model loads</h2><table><tr><th>Time</th><th>Model</th><th>Where</th><th>Seconds</th>"
               "<th>Size GB</th><th>Result</th></tr>")
    for e in sm["loads"]:
        out.append(_row([e["t"][5:19].replace("T", " "), e.get("model"), "GPU" if e.get("backend") == "vulkan" else "CPU",
                         e.get("seconds"), e.get("size_gb"), e.get("error") or "ok"]))
    out.append("</table><h2>This PC and settings</h2>"
               f"<pre>{html.escape(json.dumps(info, ensure_ascii=False, indent=1))}</pre>")
    return "\n".join(out)


def export_zip(dest: Path, settings: dict) -> Path:
    events = diag.read_events(days=3)
    info = diag.system_info(settings)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.html", render_html(events, info))
        z.writestr("events.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in events))
        eng = data_dir() / "engine.log"
        if eng.is_file():
            z.writestr("engine.log", diag.scrub("\n".join(eng.read_text(encoding="utf-8", errors="replace")
                                                          .splitlines()[-400:])))
    return dest


class ReportDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Debug report")
        self.resize(980, 720)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        head = QLabel("Debug report")
        head.setObjectName("emptyTitle")
        v.addWidget(head)
        sub = QLabel("Every message, model reply, tool step, model load and error, with how long it took. "
                     "Export it and send the file when something is slow or fails. API keys are never recorded.")
        sub.setWordWrap(True)
        sub.setProperty("muted", True)
        v.addWidget(sub)
        tabs = QTabWidget()
        v.addWidget(tabs, 1)
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(True)
        tabs.addTab(self.summary, "Summary")

        tl = QWidget()
        tv = QVBoxLayout(tl)
        tv.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["Everything", "Errors", "Slow steps", "Messages", "Model replies", "Tools", "Model loads"])
        self.filter.currentIndexChanged.connect(self._fill)
        row.addWidget(QLabel("Show"))
        row.addWidget(self.filter)
        row.addStretch(1)
        tv.addLayout(row)
        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Time", "What", "Seconds", "Status", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        self.detail = QPlainTextEdit()
        self.detail.setObjectName("toolOut")
        self.detail.setReadOnly(True)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([420, 200])
        tv.addWidget(split, 1)
        tabs.addTab(tl, "Timeline")

        bottom = QHBoxLayout()
        for text, fn in (("Refresh", self.refresh), ("Open logs folder", self._open_logs)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bottom.addWidget(b)
        bottom.addStretch(1)
        exp = QPushButton("Export report…", objectName="primary")
        exp.clicked.connect(self._export)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(exp)
        bottom.addWidget(close)
        v.addLayout(bottom)
        self.refresh()

    def refresh(self):
        self.events = diag.read_events(days=3)
        self.summary.setHtml(render_html(self.events, diag.system_info(self.s)))
        self._fill()

    def _fill(self):
        f = self.filter.currentText()
        keep = {"Everything": lambda e: e["kind"] != "message_start",
                "Errors": lambda e: e.get("level") == "error", "Slow steps": lambda e: e.get("level") == "slow",
                "Messages": lambda e: e["kind"] == "message", "Model replies": lambda e: e["kind"] == "turn",
                "Tools": lambda e: e["kind"] == "tool", "Model loads": lambda e: e["kind"] == "engine_load"}[f]
        self.rows = [e for e in self.events if keep(e)][-1500:][::-1]
        self.table.setRowCount(len(self.rows))
        for i, e in enumerate(self.rows):
            what = LABEL.get(e["kind"], e["kind"])
            if e["kind"] == "tool":
                what += f": {e.get('tool')}"
            status = {"error": "error", "slow": "slow"}.get(e.get("level"), "ok")
            detail = e.get("error") or e.get("detail") or e.get("result") or e.get("model") or ""
            if e["kind"] == "turn":
                r = e.get("request", {})
                detail = (f"answer started after {r.get('first_event_s', '–')} s · status {r.get('status', '–')}"
                          + (f" · read {r.get('engine_prompt_n')} tokens at {r.get('engine_prompt_tps')}/s, "
                             f"{r.get('engine_cache_n')} from cache" if r.get("engine_prompt_n") is not None else ""))
            if e["kind"] == "message":
                detail = f"first words {e.get('first_words_s')} s · {e.get('turns')} replies · {e.get('tools')} tools · {e.get('result')}"
            for c, val in enumerate([e["t"][5:19].replace("T", " "), what, e.get("seconds", ""), status, str(detail)[:300]]):
                it = QTableWidgetItem(str(val))
                if status == "error":
                    it.setForeground(Qt.red)
                elif status == "slow":
                    it.setForeground(Qt.darkYellow)
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()
        self.detail.clear()

    def _show(self, r):
        if 0 <= r < len(self.rows):
            self.detail.setPlainText(json.dumps(self.rows[r], ensure_ascii=False, indent=2))

    def _open_logs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(diag.logs_dir())))

    def _export(self):
        default = Path.home() / "Desktop" / f"AFIF-Coder-debug-{datetime.now():%Y%m%d-%H%M}.zip"
        if not default.parent.is_dir():
            default = Path.home() / default.name
        f, _ = QFileDialog.getSaveFileName(self, "Save debug report", str(default), "Zip file (*.zip)")
        if not f:
            return
        export_zip(Path(f), self.s)
        QMessageBox.information(self, "Report saved", f"Saved {Path(f).name}.\nSend this file when asking for help. "
                                "It contains timings and error details, never API keys.")
