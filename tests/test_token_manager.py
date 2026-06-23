from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from danxi_daily.token_manager import (
    jwt_expiration_epoch,
    maybe_refresh_api_token,
    refresh_api_token,
    token_expires_soon,
)
from danxi_daily.webvpn import WebVPNError


def make_jwt(exp: int) -> str:
    def encode(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': exp})}.sig"


class FakeTokenClient:
    def __init__(self, token: str = "fresh-token") -> None:
        self.token = token
        self.calls = 0

    def obtain_forum_api_token(self) -> str:
        self.calls += 1
        return self.token


class TokenManagerTests(unittest.TestCase):
    def test_jwt_expiry_detection(self) -> None:
        token = make_jwt(1_000)

        self.assertEqual(jwt_expiration_epoch(token), 1_000)
        self.assertTrue(token_expires_soon(token, now_epoch=1_000, margin_seconds=0))
        self.assertTrue(token_expires_soon(token, now_epoch=500, margin_seconds=600))
        self.assertFalse(token_expires_soon(token, now_epoch=100, margin_seconds=600))
        self.assertFalse(token_expires_soon("opaque-token", now_epoch=1_000, margin_seconds=600))

    def test_maybe_refreshes_missing_or_expiring_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            env_path.write_text("DANXI_API_TOKEN=old\n", encoding="utf-8")
            client = FakeTokenClient()

            with patch.dict(os.environ, {}, clear=True):
                token = maybe_refresh_api_token(
                    make_jwt(1_000),
                    client,  # type: ignore[arg-type]
                    env_path=env_path,
                    persist=True,
                    margin_seconds=600,
                )

            self.assertEqual(token, "fresh-token")
            self.assertEqual(client.calls, 1)
            self.assertIn("DANXI_API_TOKEN=fresh-token", env_path.read_text(encoding="utf-8"))

    def test_does_not_refresh_valid_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            client = FakeTokenClient()
            valid_token = make_jwt(int(time.time()) + 3_600)

            token = maybe_refresh_api_token(
                valid_token,
                client,  # type: ignore[arg-type]
                env_path=env_path,
                persist=True,
                margin_seconds=600,
            )

            self.assertEqual(token, valid_token)
            self.assertEqual(client.calls, 0)

    def test_refresh_failure_keeps_old_token(self) -> None:
        class FailingClient:
            def obtain_forum_api_token(self) -> str:
                raise WebVPNError("auth failed")

        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            token = refresh_api_token(FailingClient(), env_path=env_path)  # type: ignore[arg-type]

        self.assertIsNone(token)


if __name__ == "__main__":
    unittest.main()
