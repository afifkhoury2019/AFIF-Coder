"""Diagnostics: a timeline of everything AFIF Coder does, how long it took, and every error.

Events go to %LOCALAPPDATA%\\AFIF-Coder\\logs\\afif-YYYY-MM-DD.jsonl (one JSON object per line,
kept 14 days). API keys and tokens are scrubbed before anything is written.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .paths import data_dir

_lock = threading.Lock()
_SECRET = re.compile(
    r"(sk-ant-[\w-]{8,}|sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|ya29\.[\w.-]{20,}|\"(access_token|refresh_token)\"\s*:\s*\"[^\"]+\"|"
    r"(Bearer|x-api-key:?)\s+[\w.-]{12,})")
SLOW = {"message": 30.0, "turn": 20.0, "first_words": 8.0, "tool": 30.0, "engine_load": 60.0}
_session = datetime.now().strftime("%Y%m%d-%H%M%S")


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def scrub(value):
    if isinstance(value, str):
        return _SECRET.sub("[hidden]", value)
    if isinstance(value, dict):
        return {k: ("[hidden]" if re.search(r"key|token|secret|password", str(k), re.I) and k != "tokens_in"
                    and k != "tokens_out" and not isinstance(v, (int, float)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def event(kind: str, **fields):
    """Record one event. Never raises."""
    try:
        rec = {"t": datetime.now().isoformat(timespec="milliseconds"), "session": _session, "kind": kind}
        rec.update(scrub(fields))
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            with open(logs_dir() / f"afif-{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


_repeats: dict[str, list] = {}       # crash text -> [count, last time logged]


def error(kind: str, exc: BaseException | None = None, **fields):
    if kind == "crash":
        # The same crash repeating (e.g. on every progress update) is logged once, then counted.
        key = f"{type(exc).__name__}: {exc}|{fields.get('where', '')}"
        rec = _repeats.setdefault(key, [0, 0.0])
        rec[0] += 1
        now = time.time()
        if rec[0] > 1 and now - rec[1] < 300 and rec[0] % 100:
            return
        rec[1] = now
        if rec[0] > 1:
            fields["repeats"] = rec[0]
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
    event(kind, level="error", error=f"{type(exc).__name__}: {exc}" if exc else fields.pop("error", ""),
          traceback=tb[-6000:], **fields)


@contextmanager
def span(kind: str, **fields):
    """Time a block; records ok/error and seconds."""
    t0 = time.perf_counter()
    info: dict = {}
    try:
        yield info
    except Exception as e:
        error(kind, e, seconds=round(time.perf_counter() - t0, 3), **fields, **info)
        raise
    else:
        secs = round(time.perf_counter() - t0, 3)
        lvl = "slow" if secs >= SLOW.get(kind, 1e9) else "info"
        event(kind, level=lvl, seconds=secs, **fields, **info)


def install_crash_hooks():
    """Record crashes anywhere (main thread, worker threads, Qt slots)."""
    old = sys.excepthook

    def hook(t, v, tb):
        error("crash", v, where="main")
        old(t, v, tb)

    def thook(args):
        if args.exc_type is not SystemExit:
            error("crash", args.exc_value, where=f"thread {args.thread.name if args.thread else '?'}")

    sys.excepthook = hook
    threading.excepthook = thook
    cleanup()


def cleanup(days: int = 14):
    cutoff = datetime.now() - timedelta(days=days)
    for f in logs_dir().glob("afif-*.jsonl"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except OSError:
            pass


def read_events(days: int = 2, limit: int = 20000) -> list[dict]:
    files = sorted(logs_dir().glob("afif-*.jsonl"))[-days:]
    out = []
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        except OSError:
            pass
    return out[-limit:]


def system_info(settings: dict | None = None) -> dict:
    from . import __version__, tooling
    from .paths import total_ram_gb
    info = {"app": f"AFIF Coder {__version__}", "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "machine": platform.machine(), "cpu": platform.processor() or "unknown",
            "cpu_threads": os.cpu_count(), "ram_gb": round(total_ram_gb(), 1),
            "python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False))}
    try:
        info["tools"] = {name: ok for name, _, ok in tooling.inventory()}
        vf = tooling.tools_dir() / "VERSIONS.json"
        if vf.is_file():
            info["tool_versions"] = json.loads(vf.read_text())
    except Exception:
        pass
    if settings:
        safe = {k: v for k, v in settings.items() if k not in ("cloud", "recent", "brave_key", "mcp_servers")}
        safe["cloud_providers"] = sorted(k for k, v in (settings.get("cloud") or {}).items() if v.get("key"))
        info["settings"] = safe
    return scrub(info)
