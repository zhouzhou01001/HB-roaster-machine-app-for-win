from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import sys
import time
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "release" / "evidence"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import APP_VERSION

VERSION = APP_VERSION


def application_source_files() -> list[Path]:
    # Tests and gate code are release inputs, too.
    candidates = [ROOT / "main.py", ROOT / "version_info.txt", ROOT / "pytest.ini"]
    candidates.extend(ROOT.glob("requirements*.txt"))
    candidates.extend((ROOT / "config").glob("*.json"))
    for directory in ("app", "scripts", "tests"):
        candidates.extend((ROOT / directory).rglob("*.py"))
    return sorted({p for p in candidates if p.is_file()}, key=lambda p: p.as_posix())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def aggregate_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def source_hash() -> str:
    return aggregate_hash(application_source_files())


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class PytestReport:
    """Translate pytest outcomes into the existing release-report contract."""

    def __init__(self) -> None:
        self.discovered = 0
        self.results: dict[str, dict[str, str]] = {}
        self.failures: list[dict] = []
        self.errors: list[dict] = []
        self.skipped: list[dict] = []
        self.deselected = 0

    def pytest_collection_finish(self, session) -> None:
        self.discovered = len(session.items)

    def pytest_deselected(self, items) -> None:
        self.deselected += len(items)

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.errors.append({"test": report.nodeid, "traceback": str(report.longrepr)})
        elif report.skipped:
            self.skipped.append({"test": report.nodeid, "reason": str(report.longrepr)})

    def pytest_runtest_logreport(self, report) -> None:
        phases = self.results.setdefault(report.nodeid, {})
        outcome = report.outcome
        if hasattr(report, "wasxfail"):
            outcome = "expected_failure" if report.skipped else "unexpected_success"
        phases[report.when] = outcome
        if report.failed:
            target = self.failures if report.when == "call" else self.errors
            target.append({"test": report.nodeid, "traceback": str(report.longrepr)})
        elif report.skipped and not hasattr(report, "wasxfail"):
            self.skipped.append({"test": report.nodeid, "reason": str(report.longrepr)})

    def counts(self) -> dict[str, int]:
        outcomes = []
        for phases in self.results.values():
            values = phases.values()
            outcomes.append(next((kind for kind in ("failed", "unexpected_success", "expected_failure", "skipped") if kind in values), "passed"))
        return {
            "discovered": self.discovered, "run": len(self.results),
            "passed": outcomes.count("passed"), "failures": len(self.failures),
            "errors": len(self.errors), "skipped": len(self.skipped),
            "expected_failures": outcomes.count("expected_failure"),
            "unexpected_successes": outcomes.count("unexpected_success"),
        }


def validate_test_report(path: Path, run_id: str, expected_hash: str | None = None) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    current_hash = expected_hash or source_hash()
    counts = report.get("counts", {})
    valid = (
        report.get("schema_version") == 1 and report.get("product_version") == VERSION
        and report.get("run_id") == run_id and report.get("status") == "PASS"
        and report.get("runner") == "pytest" and report.get("pytest_exit_code") == 0
        and report.get("application_source_sha256") == current_hash
        and report.get("source_sha256_before") == current_hash
        and counts.get("run", 0) > 0 and counts.get("discovered") == counts.get("run")
        and counts.get("passed", 0) > 0 and report.get("deselected", 0) == 0
        and all(counts.get(key) == 0 for key in ("failures", "errors", "unexpected_successes", "skipped", "expected_failures"))
    )
    output = path.with_name(f"test-output-{VERSION}.txt")
    if not valid or not output.is_file() or report.get("test_output_sha256") != sha256(output):
        raise RuntimeError("Release test evidence is stale, incomplete, failed, or mismatched")
    return report


def run_tests(run_id: str | None = None, evidence_root: Path | None = None) -> tuple[int, Path]:
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid release run ID")
    destination = (evidence_root or EVIDENCE) / "runs" / run_id
    destination.mkdir(parents=True, exist_ok=False)
    started = datetime.now().astimezone()
    clock = time.perf_counter()
    before = source_hash()
    stream = io.StringIO()
    collector = PytestReport()
    exit_code = 3
    previous_cwd = Path.cwd()
    env_keys = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "QT_QPA_PLATFORM")
    saved_env = {key: os.environ.get(key) for key in env_keys}
    try:
        os.chdir(ROOT)
        os.environ.pop("PYTEST_ADDOPTS", None)
        os.environ.pop("PYTEST_PLUGINS", None)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        with redirect_stdout(stream), redirect_stderr(stream):
            import pytest
            exit_code = int(pytest.main([str(ROOT / "tests"), "-v", "-o", "addopts=", "-p", "no:cacheprovider"], plugins=[collector]))
    except (Exception, KeyboardInterrupt, SystemExit):
        collector.errors.append({"test": "pytest runner", "traceback": traceback.format_exc()})
        stream.write(traceback.format_exc())
    finally:
        os.chdir(previous_cwd)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    after = source_hash()
    counts = collector.counts()
    passed = (exit_code == 0 and before == after and counts["passed"] > 0
              and counts["discovered"] == counts["run"] and not collector.deselected
              and not any(counts[key] for key in ("failures", "errors", "unexpected_successes", "skipped", "expected_failures")))
    report = {
        "schema_version": 1, "product_version": VERSION, "status": "PASS" if passed else "FAIL",
        "run_id": run_id, "runner": "pytest", "pytest_exit_code": exit_code,
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_seconds": round(time.perf_counter() - clock, 3),
        "command_equivalent": "python -m pytest tests -v -o addopts= -p no:cacheprovider",
        "working_directory": str(ROOT),
        "environment": {"os": platform.platform(), "architecture": platform.machine(), "python": platform.python_version(), "executable": sys.executable},
        "application_source_sha256": after, "source_sha256_before": before,
        "counts": counts, "deselected": collector.deselected,
        "failures": collector.failures, "errors": collector.errors, "skipped": collector.skipped,
    }
    header = "".join(f"{key}={report[key]}\n" for key in ("status", "started_at", "ended_at", "duration_seconds", "application_source_sha256", "run_id")) + "\n"
    output = destination / f"test-output-{VERSION}.txt"
    output.write_text(header + stream.getvalue(), encoding="utf-8")
    report["test_output_sha256"] = sha256(output)
    report_path = destination / f"test-report-{VERSION}.json"
    write_json(report_path, report)
    # Compatibility aliases are not used by build gates.
    root = evidence_root or EVIDENCE
    (root / output.name).write_bytes(output.read_bytes())
    write_json(root / report_path.name, report)
    print(json.dumps(report, ensure_ascii=False))
    return (0 if passed else 1), report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pytest and unittest tests with hash-bound evidence")
    parser.add_argument("--run-id")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    return run_tests(args.run_id, args.evidence_dir)[0]


if __name__ == "__main__":
    raise SystemExit(main())
