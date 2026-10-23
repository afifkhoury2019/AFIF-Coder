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
check(last["tools"][0].get("input_schema") and "AFIF Coder" in last["system"][0]["text"], "tools + system prompt converted")
check(last["system"][0].get("cache_control") and last["tools"][-1].get("cache_control")
      and last["messages"][-1]["content"][-1].get("cache_control"), "prompt caching markers on system, tools, last turn")

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
user = [m for m in sent if m["role"] == "user"][-1]
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
user = [m for m in sent if m["role"] == "user"][-1]
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
check((pj / ".afif" / "attachments" / "big.txt").is_file() and "full copy saved as .afif/attachments/big.txt" in txt,
      "large file copied into project")
check(msg[1]["image_url"]["url"].startswith("data:image/"), "image becomes a data URL")

print("Screenshots reach vision models")
shot = Path(tempfile.mkdtemp())
img.save(str(shot / "ui.png"))
msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "check the UI"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "v1", "type": "function",
         "function": {"name": "view_image", "arguments": '{"path": "ui.png"}'}}]},
        {"role": "tool", "tool_call_id": "v1", "content": "Image ui.png attached below.",
         "_images": [msg[1]["image_url"]["url"]]}]
_, conv = backends.AnthropicBackend("k", "m").convert(msgs)
tr = conv[-1]["content"][0]
check(tr["type"] == "tool_result" and tr["content"][1]["type"] == "image", "Claude: image inside tool_result")
prep = backends.OpenAIBackend("u", "k", "m", vision=True)._prepare(msgs)
check(prep[-1]["role"] == "user" and prep[-1]["content"][1]["type"] == "image_url" and prep[-2]["role"] == "tool",
      "OpenAI-style: image follows tool results as a user turn")
prep = backends.OpenAIBackend("u", "k", "m", vision=False)._prepare(msgs)
check(prep[-1]["role"] == "tool" and "can't see images" in prep[-1]["content"], "text-only model gets a note")

print("Tools: web, commands, skills, images")
from afif_coder.tools import Ctx, Toolbox, ToolError, ToolResult  # noqa: E402
web = serve(type("W", (BaseHTTPRequestHandler,), {"log_message": lambda *a: None, "do_GET": lambda h: (
    h.send_response(200), h.send_header("Content-Type", "text/html"), h.end_headers(),
    h.wfile.write("<html><head><title>AFIF</title><script>x()</script></head><body><h1>Feeds</h1>"
                  "<p>BBC <a href='https://bbc.co.uk'>link</a></p><ul><li>one</li></ul></body></html>".encode()))}), "web")
tb = Toolbox(str(shot), {}, {})
page = tb.run("web_fetch", {"url": web + "/x"})
check("Title: AFIF" in page and "# Feeds" in page and "x()" not in page and "(https://bbc.co.uk)" in page,
      "web_fetch turns HTML into readable text")
lines = []
cmd = "python -c \"import time\nfor i in range(3): print('tick', i, flush=True); time.sleep(0.2)\"" \
    if sys.platform == "win32" else "for i in 0 1 2; do echo tick $i; sleep 0.2; done"
out = tb.run("run_command", {"command": cmd}, Ctx(on_output=lines.append))
check("[exit code 0]" in out and len([l for l in lines if "tick" in l]) == 3, "run_command streams live output")
slow = "ping -n 30 127.0.0.1" if sys.platform == "win32" else "sleep 30"
t0 = time.time()
out = tb.run("run_command", {"command": slow, "timeout_seconds": 5})
check("timed out" in out and time.time() - t0 < 15, "run_command timeout kills the process")
stop_at = time.time() + 2
t0 = time.time()
out = tb.run("run_command", {"command": slow}, Ctx(cancelled=lambda: time.time() > stop_at))
check("stopped by the user" in out and time.time() - t0 < 10, "Stop kills a running command")
r = tb.run("view_image", {"path": "ui.png"})
check(isinstance(r, ToolResult) and r.images[0].startswith("data:image/"), "view_image returns the picture")
try:
    tb.run("view_image", {"path": "../../etc/passwd"})
    check(False, "paths outside the project are refused")
except ToolError:
    check(True, "paths outside the project are refused")

from afif_coder import skills as skillmod  # noqa: E402
os.environ["AFIF_DATA"] = tempfile.mkdtemp()
sk_dir = skillmod.user_skills_dir() / "demo"
sk_dir.mkdir(parents=True)
(sk_dir / "SKILL.md").write_text("---\nname: demo-skill\ndescription: >\n  Builds demo apps\n  for tests\n---\n# Demo\nStep one.")
(sk_dir / "ref.md").write_text("reference text")
found = skillmod.discover()
check("demo-skill" in found and found["demo-skill"].description == "Builds demo apps for tests", "skill discovered with folded description")
tb2 = Toolbox(str(shot), found, {})
check("Step one." in tb2.run("use_skill", {"name": "demo-skill"}) and "ref.md" in tb2.run("use_skill", {"name": "demo-skill"}),
      "use_skill returns instructions and file list")
check(tb2.run("read_skill_file", {"skill": "demo-skill", "path": "ref.md"}) == "reference text", "read_skill_file")

print("MCP client")
fake_mcp = Path(tempfile.mkdtemp()) / "fake_mcp.py"
fake_mcp.write_text('''import json, sys
def send(m): sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
              "serverInfo": {"name": "fake", "version": "1"}}})
        send({"jsonrpc": "2.0", "id": 99, "method": "ping"})
    elif m.get("method") == "tools/list" and not m["params"].get("cursor"):
        send({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [{"name": "inspect_android_package",
              "description": "Inspect an APK", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}}],
              "nextCursor": "p2"}})
    elif m.get("method") == "tools/list":
        send({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [{"name": "patch_binary", "inputSchema": {"type": "object"}}]}})
    elif m.get("method") == "tools/call":
        a = m["params"]["arguments"]
        send({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": "package com.afiftech.news from " + a.get("path", "")}],
              "isError": False}})
''')
from afif_coder.mcp import MCPServer  # noqa: E402
srv = MCPServer("rea", [sys.executable, str(fake_mcp)]).start()
check([t["name"] for t in srv.tools] == ["inspect_android_package", "patch_binary"], "initialize + paginated tools/list")
text, imgs, err = srv.call("inspect_android_package", {"path": "app.apk"})
check(text == "package com.afiftech.news from app.apk" and not err, "tools/call returns text")
sch = srv.schemas("rea")
check(sch[0]["function"]["name"] == "rea__inspect_android_package" and sch[1]["function"]["parameters"]["properties"] == {},
      "MCP tools exposed to models with a prefix")
from afif_coder.agent import READ_ONLY_MCP  # noqa: E402
check(READ_ONLY_MCP.match("inspect_android_package") and not READ_ONLY_MCP.match("patch_binary"),
      "read-only REA tools run freely, others need approval")
srv.stop()

print("Continuity: auto skills, lessons, progress, saved chats")
bundled = Path(__file__).resolve().parent.parent / "skills"
os.environ["AFIF_DATA"] = tempfile.mkdtemp()
import afif_coder.skills as sm  # noqa: E402
sm.bundled_skills_dir = lambda: bundled
allsk = sm.discover()
check({"afif-app-rebuild", "reverse-engineer-anything", "afif-word", "afif-ui-check"} <= set(allsk), f"bundled skills found ({len(allsk)})")
rb, rea = allsk["afif-app-rebuild"], allsk["reverse-engineer-anything"]
check(rb.matches("Clone the Nabd app with my branding") and rb.matches("أريد تطبيق مثل نبض") and not rb.matches("fix this bug"),
      "playbook triggers on clone requests (English and Arabic)")
check(rea.matches("decompile my old APK") and not rea.matches("I already created the area"), "REA triggers without false hits on 'rea'")
check(rea.mcp == "rea", "REA skill connects the REA server")
pj = project()
agent = Agent({"temperature": 0.2, "max_steps": 6}, lambda: b)
agent.reset(str(pj))
check("no progress notes yet" in agent.messages[0]["content"] and "afif-app-rebuild:" in agent.messages[0]["content"],
      "system prompt lists skills and asks for progress notes")
agent._auto_skills("please rebuild the Nabd news app as AFIF News")
check(agent.loaded == ["afif-app-rebuild"] and "Ground rules" in agent.messages[-1]["content"], "matching skill loaded into the chat")
agent._auto_skills("rebuild it again")
check(agent.loaded == ["afif-app-rebuild"], "skill isn't loaded twice")
tb3 = Toolbox(str(pj), allsk, {})
tb3.run("record_lesson", {"skill": "afif-app-rebuild", "lesson": "Ammon rss.php returns HTML; use Google News search feed instead."})
check("Ammon rss.php returns HTML" in tb3.run("use_skill", {"name": "afif-app-rebuild"}), "recorded lesson appears for every model")
tb3.run("update_progress", {"content": "# Progress\n## Status\nSpec done, repo created.\n## Next steps\nFirst CI build."})
from afif_coder.project import Project  # noqa: E402
P = Project(str(pj))
P.set_pinned(["afif-app-rebuild"])
agent.reset(str(pj))
sp = agent.messages[0]["content"]
check("Spec done, repo created." in sp and 'pinned="true"' in sp and "Ammon rss.php" in sp,
      "new chat sees progress notes, pinned skill and its lessons")
agent.messages.append({"role": "user", "content": "hello"})
agent.messages.append({"role": "assistant", "content": "hi"})
agent.loaded = ["afif-word"]
agent.save()
again = Agent({}, None)
again.reset(str(pj))
shown = again.resume(P.load_chat())
check([m["content"] for m in shown] == ["hello", "hi"] and again.loaded == ["afif-word"], "saved chat resumes with its loaded skills")
check((pj / ".afif" / ".gitignore").read_text().startswith("chats/"), "chats are kept out of git")
again.new_chat()
check(P.load_chat() is None and list((pj / ".afif" / "chats").glob("chat-*.json")), "new chat archives the old one")

print("Key storage")
enc = backends.protect("sk-secret-123")
check(enc and "sk-secret" not in enc and backends.unprotect(enc) == "sk-secret-123",
      f"round trip ({enc.split(':')[0]})")

print()
print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
