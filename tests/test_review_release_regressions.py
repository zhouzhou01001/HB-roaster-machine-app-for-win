from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.utils import paths
from app.version import APP_VERSION
from scripts import assemble_delivery as delivery
from scripts import build_exe as build
from scripts import run_release_tests as gate


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "version_info.txt").write_text("@VERSION_TUPLE@ @APP_VERSION@", encoding="utf-8")
    run_id = "fresh-run"
    directory = tmp_path / "release" / "evidence" / "runs" / run_id
    directory.mkdir(parents=True)
    output = directory / f"test-output-{APP_VERSION}.txt"
    output.write_text("passed", encoding="utf-8")
    report = directory / f"test-report-{APP_VERSION}.json"
    digest = gate.source_hash()
    payload = {"schema_version": 1, "product_version": APP_VERSION, "status": "PASS",
               "run_id": run_id, "runner": "pytest", "pytest_exit_code": 0,
               "source_sha256_before": digest, "application_source_sha256": digest,
               "test_output_sha256": gate.sha256(output), "deselected": 0,
               "counts": {"discovered": 1, "run": 1, "passed": 1, "failures": 0,
                          "errors": 0, "unexpected_successes": 0, "skipped": 0, "expected_failures": 0}}
    gate.write_json(report, payload)
    return tmp_path, report, payload


def test_workspace_diagnostics_survive_later_success_and_deduplicate(tmp_path, monkeypatch):
    bad = tmp_path / "bad"
    bad.write_text("file", encoding="utf-8")
    good = tmp_path / "good"
    paths.clear_workspace_issues()
    try:
        monkeypatch.setattr(paths, "_candidate_workspace_roots", lambda: [bad, good])
        paths.default_db_path()
        first = paths.get_workspace_issues()
        paths.default_channel_mapping_path()
        assert paths.get_workspace_issues() == first
        assert len(first) == 1
        monkeypatch.setattr(paths, "_candidate_workspace_roots", lambda: [good])
        paths.default_debug_log_dir()
        assert paths.get_workspace_issues() == first
        paths.clear_workspace_issues()
        assert paths.get_workspace_issues() == ()
    finally:
        paths.clear_workspace_issues()


@pytest.mark.parametrize("version, expected", [("1.0", (1, 0, 0, 0)), ("2.3.4", (2, 3, 4, 0)), ("1.2.3.4", (1, 2, 3, 4))])
def test_version_components(version, expected):
    assert build.version_tuple(version) == expected


@pytest.mark.parametrize("version", ["1.0-beta", "1.2.3.4.5", "65536", "-1", ""])
def test_invalid_windows_version_rejected(version):
    with pytest.raises(ValueError):
        build.version_tuple(version)


def test_generated_resource_uses_app_version():
    from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
    text = build.render_version_info(APP_VERSION)
    assert "@APP_VERSION@" not in text and "@VERSION_TUPLE@" not in text
    assert repr(build.version_tuple(APP_VERSION)) in text
    assert f"-{APP_VERSION}.exe" in text
    # Parsing is tested separately from packaging; no EXE is produced.
    assert callable(load_version_info_from_text_file)
    compile(text, "generated-version", "eval")


def test_actual_spec_generation_excludes_foreign_icu(tmp_path):
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    resource = tmp_path / "version_info.txt"
    resource.write_text(build.render_version_info(APP_VERSION), encoding="utf-8")
    spec = build.generate_spec(tmp_path, tmp_path / "build", resource)
    text = spec.read_text(encoding="utf-8")
    compile(text, str(spec), "exec")
    assert '!= "icuuc.dll"' in text
    assert text.index("a.binaries = [") < text.index("pyz = PYZ(")
    assert "console=False" in text


@pytest.mark.parametrize("key, value", [("run_id", "old-run"), ("product_version", "0.0.5"),
    ("status", "FAIL"), ("runner", "unittest"), ("pytest_exit_code", 5),
    ("source_sha256_before", "OLD"), ("application_source_sha256", "OLD"), ("deselected", 1)])
def test_stale_or_incomplete_report_rejected(evidence, key, value):
    _, report, payload = evidence
    payload[key] = value
    gate.write_json(report, payload)
    with pytest.raises(RuntimeError, match="evidence"):
        gate.validate_test_report(report, "fresh-run")


def test_output_tampering_and_source_changes_rejected(evidence):
    root, report, _ = evidence
    gate.validate_test_report(report, "fresh-run")
    (root / "tests").mkdir()
    test = root / "tests" / "test_new.py"
    test.write_text("def test_new(): pass", encoding="utf-8")
    with pytest.raises(RuntimeError):
        gate.validate_test_report(report, "fresh-run")
    test.unlink()
    report.with_name(f"test-output-{APP_VERSION}.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError):
        gate.validate_test_report(report, "fresh-run")


def test_zero_tests_rejected(evidence):
    _, report, payload = evidence
    payload["counts"].update(discovered=0, run=0, passed=0)
    gate.write_json(report, payload)
    with pytest.raises(RuntimeError):
        gate.validate_test_report(report, "fresh-run")


def test_build_binds_executable_and_resource(evidence):
    root, report, payload = evidence
    executable = root / "current.exe"
    executable.write_bytes(b"fake executable")
    resource = root / "build" / "release-runs" / "fresh-run" / "version_info.txt"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(build.render_version_info(APP_VERSION).encode("utf-8"))
    record = {"status": "PASS", "product_version": APP_VERSION, "run_id": "fresh-run",
              "application_source_sha256": payload["application_source_sha256"],
              "executable_sha256": gate.sha256(executable), "test_report_sha256": gate.sha256(report),
              "version_resource_sha256": gate.sha256(resource)}
    gate.write_json(executable.with_suffix(".build.json"), record)
    assert build.validate_build_evidence(executable)[1] == report
    executable.write_bytes(b"old exe")
    with pytest.raises(RuntimeError, match="EXE"):
        build.validate_build_evidence(executable)


def test_fresh_test_gate_does_not_fall_back_to_old_report(evidence, monkeypatch):
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    with pytest.raises(FileNotFoundError):
        build.fresh_tests("brand-new-run")


def test_check_only_never_packages(evidence, monkeypatch):
    _, report, payload = evidence
    monkeypatch.setattr(build, "locked_dependencies", lambda: {})
    monkeypatch.setattr(build, "fresh_tests", lambda run: report)
    monkeypatch.setattr(gate, "validate_test_report", lambda *a: payload)
    command = Mock(side_effect=AssertionError("Packaging is forbidden"))
    monkeypatch.setattr(build.subprocess, "run", command)
    assert build.main(["--check-only"]) == 0
    command.assert_not_called()


def test_missing_snapshot_explained_without_creating_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "SNAPSHOT", tmp_path / "snapshot-1.0")
    monkeypatch.setattr(delivery, "DELIVERY_DIR", tmp_path / "delivery")
    with pytest.raises(FileNotFoundError, match="snapshot-1.0"):
        delivery.assemble()
    assert not delivery.DELIVERY_DIR.exists()


def test_source_archive_preserves_existing_history(tmp_path, monkeypatch):
    archive = tmp_path / "historical.zip"
    archive.write_bytes(b"history")
    monkeypatch.setattr(delivery, "SOURCE_ZIP", archive)
    with pytest.raises(FileExistsError):
        delivery.build_source_zip()
    assert archive.read_bytes() == b"history"


def test_smoke_early_exit_is_failure(tmp_path, monkeypatch):
    executable = tmp_path / "current.exe"
    executable.write_bytes(b"fake")
    process = Mock()
    process.wait.return_value = 0
    process.poll.return_value = 0
    monkeypatch.setattr(build.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(RuntimeError, match="exited"):
        build.smoke_exe(executable)


def test_smoke_survival_uses_isolated_data_and_cleans_process(tmp_path, monkeypatch):
    executable = tmp_path / "current.exe"
    executable.write_bytes(b"fake")
    process = Mock(pid=12345)
    process.wait.side_effect = [subprocess.TimeoutExpired("fake", 1), 0]
    process.poll.return_value = None
    def launch_ready(*args, **kwargs):
        env = kwargs["env"]
        Path(env["HB_STARTUP_PROBE_PATH"]).write_text(json.dumps({
            "status": "GUI_READY", "nonce": env["HB_STARTUP_PROBE_NONCE"],
            "product_version": APP_VERSION, "window_visible": True, "chart_visible": True,
        }), encoding="utf-8")
        return process
    launch = Mock(side_effect=launch_ready)
    cleanup = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(build.subprocess, "Popen", launch)
    monkeypatch.setattr(build.subprocess, "run", cleanup)
    result = build.smoke_exe(executable, 1)
    assert result["status"] == "PASS"
    assert result["gui_ready"]["status"] == "GUI_READY"
    assert launch.call_args.kwargs["env"]["APPDATA"] != os.environ.get("APPDATA")
    if os.name == "nt":
        assert cleanup.call_args.args[0] == ["taskkill", "/PID", "12345", "/T", "/F"]
    else:
        process.terminate.assert_called_once()


def test_alive_error_dialog_without_gui_readiness_fails(tmp_path, monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(build.time, "monotonic", Mock(side_effect=[0, 91]))
    with pytest.raises(RuntimeError, match="did not confirm"):
        build._wait_for_gui_ready(process, tmp_path / "absent.json", "nonce")


def test_wrong_gui_probe_nonce_fails(tmp_path):
    probe = tmp_path / "ready.json"
    probe.write_text(json.dumps({"status": "GUI_READY", "nonce": "wrong"}), encoding="utf-8")
    process = Mock()
    process.poll.return_value = None
    with pytest.raises(RuntimeError, match="Invalid GUI"):
        build._wait_for_gui_ready(process, probe, "expected")


def test_bundled_foreign_icu_is_rejected(tmp_path, monkeypatch):
    from PyInstaller.archive import readers
    monkeypatch.setattr(readers, "CArchiveReader", lambda _: SimpleNamespace(toc={"icuuc.dll": None}))
    with pytest.raises(RuntimeError, match="Foreign icuuc"):
        build.validate_system_icu_policy(tmp_path / "fixture.exe")
    monkeypatch.setattr(readers, "CArchiveReader", lambda _: SimpleNamespace(toc={"PySide6/Qt6Core.dll": None}))
    build.validate_system_icu_policy(tmp_path / "fixture.exe")


@pytest.mark.parametrize("source, expected, passed, failures, errors", [
    ("import unittest\ndef test_function(): pass\nclass Legacy(unittest.TestCase):\n def test_method(self): pass\n", "PASS", 2, 0, 0),
    ("def test_function(): assert False\n", "FAIL", 0, 1, 0),
    ("raise RuntimeError('collection failure')\n", "FAIL", 0, 0, 1),
    ("# no tests\n", "FAIL", 0, 0, 0),
    ("import pytest\ndef test_ok(): pass\n@pytest.mark.xfail\ndef test_xfail(): assert False\n@pytest.mark.skip\ndef test_skip(): pass\n", "FAIL", 1, 0, 0),
    ("import pytest\ndef test_ok(): pass\n@pytest.mark.xfail\ndef test_xpass(): pass\n", "FAIL", 1, 0, 0),
])
def test_real_pytest_discovery_and_report_contract(tmp_path, source, expected, passed, failures, errors):
    project = Path(__file__).resolve().parents[1]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_fixture.py").write_text(source, encoding="utf-8")
    command = ("import sys; from pathlib import Path; "
               f"sys.path.insert(0, {str(project)!r}); "
               "from scripts import run_release_tests as gate; "
               f"gate.ROOT=Path({str(tmp_path)!r}); "
               "raise SystemExit(gate.run_tests('fixture-run', gate.ROOT / 'evidence')[0])")
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True, timeout=45)
    path = tmp_path / "evidence" / f"test-report-{APP_VERSION}.json"
    assert path.exists(), result.stderr
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == expected
    assert result.returncode == (0 if expected == "PASS" else 1)
    assert report["counts"]["passed"] == passed
    assert report["counts"]["failures"] == failures
    assert report["counts"]["errors"] == errors
    assert report["schema_version"] == 1
    assert report["product_version"] == APP_VERSION
    assert report["application_source_sha256"] == report["source_sha256_before"]
    assert (path.parent / "runs" / "fixture-run" / path.name).is_file()


def test_installed_dependencies_match_locks():
    dependencies = build.locked_dependencies()
    assert "pytest" in dependencies
    assert "pyinstaller" in {name.lower() for name in dependencies}


def test_configuration_change_invalidates_test_evidence(evidence):
    root, report, _ = evidence
    gate.validate_test_report(report, "fresh-run")
    (root / "config").mkdir()
    (root / "config" / "channel.json").write_text('{"CH4":"WORK"}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        gate.validate_test_report(report, "fresh-run")


def test_delivery_requires_current_exe_smoke_before_writing(evidence, monkeypatch):
    root, report, payload = evidence
    executable = root / "current.exe"
    executable.write_bytes(b"fixture")
    executable.with_suffix(".build.json").write_text("{}", encoding="utf-8")
    snapshot = root / "snapshot"
    snapshot.mkdir()
    (snapshot / "00-交付包总览.md").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(delivery, "CURRENT_EXE", executable)
    monkeypatch.setattr(delivery, "SNAPSHOT", snapshot)
    monkeypatch.setattr(delivery, "BASELINE", snapshot)
    monkeypatch.setattr(delivery, "BASELINE_EXTRAS", {})
    for name in ("DELIVERY_DIR", "DELIVERY_ZIP", "SOURCE_ZIP", "RELEASE"):
        monkeypatch.setattr(delivery, name, root / name)
    monkeypatch.setattr(delivery, "validate_build_evidence", lambda _: ({"run_id": "fresh-run", "exe_smoke": None}, report))
    monkeypatch.setattr(gate, "validate_test_report", lambda *args: payload)
    with pytest.raises(RuntimeError, match="startup smoke"):
        delivery.assemble()
    assert not delivery.DELIVERY_DIR.exists()
