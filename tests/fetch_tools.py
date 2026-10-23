"""Build machine (Windows): download the helper tools bundled with AFIF Coder into ./tools.

Git (MinGit), GitHub CLI, Node.js 24 + REA, Temurin JDK 21, the pinned JADX MCP JAR,
rclone, Python 3.12 with document/test libraries, and Playwright's Chromium.
Each step reports a GitHub annotation; any failure stops the build.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "tools"
GH = {"Accept": "application/vnd.github+json"}
if os.environ.get("GH_TOKEN"):
    GH["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"
JADX_URL = "https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar"
JADX_SHA = "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75"
VERSIONS = {}


def note(kind, msg):
    print(f"::{kind}::{msg}", flush=True)


def fail(msg):
    note("error", msg)
    sys.exit(1)


def get(url, **kw) -> requests.Response:
    r = requests.get(url, timeout=900, **kw)
    if r.status_code != 200:
        fail(f"download failed ({r.status_code}): {url}")
    return r


def unzip_to(data: bytes, dest: Path, strip_single_root=True):
    dest.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if n.strip("/")]
    roots = {n.split("/")[0] for n in names}
    strip = strip_single_root and len(roots) == 1 and any("/" in n for n in names)
    for n in names:
        rel = n.split("/", 1)[1] if strip and "/" in n else n
        if not rel or n.endswith("/"):
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with z.open(n) as src, open(out, "wb") as fh:
            shutil.copyfileobj(src, fh)


def gh_latest_asset(repo: str, pattern: str):
    rel = get(f"https://api.github.com/repos/{repo}/releases/latest", headers=GH).json()
    a = next((a for a in rel["assets"] if re.search(pattern, a["name"])), None)
    if not a:
        fail(f"{repo} {rel['tag_name']}: no asset matching {pattern}")
    return rel["tag_name"], a


def run(cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        fail(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}")


shutil.rmtree(T, ignore_errors=True)
T.mkdir()

# --- Git (MinGit: git without the full Git Bash) ---
tag, a = gh_latest_asset("git-for-windows/git", r"^MinGit-[\d.]+-64-bit\.zip$")
unzip_to(get(a["browser_download_url"]).content, T / "git", strip_single_root=False)
VERSIONS["Git (MinGit)"] = tag

# --- GitHub CLI ---
tag, a = gh_latest_asset("cli/cli", r"^gh_[\d.]+_windows_amd64\.zip$")
unzip_to(get(a["browser_download_url"]).content, T / "gh", strip_single_root=False)
VERSIONS["GitHub CLI"] = tag

# --- Node.js 24 (REA needs ^24.22.1) ---
idx = get("https://nodejs.org/dist/index.json").json()
node = next(v for v in idx if v["version"].startswith("v24.") and "win-x64-zip" in v["files"])
ver = node["version"]
unzip_to(get(f"https://nodejs.org/dist/{ver}/node-{ver}-win-x64.zip").content, T / "node")
VERSIONS["Node.js"] = ver

# --- REA (npm package rea-agents) ---
npm = T / "node" / "npm.cmd"
env = dict(os.environ, PATH=str(T / "node") + os.pathsep + os.environ["PATH"])
run([str(npm), "install", "--prefix", str(T / "rea"), "rea-agents@latest", "--omit=dev", "--no-audit", "--no-fund"],
    env=env, shell=False)
pkg = json.loads((T / "rea" / "node_modules" / "rea-agents" / "package.json").read_text())
VERSIONS["REA (rea-agents)"] = pkg["version"]

# --- Temurin JDK 21 (full JDK: REA's JADX provider needs jdk.compiler) ---
r = get("https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse",
        allow_redirects=True)
unzip_to(r.content, T / "jdk")
VERSIONS["Temurin JDK"] = Path(r.url).name

# --- JADX headless MCP JAR (pinned by REA) ---
jar = get(JADX_URL).content
if hashlib.sha256(jar).hexdigest() != JADX_SHA:
    fail("JADX JAR SHA-256 does not match REA's pinned value")
(T / "jadx").mkdir()
(T / "jadx" / "jadx-headless-mcp-0.7.1-all.jar").write_bytes(jar)
VERSIONS["jadx-headless-mcp"] = "0.7.1 (SHA-256 verified)"

# --- rclone ---
unzip_to(get("https://downloads.rclone.org/rclone-current-windows-amd64.zip").content, T / "rclone")
VERSIONS["rclone"] = "current"

# --- Python 3.12 (NuGet package: full CPython with pip) ---
vers = get("https://api.nuget.org/v3-flatcontainer/python/index.json").json()["versions"]
py = sorted((v for v in vers if re.fullmatch(r"3\.12\.\d+", v)), key=lambda v: int(v.split(".")[2]))[-1]
z = zipfile.ZipFile(io.BytesIO(get(f"https://api.nuget.org/v3-flatcontainer/python/{py}/python.{py}.nupkg").content))
for n in z.namelist():
    if n.startswith("tools/") and not n.endswith("/"):
        out = T / "python" / n[len("tools/"):]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(z.read(n))
VERSIONS["Python"] = py
pyexe = T / "python" / "python.exe"
run([str(pyexe), "-m", "ensurepip", "--default-pip"])
run([str(pyexe), "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check",
     "python-docx", "openpyxl", "python-pptx", "reportlab", "pypdf", "pillow", "requests",
     "arabic-reshaper", "python-bidi", "playwright"])

# --- Playwright Chromium (headless shell is enough for screenshots) ---
penv = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(T / "ms-playwright"))
r = subprocess.run([str(pyexe), "-m", "playwright", "install", "--only-shell", "chromium"], env=penv)
if r.returncode != 0:
    run([str(pyexe), "-m", "playwright", "install", "chromium"], env=penv)
VERSIONS["Playwright Chromium"] = ", ".join(sorted(p.name for p in (T / "ms-playwright").iterdir()))

# --- cleanup and notices ---
for junk in T.rglob("__pycache__"):
    shutil.rmtree(junk, ignore_errors=True)
size = sum(f.stat().st_size for f in T.rglob("*") if f.is_file()) / 1024 ** 2
(T / "VERSIONS.json").write_text(json.dumps(VERSIONS, indent=2))
(T / "THIRD-PARTY.txt").write_text(
    "Helper tools bundled with AFIF Coder. Each keeps its own licence (see its folder):\n"
    + "\n".join(f"- {k}: {v}" for k, v in VERSIONS.items())
    + "\n\nGit (GPLv2), GitHub CLI (MIT), Node.js (MIT), REA (MIT; third-party engines keep their licences), "
      "Eclipse Temurin (GPLv2 with Classpath Exception), jadx-headless-mcp (see github.com/1013503897/jadx-headless-mcp), rclone (MIT), "
      "Python (PSF), Playwright (Apache-2.0), Chromium (BSD-style).\n")
note("notice", f"Bundled tools: {size:,.0f} MB. " + "; ".join(f"{k} {v}" for k, v in VERSIONS.items()))
