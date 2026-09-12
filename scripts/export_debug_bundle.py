from __future__ import annotations

import argparse
import json
import platform
import socket
from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from typing import Any


def _load_rows(log_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _collect_summary(log_path: Path) -> dict[str, Any]:
    rows = _load_rows(log_path)
    if not rows:
        return {
            "log_path": str(log_path),
            "rows": 0,
            "events": {},
            "parse_errors": 0,
            "reconnect_attempt_count": 0,
            "reconnect_failed_count": 0,
            "serial_command_count": 0,
            "first_ts": None,
            "last_ts": None,
            "issue_count": 0,
        }

    events = Counter(str(row.get("type", "unknown")) for row in rows)
    parse_error_rows = [row for row in rows if row.get("type") == "parse_error"]
    issue_rows = [
        row
        for row in rows
        if str(row.get("type", "")) in {"serial_error", "parse_error", "reconnect_failed"}
    ]
    return {
        "log_path": str(log_path),
        "rows": len(rows),
        "events": dict(events),
        "parse_errors": len(parse_error_rows),
        "sample_count": len([row for row in rows if row.get("type") == "sample"]),
        "serial_command_count": events.get("serial_command", 0),
        "reconnect_attempt_count": events.get("reconnect_attempt", 0),
        "reconnect_failed_count": events.get("reconnect_failed", 0),
        "issue_count": len(issue_rows),
        "first_ts": rows[0].get("ts"),
        "last_ts": rows[-1].get("ts"),
        "parse_error_reasons": dict(
            Counter(str(row.get("payload", {}).get("reason", "")) for row in parse_error_rows)
        ),
    }


def _iter_log_files(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.glob("roast_debug_*.jsonl"))
    if not target.is_file():
        raise FileNotFoundError(f"目标不存在或非文件：{target}")
    if target.suffix.lower() != ".jsonl":
        raise ValueError("仅支持单文件 .jsonl 日志或日志目录")
    return [target]


def _default_output_dir(target: Path) -> Path:
    if target.is_dir():
        return target / "bundle"
    return target.parent


def build_debug_bundle(target: Path, output: Path) -> Path:
    logs = _iter_log_files(target)
    if output.suffix.lower() != ".zip":
        output = output.with_suffix(".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "exported_at": datetime.now().isoformat(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "target_count": len(logs),
        "files": [],
    }
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as zf:
        events_total: Counter[str] = Counter()
        total_rows = 0
        total_issue_count = 0
        for log in logs:
            arc_name = f"logs/{log.name}"
            summary_path = log.with_suffix(".summary.json")
            zf.write(log, arc_name)
            if summary_path.exists():
                zf.write(summary_path, f"summaries/{summary_path.name}")
            log_summary = _collect_summary(log)
            report["files"].append(log_summary)
            events_total.update({k: int(v) for k, v in log_summary.get("events", {}).items()})
            total_rows += int(log_summary.get("rows", 0))
            total_issue_count += int(log_summary.get("issue_count", 0)) if "issue_count" in log_summary else 0
            zf.writestr(
                f"reports/{log.name}.report.json",
                json.dumps(log_summary, ensure_ascii=False, indent=2),
            )
        manifest = {
            "report_count": len(report["files"]),
            "file_names": [item["log_path"] for item in report["files"]],
            "rows": total_rows,
            "events": dict(events_total),
            "total_issue_count": total_issue_count,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("debug_report.json", json.dumps(report, ensure_ascii=False, indent=2))

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="导出调试日志诊断包")
    parser.add_argument(
        "target",
        type=Path,
        help="日志文件(roast_debug_*.jsonl)或日志目录",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出 zip 路径（默认：target/bundle/*.zip）",
    )
    parser.add_argument(
        "-n",
        "--name",
        default=None,
        help="zip 文件名，不含扩展名（默认：hb_debug_bundle_时间戳）",
    )
    args = parser.parse_args()

    output = args.output
    if output is None:
        base = _default_output_dir(args.target)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = base / (args.name or f"hb_debug_bundle_{stamp}")
    else:
        if args.name:
            output = args.output.with_name(f"{args.name}")
    path = build_debug_bundle(args.target, output)
    print(f"已导出诊断包：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
