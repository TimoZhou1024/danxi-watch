from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
from pathlib import Path

from .webvpn import WebVPNAuthError, WebVPNClient, WebVPNError


TOKEN_REFRESH_MARGIN_SECONDS = 600


def upsert_dotenv(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated = False
    for idx, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        current_key = text.split("=", 1)[0].strip()
        if current_key == key:
            lines[idx] = f"{key}={value}"
            updated = True
            break

    if not updated:
        lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def jwt_expiration_epoch(token: str | None) -> int | None:
    if not token:
        return None
    parts = token.strip().split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    exp = data.get("exp") if isinstance(data, dict) else None
    return int(exp) if isinstance(exp, (int, float)) else None


def token_expires_soon(
    token: str | None,
    *,
    margin_seconds: int = TOKEN_REFRESH_MARGIN_SECONDS,
    now_epoch: int | None = None,
) -> bool:
    if not token or not token.strip():
        return True
    exp = jwt_expiration_epoch(token)
    if exp is None:
        return False
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    return exp <= now + max(0, margin_seconds)


def refresh_api_token(
    webvpn_client: WebVPNClient | None,
    *,
    env_path: Path,
    persist: bool = True,
) -> str | None:
    if webvpn_client is None:
        return None
    try:
        refreshed = webvpn_client.obtain_forum_api_token()
    except (WebVPNAuthError, WebVPNError):
        return None

    if not isinstance(refreshed, str) or not refreshed.strip():
        return None

    token = refreshed.strip()
    os.environ["DANXI_API_TOKEN"] = token
    if persist:
        upsert_dotenv(env_path, "DANXI_API_TOKEN", token)
    return token


def maybe_refresh_api_token(
    api_token: str | None,
    webvpn_client: WebVPNClient | None,
    *,
    env_path: Path,
    persist: bool = True,
    margin_seconds: int = TOKEN_REFRESH_MARGIN_SECONDS,
) -> str | None:
    if webvpn_client is None:
        return api_token
    if not token_expires_soon(api_token, margin_seconds=margin_seconds):
        return api_token
    return refresh_api_token(webvpn_client, env_path=env_path, persist=persist) or api_token


def looks_like_auth_failure(exc: BaseException | str) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {401, 403}
    text = str(exc).lower()
    if "http error 401" in text or "http error 403" in text:
        return True
    markers = (
        "unauthorized",
        "forbidden",
        "token",
        "jwt",
        "expired",
        "signature",
        "credential",
        "authentication",
        "authorization",
        "未登录",
        "登录",
        "认证",
        "过期",
    )
    return any(marker in text for marker in markers)


def should_retry_after_token_refresh(
    exc: BaseException,
    old_token: str | None,
    new_token: str | None,
) -> bool:
    if not new_token or new_token == old_token:
        return False
    return looks_like_auth_failure(exc) or token_expires_soon(old_token, margin_seconds=0)
