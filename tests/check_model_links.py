"""Fail the build if any model download link in the catalog is broken.

Results are printed as GitHub annotations so they show on the run summary.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from afif_coder.models import CATALOG, urls_of  # noqa: E402

bad = 0
for m in CATALOG:
    any_ok = False
    for url in urls_of(m):
        try:
            r = requests.head(url, allow_redirects=False, timeout=30)
            size = int(r.headers.get("X-Linked-Size") or r.headers.get("Content-Length") or 0) / 1024 ** 3
            ok = r.status_code in (200, 302, 307)
            close = abs(size - m["size"]) <= max(0.4, m["size"] * 0.1)
            msg = f"{url.split('.co/')[1]}: HTTP {r.status_code}, {size:.2f} GB (catalog {m['size']})" \
                + ("" if close or not ok else "  <- size differs")
        except requests.RequestException as e:
            ok, msg = False, f"{url}: {e}"
        print(("::notice::" if ok and close else "::warning::") + msg, flush=True)
        any_ok |= ok
    if not any_ok:
        print(f"::error::No working source for {m['name']}", flush=True)
    bad += not any_ok
sys.exit(1 if bad else 0)
