#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DanXi archive repeatedly at a fixed interval.")
    parser.add_argument("--interval-minutes", type=int, default=10)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--no-download-images", action="store_true")
    args, passthrough = parser.parse_known_args()

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "archive_danxi.py"
    interval = max(1, args.interval_minutes) * 60

    while True:
        cmd = [
            sys.executable,
            str(script),
            "--hours",
            str(args.hours),
            *passthrough,
        ]
        if args.no_download_images:
            cmd.append("--no-download-images")
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{started}] running archive")
        subprocess.run(cmd, cwd=str(root), check=False)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] sleeping {interval} seconds")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
