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
    # Try direct token refresh first (TLS 1.2, no WebVPN required).
    token = _refresh_api_token_direct(env_path=env_path, persist=persist)
    if token:
        return token

    # Fall back to WebVPN-based token refresh.
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


def _refresh_api_token_direct(
    *,
    env_path: Path,
    persist: bool = True,
) -> str | None:
    """Refresh token via auth.fduhole.com/api/login directly (no WebVPN).

    Uses TLS 1.2 since fduhole.com servers don't support TLS 1.3.
    Requires DANXI_WEBVPN_USERNAME and DANXI_WEBVPN_PASSWORD in environment.
    """
    import ssl
    import urllib.request
    import json
    from http.client import HTTPException

    username = (os.getenv("DANXI_WEBVPN_USERNAME") or "").strip()
    password = (os.getenv("DANXI_WEBVPN_PASSWORD") or "").strip()
    if not username or not password:
        return None

    # Same email candidates as WebVPNClient._candidate_forum_emails
    if "@" in username:
        emails = [username]
    else:
        emails = [f"{username}@m.fudan.edu.cn", f"{username}@fudan.edu.cn"]

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )

    last_error = ""
    for email in emails:
        payload = json.dumps({"email": email, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            "https://auth.fduhole.com/api/login",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "danxi-daily-skill/1.0",
            },
            method="POST",
        )
        try:
            with opener.open(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, dict):
                continue
            access = data.get("access")
            if isinstance(access, str) and access.strip():
                token = access.strip()
                os.environ["DANXI_API_TOKEN"] = token
                if persist:
                    upsert_dotenv(env_path, "DANXI_API_TOKEN", token)
                return token
            last_error = f"{email}: no access token"
        except urllib.error.HTTPError as exc:
            last_error = f"{email}: HTTP {exc.code}"
            continue
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, HTTPException) as exc:
            last_error = f"{email}: {exc}"
            continue

    return None


def maybe_refresh_api_token(
    api_token: str | None,
    webvpn_client: WebVPNClient | None,
    *,
    env_path: Path,
    persist: bool = True,
    margin_seconds: int = TOKEN_REFRESH_MARGIN_SECONDS,
) -> str | None:
    # Always try direct token refresh first if token expires soon.
    if token_expires_soon(api_token, margin_seconds=margin_seconds):
        token = _refresh_api_token_direct(env_path=env_path, persist=persist)
        if token:
            return token
    # Fall back to WebVPN-based refresh if a client is available.
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
