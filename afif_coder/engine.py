"""AFIF Engine: the bundled local inference server (llama.cpp's llama-server).

The app starts it in the background with the chosen model, talks to it over
127.0.0.1, and stops it when the app closes. Nothing needs to be installed
separately.
"""
from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

from .paths import app_base, data_dir

EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"


def backends() -> list[tuple[str, Path]]:
    """Available engine builds, best first. AFIF_ENGINE overrides (for development)."""
    override = os.environ.get("AFIF_ENGINE")
    if override:
        return [("custom", Path(override))]
    out = []
    root = app_base() / "engine"
    for name in ("vulkan", "cpu"):
        p = root / name / EXE
        if p.is_file():
            out.append((name, p))
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _KillOnClose:
    """Windows job object: the engine dies with the app, even if the app crashes."""

    def __init__(self):
        self.job = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.job, self.k32 = job, k32

    def add(self, proc: subprocess.Popen):
        if self.job:
            import ctypes
            self.k32.AssignProcessToJobObject(self.job, ctypes.c_void_p(int(proc._handle)))


class Engine(QObject):
    state = Signal(str, str)   # state ("off" | "loading" | "ready" | "failed"), detail text

    def __init__(self):
        super().__init__()
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.model = ""
        self.backend = ""
        self.current = "off"
        self._job = _KillOnClose()
        self._lock = threading.Lock()
        self._gen = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log_path(self) -> Path:
        return data_dir() / "engine.log"

    def _emit(self, st: str, detail: str = ""):
        self.current = st
        self.state.emit(st, detail)

    def start(self, model_path: str, ctx: int, use_gpu: bool):
        """Start (or restart) with a model. Returns immediately; watch the state signal."""
        self.stop()
        model_path = str(Path(model_path).resolve())   # the engine runs from its own folder
        self._gen += 1
        gen = self._gen
        threading.Thread(target=self._boot, args=(gen, model_path, ctx, use_gpu), daemon=True).start()

    def _boot(self, gen: int, model_path: str, ctx: int, use_gpu: bool):
        name = Path(model_path).stem
        builds = backends()
        if not use_gpu:
            builds = [b for b in builds if b[0] != "vulkan"] or builds
        if not builds:
            self._emit("failed", "The AFIF Engine files are missing. Reinstall AFIF Coder.")
            return
        last_err = ""
        for backend, exe in builds:
            if gen != self._gen:
                return
            self._emit("loading", f"Loading {name} ({'GPU' if backend == 'vulkan' else 'CPU'})…")
            err = self._launch(gen, exe, backend, model_path, ctx)
            if err is None:
                self.model, self.backend = model_path, backend
                self._emit("ready", f"{name} on {'GPU' if backend == 'vulkan' else 'CPU'}")
                return
            last_err = err
            self.stop(keep_gen=True)
        if gen == self._gen:
            self._emit("failed", last_err)

    def _launch(self, gen, exe: Path, backend: str, model_path: str, ctx: int) -> str | None:
        self.port = _free_port()
        args = [str(exe), "-m", model_path, "--host", "127.0.0.1", "--port", str(self.port),
                "-c", str(ctx), "--jinja", "--alias", "afif-local",
                "-np", "1",                  # one conversation: the whole cache serves it, nothing evicted
                "--cache-reuse", "256"]      # reuse already-read text even after old output is trimmed
        if backend in ("vulkan", "custom"):
            args += ["-ngl", "999"]
        extra = os.environ.get("AFIF_ENGINE_ARGS")
        if extra:
            args += shlex.split(extra)
        log = open(self.log_path(), "w", encoding="utf-8", errors="replace")
        log.write(" ".join(args) + "\n\n")
        log.flush()
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with self._lock:
            self.proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         cwd=str(exe.parent), creationflags=flags)
            self._job.add(self.proc)
        deadline = time.time() + 600       # big models on slow disks take a while
        while time.time() < deadline:
            if gen != self._gen:
                return "cancelled"
            if self.proc.poll() is not None:
                return self._tail_reason()
            try:
                r = requests.get(f"{self.url}/health", timeout=2)
                if r.status_code == 200:
                    return None
            except requests.RequestException:
                pass
            time.sleep(0.5)
        return "The engine took too long to load the model."

    def _tail_reason(self) -> str:
        try:
            text = self.log_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        low = text.lower()
        if "failed to allocate" in low or "out of memory" in low or "unable to allocate" in low:
            return "Not enough memory for this model. Pick a smaller model or lower the context length in Settings."
        if "failed to load model" in low or "invalid magic" in low or "gguf" in low and "error" in low:
            return "The model file couldn't be loaded. It may be incomplete — delete it and download it again."
        lines = [l for l in text.strip().splitlines() if l.strip()][-3:]
        return "The engine stopped: " + (" | ".join(lines) or "no details") + f"  (log: {self.log_path()})"

    def stop(self, keep_gen: bool = False):
        if not keep_gen:
            self._gen += 1
        with self._lock:
            p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        if not keep_gen and self.current != "off":
            self._emit("off", "")
