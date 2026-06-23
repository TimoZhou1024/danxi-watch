#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danxi_daily.archive import (
    connect_archive_db,
    extract_image_urls,
    is_allowed_image_url,
    is_deleted_placeholder,
    refresh_hole_search_text,
    stored_preserved_content,
    upsert_image_ref,
    utc_now_iso,
    _floors_from_hole,
)
from danxi_daily.cli import _load_dotenv
from danxi_daily.models import normalize_hole_id
from danxi_daily.utils import parse_int


def recover_preserved_content(
    db_path: Path,
    snapshot_paths: list[Path],
    dry_run: bool = False,
) -> dict[str, Any]:
    conn = connect_archive_db(db_path)
    stats: dict[str, Any] = {
        "db_path": str(db_path),
        "files_scanned": 0,
        "holes_examined": 0,
        "floors_examined": 0,
        "floors_recovered": 0,
        "image_refs_added": 0,
        "missing_floors": 0,
        "dry_run": dry_run,
    }
    affected_holes: set[int] = set()
    now = utc_now_iso()
    try:
        for path in snapshot_paths:
            holes = load_snapshot_holes(path)
            if holes is None:
                continue
            stats["files_scanned"] += 1
            for hole in holes:
                stats["holes_examined"] += 1
                try:
                    snapshot_hole_id = normalize_hole_id(hole)
                except ValueError:
                    continue
                for floor in _floors_from_hole(hole):
                    if not isinstance(floor, dict):
                        continue
                    content = floor.get("content")
                    if not content or is_deleted_placeholder(content):
                        continue
                    floor_id = parse_int(floor.get("floor_id") or floor.get("id"), -1)
                    if floor_id < 0:
                        continue
                    stats["floors_examined"] += 1
                    row = conn.execute("SELECT * FROM floors WHERE floor_id = ?", (floor_id,)).fetchone()
                    if row is None:
                        stats["missing_floors"] += 1
                        continue

                    preserved = stored_preserved_content(row)
                    current_content = row["content"]
                    latest_content = row["latest_content"] or current_content
                    should_update_preserved = not preserved
                    should_update_display = not current_content or is_deleted_placeholder(current_content)
                    if not should_update_preserved and not should_update_display:
                        continue

                    display_content = str(content) if should_update_display else current_content
                    preserved_content = preserved or str(content)
                    status = "deleted_notice" if is_deleted_placeholder(latest_content) else (row["content_status"] or "normal")
                    notice = latest_content if status == "deleted_notice" else row["content_notice"]
                    floor_raw = json.dumps(floor, ensure_ascii=False, sort_keys=True)

                    stats["floors_recovered"] += 1
                    db_hole_id = int(row["hole_id"] or snapshot_hole_id)
                    affected_holes.add(db_hole_id)
                    if dry_run:
                        continue

                    conn.execute(
                        """
                        UPDATE floors
                        SET content=?, preserved_content=?, content_status=?, content_notice=?,
                            preserved_raw_json=?
                        WHERE floor_id=?
                        """,
                        (display_content, preserved_content, status, notice, floor_raw, floor_id),
                    )
                    for url in extract_image_urls(str(content)):
                        if is_allowed_image_url(url):
                            upsert_image_ref(conn, url, db_hole_id, floor_id, now)
                            stats["image_refs_added"] += 1

        if not dry_run:
            for hole_id in affected_holes:
                refresh_hole_search_text(conn, hole_id)
            conn.commit()
        stats["holes_refreshed"] = len(affected_holes)
        return stats
    finally:
        conn.close()


def load_snapshot_holes(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        holes = payload.get("holes") or payload.get("items")
        if isinstance(holes, list):
            return [item for item in holes if isinstance(item, dict)]
    return None


def default_snapshot_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    history = root / "outputs" / "history"
    if history.exists():
        candidates.extend(history.glob("**/holes_*.json"))
    raw = root / "outputs" / "holes.raw.json"
    if raw.exists():
        candidates.append(raw)
    return sorted({path.resolve() for path in candidates})


def expand_snapshot_args(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        matches = glob.glob(value, recursive=True)
        if matches:
            paths.extend(Path(match) for match in matches)
        else:
            paths.append(Path(value))
    return sorted({path.resolve() for path in paths})


def main() -> int:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Recover preserved floor content from historical DanXi snapshots.")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("DANXI_ARCHIVE_DB", "data/danxi.sqlite")))
    parser.add_argument("--snapshot", action="append", default=[], help="Snapshot JSON path or glob. Can be repeated.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    snapshots = expand_snapshot_args(args.snapshot) if args.snapshot else default_snapshot_paths(Path("."))
    result = recover_preserved_content(args.db, snapshots, dry_run=args.dry_run)
    result["snapshots"] = [str(path) for path in snapshots]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
