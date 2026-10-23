"""Colour tokens and the Qt stylesheet built from them."""

LIGHT = {
    "paper": "#ECEFF1",     # window background, cool drafting grey
    "panel": "#FFFFFF",     # input and tool rows
    "ink": "#18232E",       # body text
    "steel": "#5E6E7D",     # secondary text
    "line": "#CFD6DC",      # hairlines
    "accent": "#2257A8",    # busbar blue, the one accent
    "accent_ink": "#FFFFFF",
    "tint": "#E4ECF7",      # accent wash for selected / user rule
    "ok": "#2E7D4F",
    "warn": "#9A6A00",
    "bad": "#A8322B",
    "code_bg": "#F4F6F8",
}

DARK = {
    "paper": "#151B21",
    "panel": "#1D252D",
    "ink": "#E2E7EB",
    "steel": "#8D9CA9",
    "line": "#2E3A45",
    "accent": "#6F9BE0",
    "accent_ink": "#0E1620",
    "tint": "#1F2E44",
    "ok": "#6CC08F",
    "warn": "#E0B24D",
    "bad": "#E4776E",
    "code_bg": "#11171C",
}

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", "Noto Sans", sans-serif'
MONO_FONT = '"Cascadia Mono", "Cascadia Code", Consolas, "DejaVu Sans Mono", monospace'


def stylesheet(t: dict) -> str:
    return f"""
* {{ font-family: {UI_FONT}; font-size: 10pt; color: {t['ink']}; }}
QMainWindow, #root {{ background: {t['paper']}; }}
QToolTip {{ background: {t['panel']}; color: {t['ink']}; border: 1px solid {t['line']}; padding: 4px; }}

#header {{ background: {t['paper']}; border-bottom: 1px solid {t['line']}; }}
#brand {{ font-size: 12pt; font-weight: 600; }}
#brandSub {{ color: {t['steel']}; }}
#muted, QLabel[muted="true"] {{ color: {t['steel']}; }}

QPushButton {{ background: {t['panel']}; border: 1px solid {t['line']}; border-radius: 6px;
              padding: 5px 12px; }}
QPushButton:hover {{ border-color: {t['steel']}; }}
QPushButton:focus {{ border: 2px solid {t['accent']}; padding: 4px 11px; }}
QPushButton:disabled {{ color: {t['steel']}; }}
QPushButton#primary {{ background: {t['accent']}; color: {t['accent_ink']}; border: 1px solid {t['accent']};
                      font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['ink']}; border-color: {t['ink']}; color: {t['paper']}; }}
QPushButton#danger {{ color: {t['bad']}; }}
QPushButton#flat {{ background: transparent; border: none; color: {t['steel']}; padding: 4px 6px; }}
QPushButton#flat:hover {{ color: {t['ink']}; }}
QPushButton#project {{ background: transparent; border: 1px solid transparent; text-align: left;
                      font-weight: 600; padding: 5px 8px; }}
QPushButton#project:hover {{ border-color: {t['line']}; background: {t['panel']}; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {t['panel']}; border: 1px solid {t['line']};
              border-radius: 6px; padding: 4px 8px; min-height: 20px; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {t['accent']}; }}
QComboBox QAbstractItemView {{ background: {t['panel']}; border: 1px solid {t['line']};
              selection-background-color: {t['tint']}; selection-color: {t['ink']}; }}
QCheckBox {{ color: {t['steel']}; spacing: 6px; }}

QScrollArea, #transcript {{ background: {t['paper']}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['steel']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

#userMsg {{ border-left: 3px solid {t['accent']}; padding: 2px 0 2px 12px; }}
#userText {{ font-size: 10.5pt; }}
#who {{ color: {t['steel']}; font-size: 9pt; }}

QTextBrowser#reply {{ background: transparent; border: none; font-size: 10.5pt; }}

#tool {{ background: {t['panel']}; border: 1px solid {t['line']}; border-radius: 6px; }}
#toolKind {{ font-weight: 600; min-width: 64px; }}
#toolSummary {{ font-family: {MONO_FONT}; font-size: 9pt; color: {t['steel']}; }}
#toolState[state="run"] {{ color: {t['steel']}; }}
#toolState[state="ok"] {{ color: {t['ok']}; }}
#toolState[state="bad"] {{ color: {t['bad']}; }}
#toolState[state="ask"] {{ color: {t['warn']}; font-weight: 600; }}
QPlainTextEdit#toolOut {{ background: {t['code_bg']}; border: none; border-top: 1px solid {t['line']};
              font-family: {MONO_FONT}; font-size: 9pt; padding: 6px; }}
#askBar {{ border-top: 1px solid {t['line']}; }}

#composer {{ background: {t['panel']}; border: 1px solid {t['line']}; border-radius: 10px; }}
QPlainTextEdit#input {{ background: transparent; border: none; font-size: 10.5pt; padding: 4px; }}

#empty {{ background: {t['paper']}; }}
#emptyTitle {{ font-size: 17pt; font-weight: 600; }}
#statusDot[on="true"] {{ color: {t['ok']}; }}
#statusDot[on="false"] {{ color: {t['bad']}; }}
#statusDot[on="wait"] {{ color: {t['warn']}; }}
QProgressBar {{ background: {t['line']}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
QMessageBox {{ background: {t['paper']}; }}
QPushButton#chip {{ background: {t['tint']}; border: 1px solid {t['line']}; border-radius: 12px;
                   padding: 2px 10px; font-size: 9pt; }}
QPushButton#chip:hover {{ border-color: {t['bad']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 6px 14px; border: none; color: {t['steel']};
               border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t['ink']}; border-bottom: 2px solid {t['accent']}; }}
QTabBar::tab:hover {{ color: {t['ink']}; }}
QDialog {{ background: {t['paper']}; }}
"""


def reply_css(t: dict) -> str:
    """CSS for the markdown inside assistant replies (QTextDocument subset)."""
    return f"""
    p, li {{ line-height: 145%; }}
    code {{ font-family: {MONO_FONT}; background-color: {t['code_bg']}; }}
    pre {{ font-family: {MONO_FONT}; background-color: {t['code_bg']}; padding: 8px; }}
    a {{ color: {t['accent']}; }}
    h1, h2, h3 {{ font-weight: 600; }}
    """
