from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import extract_prefetch_floors, normalize_hole_id
from .utils import clean_publish_text, extract_text_lines, parse_int


@dataclass
class WatchRule:
    name: str
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    include_regex: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    min_reply: int = 0
    min_view: int = 0
    min_like: int = 0
    severity: str = "normal"
    description: str = ""


@dataclass
class DetectionResult:
    rule: str
    severity: str
    hole_id: int
    division_id: int | None
    time_created: str | None
    time_updated: str | None
    reply: int
    view: int
    like_sum: int
    matched_keywords: list[str]
    excerpt: str
    source_endpoint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_watch_rules(path: Path) -> list[WatchRule]:
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("watch rules file must contain a JSON list")

    rules: list[WatchRule] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()
        if not name:
            continue

        rules.append(
            WatchRule(
                name=name,
                include_keywords=_string_list(item.get("include_keywords")),
                exclude_keywords=_string_list(item.get("exclude_keywords")),
                include_regex=_string_list(item.get("include_regex")),
                exclude_regex=_string_list(item.get("exclude_regex")),
                min_reply=max(0, parse_int(item.get("min_reply"), default=0)),
                min_view=max(0, parse_int(item.get("min_view"), default=0)),
                min_like=max(0, parse_int(item.get("min_like"), default=0)),
                severity=str(item.get("severity") or "normal").strip() or "normal",
                description=str(item.get("description") or "").strip(),
            )
        )
    return rules


def detect_holes(
    holes: list[dict[str, Any]],
    rules: list[WatchRule],
    source_endpoint: str,
    max_per_rule: int = 20,
) -> list[DetectionResult]:
    if not rules:
        return []

    counts: dict[str, int] = {rule.name: 0 for rule in rules}
    results: list[DetectionResult] = []

    for hole in holes:
        try:
            hole_id = normalize_hole_id(hole)
        except ValueError:
            continue

        floors = extract_prefetch_floors(hole)
        reply = parse_int(hole.get("reply"), default=len(floors))
        view = parse_int(hole.get("view"), default=0)
        like_sum = _sum_floor_likes(floors)
        text = _collect_text(hole, floors)
        normalized_text = text.lower()

        for rule in rules:
            if counts.get(rule.name, 0) >= max(1, max_per_rule):
                continue
            matched = _matched_keywords(normalized_text, rule.include_keywords)
            if rule.include_keywords and not matched:
                continue
            if rule.include_regex and not _matches_any_regex(text, rule.include_regex):
                continue
            if _matched_keywords(normalized_text, rule.exclude_keywords):
                continue
            if rule.exclude_regex and _matches_any_regex(text, rule.exclude_regex):
                continue
            if reply < rule.min_reply or view < rule.min_view or like_sum < rule.min_like:
                continue

            results.append(
                DetectionResult(
                    rule=rule.name,
                    severity=rule.severity,
                    hole_id=hole_id,
                    division_id=parse_int(hole.get("division_id"), default=0) or None,
                    time_created=hole.get("time_created"),
                    time_updated=hole.get("time_updated"),
                    reply=reply,
                    view=view,
                    like_sum=like_sum,
                    matched_keywords=matched,
                    excerpt=_excerpt(text),
                    source_endpoint=source_endpoint,
                )
            )
            counts[rule.name] = counts.get(rule.name, 0) + 1

    results.sort(
        key=lambda item: (
            item.rule,
            -_severity_weight(item.severity),
            -item.reply,
            -item.view,
            -item.like_sum,
            -item.hole_id,
        )
    )
    return results


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _sum_floor_likes(floors: list[dict[str, Any]]) -> int:
    total = 0
    for floor in floors:
        total += parse_int(floor.get("like"), default=0)
    return total


def _collect_text(hole: dict[str, Any], floors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    content = hole.get("content")
    if isinstance(content, str):
        parts.extend(extract_text_lines(clean_publish_text(content)))

    for floor in floors[:12]:
        text = floor.get("content")
        if isinstance(text, str):
            parts.extend(extract_text_lines(clean_publish_text(text)))

    return "\n".join(parts)


def _matched_keywords(normalized_text: str, keywords: list[str]) -> list[str]:
    matched: list[str] = []
    for keyword in keywords:
        normalized_keyword = keyword.lower()
        if normalized_keyword and normalized_keyword in normalized_text:
            matched.append(keyword)
    return matched


def _matches_any_regex(text: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return True
        except re.error:
            continue
    return False


def _excerpt(text: str, max_chars: int = 140) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _severity_weight(value: str) -> int:
    normalized = value.strip().lower()
    if normalized in {"critical", "high"}:
        return 3
    if normalized in {"medium", "normal"}:
        return 2
    if normalized == "low":
        return 1
    return 0
