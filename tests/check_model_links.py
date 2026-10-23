"""Fail the build if any model download link in the catalog is broken.

Results are printed as GitHub annotations so they show on the run summary.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from afif_coder.models import CATALOG, url_of  # noqa: E402

bad = 0
for m in CATALOG:
    try:
        r = requests.head(url_of(m), allow_redirects=False, timeout=30)
        size = int(r.headers.get("X-Linked-Size") or r.headers.get("Content-Length") or 0) / 1024 ** 3
        ok = r.status_code in (200, 302, 307) and (size == 0 or abs(size - m["size"]) <= max(0.6, m["size"] * 0.15))
        msg = f"{m['file']}: HTTP {r.status_code}, {size:.2f} GB (catalog {m['size']})"
    except requests.RequestException as e:
        ok, msg = False, f"{m['file']}: {e}"
    print(("::notice::" if ok else "::error::") + msg, flush=True)
    bad += not ok
sys.exit(1 if bad else 0)
