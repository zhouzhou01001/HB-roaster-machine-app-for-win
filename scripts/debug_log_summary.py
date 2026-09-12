from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _load_json_lines(log_path: Path) -> list[dict[str, Any]]:
    rows = []
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def summarize(log_path: Path) -> dict[str, Any]:
    rows = _load_json_lines(log_path)
    if not rows:
        return {
            "log_path": str(log_path),
            "events": {},
            "parse_errors": 0,
            "source": "",
            "source_display": "",
            "source_details": {},
            "profile": "",
            "sample_count": 0,
            "first_event_ts": None,
            "last_event_ts": None,
            "error_samples": [],
            "first_sample_ts": None,
            "last_sample_ts": None,
            "first_issue_ts": None,
            "last_issue_ts": None,
            "total_rows": 0,
        }

    events = Counter(str(row.get("type", "unknown")) for row in rows)
    parse_error_rows = [row for row in rows if row.get("type") in {"parse_error", "sample_reject"}]
    sample_rows = [row for row in rows if row.get("type") == "sample"]
    raw_rows = [row for row in rows if row.get("type") == "serial_raw"]
    issue_rows = [
        row
        for row in rows
        if str(row.get("type", "")) in {"serial_error", "parse_error", "reconnect_failed"}
    ]
    by_session = defaultdict(int)
    for row in rows:
        by_session[row.get("session_id", "unknown")] += 1

    return {
        "log_path": str(log_path),
        "total_rows": len(rows),
        "session_count": len(by_session),
        "source": rows[0].get("source", ""),
        "source_display": rows[0].get("source_display", ""),
        "source_details": rows[0].get("source_details", {}),
        "profile": rows[0].get("profile", ""),
        "events": dict(events),
        "sample_count": len(sample_rows),
        "raw_count": len(raw_rows),
        "parse_errors": len(parse_error_rows),
        "sample_reject_count": events.get("sample_reject", 0),
        "reconnect_attempt_count": events.get("reconnect_attempt", 0),
        "reconnect_failed_count": events.get("reconnect_failed", 0),
        "serial_command_count": events.get("serial_command", 0),
        "issue_count": len(issue_rows),
        "first_event_ts": rows[0].get("ts"),
        "last_event_ts": rows[-1].get("ts"),
        "first_sample_ts": sample_rows[0].get("ts") if sample_rows else None,
        "last_sample_ts": sample_rows[-1].get("ts") if sample_rows else None,
        "first_issue_ts": issue_rows[0].get("ts") if issue_rows else None,
        "last_issue_ts": issue_rows[-1].get("ts") if issue_rows else None,
        "parse_error_count_max": max(
            (int(row.get("payload", {}).get("parse_error_count") or 0) for row in parse_error_rows),
            default=0,
        ),
        "error_samples": parse_error_rows[-10:],
        "source": rows[0].get("source", ""),
        "source_display": rows[0].get("source_display", ""),
        "profile": rows[0].get("profile", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="烘焙软件调试日志汇总")
    parser.add_argument("log_path", type=Path)
    parser.add_argument("--pretty", action="store_true", help="以格式化 JSON 输出")
    args = parser.parse_args()

    summary = summarize(args.log_path)
    if args.pretty:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
