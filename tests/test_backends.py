"""Offline tests for cloud backends, attachments and key storage, using fake servers that
speak the Anthropic and OpenAI streaming formats and check what the app sends back.

Run: python tests/test_backends.py   (exit code 0 = all passed)
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication  # noqa: E402

from afif_coder import backends  # noqa: E402
from afif_coder.agent import Agent  # noqa: E402
from afif_coder.attachments import build_message, from_image_bytes, load  # noqa: E402

app = QApplication.instance() or QApplication([])
FAILS = []
SEEN = {}          # last request body per fake server


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond:
        FAILS.append(what)


def sse(h, obj, event=None):
    if event:
        h.wfile.write(f"event: {event}\n".encode())
    h.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
    h.wfile.flush()


# ---------------------------------------------------------------- fake Anthropic
class Anthropic(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        ok = self.headers.get("x-api-key") == "sk-ant-test"
        self.send_response(200 if ok else 401)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-opus-5-5"}]}
                                    if ok else {"error": {"message": "invalid x-api-key"}}).encode())

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN["anthropic"] = b
        assert self.headers.get("x-api-key") == "sk-ant-test" and self.headers.get("anthropic-version")
        msgs = b["messages"]
        roles = [m["role"] for m in msgs]
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles
        n_results = sum(1 for m in msgs for c in m["content"] if isinstance(c, dict) and c.get("type") == "tool_result")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        sse(self, {"type": "message_start", "message": {"usage": {"input_tokens": 1200, "output_tokens": 1}}}, "message_start")
        if n_results == 0:
            sse(self, {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
            sse(self, {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Read it first."}})
            sse(self, {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG123"}})
            sse(self, {"type": "content_block_stop", "index": 0})
            sse(self, {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}})
            sse(self, {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Looking at calc.py."}})
            sse(self, {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {}}})
            for piece in ['{"pa', 'th": "ca', 'lc.py"}']:
                sse(self, {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": piece}})
        elif n_results == 1:
            sse(self, {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_02", "name": "edit_file", "input": {}}})
            sse(self, {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta",
                       "partial_json": json.dumps({"path": "calc.py", "old_text": "a - b", "new_text": "a + b"})}})
        else:
            sse(self, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            sse(self, {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Fixed add()."}})
        sse(self, {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}})
        sse(self, {"type": "message_stop"})


# ---------------------------------------------------------------- fake OpenAI-compatible (Gemini / DeepSeek)
class OpenAI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-embedding-001"},
                                              {"id": "deepseek-flash"}]}).encode())

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN[self.server.name] = b
        tools_done = sum(1 for m in b["messages"] if m["role"] == "tool")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if tools_done == 0:
            if self.server.name == "deepseek":
                sse(self, {"choices": [{"index": 0, "delta": {"reasoning_content": "Need to read."}}]})
            call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "read_file", "arguments": ""}}
            if self.server.name == "gemini":
                call["extra_content"] = {"google": {"thought_signature": "GSIG"}}
            sse(self, {"choices": [{"index": 0, "delta": {"tool_calls": [call]}}]})
            sse(self, {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path":"calc.py"}'}}]}}]})
            sse(self, {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            sse(self, {"choices": [{"index": 0, "delta": {"content": "It subtracts."}}]})
            sse(self, {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        sse(self, {"choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 20}})
        self.wfile.write(b"data: [DONE]\n\n")


def serve(handler, name):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.name = name
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def run_agent(backend, project, content):
    out = {"tools": [], "text": [], "usage": (0, 0)}
    agent = Agent({"temperature": 0.2, "max_steps": 8}, lambda: backend)
    agent.reset(project)
    agent.auto_approve = True
    agent.tool_finished.connect(lambda cid, ok, o: out["tools"].append(ok))
    agent.text_delta.connect(out["text"].append)
    agent.usage.connect(lambda i, o: out.__setitem__("usage", (i, o)))
    agent.finished.connect(lambda e: out.__setitem__("err", e))
    th = threading.Thread(target=agent.run, args=(content,))
    th.start()
    while th.is_alive():
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return out, agent


def project():
    d = Path(tempfile.mkdtemp())
    (d / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    return d


print("Claude (Anthropic Messages API)")
a_url = serve(Anthropic, "anthropic")
backends.PROVIDERS["anthropic"].base_url = a_url
check(backends.list_models("anthropic", {"key": "sk-ant-test"}) == ["claude-sonnet-5-5", "claude-opus-5-5"], "lists models")
try:
    backends.list_models("anthropic", {"key": "wrong"})
    check(False, "bad key is reported")
except backends.BackendError as e:
    check("rejected the API key" in str(e), "bad key is reported clearly")
b = backends.make_backend("anthropic", "claude-sonnet-5-5", {"key": "sk-ant-test"})
b.URL = a_url + "/v1/messages"
proj = project()
out, agent = run_agent(b, str(proj), "fix add")
check(not out.get("err"), f"no error ({out.get('err')})")
check(out["tools"] == [True, True], "ran read_file then edit_file")
check("a + b" in (proj / "calc.py").read_text(), "file was fixed")
check(out["usage"] == (3600, 150), f"token usage summed over 3 turns {out['usage']}")
last = SEEN["anthropic"]
first_asst = next(m for m in last["messages"] if m["role"] == "assistant")
check(first_asst["content"][0].get("type") == "thinking" and first_asst["content"][0].get("signature") == "SIG123",
      "thinking block with signature sent back")
results = [c for m in last["messages"] for c in m["content"] if isinstance(c, dict) and c.get("type") == "tool_result"]
check([r["tool_use_id"] for r in results] == ["toolu_01", "toolu_02"], "tool results match tool ids")
check(last["tools"][0].get("input_schema") and "system" in last and "AFIF Coder" in last["system"], "tools + system prompt converted")

print("Gemini (OpenAI-compatible)")
g_url = serve(OpenAI, "gemini")
models = backends.list_models("gemini", {"key": "g", "base_url": g_url})
check(models == ["gemini-3.8-flash"], f"lists chat models only, strips 'models/' ({models})")
png = None
from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
img = QImage(40, 30, QImage.Format_RGB32)
img.fill(QColor("#2257A8"))
ba = QByteArray()
buf = QBuffer(ba)
buf.open(QIODevice.WriteOnly)
img.save(buf, "PNG")
png = bytes(ba)
content = build_message("what is in calc.py? see screenshot", [from_image_bytes(png)], str(proj))
b = backends.make_backend("gemini", "gemini-3.8-flash", {"key": "g", "base_url": g_url})
out, agent = run_agent(b, str(project()), content)
check(not out.get("err") and out["tools"] == [True], f"tool call works ({out.get('err')})")
sent = SEEN["gemini"]["messages"]
asst = next(m for m in sent if m["role"] == "assistant")
check(asst["tool_calls"][0].get("extra_content", {}).get("google", {}).get("thought_signature") == "GSIG",
      "thought signature echoed back")
user = next(m for m in sent if m["role"] == "user")
check(isinstance(user["content"], list) and user["content"][1]["type"] == "image_url"
      and "_name" not in user["content"][1], "image sent as image_url part, private keys stripped")
check(out["usage"] == (600, 40), "usage reported")

print("DeepSeek (OpenAI-compatible, reasoning)")
d_url = serve(OpenAI, "deepseek")
b = backends.make_backend("deepseek", "deepseek-flash", {"key": "d", "base_url": d_url})
out, agent = run_agent(b, str(project()), content)
sent = SEEN["deepseek"]["messages"]
asst = next(m for m in sent if m["role"] == "assistant")
check(asst.get("reasoning_content") == "Need to read.", "reasoning_content echoed back")
user = next(m for m in sent if m["role"] == "user")
check(isinstance(user["content"], str) and "can't see images" in user["content"], "image turned into a note for a text-only model")

print("Switching provider mid-conversation")
b2 = backends.make_backend("anthropic", "claude-sonnet-5-5", {"key": "sk-ant-test"})
b2.URL = a_url + "/v1/messages"
system, conv = b2.convert(agent.messages)
check(conv[0]["role"] == "user" and any(c.get("type") == "tool_use" for c in conv[1]["content"]),
      "OpenAI-format history converts to Anthropic format")

print("Attachments")
tmp = Path(tempfile.mkdtemp())
(tmp / "notes.md").write_text("# Title\nhello")
import docx  # noqa: E402
import openpyxl  # noqa: E402
d = docx.Document()
d.add_paragraph("Substation 132/33 kV")
d.save(tmp / "spec.docx")
wb = openpyxl.Workbook()
wb.active.append(["Cable", "Ampacity"])
wb.active.append(["XLPE 500", 650])
wb.save(tmp / "cables.xlsx")
(tmp / "big.txt").write_text("x" * 70_000)
(tmp / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
img.save(str(tmp / "shot.png"))
files = [load(tmp / n) for n in ("notes.md", "spec.docx", "cables.xlsx", "big.txt", "blob.bin", "shot.png")]
check([f.kind for f in files] == ["text", "text", "text", "text", "error", "image"], f"kinds {[f.kind for f in files]}")
pj = project()
msg = build_message("review these", files, str(pj))
txt = msg[0]["text"]
check("Substation 132/33 kV" in txt and "XLPE 500,650" in txt and "# Title" in txt, "docx/xlsx/md text included")
check((pj / ".afif-attachments" / "big.txt").is_file() and "full copy saved as .afif-attachments/big.txt" in txt,
      "large file copied into project")
check(msg[1]["image_url"]["url"].startswith("data:image/"), "image becomes a data URL")

print("Key storage")
enc = backends.protect("sk-secret-123")
check(enc and "sk-secret" not in enc and backends.unprotect(enc) == "sk-secret-123",
      f"round trip ({enc.split(':')[0]})")

print()
print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
