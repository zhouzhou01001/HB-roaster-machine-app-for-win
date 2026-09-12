from __future__ import annotations

import json
import math
import platform
import sys
from collections import deque
from collections import Counter
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from typing import Any, Dict, Mapping, Optional, Sequence

from app.version import APP_VERSION


def _safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return str(value)


class SessionDebugLogger:
    """
    Append-only JSONL recorder for one roast session.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path = self.path.with_suffix(".summary.json")
        self._lock = threading.Lock()
        self._active = False
        self._started_ts: Optional[datetime] = None
        self._last_event_ts: Optional[datetime] = None
        self._event_count = 0
        self._parse_error_count = 0
        self._sample_reject_count = 0
        self._sample_count = 0
        self._raw_frame_count = 0
        self._sequence = 0
        self._session_id = str(uuid.uuid4())
        self._session_meta: Dict[str, Any] = {}
        self._batch_id: Optional[int] = None
        self._recent_events = deque(maxlen=10)

    def is_active(self) -> bool:
        return self._active

    @staticmethod
    def export_log_bundle(log_path: Path | str, output: Path | str | None = None) -> Path:
        logger = SessionDebugLogger(log_path)
        logger._hydrate_from_existing_log()
        return logger.export_bundle(output)

    def start(
        self,
        batch_id: Optional[int] = None,
        source: str = "",
        profile: str = "",
        baudrate: Optional[int] = None,
        source_display: str = "",
        source_details: Mapping[str, Any] | None = None,
    ) -> None:
        self._started_ts = datetime.now(timezone.utc)
        self._batch_id = int(batch_id) if batch_id is not None else None
        safe_source_details = _safe_value(dict(source_details or {}))
        self._session_meta = {
            "batch_id": self._batch_id,
            "source": source,
            "source_display": source_display,
            "profile": profile,
            "baudrate": baudrate,
            "source_details": safe_source_details,
            "software_version": APP_VERSION,
            "created_ts": self._started_ts.isoformat(),
            "environment": {
                "platform": platform.platform(),
                "python": sys.version,
            },
        }
        self._sequence = 0
        self._last_event_ts = self._started_ts
        self._event_count = 0
        self._parse_error_count = 0
        self._sample_reject_count = 0
        self._sample_count = 0
        self._raw_frame_count = 0
        self._active = True
        self._recent_events.clear()
        self._session_meta["last_raw_frame"] = None
        self._session_meta["last_parse_error"] = None
        self.log_event("session_start", self._session_meta)

    def stop(self, reason: str = "normal") -> None:
        if not self._active:
            return
        summary = self._build_session_summary(reason)
        self.log_event("session_stop", summary)
        self._write_summary(summary)
        self._active = False

    def export_bundle(self, output: Path | str | None = None) -> Path:
        out_path = Path(output) if output is not None else self._default_bundle_path()
        if out_path.suffix.lower() != ".zip":
            out_path = out_path.with_suffix(".zip")
        if not self.path.exists():
            raise FileNotFoundError(f"日志文件不存在：{self.path}")
        self._append_summary_to_disk()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(out_path, "w", compression=ZIP_DEFLATED) as zf:
            if self.path.exists():
                zf.write(self.path, f"logs/{self.path.name}")
            if self.summary_path.exists():
                zf.write(self.summary_path, f"summaries/{self.summary_path.name}")
            summary = self._build_session_summary("export")
            zf.writestr(
                f"reports/{self.path.name}.report.json",
                json.dumps(
                    self._build_report_from_log(self.path),
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "session_id": self._session_id,
                        "created_at": self._session_meta.get("created_ts"),
                        "summary_source": "live" if self._active else "offline_replay",
                        "log_file": self.path.name,
                        "summary_file": self.summary_path.name,
                        "session_summary": summary,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return out_path

    def _default_bundle_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.path.parent / "bundle" / f"hb_debug_bundle_{stamp}_{self._session_id[:8]}.zip"

    def _append_summary_to_disk(self) -> None:
        if not self.summary_path.exists():
            return self._write_summary(self._build_session_summary("finalize"))

    def _hydrate_from_existing_log(self) -> None:
        if not self.path.exists():
            return
        summary = self._load_summary_file()
        if summary:
            self._session_meta = {
                "batch_id": summary.get("batch_id"),
                "source": summary.get("source", ""),
                "source_display": summary.get("source_display", ""),
                "profile": summary.get("profile", ""),
                "baudrate": summary.get("baudrate"),
                "source_details": summary.get("source_details", {}),
                "software_version": summary.get("version", APP_VERSION),
                "created_ts": summary.get("start_ts"),
                "environment": summary.get("environment", {}),
            }
            self._session_id = str(summary.get("session_id") or self._session_id)
            self._event_count = int(summary.get("events") or 0)
            self._parse_error_count = int(summary.get("parse_error_count") or 0)
            self._sample_reject_count = int(summary.get("sample_reject_count") or 0)
            self._sample_count = int(summary.get("sample_count") or 0)
            self._raw_frame_count = int(summary.get("raw_frame_count") or 0)
            self._session_meta["last_raw_frame"] = summary.get("last_raw_frame")
            self._session_meta["last_parse_error"] = summary.get("last_parse_error")
            self._last_event_ts = self._parse_ts(summary.get("last_event_ts"))
            return

        rows = self._load_log_rows(self.path)
        if not rows:
            return
        self._session_meta = {
            "batch_id": rows[0].get("batch_id"),
            "source": rows[0].get("source", ""),
            "source_display": rows[0].get("source_display", ""),
            "profile": rows[0].get("profile", ""),
            "baudrate": rows[0].get("baudrate"),
            "source_details": _safe_value(rows[0].get("source_details") or {}),
            "software_version": rows[0].get("software_version", APP_VERSION),
            "created_ts": str(rows[0].get("ts")),
            "environment": {},
        }
        self._session_id = str(rows[0].get("session_id") or self._session_id)
        try:
            self._batch_id = int(rows[0].get("batch_id")) if rows[0].get("batch_id") is not None else None
        except (TypeError, ValueError):
            self._batch_id = None
        self._started_ts = self._parse_ts(rows[0].get("ts"))
        self._event_count = len(rows)
        self._parse_error_count = len([row for row in rows if row.get("type") == "parse_error"])
        self._sample_reject_count = len([row for row in rows if row.get("type") == "sample_reject"])
        self._sample_count = len([row for row in rows if row.get("type") == "sample" and ((row.get("payload") or {}).get("accepted", True))])
        self._raw_frame_count = len([row for row in rows if row.get("type") == "serial_raw"])
        self._session_meta["last_raw_frame"] = str(_safe_value(rows[-1]).get("payload", {}).get("raw_frame") or rows[-1].get("raw_frame"))
        self._session_meta["last_parse_error"] = str(_safe_value(rows[-1]).get("payload", {}).get("reason") or "")
        self._last_event_ts = self._parse_ts(rows[-1].get("ts"))

    @staticmethod
    def _parse_ts(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    def _load_summary_file(self) -> Mapping[str, Any] | None:
        if not self.summary_path.exists():
            return None
        try:
            with self.summary_path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
            if isinstance(payload, Mapping):
                return payload
        except Exception:
            return None
        return None

    def _build_report_from_log(self, log_path: Path) -> dict[str, Any]:
        rows = self._load_log_rows(log_path)
        events = Counter(str(row.get("type", "unknown")) for row in rows)
        parse_error_rows = [row for row in rows if row.get("type") == "parse_error"]
        issue_rows = [
            row
            for row in rows
            if str(row.get("type", "")) in {"serial_error", "parse_error", "reconnect_failed"}
        ]
        sample_rows = [row for row in rows if row.get("type") == "sample"]
        serial_raw_rows = [row for row in rows if row.get("type") == "serial_raw"]
        return {
            "log_path": str(log_path),
            "rows": len(rows),
            "events": dict(events),
            "parse_errors": len(parse_error_rows),
            "issue_count": len(issue_rows),
            "serial_command_count": events.get("serial_command", 0),
            "reconnect_attempt_count": events.get("reconnect_attempt", 0),
            "reconnect_failed_count": events.get("reconnect_failed", 0),
            "first_ts": rows[0].get("ts") if rows else None,
            "last_ts": rows[-1].get("ts") if rows else None,
            "source": rows[0].get("source") if rows else "",
            "source_display": rows[0].get("source_display") if rows and rows[0].get("source_display") is not None else "",
            "profile": rows[0].get("profile") if rows else "",
            "source_details": rows[0].get("source_details") if rows else {},
            "sample_count": len(sample_rows),
            "raw_count": len(serial_raw_rows),
            "first_sample_ts": sample_rows[0].get("ts") if sample_rows else None,
            "last_sample_ts": sample_rows[-1].get("ts") if sample_rows else None,
            "first_issue_ts": issue_rows[0].get("ts") if issue_rows else None,
            "last_issue_ts": issue_rows[-1].get("ts") if issue_rows else None,
            "last_issue": _safe_value(issue_rows[-1]) if issue_rows else None,
        }

    @staticmethod
    def _load_log_rows(log_path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with log_path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                raw = raw.strip()
                if not raw:
                    continue
                rows.append(json.loads(raw))
        return rows

    def _build_session_summary(self, reason: str) -> dict[str, Any]:
        end_ts = datetime.now(timezone.utc)
        return {
            "version": APP_VERSION,
            "reason": reason,
            "session_id": self._session_id,
            "source": self._session_meta.get("source", ""),
            "source_display": self._session_meta.get("source_display", ""),
            "profile": self._session_meta.get("profile", ""),
            "baudrate": self._session_meta.get("baudrate"),
            "source_details": self._session_meta.get("source_details", {}),
            "batch_id": self._batch_id,
            "start_ts": self._session_meta.get("created_ts"),
            "end_ts": end_ts.isoformat(),
            "duration_seconds": (
                (end_ts - self._started_ts).total_seconds()
                if self._started_ts is not None
                else None
            ),
            "events": self._event_count,
            "parse_error_count": self._parse_error_count,
            "sample_reject_count": self._sample_reject_count,
            "sample_count": self._sample_count,
            "raw_frame_count": self._raw_frame_count,
            "last_event_ts": self._last_event_ts.isoformat() if self._last_event_ts else None,
            "last_raw_frame": self._session_meta.get("last_raw_frame"),
            "last_parse_error": self._session_meta.get("last_parse_error"),
            "environment": self._session_meta.get("environment", {}),
        }

    def _write_summary(self, summary: Mapping[str, Any]) -> None:
        try:
            with self.summary_path.open("w", encoding="utf-8") as fp:
                json.dump(summary, fp, ensure_ascii=False, indent=2)
        except Exception:
            # Debug reporting path must not impact runtime.
            pass

    def _event_count_plus(self) -> dict[str, int]:
        return {
            "events": self._event_count,
            "parse_error_count": self._parse_error_count,
            "sample_reject_count": self._sample_reject_count,
            "sample_count": self._sample_count,
        }

    def log_raw(self, raw_frame: str, **context: Any) -> None:
        if not raw_frame:
            return
        self._raw_frame_count += 1
        self._session_meta["last_raw_frame"] = raw_frame[:200]
        self.log_event("serial_raw", {"raw_frame": raw_frame, **context})

    def log_sample(self, sample: Mapping[str, Any], accepted: bool, **context: Any) -> None:
        if accepted:
            self._sample_count += 1
        self.log_event(
            "sample",
            {
                "accepted": bool(accepted),
                "sample": dict(sample),
                **context,
            },
        )

    def log_parse_error(self, reason: str, raw_frame: str = "", **context: Any) -> None:
        self._parse_error_count += 1
        self._session_meta["last_parse_error"] = reason
        self.log_event(
            "parse_error",
            {
                "reason": reason,
                "raw_frame": raw_frame or context.get("raw_frame", ""),
                **context,
            },
        )

    def log_event(self, event: str, payload: Mapping[str, Any] | Sequence[tuple] | None = None) -> None:
        if not self._active:
            return
        with self._lock:
            self._sequence += 1
            now = datetime.now(timezone.utc)
            if payload is None:
                payload = {}
            if str(event) == "sample_reject":
                self._sample_reject_count += 1
            data = {
                "sequence": self._sequence,
                "ts": now.isoformat(),
                "type": str(event),
                "batch_id": self._batch_id,
                "source": self._session_meta.get("source", ""),
                "profile": self._session_meta.get("profile", ""),
                "baudrate": self._session_meta.get("baudrate"),
                "software_version": APP_VERSION,
                "payload": _safe_value(payload),
            }
            self._last_event_ts = now
            self._event_count += 1
            self._recent_events.append(data)
            self._append_jsonl(data)

    def _append_jsonl(self, payload: Mapping[str, Any]) -> None:
        record = {"session_id": self._session_id, **payload}
        try:
            text = json.dumps(record, ensure_ascii=False)
        except TypeError:
            text = json.dumps(_safe_value(record), ensure_ascii=False)
        try:
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(text)
                fp.write("\n")
        except Exception:
            # Debug logging must never interrupt roasting workflow.
            pass
