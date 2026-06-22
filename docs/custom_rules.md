# Custom Detection Rules

`rules/watch_rules.json` controls the local MVP detection system. The project
still generates the original daily report, and it also writes custom detection
outputs:

- `outputs/detections.json`
- `outputs/detections.md`

## Rule Fields

- `name`: Rule group name shown in the Markdown report.
- `description`: Human note for why this rule exists.
- `severity`: `low`, `normal`, `medium`, `high`, or `critical`.
- `include_keywords`: Any keyword here can trigger the rule.
- `exclude_keywords`: If any keyword here appears, the rule is skipped.
- `include_tags`: Optional DanXi tag names. If present, at least one matching tag is required.
- `exclude_tags`: Optional DanXi tag names. If any matching tag appears, the rule is skipped.
- `include_regex`: Optional regular expressions. If present, at least one must match.
- `exclude_regex`: Optional regular expressions. If any matches, the rule is skipped.
- `min_reply`: Minimum reply count.
- `min_view`: Minimum view count.
- `min_like`: Minimum total likes from prefetched floors.

## Minimal Example

```json
{
  "name": "宿舍问题",
  "description": "关注宿舍维修、停水停电、搬迁等信息。",
  "severity": "medium",
  "include_keywords": ["宿舍", "寝室", "停水", "停电", "维修"],
  "exclude_keywords": [],
  "include_regex": [],
  "exclude_regex": [],
  "min_reply": 0,
  "min_view": 0,
  "min_like": 0
}
```

## Local Commands

Run with the default rules:

```powershell
py scripts\generate_daily.py --hours 24 --top 12
```

Use another rules file:

```powershell
py scripts\generate_daily.py --watch-rules rules\my_rules.json
```

Disable custom detections and only keep the original daily report:

```powershell
py scripts\generate_daily.py --no-watch
```
