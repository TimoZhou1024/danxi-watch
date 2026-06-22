from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from danxi_daily.detectors import detect_holes, load_watch_rules
from danxi_daily.pipeline import PipelineConfig, run_pipeline
from danxi_daily.reporter import build_detections_markdown


def _hole(hole_id: int, content: str, reply: int = 1, view: int = 10) -> dict:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "hole_id": hole_id,
        "division_id": 1,
        "content": content,
        "view": view,
        "reply": reply,
        "time_created": now_utc,
        "time_updated": now_utc,
        "floors": {
            "prefetch": [
                {"floor_id": 1, "like": 2, "content": "补充楼层信息"},
            ]
        },
    }


class DetectorTests(unittest.TestCase):
    def test_detect_holes_matches_keywords_and_excludes_noise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rules_path = Path(td) / "rules.json"
            rules_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "考试与成绩",
                            "include_keywords": ["考试", "绩点"],
                            "exclude_keywords": ["出资料"],
                            "min_reply": 0,
                            "min_view": 0,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            rules = load_watch_rules(rules_path)
            results = detect_holes(
                [
                    _hole(1, "这次考试安排出来了吗"),
                    _hole(2, "出资料 考试复习包"),
                ],
                rules,
                source_endpoint="https://forum.fduhole.com/api",
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].hole_id, 1)
        self.assertEqual(results[0].matched_keywords, ["考试"])

    def test_build_detections_markdown_empty_case(self) -> None:
        text = build_detections_markdown([])

        self.assertIn("命中结果", text)
        self.assertIn("没有命中", text)

    @patch("danxi_daily.pipeline.fetch_hole_floors", return_value=[])
    @patch("danxi_daily.pipeline.fetch_holes_with_fallback")
    def test_pipeline_writes_detection_outputs(self, mock_fetch_holes, _mock_fetch_floors) -> None:
        mock_fetch_holes.return_value = (
            [_hole(100, "一卡通丢了，有人捡到吗", reply=2, view=80)],
            "https://forum.fduhole.com/api",
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rules_path = root / "rules.json"
            rules_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "失物招领",
                            "include_keywords": ["丢了", "捡到"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = PipelineConfig(
                base_urls=["https://forum.fduhole.com/api"],
                output_markdown=root / "daily.md",
                output_holes=root / "holes.json",
                output_ranked=root / "ranked.json",
                watch_rules_path=rules_path,
                output_detections=root / "detections.json",
                output_detections_markdown=root / "detections.md",
                archive_outputs=False,
                post=False,
            )
            result = run_pipeline(config)

            self.assertEqual(result["detections"], 1)
            self.assertTrue((root / "detections.json").exists())
            self.assertTrue((root / "detections.md").exists())
            self.assertIn("失物招领", (root / "detections.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
