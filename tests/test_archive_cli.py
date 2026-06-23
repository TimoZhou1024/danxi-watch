from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import archive_danxi


def make_jwt(exp: int) -> str:
    def encode(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': exp})}.sig"


class ArchiveCliTests(unittest.TestCase):
    @patch("scripts.archive_danxi.run_archive", return_value={"ok": True})
    def test_expiring_api_token_is_refreshed_before_archive(self, mock_run_archive) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env").write_text(
                f"DANXI_API_TOKEN={make_jwt(1)}\n"
                "DANXI_WEBVPN_USERNAME=uid\n"
                "DANXI_WEBVPN_PASSWORD=pwd\n",
                encoding="utf-8",
            )

            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                with patch.dict(os.environ, {}, clear=True):
                    argv = [
                        "archive_danxi.py",
                        "--base-urls",
                        "https://forum.fduhole.com/api",
                        "--webvpn-mode",
                        "force",
                        "--no-download-images",
                    ]
                    with patch("sys.argv", argv), patch(
                        "danxi_daily.cli.WebVPNClient.obtain_forum_api_token",
                        return_value="fresh-token",
                    ):
                        code = archive_danxi.main()
                self.assertEqual(code, 0)
            finally:
                os.chdir(old_cwd)

            env_text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("DANXI_API_TOKEN=fresh-token", env_text)

        config = mock_run_archive.call_args[0][0]
        self.assertEqual(config.api_token, "fresh-token")
        self.assertTrue(config.force_webvpn)
        self.assertIsNotNone(config.webvpn_client)

    @patch("scripts.archive_danxi.run_archive")
    def test_archive_refreshes_token_after_auth_failure_and_retries(self, mock_run_archive) -> None:
        mock_run_archive.side_effect = [RuntimeError("HTTP Error 401: Unauthorized"), {"ok": True}]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env").write_text(
                "DANXI_API_TOKEN=old-token\n"
                "DANXI_WEBVPN_USERNAME=uid\n"
                "DANXI_WEBVPN_PASSWORD=pwd\n",
                encoding="utf-8",
            )

            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                with patch.dict(os.environ, {}, clear=True):
                    argv = [
                        "archive_danxi.py",
                        "--base-urls",
                        "https://forum.fduhole.com/api",
                        "--webvpn-mode",
                        "force",
                        "--no-download-images",
                    ]
                    with patch("sys.argv", argv), patch(
                        "danxi_daily.cli.WebVPNClient.obtain_forum_api_token",
                        return_value="fresh-token",
                    ):
                        code = archive_danxi.main()
                self.assertEqual(code, 0)
            finally:
                os.chdir(old_cwd)

            env_text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("DANXI_API_TOKEN=fresh-token", env_text)

        self.assertEqual(mock_run_archive.call_count, 2)
        second_config = mock_run_archive.call_args_list[1][0][0]
        self.assertEqual(second_config.api_token, "fresh-token")


if __name__ == "__main__":
    unittest.main()
