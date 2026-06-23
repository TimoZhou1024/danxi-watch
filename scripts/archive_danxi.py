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
from danxi_daily.cli import _bool_from_env, _load_dotenv, _maybe_fill_api_token, _prepare_webvpn_client, _refresh_api_token
from danxi_daily.security import normalize_allowed_hosts, require_https, validate_allowed_host
from danxi_daily.token_manager import should_retry_after_token_refresh


def main() -> int:
    env_path = Path(".env")
    _load_dotenv(env_path)
    parser = argparse.ArgumentParser(description="Archive DanXi holes into local SQLite storage.")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("DANXI_ARCHIVE_DB", "data/danxi.sqlite")))
    parser.add_argument("--image-root", type=Path, default=Path(os.getenv("DANXI_IMAGE_ROOT", "data/images")))
    parser.add_argument("--hours", type=int, default=int(os.getenv("DANXI_ARCHIVE_HOURS", "24")))
    parser.add_argument("--base-urls", default=os.getenv("DANXI_BASE_URLS", "https://forum.fduhole.com/api"))
    parser.add_argument("--allowed-read-hosts", default=os.getenv("DANXI_ALLOWED_READ_HOSTS", "forum.fduhole.com,api.fduhole.com"))
    parser.add_argument("--unsafe-allow-any-host", action="store_true")
    parser.add_argument("--fetch-limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--division-id", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--floor-fetch-size", type=int, default=80)
    parser.add_argument("--no-download-images", action="store_true")
    parser.add_argument("--from-json", type=Path, default=None, help="Import an existing holes.raw.json snapshot instead of fetching API.")
    parser.add_argument(
        "--webvpn-mode",
        choices=["auto", "off", "force"],
        default=os.getenv("DANXI_WEBVPN_MODE", "auto").strip().lower(),
        help="auto: direct first then webvpn fallback; off: disable webvpn; force: webvpn only",
    )
    parser.add_argument("--webvpn-no-prompt", action="store_true")
    parser.add_argument(
        "--webvpn-save-credentials",
        action="store_true",
        default=_bool_from_env("DANXI_WEBVPN_SAVE_CREDENTIALS", True),
    )
    parser.add_argument("--webvpn-no-save-credentials", action="store_true")
    args = parser.parse_args()

    base_urls = [x.strip().rstrip("/") for x in args.base_urls.split(",") if x.strip()]
    if not base_urls:
        parser.error("at least one base URL is required")
    read_allowlist = normalize_allowed_hosts(args.allowed_read_hosts)
    for url in base_urls:
        require_https(url)
        if not args.unsafe_allow_any_host:
            validate_allowed_host(url, read_allowlist)

    webvpn_client = None
    force_webvpn = False
    api_token = os.getenv("DANXI_API_TOKEN")
    if args.from_json is None:
        try:
            webvpn_client, force_webvpn = _prepare_webvpn_client(args, env_path, read_allowlist)
        except ValueError as exc:
            parser.error(str(exc))
        api_token = _maybe_fill_api_token(args, env_path, webvpn_client, api_token)

    config = ArchiveConfig(
        base_urls=base_urls,
        db_path=args.db,
        image_root=args.image_root,
        hours=args.hours,
        fetch_limit=args.fetch_limit,
        max_pages=args.max_pages,
        division_id=args.division_id,
        api_token=api_token,
        timeout=args.timeout,
        floor_fetch_size=max(0, args.floor_fetch_size),
        download_images=not args.no_download_images,
        webvpn_client=webvpn_client,
        force_webvpn=force_webvpn,
    )
    if args.from_json:
        result = import_snapshot(config, args.from_json)
    else:
        try:
            result = run_archive(config)
        except RuntimeError as exc:
            refreshed_token = _refresh_api_token(args, env_path, config.webvpn_client)
            if not should_retry_after_token_refresh(exc, config.api_token, refreshed_token):
                raise
            config.api_token = refreshed_token
            result = run_archive(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
