from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _load_rows(log_path: Path) -> list[dict[str, Any]]:
    rows = []
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _collect_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in rows:
        event_type = str(row.get("type", ""))
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "serial_state" and payload.get("connected") is False:
            issues.append({
                "type": "disconnect",
                "ts": row.get("ts"),
                "batch_id": row.get("batch_id"),
            })
        if event_type == "serial_error":
            issues.append({
                "type": "serial_error",
                "ts": row.get("ts"),
                "message": payload.get("message"),
                "batch_id": row.get("batch_id"),
            })
        if event_type == "parse_error":
            issues.append({
                "type": "parse_error",
                "ts": row.get("ts"),
                "reason": payload.get("reason"),
                "count": int(payload.get("parse_error_count", 0) or 0),
                "batch_id": row.get("batch_id"),
            })
        if event_type == "sample_reject":
            issues.append({
                "type": "sample_reject",
                "ts": row.get("ts"),
                "reason": payload.get("reason"),
                "batch_id": row.get("batch_id"),
            })
        if event_type == "reconnect_attempt":
            issues.append({
                "type": "reconnect_attempt",
                "ts": row.get("ts"),
                "batch_id": row.get("batch_id"),
                "attempt": payload.get("attempt"),
            })
        if event_type == "reconnect_failed":
            issues.append({
                "type": "reconnect_failed",
                "ts": row.get("ts"),
                "batch_id": row.get("batch_id"),
            })
    return issues


def summarize_log(log_path: Path) -> dict[str, Any]:
    rows = _load_rows(log_path)
    by_event = Counter(str(row.get("type", "unknown")) for row in rows)
    issue_rows = _collect_issues(rows)
    parse_reasons = defaultdict(int)
    for issue in issue_rows:
        if issue["type"] == "parse_error":
            parse_reasons[issue.get("reason") or ""] += 1
    session_ids = sorted({str(row.get("session_id", "")) for row in rows if row.get("session_id")})
    issue_type_counts = Counter(row.get("type") for row in issue_rows)
    return {
        "log": str(log_path),
        "rows": len(rows),
        "sessions": len(session_ids),
        "event_counts": dict(by_event),
        "source": rows[0].get("source", "") if rows else "",
        "source_display": rows[0].get("source_display", "") if rows else "",
        "profile": rows[0].get("profile", "") if rows else "",
        "source_details": rows[0].get("source_details", {}) if rows else {},
        "serial_command_count": by_event.get("serial_command", 0),
        "reconnect_attempt_count": by_event.get("reconnect_attempt", 0),
        "reconnect_failed_count": by_event.get("reconnect_failed", 0),
        "issues": issue_rows,
        "issue_count": len(issue_rows),
        "issue_type_counts": dict(issue_type_counts),
        "parse_reasons": dict(parse_reasons),
        "first_ts": rows[0].get("ts") if rows else None,
        "last_ts": rows[-1].get("ts") if rows else None,
        "first_issue_ts": issue_rows[0].get("ts") if issue_rows else None,
        "last_issue_ts": issue_rows[-1].get("ts") if issue_rows else None,
        "last_issue": issue_rows[-1] if issue_rows else None,
    }


def summarize_dir(log_dir: Path) -> list[dict[str, Any]]:
    summary = []
    for path in sorted(log_dir.glob("roast_debug_*.jsonl")):
        summary.append(summarize_log(path))
    return summary


def print_summary(payload: Any, pretty: bool = False) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="烘焙软件调试日志审计与问题聚合")
    parser.add_argument("target", type=Path, help="日志文件或日志目录")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if args.target.is_dir():
        payload = summarize_dir(args.target)
    else:
        payload = summarize_log(args.target)
    print_summary(payload, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
