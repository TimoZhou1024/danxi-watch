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

from danxi_daily.archive import ArchiveConfig, import_snapshot, run_archive
from danxi_daily.cli import _load_dotenv


def main() -> int:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Archive DanXi holes into local SQLite storage.")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("DANXI_ARCHIVE_DB", "data/danxi.sqlite")))
    parser.add_argument("--image-root", type=Path, default=Path(os.getenv("DANXI_IMAGE_ROOT", "data/images")))
    parser.add_argument("--hours", type=int, default=int(os.getenv("DANXI_ARCHIVE_HOURS", "24")))
    parser.add_argument("--base-urls", default=os.getenv("DANXI_BASE_URLS", "https://forum.fduhole.com/api"))
    parser.add_argument("--fetch-limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--division-id", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--floor-fetch-size", type=int, default=80)
    parser.add_argument("--no-download-images", action="store_true")
    parser.add_argument("--from-json", type=Path, default=None, help="Import an existing holes.raw.json snapshot instead of fetching API.")
    args = parser.parse_args()

    base_urls = [x.strip().rstrip("/") for x in args.base_urls.split(",") if x.strip()]
    config = ArchiveConfig(
        base_urls=base_urls,
        db_path=args.db,
        image_root=args.image_root,
        hours=args.hours,
        fetch_limit=args.fetch_limit,
        max_pages=args.max_pages,
        division_id=args.division_id,
        api_token=os.getenv("DANXI_API_TOKEN"),
        timeout=args.timeout,
        floor_fetch_size=max(0, args.floor_fetch_size),
        download_images=not args.no_download_images,
    )
    result = import_snapshot(config, args.from_json) if args.from_json else run_archive(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
