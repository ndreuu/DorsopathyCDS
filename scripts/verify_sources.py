"""Verify local official source PDF against the pinned source catalog."""

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sources = json.loads((ROOT / "src/dorsopathy_cds/catalog/sources.json").read_text(encoding="utf-8"))
failed = False
for document in sources["documents"]:
    path = ROOT / "research/sources" / (document["id"] + ".pdf")
    if not path.is_file():
        print(document["id"] + ": missing local PDF")
        failed = True
        continue
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    matches = digest.hexdigest() == document["pdf_sha256"]
    print(document["id"] + (": SHA-256 verified" if matches else ": SHA-256 mismatch"))
    failed = failed or not matches
raise SystemExit(1 if failed else 0)
