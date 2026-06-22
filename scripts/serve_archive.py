#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danxi_daily.archive_server import serve_archive
from danxi_daily.cli import _load_dotenv


def main() -> int:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Serve local DanXi archive API.")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("DANXI_ARCHIVE_DB", "data/danxi.sqlite")))
    parser.add_argument("--image-root", type=Path, default=Path(os.getenv("DANXI_IMAGE_ROOT", "data/images")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    serve_archive(args.db, args.image_root, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
