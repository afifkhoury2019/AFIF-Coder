"""Fail the build if any model download link in the catalog is broken."""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from afif_coder.models import CATALOG, url_of  # noqa: E402

bad = 0
for m in CATALOG:
    r = requests.head(url_of(m), allow_redirects=True, timeout=30)
    size = int(r.headers.get("Content-Length", 0)) / 1024 ** 3
    print(f"{r.status_code}  {size:5.1f} GB (catalog says {m['size']})  {m['file']}")
    if r.status_code != 200 or abs(size - m["size"]) > max(0.6, m["size"] * 0.15):
        bad += 1
sys.exit(1 if bad else 0)
