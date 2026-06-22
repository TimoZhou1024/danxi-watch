from __future__ import annotations

import json
import mimetypes
import sqlite3
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .archive import connect_archive_db


class ArchiveApiServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], db_path: Path, image_root: Path):
        super().__init__(server_address, handler_class)
        self.db_path = db_path
        self.image_root = image_root.resolve()


class ArchiveRequestHandler(BaseHTTPRequestHandler):
    server: ArchiveApiServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/stats":
                self.write_json(get_stats(self.server.db_path))
                return
            if path == "/api/tags":
                self.write_json(get_tags(self.server.db_path))
                return
            if path == "/api/search":
                self.write_json(search_holes(self.server.db_path, params))
                return
            if path.startswith("/api/holes/"):
                hole_id = int(path.rsplit("/", 1)[-1])
                payload = get_hole_detail(self.server.db_path, hole_id)
                if payload is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "hole not found")
                else:
                    self.write_json(payload)
                return
            if path.startswith("/media/"):
                self.serve_media(path[len("/media/") :])
                return
            self.write_error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def write_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def write_error(self, status: HTTPStatus, message: str) -> None:
        self.write_json({"error": message}, status=status)

    def serve_media(self, relative_url_path: str) -> None:
        relative = Path(urllib.parse.unquote(relative_url_path))
        target = (self.server.image_root / relative).resolve()
        if not str(target).lower().startswith(str(self.server.image_root).lower()) or not target.exists():
            self.write_error(HTTPStatus.NOT_FOUND, "media not found")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve_archive(db_path: Path, image_root: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    connect_archive_db(db_path).close()
    server = ArchiveApiServer((host, port), ArchiveRequestHandler, db_path=db_path, image_root=image_root)
    print(f"DanXi archive API listening on http://{host}:{port}")
    server.serve_forever()


def get_stats(db_path: Path) -> dict[str, Any]:
    conn = connect_archive_db(db_path)
    try:
        return {
            "holes": scalar(conn, "SELECT COUNT(*) FROM holes"),
            "floors": scalar(conn, "SELECT COUNT(*) FROM floors"),
            "tags": scalar(conn, "SELECT COUNT(DISTINCT name) FROM hole_tags"),
            "images": scalar(conn, "SELECT COUNT(*) FROM images"),
            "downloaded_images": scalar(conn, "SELECT COUNT(*) FROM images WHERE status='downloaded'"),
            "first_created": scalar(conn, "SELECT MIN(time_created) FROM holes"),
            "last_updated": scalar(conn, "SELECT MAX(time_updated) FROM holes"),
        }
    finally:
        conn.close()


def get_tags(db_path: Path) -> dict[str, Any]:
    conn = connect_archive_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT name, COUNT(*) AS count
            FROM hole_tags
            GROUP BY name
            ORDER BY count DESC, name ASC
            LIMIT 500
            """
        ).fetchall()
        return {"items": [dict(row) for row in rows]}
    finally:
        conn.close()


def search_holes(db_path: Path, params: dict[str, list[str]]) -> dict[str, Any]:
    conn = connect_archive_db(db_path)
    try:
        clauses: list[str] = []
        values: list[Any] = []
        joins = ""

        q = first(params, "q").strip()
        exact = first(params, "exact").strip()
        tag = first(params, "tag").strip()
        start = first(params, "start").strip()
        end = first(params, "end").strip()
        division_id = first(params, "division_id").strip()
        min_view = first_int(params, "min_view")
        min_reply = first_int(params, "min_reply")
        has_image = first(params, "has_image").strip().lower()
        deleted = first(params, "deleted").strip().lower()

        if q:
            clauses.append("h.search_text LIKE ?")
            values.append(f"%{escape_like(q)}%")
        if exact:
            clauses.append("instr(h.search_text, ?) > 0")
            values.append(exact)
        if tag:
            joins += " JOIN hole_tags ht_filter ON ht_filter.hole_id = h.hole_id"
            clauses.append("ht_filter.name LIKE ?")
            values.append(f"%{escape_like(tag)}%")
        if start:
            clauses.append("COALESCE(h.time_created, h.time_updated) >= ?")
            values.append(start)
        if end:
            clauses.append("COALESCE(h.time_created, h.time_updated) <= ?")
            values.append(end)
        if division_id:
            clauses.append("h.division_id = ?")
            values.append(int(division_id))
        if min_view is not None:
            clauses.append("h.view_count >= ?")
            values.append(min_view)
        if min_reply is not None:
            clauses.append("h.reply_count >= ?")
            values.append(min_reply)
        if has_image in {"1", "true", "yes"}:
            clauses.append("EXISTS (SELECT 1 FROM image_refs ir WHERE ir.hole_id = h.hole_id)")
        if deleted in {"1", "true", "yes"}:
            clauses.append("h.time_deleted IS NOT NULL")
        elif deleted in {"0", "false", "no"}:
            clauses.append("h.time_deleted IS NULL")

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sort = first(params, "sort") or "updated_desc"
        order_by = {
            "created_desc": "h.time_created DESC",
            "created_asc": "h.time_created ASC",
            "updated_asc": "h.time_updated ASC",
            "view_desc": "h.view_count DESC",
            "reply_desc": "h.reply_count DESC",
            "updated_desc": "h.time_updated DESC",
        }.get(sort, "h.time_updated DESC")
        page = max(1, first_int(params, "page") or 1)
        page_size = min(100, max(1, first_int(params, "page_size") or 30))
        offset = (page - 1) * page_size

        total = scalar(conn, f"SELECT COUNT(DISTINCT h.hole_id) FROM holes h {joins}{where}", values)
        rows = conn.execute(
            f"""
            SELECT h.*, GROUP_CONCAT(DISTINCT ht.name) AS tags,
                   EXISTS (SELECT 1 FROM image_refs ir WHERE ir.hole_id = h.hole_id) AS has_image
            FROM holes h
            LEFT JOIN hole_tags ht ON ht.hole_id = h.hole_id
            {joins}
            {where}
            GROUP BY h.hole_id
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (*values, page_size, offset),
        ).fetchall()
        return {
            "items": [hole_row_to_summary(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    finally:
        conn.close()


def get_hole_detail(db_path: Path, hole_id: int) -> dict[str, Any] | None:
    conn = connect_archive_db(db_path)
    try:
        hole = conn.execute("SELECT * FROM holes WHERE hole_id = ?", (hole_id,)).fetchone()
        if hole is None:
            return None
        floors = conn.execute(
            "SELECT * FROM floors WHERE hole_id = ? ORDER BY ranking ASC, floor_id ASC",
            (hole_id,),
        ).fetchall()
        tags = conn.execute(
            "SELECT name, tag_id, temperature, nsfw FROM hole_tags WHERE hole_id = ? ORDER BY name",
            (hole_id,),
        ).fetchall()
        images = conn.execute(
            """
            SELECT images.*, image_refs.floor_id
            FROM images
            JOIN image_refs ON image_refs.url = images.url
            WHERE image_refs.hole_id = ?
            ORDER BY image_refs.floor_id
            """,
            (hole_id,),
        ).fetchall()
        return {
            "hole": hole_row_to_summary(hole),
            "raw": json.loads(hole["raw_json"]),
            "floors": [floor_row_to_dict(row) for row in floors],
            "tags": [dict(row) for row in tags],
            "images": [image_row_to_dict(row) for row in images],
        }
    finally:
        conn.close()


def hole_row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    tags_value = row["tags"] if "tags" in row.keys() else ""
    tags = [x for x in str(tags_value or "").split(",") if x]
    excerpt = str(row["search_text"] or "").replace("\r", " ").replace("\n", " ").strip()
    if len(excerpt) > 220:
        excerpt = excerpt[:217].rstrip() + "..."
    return {
        "hole_id": row["hole_id"],
        "division_id": row["division_id"],
        "time_created": row["time_created"],
        "time_updated": row["time_updated"],
        "time_deleted": row["time_deleted"],
        "view": row["view_count"],
        "reply": row["reply_count"],
        "favorite_count": row["favorite_count"],
        "subscription_count": row["subscription_count"],
        "hidden": bool(row["hidden"]),
        "locked": bool(row["locked"]),
        "tags": tags,
        "has_image": bool(row["has_image"]) if "has_image" in row.keys() else False,
        "excerpt": excerpt,
    }


def floor_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "floor_id": row["floor_id"],
        "hole_id": row["hole_id"],
        "ranking": row["ranking"],
        "reply_to": row["reply_to"],
        "anonyname": row["anonyname"],
        "content": row["content"],
        "time_created": row["time_created"],
        "time_updated": row["time_updated"],
        "deleted": bool(row["deleted"]),
        "like": row["like_count"],
        "dislike": row["dislike_count"],
    }


def image_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    local_path = row["local_path"]
    return {
        "url": row["url"],
        "local_path": local_path,
        "media_url": f"/media/{urllib.parse.quote(local_path.replace(chr(92), '/'))}" if local_path else None,
        "status": row["status"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "floor_id": row["floor_id"] if "floor_id" in row.keys() else None,
    }


def scalar(conn: sqlite3.Connection, sql: str, values: list[Any] | tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, values).fetchone()
    return row[0] if row else None


def first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def first_int(params: dict[str, list[str]], key: str) -> int | None:
    text = first(params, key).strip()
    if not text:
        return None
    return int(text)


def escape_like(value: str) -> str:
    return value.replace("%", "\\%").replace("_", "\\_")
