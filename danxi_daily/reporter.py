from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .detectors import DetectionResult
from .models import RankedPost


def build_daily_markdown(
    posts: list[RankedPost],
    title_prefix: str = "旦夕热榜日报",
    github_repo: str = "https://github.com/0patsick0/danxi-daily-skill",
) -> str:
    now = datetime.now(timezone.utc)
    date_label = now.astimezone().strftime("%Y年%m月%d日")
    time_label = now.astimezone().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = [
        f"# {title_prefix}｜{date_label}",
        "",
        f"> 数据整理时间：{time_label}",
        "",
        "## 今日热门话题",
        "",
    ]

    if not posts:
        lines.append("今天暂未抓取到符合条件的热点讨论。")
        return "\n".join(lines) + "\n"

    for idx, post in enumerate(posts, start=1):
        lines.append(
            f"{idx}. #{post.hole_id}"
            f"　热度 {post.hot_score:.1f}"
            f"　👀{post.view} 💬{post.reply} 👍{post.like_sum}"
        )

    lines.extend([
        "",
        f"🔗 {github_repo}",
    ])

    return "\n".join(lines) + "\n"


def build_detections_markdown(
    detections: list[DetectionResult],
    title_prefix: str = "旦夕自定义检测",
) -> str:
    now = datetime.now(timezone.utc)
    date_label = now.astimezone().strftime("%Y年%m月%d日")
    time_label = now.astimezone().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = [
        f"# {title_prefix}｜{date_label}",
        "",
        f"> 数据整理时间：{time_label}",
        "",
        "## 命中结果",
        "",
    ]

    if not detections:
        lines.append("当前规则没有命中内容。")
        return "\n".join(lines) + "\n"

    counts = Counter(item.rule for item in detections)
    summary = "；".join(f"{rule} {count}" for rule, count in counts.items())
    lines.extend([
        f"规则命中统计：{summary}",
        "",
    ])

    current_rule = ""
    for item in detections:
        if item.rule != current_rule:
            current_rule = item.rule
            lines.extend(["", f"### {current_rule}", ""])

        matches = (
            item.matched_keywords
            + [f"标签:{tag}" for tag in item.matched_tags]
            + [f"正则:{value}" for value in item.matched_regex]
        )
        keyword_text = "、".join(matches) if matches else "规则阈值"
        lines.append(
            f"- #{item.hole_id} [{item.severity}] "
            f"命中：{keyword_text} "
            f"👀{item.view} 💬{item.reply} 👍{item.like_sum}"
        )
        if item.excerpt:
            lines.append(f"  - {item.excerpt}")

    return "\n".join(lines).strip() + "\n"
