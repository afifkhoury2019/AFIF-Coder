"""Build-machine test: the real Claude Code, installed from npm with the bundled Node.js, running inside
AFIF Coder's agent on the AFIF Engine with the built-in model. No Anthropic account, no API key.

Usage: python tests/smoke_claude_code.py <model.gguf>
Env: AFIF_ENGINE (llama-server), AFIF_TOOLS (bundled tools), AFIF_DATA (scratch data folder).
Exit 2 = the setup itself is broken (install, launch, event stream); exit 1 = the small model didn't
finish the task (model quality, reported as a warning); 0 = fixed the bug.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QCoreApplication  # noqa: E402

from afif_coder import claude_code as cc  # noqa: E402
from afif_coder.agent import Agent  # noqa: E402
from afif_coder.engine import Engine  # noqa: E402


def note(kind, title, msg):
    print(f"::{kind} title={title}::" + str(msg).replace("\n", "%0A")[:3000], flush=True)


app = QCoreApplication([])
t0 = time.time()
lines = []
ok, msg = cc.install(lambda l: (lines.append(l), print("[npm]", l, flush=True)))
if not ok:
    note("error", "Claude Code install", msg + "\n" + "\n".join(lines[-20:]))
    sys.exit(2)
print("installed in", round(time.time() - t0, 1), "s; command:", cc.command(), flush=True)
import subprocess  # noqa: E402
v = subprocess.run(cc.command() + ["--version"], capture_output=True, text=True, timeout=120, env=cc.environment("http://127.0.0.1:1"))
version = (v.stdout or v.stderr).strip()
note("notice", "Claude Code installed", f"{version} in {time.time() - t0:.0f}s via {cc.command()[0]}")

eng = Engine()
eng.start(sys.argv[1], 32768, use_gpu=False)
t0 = time.time()
while eng.current not in ("ready", "failed") and time.time() - t0 < 600:
    app.processEvents()
    time.sleep(0.2)
if eng.current != "ready":
    note("error", "Engine", "did not start")
    sys.exit(2)
print("engine ready, context", eng.ctx_effective, flush=True)

results = {}
fixed_any = False
for lite in (True, False):
    proj = Path(tempfile.mkdtemp())
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n")
    agent = Agent({"temperature": 0.0, "max_steps": 12}, lambda: cc.Harness(eng.url, lite=lite))
    agent.reset(str(proj))
    agent.auto_approve = True
    tools, text, res = [], [], {}
    agent.tool_started.connect(lambda cid, n, s: (tools.append(n), print("[tool]", n, s, flush=True)))
    agent.text_delta.connect(text.append)
    agent.status.connect(lambda s: print("[status]", s, flush=True))
    agent.finished.connect(lambda e: res.setdefault("err", e))
    t1 = time.time()
    th = threading.Thread(target=agent.run, args=("The add function in calc.py is wrong. Fix it, then say done.",))
    th.start()
    while th.is_alive() and time.time() - t1 < 1500:
        app.processEvents()
        time.sleep(0.1)
    if th.is_alive():
        agent.stop()
        th.join(30)
    app.processEvents()
    secs = time.time() - t1
    fixed = "a + b" in (proj / "calc.py").read_text()
    fixed_any |= fixed
    timing = getattr(agent._backend, "timing", {})
    results["lite" if lite else "full"] = {"seconds": round(secs), "tools": tools, "fixed": fixed,
                                          "error": res.get("err"), "usage": timing.get("usage"),
                                          "reply": "".join(text)[-300:]}
    print(json.dumps(results, indent=1), flush=True)
    if not tools and not text and res.get("err") and "stopped without an answer" in str(res.get("err")):
        note("error", "Claude Code run", res.get("err"))
        eng.stop()
        sys.exit(2)
eng.stop()
for k, r in results.items():
    note("notice", f"Claude Code {k} setup", f"{r['seconds']}s, tools {r['tools']}, fixed={r['fixed']}, "
         f"tokens {r['usage']}, error: {r['error']}")
sys.exit(0 if fixed_any else 1)
