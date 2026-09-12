from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from importlib import metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.version import APP_VERSION
from scripts import run_release_tests as gate


def version_tuple(version: str) -> tuple[int, int, int, int]:
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        raise ValueError("APP_VERSION must have one to four numeric components")
    parts = [int(part) for part in version.split(".")]
    if any(part > 65535 for part in parts):
        raise ValueError("Windows version components must be <= 65535")
    return tuple(parts + [0] * (4 - len(parts)))


def render_version_info(version: str) -> str:
    template = (PROJECT_ROOT / "version_info.txt").read_text(encoding="utf-8")
    if "@VERSION_TUPLE@" not in template or "@APP_VERSION@" not in template:
        raise RuntimeError("Version resource template placeholders are missing")
    return template.replace("@VERSION_TUPLE@", repr(version_tuple(version))).replace("@APP_VERSION@", version)


def locked_dependencies() -> dict[str, str]:
    from packaging.requirements import Requirement
    found = {}
    for filename in ("requirements.txt", "requirements-build.txt", "requirements-dev.txt"):
        for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-r ")):
                continue
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            specs = list(requirement.specifier)
            if len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
                raise RuntimeError(f"Dependency is not exactly locked: {line}")
            actual = metadata.version(requirement.name)
            if actual != specs[0].version:
                raise RuntimeError(f"Dependency mismatch: {line}; installed {actual}")
            found[requirement.name] = actual
    return found


def fresh_tests(run_id: str) -> Path:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "run_release_tests.py"), "--run-id", run_id],
        cwd=PROJECT_ROOT, timeout=600,
    )
    if result.returncode:
        raise RuntimeError("Fresh release tests failed; packaging is blocked")
    report = PROJECT_ROOT / "release" / "evidence" / "runs" / run_id / f"test-report-{APP_VERSION}.json"
    gate.validate_test_report(report, run_id)
    return report


def validate_build_evidence(executable: Path) -> tuple[dict, Path]:
    record = json.loads(executable.with_suffix(".build.json").read_text(encoding="utf-8"))
    expected_hash = gate.source_hash()
    if (record.get("status") != "PASS" or record.get("product_version") != APP_VERSION
            or record.get("application_source_sha256") != expected_hash
            or record.get("executable_sha256") != gate.sha256(executable)
            or record.get("version_resource_sha256") != hashlib.sha256(render_version_info(APP_VERSION).encode("utf-8")).hexdigest().upper()):
        raise RuntimeError("EXE build evidence does not match current source/version/artifact")
    run_id = record.get("run_id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise RuntimeError("Invalid build run ID")
    report = PROJECT_ROOT / "release" / "evidence" / "runs" / run_id / f"test-report-{APP_VERSION}.json"
    gate.validate_test_report(report, run_id, expected_hash)
    if record.get("test_report_sha256") != gate.sha256(report):
        raise RuntimeError("Build-bound test report was replaced")
    resource = PROJECT_ROOT / "build" / "release-runs" / run_id / "version_info.txt"
    if not resource.is_file() or gate.sha256(resource) != record["version_resource_sha256"]:
        raise RuntimeError("Generated build version resource is missing or changed")
    return record, report


def _wait_for_gui_ready(process, probe: Path, nonce: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"EXE exited before GUI readiness: {code}")
        if probe.is_file():
            ready = json.loads(probe.read_text(encoding="utf-8"))
            if (ready.get("status") != "GUI_READY" or ready.get("nonce") != nonce
                    or ready.get("product_version") != APP_VERSION
                    or ready.get("window_visible") is not True
                    or ready.get("chart_visible") is not True):
                raise RuntimeError("Invalid GUI readiness signal")
            return ready
        time.sleep(0.1)
    raise RuntimeError("EXE did not confirm main-window readiness; startup dialog or DLL failure suspected")


def validate_system_icu_policy(executable: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    if any(name.replace("\\", "/").rsplit("/", 1)[-1].lower() == "icuuc.dll"
           for name in archive.toc):
        raise RuntimeError("Foreign icuuc.dll was bundled; Windows Qt must use the system ICU")


def generate_spec(root: Path, run_dir: Path, resource: Path) -> Path:
    from PyInstaller.building import makespec
    spec = Path(makespec.main(
        [str(root / "main.py")], name=f"烘焙软件-{APP_VERSION}",
        onefile=True, console=False, version_file=str(resource),
        specpath=str(run_dir), collect_all=["PySide6", "pyqtgraph"],
        shorthand_manifest=None,
    ))
    spec_text = spec.read_text(encoding="utf-8")
    marker = "pyz = PYZ("
    if spec_text.count(marker) != 1:
        raise RuntimeError("Unexpected generated spec format")
    # Windows Qt needs the system ICU, not a foreign library found on PATH.
    policy = ('a.binaries = [entry for entry in a.binaries '
              'if entry[0].replace("\\\\", "/").rsplit("/", 1)[-1].lower() != "icuuc.dll"]\n')
    spec.write_text(spec_text.replace(marker, policy + marker), encoding="utf-8")
    return spec


def smoke_exe(executable: Path, seconds: float = 10.0) -> dict:
    """Require the GUI event loop and visible chart, then sustained survival."""
    if not 0 < seconds <= 120:
        raise ValueError("Smoke duration must be greater than zero and at most 120 seconds")
    if not executable.is_file():
        raise FileNotFoundError(executable)
    before = gate.sha256(executable)
    with tempfile.TemporaryDirectory(prefix="hb-exe-smoke-") as temporary:
        env = os.environ.copy()
        env.update({key: temporary for key in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME")})
        env["QT_QPA_PLATFORM"] = "offscreen"
        probe = Path(temporary) / "gui-ready.json"
        nonce = uuid.uuid4().hex
        env["HB_STARTUP_PROBE_PATH"] = str(probe)
        env["HB_STARTUP_PROBE_NONCE"] = nonce
        process = subprocess.Popen([str(executable.resolve())], cwd=temporary, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ready = _wait_for_gui_ready(process, probe, nonce)
            try:
                code = process.wait(timeout=seconds)
                raise RuntimeError(f"EXE exited during startup smoke: {code}")
            except subprocess.TimeoutExpired:
                pass
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=15, check=True)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    if gate.sha256(executable) != before:
        raise RuntimeError("EXE changed during smoke")
    return {"schema_version": 2, "status": "PASS", "product_version": APP_VERSION,
            "executable_sha256": before, "application_source_sha256": gate.source_hash(),
            "duration_seconds": seconds, "gui_ready": ready,
            "scope": "GUI event-loop readiness and sustained startup; no hardware certification",
            "completed_at": datetime.now().astimezone().isoformat()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fresh-test-gated release build; preserves prior build runs and specs")
    parser.add_argument("--check-only", action="store_true", help="Run gates without packaging")
    parser.add_argument("--smoke-exe", action="store_true", help="Require current EXE startup survival")
    parser.add_argument("--smoke-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    root = PROJECT_ROOT
    try:
        dependencies = locked_dependencies()
        resource_text = render_version_info(APP_VERSION)
        run_id = uuid.uuid4().hex
        report = fresh_tests(run_id)
        tested_hash = gate.validate_test_report(report, run_id)["application_source_sha256"]
        current_exe = root / "dist" / f"烘焙软件-{APP_VERSION}.exe"
        if args.check_only:
            if args.smoke_exe:
                validate_build_evidence(current_exe)
                gate.write_json(report.parent / f"exe-smoke-{APP_VERSION}.json", smoke_exe(current_exe, args.smoke_seconds))
            return 0
        run_dir = root / "build" / "release-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        resource = run_dir / "version_info.txt"
        resource.write_bytes(resource_text.encode("utf-8"))
        output = run_dir / "dist"
        spec = generate_spec(root, run_dir, resource)
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--workpath", str(run_dir / "work"), "--distpath", str(output), str(spec)]
        subprocess.run(cmd, cwd=root, check=True, timeout=900)
        gate.validate_test_report(report, run_id)
        if gate.source_hash() != tested_hash:
            raise RuntimeError("Source changed during packaging")
        executable = output / current_exe.name
        validate_system_icu_policy(executable)
        smoke = smoke_exe(executable, args.smoke_seconds) if args.smoke_exe else None
        gate.validate_test_report(report, run_id)
        record = {"schema_version": 1, "status": "PASS", "run_id": run_id, "product_version": APP_VERSION,
                  "application_source_sha256": tested_hash, "test_report_sha256": gate.sha256(report),
                  "version_resource_sha256": gate.sha256(resource), "executable_sha256": gate.sha256(executable),
                  "dependencies": dependencies, "exe_smoke": smoke,
                  "built_at": datetime.now().astimezone().isoformat(), "command": cmd}
        gate.write_json(executable.with_suffix(".build.json"), record)
        current_exe.parent.mkdir(parents=True, exist_ok=True)
        if current_exe.exists():
            previous = run_dir / "previous-current"
            previous.mkdir()
            shutil.copy2(current_exe, previous / current_exe.name)
            sidecar = current_exe.with_suffix(".build.json")
            if sidecar.exists():
                shutil.copy2(sidecar, previous / sidecar.name)
        shutil.copy2(executable, current_exe)
        gate.write_json(current_exe.with_suffix(".build.json"), record)
        print(f"Built {current_exe}; evidence run {run_id}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, metadata.PackageNotFoundError) as exc:
        print(f"Release gate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
