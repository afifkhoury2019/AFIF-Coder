"""Download the latest llama.cpp Windows builds (CPU + Vulkan) into ./engine/<kind>/.

Keeps llama-server.exe and its DLLs only. Reports progress as GitHub annotations.
"""
import io
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent / "engine"
H = {"Accept": "application/vnd.github+json"}
if os.environ.get("GH_TOKEN"):
    H["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"


def note(kind, msg):
    print(f"::{kind}::{msg}", flush=True)


rel = requests.get("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", headers=H, timeout=60)
if rel.status_code != 200:
    note("error", f"GitHub API {rel.status_code}: {rel.text[:200]}")
    sys.exit(1)
rel = rel.json()
tag = rel["tag_name"]
win = [a["name"] for a in rel["assets"] if "win" in a["name"]]
note("notice", f"llama.cpp {tag}; Windows assets: {', '.join(win)}")

shutil.rmtree(ROOT, ignore_errors=True)
for kind in ("cpu", "vulkan"):
    pat = re.compile(rf"bin-win-{kind}-x64\.zip$")
    asset = next((a for a in rel["assets"] if pat.search(a["name"])), None)
    if not asset:
        note("error", f"No win-{kind}-x64 build in {tag}")
        sys.exit(1)
    data = requests.get(asset["browser_download_url"], timeout=600).content
    z = zipfile.ZipFile(io.BytesIO(data))
    server = next((n for n in z.namelist() if n.endswith("llama-server.exe")), None)
    if not server:
        note("error", f"llama-server.exe not in {asset['name']}: {z.namelist()[:20]}")
        sys.exit(1)
    prefix = server[: -len("llama-server.exe")]
    dest = ROOT / kind
    dest.mkdir(parents=True)
    kept = []
    for n in z.namelist():
        if not n.startswith(prefix) or n.endswith("/"):
            continue
        name = n[len(prefix):]
        if "/" in name:
            continue
        if name.lower().endswith(".exe") and name != "llama-server.exe":
            continue
        (dest / name).write_bytes(z.read(n))
        kept.append(name)
    mb = sum((dest / k).stat().st_size for k in kept) / 1024 ** 2
    note("notice", f"{kind}: {asset['name']} -> {len(kept)} files, {mb:.0f} MB")

lic = requests.get(f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/LICENSE", timeout=60)
(ROOT / "LICENSE-llama.cpp.txt").write_text(lic.text if lic.ok else "MIT License - see https://github.com/ggml-org/llama.cpp")
(ROOT / "VERSION.txt").write_text(tag)
