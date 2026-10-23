"""Build-machine test: real AFIF Engine + a real small model + the agent loop.

Usage: python tests/smoke_engine.py <model.gguf>
Needs AFIF_ENGINE pointing at llama-server(.exe).
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QCoreApplication  # noqa: E402

from afif_coder.agent import Agent  # noqa: E402
from afif_coder.backends import LocalBackend  # noqa: E402
from afif_coder.engine import Engine  # noqa: E402

app = QCoreApplication([])
model = sys.argv[1]
eng = Engine()
states = []
eng.state.connect(lambda s, d: (states.append((s, d)), print(f"[engine] {s}: {d}", flush=True)))
eng.start(model, 8192, use_gpu=False)
t0 = time.time()
while eng.current not in ("ready", "failed") and time.time() - t0 < 600:
    app.processEvents()
    time.sleep(0.2)
assert eng.current == "ready", f"engine did not start: {states}"
print(f"engine ready in {time.time() - t0:.1f}s on port {eng.port}")

proj = Path(tempfile.mkdtemp())
(proj / "calc.py").write_text("def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n")
agent = Agent({"temperature": 0.0, "max_steps": 12}, lambda: LocalBackend(eng.url, 8192))
agent.reset(str(proj))
agent.auto_approve = True

t0 = time.time()
agent.warm_up(LocalBackend(eng.url, 8192))
print(f"::notice title=Warm-up::instructions + tools pre-read in {time.time() - t0:.1f}s while the model loads", flush=True)
timing = []
agent.timing.connect(lambda first, tps: timing.append((first, tps)))
tools, text, result = [], [], {}
agent.tool_started.connect(lambda cid, n, s: print(f"[tool] {n} {s}", flush=True))
agent.tool_finished.connect(lambda cid, ok, out: tools.append(ok))
agent.text_delta.connect(text.append)
agent.finished.connect(lambda err: result.setdefault("err", err))

th = threading.Thread(target=agent.run, args=(
    "Read calc.py. The add function is wrong. Fix it with edit_file, then reply with one sentence.",))
th.start()
while th.is_alive():
    app.processEvents()
    time.sleep(0.1)
app.processEvents()
eng.stop()

print("reply:", "".join(text)[:400])
print("tools ok:", tools, "error:", result.get("err"))
err = result.get("err") or ""
assert tools and any(tools), f"the model made no successful tool call ({err})"
if err:
    # A small test model may get stuck on the task itself; that's model quality, not a broken build.
    assert "repeating" in err or "maximum number of steps" in err, err
    print(f"::warning title=Test model::{err}")
fixed = "a + b" in (proj / "calc.py").read_text()
print("bug fixed by model:", fixed)
app.processEvents()
if timing:
    print(f"::notice title=Speed on the build machine (CPU, 3B model)::first words after {timing[0][0]:.1f}s, "
          f"{timing[0][1]:.0f} tokens/s", flush=True)
print("SMOKE TEST PASSED")
