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

import json
import re

import requests
from PySide6.QtCore import QObject, Signal

from . import diag
from .paths import app_base, data_dir

# Measured prompt-reading speed of the running engine (tokens/s). The agent sizes the chat history
# it sends from this, so a slow PC isn't kept waiting minutes for an old chat to be re-read.
SPEED = {"prompt_tps": 0.0}


def _choices_file() -> Path:
    return data_dir() / "gpu_choice.json"


def _load_choices() -> dict:
    try:
        return json.loads(_choices_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_choice(gpu: str, rec: dict):
    d = _load_choices()
    d[gpu] = rec
    try:
        _choices_file().write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError:
        pass

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
        self.ctx_effective = 0     # context the engine really uses (capped to what the model was trained on)

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
        known = _load_choices()
        if any(v.get("choice") == "cpu" for v in known.values()) and len(builds) > 1:
            builds = [b for b in builds if b[0] != "vulkan"] + [b for b in builds if b[0] == "vulkan"]
        last_err = ""
        try:
            size_gb = round(Path(model_path).stat().st_size / 1024 ** 3, 2)
        except OSError:
            size_gb = 0
        for backend, exe in builds:
            use_ctx = ctx
            while True:
                if gen != self._gen:
                    return
                self._emit("loading", f"Loading {name} ({'GPU' if backend == 'vulkan' else 'CPU'})…")
                t0 = time.time()
                err = self._launch(gen, exe, backend, model_path, use_ctx)
                secs = round(time.time() - t0, 2)
                if err is None:
                    break
                if err != "cancelled":
                    diag.event("engine_load", level="error", model=name, backend=backend, seconds=secs,
                               size_gb=size_gb, context=use_ctx, error=err, engine_log=self._log_tail(40))
                self.stop(keep_gen=True)
                if err.startswith("Not enough memory") and use_ctx > 8192:
                    use_ctx = max(8192, use_ctx // 2)        # try again with a smaller context
                    continue
                break
            if err is None:
                self._read_props(use_ctx)
                diag.event("engine_load", level="slow" if secs >= diag.SLOW["engine_load"] else "info",
                           model=name, backend=backend, seconds=secs, size_gb=size_gb, context=use_ctx,
                           context_used=self.ctx_effective, gpu=self.gpu_name())
                self.model, self.backend = model_path, backend
                rec = next(iter(known.values()), {}) if known else {}
                SPEED["prompt_tps"] = float(rec.get(backend) or 0)
                if backend == "vulkan" and len(builds) > 1 and self._maybe_cpu_faster(gen, model_path, use_ctx):
                    return
                note = "" if use_ctx == ctx else f" (context lowered to {use_ctx} to fit in memory)"
                self._emit("ready", f"{name}{note} on {'GPU' if self.backend == 'vulkan' else 'CPU'}")
                return
            last_err = err
        if gen == self._gen:
            self._emit("failed", last_err)

    def _read_props(self, asked: int):
        self.ctx_effective = asked
        try:
            p = requests.get(f"{self.url}/props", timeout=5).json()
            n = (p.get("default_generation_settings") or {}).get("n_ctx") or p.get("n_ctx")
            if n:
                self.ctx_effective = int(n)
        except (requests.RequestException, ValueError, AttributeError):
            pass

    def gpu_name(self) -> str:
        m = re.search(r"ggml_vulkan: 0 = (.+?) \|", self._log_tail(400))
        return m.group(1).strip() if m else ""

    def bench(self) -> float:
        """Prompt-reading speed in tokens/s, measured on ~300 tokens."""
        text = " ".join(f"line {i}: the quick brown fox jumps over the lazy dog." for i in range(30))
        try:
            r = requests.post(f"{self.url}/completion", json={"prompt": text, "n_predict": 1, "cache_prompt": False},
                              timeout=180)
            return float((r.json().get("timings") or {}).get("prompt_per_second") or 0)
        except (requests.RequestException, ValueError):
            return 0.0

    def _maybe_cpu_faster(self, gen, model_path: str, ctx: int) -> bool:
        """Built-in graphics (shared memory) are often slower than the processor. Measure both once
        per graphics chip and keep the faster; the result is remembered in gpu_choice.json."""
        tail = self._log_tail(400)
        gpu = self.gpu_name()
        if not gpu or "uma: 1" not in tail:
            SPEED["prompt_tps"] = 0.0
            return False                                  # a real graphics card: keep it
        known = _load_choices().get(gpu)
        if known and known.get("choice") == "vulkan":
            SPEED["prompt_tps"] = float(known.get("vulkan") or 0)
            return False
        self._emit("loading", "Checking whether the graphics chip or the processor is faster (once)…")
        gpu_tps = self.bench()
        cpu = next(((b, e) for b, e in backends() if b == "cpu"), None)
        if not cpu or gen != self._gen:
            return False
        self.stop(keep_gen=True)
        err = self._launch(gen, cpu[1], "cpu", model_path, ctx)
        if err is not None:
            self.stop(keep_gen=True)
            self._emit("loading", "Going back to the graphics chip…")
            if self._launch(gen, backends()[0][1], "vulkan", model_path, ctx) is None:
                self._emit("ready", f"{Path(model_path).stem} on GPU")
                return True
            return False
        cpu_tps = self.bench()
        choice = "cpu" if cpu_tps >= gpu_tps * 0.9 else "vulkan"
        _save_choice(gpu, {"vulkan": round(gpu_tps, 1), "cpu": round(cpu_tps, 1), "choice": choice})
        diag.event("engine_bench", gpu=gpu, gpu_tokens_per_s=round(gpu_tps, 1), cpu_tokens_per_s=round(cpu_tps, 1),
                   choice=choice)
        if choice == "vulkan":
            self.stop(keep_gen=True)
            if self._launch(gen, backends()[0][1], "vulkan", model_path, ctx) is not None:
                return False
            SPEED["prompt_tps"] = gpu_tps
        else:
            self.backend = "cpu"
            SPEED["prompt_tps"] = cpu_tps
        self._read_props(ctx)
        self._emit("ready", f"{Path(model_path).stem} on {'GPU' if choice == 'vulkan' else 'CPU'}")
        return True

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

    def _log_tail(self, n: int = 40) -> str:
        try:
            return "\n".join(self.log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
        except OSError:
            return ""

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
