"""Build machine (Windows): prove every bundled tool works the way the skills use it.

Run after fetch_tools.py with AFIF_TOOLS pointing at ./tools.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("AFIF_TOOLS", str(ROOT / "tools"))
os.environ.setdefault("AFIF_DATA", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))
from afif_coder import tooling  # noqa: E402

FAILS, LINES = [], []


def check(cond, what, detail=""):
    line = ("ok   " if cond else "FAIL ") + what + (f" ({detail})" if detail and not cond else "")
    print(line, flush=True)
    LINES.append(line)
    if not cond:
        FAILS.append(what)


def out(args, timeout=120, cwd=None):
    exe = tooling.which(args[0])
    if not exe:
        return 127, f"{args[0]} not found on bundled PATH"
    r = subprocess.run([exe, *args[1:]], capture_output=True, text=True, timeout=timeout, env=tooling.env(), cwd=cwd,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


inv = {n: ok for n, _, ok in tooling.inventory()}
check(all(inv.values()), "all bundled tools detected", ", ".join(n for n, ok in inv.items() if not ok))
for name in ("git", "gh", "node", "python", "java", "rclone"):
    p = tooling.which(name) or ""
    check(str(tooling.tools_dir()).lower() in p.lower(), f"{name} resolves to the bundled copy", p)

c, o = out(["git", "--version"]); check(c == 0, "git runs", o)
work = Path(tempfile.mkdtemp())
c, o = out(["git", "init", "-q", str(work / "r")]); check(c == 0, "git init", o)
c, o = out(["gh", "--version"]); check(c == 0 and "gh version" in o, "gh runs", o)
c, o = out(["node", "--version"]); check(c == 0 and o.strip().startswith("v24."), "node 24", o)
c, o = out(["java", "--list-modules"]); check(c == 0 and "jdk.compiler" in o, "full JDK with jdk.compiler", o[:200])
check(bool(tooling.jadx_jar()), "JADX MCP JAR in place")
c, o = out(["rclone", "version"]); check(c == 0 and "rclone" in o, "rclone runs", o)
c, o = out(["python", "-c", "import docx, openpyxl, pptx, reportlab, pypdf, playwright, arabic_reshaper, bidi; print('ok')"])
check(c == 0 and "ok" in o, "python document + test libraries import", o)

# Skills' code samples run with the bundled Python
import re  # noqa: E402
for skill in ("afif-word", "afif-excel", "afif-powerpoint", "afif-pdf"):
    code = re.findall(r"```python\n(.*?)```", (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8"), re.S)[0]
    d = Path(tempfile.mkdtemp()); (d / "out").mkdir(); (d / "s.py").write_text(code, encoding="utf-8")
    c, o = out(["python", "s.py"], cwd=d)
    check(c == 0 and any((d / "out").iterdir()), f"{skill} sample runs with bundled Python", o[-300:])

# Playwright UI check from the afif-ui-check skill, against a small page with a mocked bridge
script = re.findall(r"```python\n(.*?)```", (ROOT / "skills/afif-ui-check/SKILL.md").read_text(encoding="utf-8"), re.S)[0]
d = Path(tempfile.mkdtemp())
(d / "tools").mkdir(); (d / "tools/ui_check.py").write_text(script, encoding="utf-8")
(d / "index.html").write_text("""<!doctype html><meta charset=utf-8><body dir=rtl style="font-family:sans-serif">
<h1>AFIF News</h1><ul id=l></ul><script>
window.__nf=(id,ok,t)=>{const d=new DOMParser().parseFromString(t,'application/xml');
[...d.getElementsByTagName('item')].forEach(i=>{const li=document.createElement('li');li.textContent=i.querySelector('title').textContent;l.append(li)})};
Native.fetch(1,'https://feeds.bbci.co.uk/arabic/rss.xml',0);</script>""", encoding="utf-8")
c, o = out(["python", "tools/ui_check.py", "index.html"], timeout=300, cwd=d)
shots = list((d / "ui-shots").glob("*.png")) if (d / "ui-shots").is_dir() else []
check(c == 0 and "JS errors: none" in o and len(shots) == 2, "Playwright screenshots (light + dark), no JS errors", o[-400:])

# REA MCP server: start, list tools, Android tools present
from afif_coder.mcp import MCPServer  # noqa: E402
cmd = tooling.rea_launcher()
check(bool(cmd), "REA launcher found")
if cmd:
    t0 = time.time()
    try:
        srv = MCPServer("rea", cmd, {"REA_MCP_INPUT_SCHEMA_PROFILE": "compact"}).start(timeout=240)
        names = [t["name"] for t in srv.tools]
        check(len(names) > 10, f"REA MCP answered with {len(names)} tools in {time.time() - t0:.0f}s")
        check(any("android" in n for n in names), "REA Android tools available", ", ".join(names[:15]))
        sch = srv.schemas("rea")
        check(all(len(s["function"]["name"]) <= 64 for s in sch), "REA tool names fit model limits")
        srv.stop()
    except Exception as e:
        check(False, "REA MCP server starts", f"{type(e).__name__}: {e} | {getattr(locals().get('srv'), 'last_error', lambda: '')()}")

print()
print("::notice title=Bundled tools check::" + "%0A".join(LINES))
print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
