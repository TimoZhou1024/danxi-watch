#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danxi_daily.archive import export_pages_data
from danxi_daily.cli import _load_dotenv


def main() -> int:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Export DanXi archive data for the static web viewer.")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("DANXI_ARCHIVE_DB", "data/danxi.sqlite")))
    parser.add_argument("--out", type=Path, default=Path("exports/danxi-export.zip"))
    parser.add_argument("--include-images", action="store_true")
    args = parser.parse_args()
    result = export_pages_data(args.db, args.out, include_images=args.include_images)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
