from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .client import fetch_hole_floors, fetch_holes_with_fallback
from .models import normalize_hole_id
from .utils import ensure_parent, parse_int, parse_iso8601
from .webvpn import WebVPNClient


IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
DELETED_NOTICE_EXACT = {
    "该内容已被作者删除",
    "该内容被作者删除",
    "该内容因色情低俗被删除",
    "该内容因违反社区规范被删除",
    "该内容正在审核中",
}
ALLOWED_IMAGE_HOSTS = {"image.fduhole.com"}
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class ArchiveConfig:
    base_urls: list[str]
    db_path: Path = Path("data/danxi.sqlite")
    image_root: Path = Path("data/images")
    hours: int = 24
    fetch_limit: int = 10
    max_pages: int = 300
    division_id: int | None = None
    api_token: str | None = None
    timeout: int = 20
    floor_fetch_size: int = 80
    download_images: bool = True
    image_retry_limit: int = 3
    webvpn_client: WebVPNClient | None = None
    force_webvpn: bool = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_time_cursor() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def utc_hours_ago_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect_archive_db(path: Path) -> sqlite3.Connection:
    ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_archive_db(conn)
    return conn


def init_archive_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS holes (
            hole_id INTEGER PRIMARY KEY,
            division_id INTEGER,
            time_created TEXT,
            time_updated TEXT,
            time_deleted TEXT,
            view_count INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            favorite_count INTEGER DEFAULT 0,
            subscription_count INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0,
            locked INTEGER DEFAULT 0,
            good INTEGER DEFAULT 0,
            frozen INTEGER DEFAULT 0,
            no_purge INTEGER DEFAULT 0,
            ai_summary_available INTEGER DEFAULT 0,
            search_text TEXT DEFAULT '',
            raw_json TEXT NOT NULL,
            preserved_raw_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS floors (
            floor_id INTEGER PRIMARY KEY,
            hole_id INTEGER NOT NULL,
            ranking INTEGER,
            reply_to INTEGER,
            anonyname TEXT,
            content TEXT,
            latest_content TEXT,
            preserved_content TEXT,
            content_status TEXT DEFAULT 'normal',
            content_notice TEXT,
            time_created TEXT,
            time_updated TEXT,
            deleted INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            dislike_count INTEGER DEFAULT 0,
            raw_json TEXT NOT NULL,
            preserved_raw_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            FOREIGN KEY (hole_id) REFERENCES holes(hole_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS hole_tags (
            hole_id INTEGER NOT NULL,
            tag_id INTEGER,
            name TEXT NOT NULL,
            temperature INTEGER,
            nsfw INTEGER DEFAULT 0,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (hole_id, name),
            FOREIGN KEY (hole_id) REFERENCES holes(hole_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS images (
            url TEXT PRIMARY KEY,
            local_path TEXT,
            host TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            size_bytes INTEGER,
            content_type TEXT,
            attempts INTEGER DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_attempt_at TEXT
        );

        CREATE TABLE IF NOT EXISTS image_refs (
            url TEXT NOT NULL,
            hole_id INTEGER NOT NULL,
            floor_id INTEGER,
            PRIMARY KEY (url, hole_id, floor_id),
            FOREIGN KEY (url) REFERENCES images(url) ON DELETE CASCADE,
            FOREIGN KEY (hole_id) REFERENCES holes(hole_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS archive_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            fetched_count INTEGER DEFAULT 0,
            upserted_holes INTEGER DEFAULT 0,
            upserted_floors INTEGER DEFAULT 0,
            image_downloaded INTEGER DEFAULT 0,
            image_failed INTEGER DEFAULT 0,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_holes_time_updated ON holes(time_updated);
        CREATE INDEX IF NOT EXISTS idx_holes_time_created ON holes(time_created);
        CREATE INDEX IF NOT EXISTS idx_holes_counts ON holes(reply_count, view_count);
        CREATE INDEX IF NOT EXISTS idx_floors_hole ON floors(hole_id);
        CREATE INDEX IF NOT EXISTS idx_tags_name ON hole_tags(name);
        CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
        """
    )
    migrate_archive_db(conn)
    conn.commit()


def migrate_archive_db(conn: sqlite3.Connection) -> None:
    holes_raw_added = ensure_column(conn, "holes", "preserved_raw_json", "TEXT")
    floor_schema_changed = any(
        [
            ensure_column(conn, "floors", "latest_content", "TEXT"),
            ensure_column(conn, "floors", "preserved_content", "TEXT"),
            ensure_column(conn, "floors", "content_status", "TEXT DEFAULT 'normal'"),
            ensure_column(conn, "floors", "content_notice", "TEXT"),
            ensure_column(conn, "floors", "preserved_raw_json", "TEXT"),
        ]
    )
    if holes_raw_added:
        conn.execute("UPDATE holes SET preserved_raw_json = raw_json WHERE preserved_raw_json IS NULL")
    if not floor_schema_changed:
        return

    rows = conn.execute(
        """
        SELECT floor_id, content, latest_content, preserved_content, content_status,
               content_notice, deleted, raw_json, preserved_raw_json
        FROM floors
        """
    ).fetchall()
    for row in rows:
        content = row["content"]
        latest_content = row["latest_content"] if row["latest_content"] is not None else content
        preserved_content = stored_preserved_content(row)
        status = row["content_status"] or "normal"
        notice = row["content_notice"]
        deleted = int(row["deleted"] or 0)

        if is_deleted_placeholder(latest_content):
            status = "deleted_notice"
            notice = latest_content
            deleted = 1
        elif status == "deleted_notice" and not is_deleted_placeholder(latest_content):
            status = "normal"
            notice = None

        display_content = content
        if is_deleted_placeholder(display_content) and preserved_content:
            display_content = preserved_content

        preserved_raw_json = row["preserved_raw_json"]
        if preserved_content and preserved_raw_json is None:
            preserved_raw_json = row["raw_json"]

        conn.execute(
            """
            UPDATE floors
            SET content=?, latest_content=?, preserved_content=?, content_status=?,
                content_notice=?, deleted=?, preserved_raw_json=?
            WHERE floor_id=?
            """,
            (
                display_content,
                latest_content,
                preserved_content,
                status,
                notice,
                deleted,
                preserved_raw_json,
                row["floor_id"],
            ),
        )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        return True
    return False


def run_archive(config: ArchiveConfig) -> dict[str, Any]:
    conn = connect_archive_db(config.db_path)
    started_at = utc_now_iso()
    run_id = _start_run(conn, started_at)
    result = {
        "db_path": str(config.db_path),
        "image_root": str(config.image_root),
        "fetched": 0,
        "upserted_holes": 0,
        "upserted_floors": 0,
        "images_downloaded": 0,
        "images_failed": 0,
    }
    try:
        holes, endpoint = fetch_archive_holes(config)
        result["used_endpoint"] = endpoint
        result["fetched"] = len(holes)
        stored = store_holes(conn, config, holes, endpoint=endpoint, fetch_missing_floors=True)
        result.update(stored)

        _finish_run(conn, run_id, "ok", result)
        return result
    except Exception as exc:
        _finish_run(conn, run_id, "error", result, error=str(exc))
        raise
    finally:
        conn.close()


def import_snapshot(config: ArchiveConfig, snapshot_path: Path) -> dict[str, Any]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("snapshot must be a JSON list of holes")
    holes = [item for item in payload if isinstance(item, dict)]
    conn = connect_archive_db(config.db_path)
    started_at = utc_now_iso()
    run_id = _start_run(conn, started_at)
    result = {
        "db_path": str(config.db_path),
        "image_root": str(config.image_root),
        "used_endpoint": "snapshot",
        "fetched": len(holes),
        "upserted_holes": 0,
        "upserted_floors": 0,
        "images_downloaded": 0,
        "images_failed": 0,
    }
    try:
        result.update(store_holes(conn, config, holes, endpoint="snapshot", fetch_missing_floors=False))
        _finish_run(conn, run_id, "ok", result)
        return result
    except Exception as exc:
        _finish_run(conn, run_id, "error", result, error=str(exc))
        raise
    finally:
        conn.close()


def store_holes(
    conn: sqlite3.Connection,
    config: ArchiveConfig,
    holes: list[dict[str, Any]],
    endpoint: str,
    fetch_missing_floors: bool,
) -> dict[str, int]:
    result = {
        "upserted_holes": 0,
        "upserted_floors": 0,
        "images_downloaded": 0,
        "images_failed": 0,
    }
    now = utc_now_iso()
    for hole in holes:
        hole_id = normalize_hole_id(hole)
        floors = _floors_from_hole(hole)
        if fetch_missing_floors and config.floor_fetch_size > 0:
            fetched_floors = fetch_hole_floors(
                base_url=endpoint,
                hole_id=hole_id,
                token=config.api_token,
                size=config.floor_fetch_size,
                timeout=config.timeout,
                webvpn_client=config.webvpn_client,
                force_webvpn=config.force_webvpn,
            )
            if fetched_floors:
                floors = fetched_floors
                hole.setdefault("floors", {})["prefetch"] = fetched_floors

        upsert_hole(conn, hole, now)
        result["upserted_holes"] += 1
        upsert_tags(conn, hole_id, hole.get("tags"), now)
        for floor in floors:
            if not isinstance(floor, dict):
                continue
            floor_id = parse_int(floor.get("floor_id") or floor.get("id"), -1)
            upsert_floor(conn, hole_id, floor, now)
            result["upserted_floors"] += 1
            for url in extract_image_urls(str(floor.get("content") or "")):
                if is_allowed_image_url(url):
                    upsert_image_ref(conn, url, hole_id, floor_id, now)
        refresh_hole_search_text(conn, hole_id)

    if config.download_images:
        downloaded, failed = download_pending_images(
            conn,
            image_root=config.image_root,
            retry_limit=config.image_retry_limit,
            timeout=config.timeout,
        )
        result["images_downloaded"] = downloaded
        result["images_failed"] = failed
    return result


def fetch_archive_holes(config: ArchiveConfig) -> tuple[list[dict[str, Any]], str]:
    start_time = utc_hours_ago_iso(config.hours)
    offset: str | None = local_time_cursor()
    previous_cursor: str | None = None
    merged: dict[int, dict[str, Any]] = {}
    endpoint = config.base_urls[0].rstrip("/")
    page_size = max(1, min(config.fetch_limit, 10))

    for _ in range(max(1, config.max_pages)):
        holes, endpoint = fetch_holes_with_fallback(
            base_urls=config.base_urls,
            start_time=start_time,
            limit=page_size,
            offset=offset,
            division_id=config.division_id,
            token=config.api_token,
            timeout=config.timeout,
            webvpn_client=config.webvpn_client,
            force_webvpn=config.force_webvpn,
        )
        if not holes:
            break

        new_count = 0
        for hole in holes:
            try:
                hole_id = normalize_hole_id(hole)
            except ValueError:
                continue
            if hole_id not in merged:
                new_count += 1
            merged[hole_id] = hole

        if len(holes) < page_size or new_count == 0:
            break

        next_cursor = page_time_cursor(holes)
        if not next_cursor or next_cursor == previous_cursor:
            break
        previous_cursor = next_cursor
        offset = next_cursor

    return list(merged.values()), endpoint


def page_time_cursor(holes: list[dict[str, Any]]) -> str | None:
    oldest: datetime | None = None
    for hole in holes:
        dt = parse_iso8601(hole.get("time_updated")) or parse_iso8601(hole.get("time_created"))
        if dt is not None and (oldest is None or dt < oldest):
            oldest = dt
    if oldest is None:
        return None
    return oldest.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def upsert_hole(conn: sqlite3.Connection, hole: dict[str, Any], now: str) -> None:
    hole_id = normalize_hole_id(hole)
    floors = _floors_from_hole(hole)
    search_text = build_search_text_for_upsert(conn, hole_id, hole, floors)
    existing = conn.execute(
        "SELECT first_seen_at, raw_json, preserved_raw_json FROM holes WHERE hole_id = ?",
        (hole_id,),
    ).fetchone()
    first_seen = existing["first_seen_at"] if existing else now
    raw_json = json.dumps(hole, ensure_ascii=False, sort_keys=True)
    has_preservable_floor_content = hole_has_preservable_floor_content(floors)
    preserved_raw_json = raw_json if has_preservable_floor_content else None
    if existing and not has_preservable_floor_content:
        preserved_raw_json = existing["preserved_raw_json"] or existing["raw_json"]
    conn.execute(
        """
        INSERT INTO holes (
            hole_id, division_id, time_created, time_updated, time_deleted, view_count,
            reply_count, favorite_count, subscription_count, hidden, locked, good,
            frozen, no_purge, ai_summary_available, search_text, raw_json, preserved_raw_json,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(hole_id) DO UPDATE SET
            division_id=excluded.division_id,
            time_created=excluded.time_created,
            time_updated=excluded.time_updated,
            time_deleted=excluded.time_deleted,
            view_count=excluded.view_count,
            reply_count=excluded.reply_count,
            favorite_count=excluded.favorite_count,
            subscription_count=excluded.subscription_count,
            hidden=excluded.hidden,
            locked=excluded.locked,
            good=excluded.good,
            frozen=excluded.frozen,
            no_purge=excluded.no_purge,
            ai_summary_available=excluded.ai_summary_available,
            search_text=excluded.search_text,
            raw_json=excluded.raw_json,
            preserved_raw_json=excluded.preserved_raw_json,
            last_seen_at=excluded.last_seen_at
        """,
        (
            hole_id,
            parse_int(hole.get("division_id"), 0) or None,
            hole.get("time_created"),
            hole.get("time_updated"),
            hole.get("time_deleted"),
            parse_int(hole.get("view"), 0),
            parse_int(hole.get("reply"), 0),
            parse_int(hole.get("favorite_count"), 0),
            parse_int(hole.get("subscription_count"), 0),
            int(bool(hole.get("hidden"))),
            int(bool(hole.get("locked"))),
            int(bool(hole.get("good"))),
            int(bool(hole.get("frozen"))),
            int(bool(hole.get("no_purge"))),
            int(bool(hole.get("ai_summary_available"))),
            search_text,
            raw_json,
            preserved_raw_json,
            first_seen,
            now,
        ),
    )
    conn.commit()


def upsert_floor(conn: sqlite3.Connection, hole_id: int, floor: dict[str, Any], now: str) -> None:
    floor_id = parse_int(floor.get("floor_id") or floor.get("id"), -1)
    if floor_id < 0:
        return
    existing = conn.execute("SELECT * FROM floors WHERE floor_id = ?", (floor_id,)).fetchone()
    first_seen = existing["first_seen_at"] if existing else now
    incoming_content = floor.get("content")
    incoming_text = str(incoming_content) if incoming_content is not None else None
    incoming_is_notice = is_deleted_placeholder(incoming_text)
    existing_preserved_content = stored_preserved_content(existing) if existing else None
    existing_preserved_raw = None
    if existing:
        existing_preserved_raw = existing["preserved_raw_json"] or existing["raw_json"]

    raw_json = json.dumps(floor, ensure_ascii=False, sort_keys=True)
    if incoming_text is None and existing:
        display_content = existing["content"]
        latest_content = existing["latest_content"]
        preserved_content = existing_preserved_content
        content_status = existing["content_status"] or "normal"
        content_notice = existing["content_notice"]
        preserved_raw_json = existing_preserved_raw
    elif incoming_is_notice:
        preserved_content = existing_preserved_content
        display_content = preserved_content or incoming_text
        latest_content = incoming_text
        content_status = "deleted_notice"
        content_notice = incoming_text
        preserved_raw_json = existing_preserved_raw if preserved_content else None
    else:
        display_content = incoming_text
        latest_content = incoming_text
        preserved_content = incoming_text if incoming_text else existing_preserved_content
        content_status = "normal"
        content_notice = None
        preserved_raw_json = raw_json if incoming_text else existing_preserved_raw

    deleted = int(bool(floor.get("deleted")) or incoming_is_notice)
    conn.execute(
        """
        INSERT INTO floors (
            floor_id, hole_id, ranking, reply_to, anonyname, content, latest_content,
            preserved_content, content_status, content_notice, time_created, time_updated,
            deleted, like_count, dislike_count, raw_json, preserved_raw_json, first_seen_at,
            last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(floor_id) DO UPDATE SET
            hole_id=excluded.hole_id,
            ranking=excluded.ranking,
            reply_to=excluded.reply_to,
            anonyname=excluded.anonyname,
            content=excluded.content,
            latest_content=excluded.latest_content,
            preserved_content=excluded.preserved_content,
            content_status=excluded.content_status,
            content_notice=excluded.content_notice,
            time_created=excluded.time_created,
            time_updated=excluded.time_updated,
            deleted=excluded.deleted,
            like_count=excluded.like_count,
            dislike_count=excluded.dislike_count,
            raw_json=excluded.raw_json,
            preserved_raw_json=excluded.preserved_raw_json,
            last_seen_at=excluded.last_seen_at
        """,
        (
            floor_id,
            hole_id,
            parse_int(floor.get("ranking"), 0),
            parse_int(floor.get("reply_to"), 0),
            floor.get("anonyname"),
            display_content,
            latest_content,
            preserved_content,
            content_status,
            content_notice,
            floor.get("time_created"),
            floor.get("time_updated"),
            deleted,
            parse_int(floor.get("like"), 0),
            parse_int(floor.get("dislike"), 0),
            raw_json,
            preserved_raw_json,
            first_seen,
            now,
        ),
    )
    conn.commit()


def upsert_tags(conn: sqlite3.Connection, hole_id: int, tags: Any, now: str) -> None:
    conn.execute("DELETE FROM hole_tags WHERE hole_id = ?", (hole_id,))
    if not isinstance(tags, list):
        conn.commit()
        return
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        name = str(tag.get("name") or "").strip()
        if not name:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO hole_tags
                (hole_id, tag_id, name, temperature, nsfw, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                hole_id,
                parse_int(tag.get("tag_id") or tag.get("id"), 0) or None,
                name,
                parse_int(tag.get("temperature"), 0),
                int(bool(tag.get("nsfw"))),
                json.dumps(tag, ensure_ascii=False, sort_keys=True),
            ),
        )
    conn.commit()


def upsert_image_ref(conn: sqlite3.Connection, url: str, hole_id: int, floor_id: int, now: str) -> None:
    parsed = urllib.parse.urlparse(url)
    existing = conn.execute("SELECT first_seen_at FROM images WHERE url = ?", (url,)).fetchone()
    first_seen = existing["first_seen_at"] if existing else now
    conn.execute(
        """
        INSERT INTO images (url, host, status, first_seen_at, last_seen_at)
        VALUES (?, ?, 'pending', ?, ?)
        ON CONFLICT(url) DO UPDATE SET last_seen_at=excluded.last_seen_at
        """,
        (url, parsed.hostname or "", first_seen, now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO image_refs (url, hole_id, floor_id)
        VALUES (?, ?, ?)
        """,
        (url, hole_id, floor_id if floor_id >= 0 else None),
    )
    conn.commit()


def download_pending_images(
    conn: sqlite3.Connection,
    image_root: Path,
    retry_limit: int = 3,
    timeout: int = 20,
) -> tuple[int, int]:
    ensure_parent(image_root / ".keep")
    rows = conn.execute(
        """
        SELECT url, attempts FROM images
        WHERE status IN ('pending', 'failed') AND attempts < ?
        ORDER BY last_seen_at DESC
        """,
        (retry_limit,),
    ).fetchall()
    downloaded = 0
    failed = 0
    for row in rows:
        url = row["url"]
        now = utc_now_iso()
        try:
            local_path, size, content_type = download_image(url, image_root, timeout=timeout)
            conn.execute(
                """
                UPDATE images
                SET status='downloaded', local_path=?, error=NULL, size_bytes=?,
                    content_type=?, attempts=attempts+1, last_attempt_at=?
                WHERE url=?
                """,
                (local_path, size, content_type, now, url),
            )
            downloaded += 1
        except Exception as exc:
            conn.execute(
                """
                UPDATE images
                SET status='failed', error=?, attempts=attempts+1, last_attempt_at=?
                WHERE url=?
                """,
                (str(exc)[:500], now, url),
            )
            failed += 1
        conn.commit()
    return downloaded, failed


def download_image(url: str, image_root: Path, timeout: int = 20) -> tuple[str, int, str | None]:
    if not is_allowed_image_url(url):
        raise ValueError("image host is not allowed")
    relative = image_url_to_relative_path(url)
    target = image_root / relative
    ensure_parent(target)
    if target.exists() and target.stat().st_size > 0:
        return str(relative), target.stat().st_size, mimetypes.guess_type(str(target))[0]

    req = urllib.request.Request(url, headers={"User-Agent": "danxi-watch-archive/1.0"})
    with NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type")
        if content_type and not content_type.lower().startswith("image/") and not has_image_extension(url):
            raise ValueError(f"unexpected content type: {content_type}")
        data = resp.read()
    target.write_bytes(data)
    return str(relative), len(data), content_type


def image_url_to_relative_path(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "unknown-host"
    parts = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
    safe_parts = [safe_path_part(x) for x in parts]
    return Path(host, *safe_parts)


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value.strip())
    return cleaned or "_"


def is_allowed_image_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_IMAGE_HOSTS


def has_image_extension(url: str) -> bool:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}


def extract_image_urls(content: str) -> list[str]:
    return [x.strip() for x in IMAGE_RE.findall(content or "") if x.strip()]


def is_deleted_placeholder(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if text in DELETED_NOTICE_EXACT:
        return True
    if len(text) > 80 or not text.startswith("该内容"):
        return False
    return bool(
        re.fullmatch(r"该内容(?:已|被)?作者删除", text)
        or re.fullmatch(r"该内容因.{1,40}被删除", text)
        or re.fullmatch(r"该内容.{0,20}(?:删除|审核中)", text)
    )


def hole_has_preservable_floor_content(floors: list[dict[str, Any]]) -> bool:
    if not floors:
        return True
    for floor in floors:
        if not isinstance(floor, dict):
            continue
        content = floor.get("content")
        if content is not None and str(content).strip() and not is_deleted_placeholder(content):
            return True
    return False


def stored_preserved_content(row: sqlite3.Row | None) -> str | None:
    if row is None:
        return None
    keys = set(row.keys())
    preserved = row["preserved_content"] if "preserved_content" in keys else None
    if preserved and not is_deleted_placeholder(preserved):
        return str(preserved)
    content = row["content"] if "content" in keys else None
    if content and not is_deleted_placeholder(content):
        return str(content)
    return None


def floor_search_content(row: sqlite3.Row | None) -> str | None:
    if row is None:
        return None
    content = stored_preserved_content(row)
    if content:
        return content
    keys = set(row.keys())
    latest = row["latest_content"] if "latest_content" in keys else None
    if latest and not is_deleted_placeholder(latest):
        return str(latest)
    return None


def build_search_text(hole: dict[str, Any], floors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for tag in hole.get("tags") or []:
        if isinstance(tag, dict) and tag.get("name"):
            parts.append(str(tag["name"]))
    for floor in floors:
        if isinstance(floor, dict) and floor.get("content") and not is_deleted_placeholder(floor.get("content")):
            parts.append(str(floor["content"]))
    return "\n".join(parts)


def build_search_text_for_upsert(
    conn: sqlite3.Connection,
    hole_id: int,
    hole: dict[str, Any],
    floors: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    for tag in hole.get("tags") or []:
        if isinstance(tag, dict) and tag.get("name"):
            parts.append(str(tag["name"]))

    seen_floor_ids: set[int] = set()
    for floor in floors:
        if not isinstance(floor, dict):
            continue
        floor_id = parse_int(floor.get("floor_id") or floor.get("id"), -1)
        if floor_id >= 0:
            seen_floor_ids.add(floor_id)
        content = floor.get("content")
        if is_deleted_placeholder(content) and floor_id >= 0:
            existing = conn.execute(
                "SELECT * FROM floors WHERE floor_id = ?",
                (floor_id,),
            ).fetchone()
            content = floor_search_content(existing)
        if content and not is_deleted_placeholder(content):
            parts.append(str(content))

    existing_rows = conn.execute(
        "SELECT * FROM floors WHERE hole_id = ? ORDER BY ranking ASC, floor_id ASC",
        (hole_id,),
    ).fetchall()
    for row in existing_rows:
        if row["floor_id"] in seen_floor_ids:
            continue
        content = floor_search_content(row)
        if content:
            parts.append(content)
    return "\n".join(parts)


def refresh_hole_search_text(conn: sqlite3.Connection, hole_id: int) -> None:
    parts: list[str] = []
    tag_rows = conn.execute(
        "SELECT name FROM hole_tags WHERE hole_id = ? ORDER BY name",
        (hole_id,),
    ).fetchall()
    parts.extend(str(row["name"]) for row in tag_rows if row["name"])
    floor_rows = conn.execute(
        "SELECT * FROM floors WHERE hole_id = ? ORDER BY ranking ASC, floor_id ASC",
        (hole_id,),
    ).fetchall()
    for row in floor_rows:
        content = floor_search_content(row)
        if content:
            parts.append(content)
    conn.execute("UPDATE holes SET search_text = ? WHERE hole_id = ?", ("\n".join(parts), hole_id))
    conn.commit()


def _floors_from_hole(hole: dict[str, Any]) -> list[dict[str, Any]]:
    floors = hole.get("floors")
    if not isinstance(floors, dict):
        return []
    result: list[dict[str, Any]] = []
    first = floors.get("first_floor")
    if isinstance(first, dict):
        result.append(first)
    prefetch = floors.get("prefetch")
    if isinstance(prefetch, list):
        seen = {parse_int(first.get("floor_id") or first.get("id"), -1)} if isinstance(first, dict) else set()
        for floor in prefetch:
            if not isinstance(floor, dict):
                continue
            floor_id = parse_int(floor.get("floor_id") or floor.get("id"), -1)
            if floor_id in seen:
                continue
            result.append(floor)
            seen.add(floor_id)
    return result


def _start_run(conn: sqlite3.Connection, started_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO archive_runs (started_at, status) VALUES (?, 'running')",
        (started_at,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    result: dict[str, Any],
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE archive_runs
        SET finished_at=?, status=?, fetched_count=?, upserted_holes=?,
            upserted_floors=?, image_downloaded=?, image_failed=?, error=?
        WHERE run_id=?
        """,
        (
            utc_now_iso(),
            status,
            int(result.get("fetched") or 0),
            int(result.get("upserted_holes") or 0),
            int(result.get("upserted_floors") or 0),
            int(result.get("images_downloaded") or 0),
            int(result.get("images_failed") or 0),
            error,
            run_id,
        ),
    )
    conn.commit()


def export_pages_data(db_path: Path, out_path: Path, include_images: bool = False) -> dict[str, Any]:
    ensure_parent(out_path)
    conn = connect_archive_db(db_path)
    try:
        holes = conn.execute("SELECT * FROM holes ORDER BY time_updated DESC").fetchall()
        floors = conn.execute("SELECT * FROM floors ORDER BY hole_id, ranking, floor_id").fetchall()
        tags = conn.execute("SELECT * FROM hole_tags ORDER BY hole_id, name").fetchall()
        images = conn.execute("SELECT * FROM images ORDER BY last_seen_at DESC").fetchall()
        image_refs = conn.execute("SELECT * FROM image_refs ORDER BY hole_id, floor_id").fetchall()
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "format": "danxi-watch-export-v1",
                "created_at": utc_now_iso(),
                "include_images": include_images,
                "holes": len(holes),
                "floors": len(floors),
                "tags": len(tags),
                "images": len(images),
                "image_refs": len(image_refs),
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("holes.jsonl", "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in holes))
            zf.writestr("floors.jsonl", "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in floors))
            zf.writestr("tags.jsonl", "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in tags))
            zf.writestr("images.jsonl", "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in images))
            zf.writestr("image_refs.jsonl", "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in image_refs))
        return {"output": str(out_path), "holes": len(holes), "floors": len(floors), "images": len(images)}
    finally:
        conn.close()
