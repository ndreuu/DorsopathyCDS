#!/usr/bin/env python3
"""Fetch pinned official ultrasound assets into an ignored local directory."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dorsopathy_workers.ultrasound.fetch import main


if __name__ == "__main__":
    raise SystemExit(main())
