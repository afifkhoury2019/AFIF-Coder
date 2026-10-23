"""Build-machine test: real AFIF Engine + a real small model + the agent loop.

Usage: python tests/smoke_engine.py <model.gguf>
Needs AFIF_ENGINE pointing at llama-server(.exe).
"""
import sys
import tempfile
import os
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
eng.start(model, int(os.environ.get("SMOKE_CTX", "8192")), use_gpu=False)
t0 = time.time()
while eng.current not in ("ready", "failed") and time.time() - t0 < 600:
    app.processEvents()
    time.sleep(0.2)
assert eng.current == "ready", f"engine did not start: {states}"
print(f"engine ready in {time.time() - t0:.1f}s on port {eng.port}, context {eng.ctx_effective}")
assert eng.ctx_effective > 0, "engine context not read from /props"

proj = Path(tempfile.mkdtemp())
(proj / "calc.py").write_text("def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n")
agent = Agent({"temperature": 0.0, "max_steps": 12}, lambda: LocalBackend(eng.url, eng.ctx_effective))
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

# Claude Code talks Anthropic's Messages API; the engine must answer it (with tools) for "Claude Code, free".
import requests  # noqa: E402
t1 = time.time()
r = requests.post(f"{eng.url}/v1/messages", timeout=300, headers={"x-api-key": "afif-local", "anthropic-version": "2023-06-01"},
                  json={"model": "afif-local", "max_tokens": 64,
                        "tools": [{"name": "read_file", "description": "Read a file",
                                   "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                                                    "required": ["path"]}}],
                        "messages": [{"role": "user", "content": "Read the file calc.py using the tool."}]})
body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
kinds = [c.get("type") for c in body.get("content", [])]
print("anthropic /v1/messages:", r.status_code, kinds, body.get("stop_reason"))
assert r.status_code == 200 and kinds, f"engine doesn't speak the Messages API: {r.status_code} {r.text[:300]}"
print(f"::notice title=Claude Code endpoint::/v1/messages answered in {time.time() - t1:.1f}s with {kinds} "
      f"(stop: {body.get('stop_reason')})", flush=True)
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
