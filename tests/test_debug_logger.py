import json
import tempfile
from pathlib import Path
from zipfile import ZipFile

from app.debug import SessionDebugLogger


def test_debug_logger_writes_parse_error_and_sample() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        log_file = Path(workspace) / "roast_debug_test.jsonl"
        logger = SessionDebugLogger(log_file)
        logger.start(batch_id=123, source="COM7", profile="HB_TC4", baudrate=115200)
        logger.log_raw("10,20,30,40")
        logger.log_sample({"time_s": 1.0, "it": 10, "et": 20, "bt": 30}, accepted=True)
        logger.log_parse_error("非法帧", raw_frame="bad")
        logger.stop("unit-test")

        lines = [line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) >= 4
        first = lines[0]
        last = lines[-1]
        assert "session_start" in first
        assert "session_stop" in last
        assert any("serial_raw" in line for line in lines)
        assert any("\"type\": \"sample\"" in line for line in lines)
        assert any("\"type\": \"parse_error\"" in line for line in lines)


def test_debug_logger_session_summary_counts() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        log_file = Path(workspace) / "roast_debug_summary.jsonl"
        logger = SessionDebugLogger(log_file)
        logger.start(batch_id=7, source="COM9", profile="HB_TC4", baudrate=115200)
        logger.log_event("serial_command", {"command": "READ"})
        logger.log_sample({"time_s": 2.0, "it": 1, "et": 2, "bt": 3}, accepted=True)
        logger.log_sample({"time_s": 3.0}, accepted=True)
        logger.log_event("sample_reject", {"reason": "bad_mapping"})
        logger.log_parse_error("非法帧", raw_frame="BAD")
        logger.stop("unit-test")

        rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        stop_rows = [row for row in rows if row.get("type") == "session_stop"]
        assert stop_rows, "session_stop should be emitted"
        payload = stop_rows[0]["payload"]
        assert payload["sample_count"] == 2
        assert payload["sample_reject_count"] == 1
        assert payload["parse_error_count"] == 1
        assert payload["events"] >= 6
        assert payload["raw_frame_count"] >= 0
        assert payload["session_id"]
        summary_path = log_file.with_suffix(".summary.json")
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["events"] >= 6
        assert summary["sample_reject_count"] == 1


def test_debug_logger_export_bundle() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        log_file = Path(workspace) / "roast_debug_bundle.jsonl"
        logger = SessionDebugLogger(log_file)
        logger.start(batch_id=9, source="COM3", profile="HB_TC4", baudrate=115200)
        logger.log_raw("12.1,13.2,14.3,15.4")
        logger.log_sample({"time_s": 0.5, "it": 10.0, "et": 11.0, "bt": 12.0}, accepted=True)
        logger.log_event("sample_reject", {"reason": "manual check"})
        logger.stop("unit-test")

        bundle_path = logger.export_bundle(Path(workspace) / "bundle.zip")
        assert bundle_path.exists()
        with ZipFile(bundle_path, "r") as zf:
            names = set(zf.namelist())
            assert "logs/roast_debug_bundle.jsonl" in names
            assert "summaries/roast_debug_bundle.summary.json" in names
            assert "manifest.json" in names
            assert any(name.startswith("reports/roast_debug_bundle.jsonl.report.json") for name in names)


def test_debug_logger_exports_source_display() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        log_file = Path(workspace) / "roast_debug_display.jsonl"
        logger = SessionDebugLogger(log_file)
        logger.start(
            batch_id=12,
            source="COM8",
            source_display="USB-SERIAL CH430 (COM8)",
            profile="HB_TC4",
            baudrate=115200,
        )
        logger.log_event("serial_command", {"command": "READ"})
        logger.stop("unit-test")

        summary_path = log_file.with_suffix(".summary.json")
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["source"] == "COM8"
        assert summary["source_display"] == "USB-SERIAL CH430 (COM8)"


def test_debug_logger_exports_source_details() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        log_file = Path(workspace) / "roast_debug_source_details.jsonl"
        logger = SessionDebugLogger(log_file)
        logger.start(
            batch_id=13,
            source="COM4",
            source_display="USB-SERIAL CH43x (COM4)",
            source_details={"usb_family": "CH43x（待验证）", "vid": 6790, "pid": 29987},
            profile="HB_TC4",
            baudrate=115200,
        )
        logger.log_event("serial_command", {"command": "READ"})
        logger.stop("unit-test")

        summary = json.loads((log_file.with_suffix(".summary.json")).read_text(encoding="utf-8"))
        assert summary["source_details"]["usb_family"] == "CH43x（待验证）"
        assert summary["source_details"]["vid"] == 6790
        assert summary["source_details"]["pid"] == 29987
