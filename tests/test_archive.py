from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from danxi_daily.archive import (
    ArchiveConfig,
    connect_archive_db,
    export_pages_data,
    extract_image_urls,
    import_snapshot,
    image_url_to_relative_path,
    refresh_hole_search_text,
    upsert_floor,
    upsert_hole,
    upsert_image_ref,
    upsert_tags,
)
from danxi_daily.archive_server import get_hole_detail, search_holes
from scripts.recover_preserved_content import recover_preserved_content


def _hole() -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "hole_id": 123,
        "division_id": 1,
        "time_created": now,
        "time_updated": now,
        "view": 42,
        "reply": 2,
        "tags": [{"name": "学习", "tag_id": 1, "temperature": 10}],
        "floors": {
            "prefetch": [
                {
                    "floor_id": 1001,
                    "hole_id": 123,
                    "ranking": 0,
                    "content": "考试安排 ![](https://image.fduhole.com/i/2026/06/23/a.png)",
                    "time_created": now,
                    "time_updated": now,
                    "like": 3,
                    "dislike": 0,
                }
            ]
        },
    }


class ArchiveTests(unittest.TestCase):
    def test_extract_image_urls_and_path_mapping(self) -> None:
        urls = extract_image_urls("a ![](https://image.fduhole.com/i/2026/06/23/a.png) b")

        self.assertEqual(urls, ["https://image.fduhole.com/i/2026/06/23/a.png"])
        self.assertEqual(
            str(image_url_to_relative_path(urls[0])).replace("\\", "/"),
            "image.fduhole.com/i/2026/06/23/a.png",
        )

    def test_archive_db_upsert_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "archive.sqlite"
            conn = connect_archive_db(db_path)
            now = "2026-01-01T00:00:00Z"
            hole = _hole()
            floor = hole["floors"]["prefetch"][0]
            upsert_hole(conn, hole, now)
            upsert_floor(conn, 123, floor, now)
            upsert_tags(conn, 123, hole["tags"], now)
            upsert_image_ref(conn, "https://image.fduhole.com/i/2026/06/23/a.png", 123, 1001, now)
            conn.close()

            results = search_holes(db_path, {"q": ["考试"], "tag": ["学习"]})
            detail = get_hole_detail(db_path, 123)

        self.assertEqual(results["total"], 1)
        self.assertEqual(results["items"][0]["hole_id"], 123)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["floors"][0]["floor_id"], 1001)
        self.assertEqual(detail["floors"][0]["images"][0]["url"], "https://image.fduhole.com/i/2026/06/23/a.png")
        self.assertEqual(detail["tags"][0]["name"], "学习")

    def test_deleted_placeholder_does_not_overwrite_preserved_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "archive.sqlite"
            conn = connect_archive_db(db_path)
            now = "2026-01-01T00:00:00Z"
            hole = _hole()
            floor = hole["floors"]["prefetch"][0]
            upsert_hole(conn, hole, now)
            upsert_floor(conn, 123, floor, now)
            upsert_tags(conn, 123, hole["tags"], now)
            refresh_hole_search_text(conn, 123)

            deleted_hole = copy.deepcopy(hole)
            deleted_floor = deleted_hole["floors"]["prefetch"][0]
            deleted_floor["content"] = "该内容因色情低俗被删除"
            deleted_floor["deleted"] = True
            upsert_hole(conn, deleted_hole, "2026-01-02T00:00:00Z")
            upsert_floor(conn, 123, deleted_floor, "2026-01-02T00:00:00Z")
            refresh_hole_search_text(conn, 123)
            conn.close()

            detail = get_hole_detail(db_path, 123)
            results = search_holes(db_path, {"q": ["考试安排"]})

        assert detail is not None
        floor_detail = detail["floors"][0]
        self.assertEqual(floor_detail["content"], floor["content"])
        self.assertEqual(floor_detail["latest_content"], "该内容因色情低俗被删除")
        self.assertEqual(floor_detail["content_status"], "deleted_notice")
        self.assertEqual(floor_detail["content_notice"], "该内容因色情低俗被删除")
        self.assertEqual(results["total"], 1)

    def test_placeholder_then_normal_content_becomes_normal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "archive.sqlite"
            conn = connect_archive_db(db_path)
            now = "2026-01-01T00:00:00Z"
            hole = _hole()
            placeholder_hole = copy.deepcopy(hole)
            placeholder_hole["floors"]["prefetch"][0]["content"] = "该内容已被作者删除"
            upsert_hole(conn, placeholder_hole, now)
            upsert_floor(conn, 123, placeholder_hole["floors"]["prefetch"][0], now)

            upsert_hole(conn, hole, "2026-01-02T00:00:00Z")
            upsert_floor(conn, 123, hole["floors"]["prefetch"][0], "2026-01-02T00:00:00Z")
            refresh_hole_search_text(conn, 123)
            conn.close()

            detail = get_hole_detail(db_path, 123)

        assert detail is not None
        floor_detail = detail["floors"][0]
        self.assertEqual(floor_detail["content"], hole["floors"]["prefetch"][0]["content"])
        self.assertEqual(floor_detail["content_status"], "normal")
        self.assertIsNone(floor_detail["content_notice"])

    def test_recover_preserved_content_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "archive.sqlite"
            snapshot = root / "holes.json"
            original_hole = _hole()
            placeholder_hole = copy.deepcopy(original_hole)
            placeholder_hole["floors"]["prefetch"][0]["content"] = "该内容被作者删除"
            snapshot.write_text(json.dumps([original_hole], ensure_ascii=False), encoding="utf-8")

            conn = connect_archive_db(db_path)
            upsert_hole(conn, placeholder_hole, "2026-01-01T00:00:00Z")
            upsert_floor(conn, 123, placeholder_hole["floors"]["prefetch"][0], "2026-01-01T00:00:00Z")
            conn.close()

            result = recover_preserved_content(db_path, [snapshot])
            detail = get_hole_detail(db_path, 123)
            results = search_holes(db_path, {"q": ["考试安排"]})

        assert detail is not None
        self.assertEqual(result["floors_recovered"], 1)
        self.assertEqual(detail["floors"][0]["content"], original_hole["floors"]["prefetch"][0]["content"])
        self.assertEqual(detail["floors"][0]["content_status"], "deleted_notice")
        self.assertEqual(results["total"], 1)

    def test_export_pages_data_writes_expected_members(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "archive.sqlite"
            conn = connect_archive_db(db_path)
            now = "2026-01-01T00:00:00Z"
            hole = _hole()
            upsert_hole(conn, hole, now)
            upsert_tags(conn, 123, hole["tags"], now)
            conn.close()

            out = root / "export.zip"
            result = export_pages_data(db_path, out)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

        self.assertEqual(result["holes"], 1)
        self.assertIn("holes.jsonl", names)
        self.assertIn("image_refs.jsonl", names)
        self.assertEqual(manifest["format"], "danxi-watch-export-v1")

    def test_import_snapshot_populates_archive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snapshot = root / "holes.json"
            snapshot.write_text(json.dumps([_hole()], ensure_ascii=False), encoding="utf-8")

            result = import_snapshot(
                ArchiveConfig(
                    base_urls=["https://forum.fduhole.com/api"],
                    db_path=root / "archive.sqlite",
                    image_root=root / "images",
                    download_images=False,
                ),
                snapshot,
            )

        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["upserted_holes"], 1)


if __name__ == "__main__":
    unittest.main()
