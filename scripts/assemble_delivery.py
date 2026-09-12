from __future__ import annotations

import hashlib
import argparse
import json
import platform
import re
import shutil
import zipfile
from datetime import datetime
from importlib import metadata
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import APP_VERSION
from scripts import run_release_tests as gate
from scripts.build_exe import validate_build_evidence, smoke_exe

VERSION = APP_VERSION
DELIVERY_NAME = f"烘焙软件-{VERSION}-完整交付包"
DELIVERY_DIR = RELEASE / DELIVERY_NAME
DELIVERY_ZIP = RELEASE / f"{DELIVERY_NAME}.zip"
SOURCE_ZIP = RELEASE / f"烘焙软件-{VERSION}-源码.zip"
CURRENT_EXE = ROOT / "dist" / f"烘焙软件-{VERSION}.exe"
TEST_EVIDENCE = RELEASE / "evidence"
SNAPSHOT = ROOT / "docs" / f"交付快照-{VERSION}"
BASELINE = ROOT / "docs" / "框架冻结基线-0.0.3"
UI_DESIGN = ROOT / "UI设计"
BASELINE_EXTRAS = {
    UI_DESIGN / "UI设计指导文档.md": Path("UI设计指导文档.md"),
    UI_DESIGN / "周周ROAST-完整操作文档.md": Path("周周ROAST-完整操作文档.md"),
    UI_DESIGN / "周周ROAST-优化验收清单.md": Path("周周ROAST-优化验收清单.md"),
    UI_DESIGN / "_qa" / "界面精简最终-主界面.png": Path("最终主界面截图.png"),
    UI_DESIGN / "_qa" / "界面精简最终-坐标控制台.png": Path("最终坐标控制台截图.png"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def application_source_files() -> list[Path]:
    return gate.application_source_files()


def aggregate_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def source_files() -> list[Path]:
    files: set[Path] = set()
    for root in (ROOT / "app", ROOT / "tests", ROOT / "scripts", ROOT / "docs", ROOT / "UI设计", ROOT / "config"):
        if root.exists():
            files.update(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for pattern in ("main.py", "build_exe.py", "version_info.txt", "requirements*.txt", "pytest.ini", "README.md", "TESTING.md", "PROJECT_TASKS.md", "*.spec"):
        files.update(p for p in ROOT.glob(pattern) if p.is_file())
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def build_source_zip() -> None:
    SOURCE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(SOURCE_ZIP, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files():
            archive.write(path, path.relative_to(ROOT).as_posix())


def copy_tree(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def dependency_report() -> str:
    names = ["PySide6", "pyqtgraph", "pyserial", "numpy", "pandas", "PyInstaller", "pytest"]
    lines = [f"generated_at={datetime.now().astimezone().isoformat(timespec='seconds')}", f"python={platform.python_version()}", f"platform={platform.platform()}"]
    for name in names:
        try:
            package = metadata.metadata(name)
            license_value = package.get("License-Expression") or package.get("License") or "not declared in package metadata"
            lines.append(f"{name}=={metadata.version(name)} | license_metadata={license_value}")
        except metadata.PackageNotFoundError:
            lines.append(f"{name}=not installed in assembly environment")
    return "\n".join(lines) + "\n"


def baseline_verification(copied: Path) -> dict[str, object]:
    entries = []
    passed = True
    sources = [(source, source.relative_to(BASELINE)) for source in sorted(p for p in BASELINE.rglob("*") if p.is_file())]
    sources.extend(BASELINE_EXTRAS.items())
    for source, relative in sources:
        target = copied / relative
        source_hash = sha256(source)
        copied_hash = sha256(target) if target.exists() else None
        match = source_hash == copied_hash
        passed = passed and match
        entries.append({"path": relative.as_posix(), "source_path": str(source), "source_sha256": source_hash, "copied_sha256": copied_hash, "match": match})
    return {"baseline": "0.0.3-r1", "source_paths": [str(BASELINE), str(UI_DESIGN)], "expected_file_count": 9, "verified_file_count": len(entries), "delivery_path": "02-冻结设计基线-0.0.3-r1", "status": "PASS" if passed and len(entries) == 9 else "FAIL", "files": entries}


def manifest_entries(root: Path) -> list[dict[str, object]]:
    excluded = {"06-校验/MANIFEST.json", "06-校验/MANIFEST.txt", "06-校验/SHA256SUMS.txt"}
    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        stat = path.stat()
        entries.append({"path": relative, "bytes": stat.st_size, "modified_local": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"), "sha256": sha256(path)})
    return entries


def write_checksums(root: Path) -> None:
    output = root / "06-校验" / "SHA256SUMS.txt"
    lines = [f"{sha256(path)}  {path.relative_to(root).as_posix()}" for path in sorted(p for p in root.rglob("*") if p.is_file() and p != output)]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assemble(require_exe_smoke: bool = False) -> None:
    required = [CURRENT_EXE, CURRENT_EXE.with_suffix(".build.json"), SNAPSHOT, BASELINE, SNAPSHOT / "00-交付包总览.md", *BASELINE_EXTRAS]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required release inputs: " + ", ".join(missing))
    occupied = [str(path) for path in (DELIVERY_DIR, DELIVERY_ZIP, SOURCE_ZIP, RELEASE / f"{DELIVERY_ZIP.name}.sha256") if path.exists()]
    if occupied:
        raise FileExistsError("Refusing to overwrite historical delivery: " + ", ".join(occupied))
    build_record, bound_report = validate_build_evidence(CURRENT_EXE)
    app_hash = gate.source_hash()
    gate.validate_test_report(bound_report, build_record["run_id"], app_hash)
    smoke_report = smoke_exe(CURRENT_EXE) if require_exe_smoke else build_record.get("exe_smoke")
    if (not smoke_report or smoke_report.get("status") != "PASS"
            or smoke_report.get("product_version") != VERSION
            or smoke_report.get("executable_sha256") != build_record["executable_sha256"]
            or smoke_report.get("application_source_sha256") != app_hash
            or smoke_report.get("duration_seconds", 0) < 10
            or smoke_report.get("gui_ready", {}).get("status") != "GUI_READY"
            or smoke_report.get("gui_ready", {}).get("window_visible") is not True
            or smoke_report.get("gui_ready", {}).get("chart_visible") is not True):
        raise RuntimeError("Current EXE requires a hash-matched startup smoke of at least 10 seconds; use --smoke-exe")
    build_source_zip()

    overview_dir = DELIVERY_DIR / "00-交付说明"
    docs_dir = DELIVERY_DIR / f"01-当前版本文档-{VERSION}"
    baseline_dir = DELIVERY_DIR / "02-冻结设计基线-0.0.3-r1"
    ui_dir = DELIVERY_DIR / "03-UI设计资料"
    exe_dir = DELIVERY_DIR / "04-可执行程序"
    source_dir = DELIVERY_DIR / "05-源代码"
    evidence_dir = DELIVERY_DIR / "06-校验"
    for directory in (overview_dir, docs_dir, exe_dir, source_dir, evidence_dir):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copy2(SNAPSHOT / "00-交付包总览.md", overview_dir / "README.md")
    copy_tree(SNAPSHOT, docs_dir)
    copy_tree(BASELINE, baseline_dir)
    for source, relative in BASELINE_EXTRAS.items():
        if not source.is_file():
            raise FileNotFoundError(f"Missing frozen baseline member: {source}")
        target = baseline_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    copy_tree(UI_DESIGN, ui_dir)
    shutil.copy2(CURRENT_EXE, exe_dir / CURRENT_EXE.name)
    shutil.copy2(SOURCE_ZIP, source_dir / SOURCE_ZIP.name)
    for evidence in (bound_report, bound_report.with_name(f"test-output-{VERSION}.txt"), CURRENT_EXE.with_suffix(".build.json")):
        shutil.copy2(evidence, evidence_dir / evidence.name)
    if smoke_report:
        gate.write_json(evidence_dir / f"exe-smoke-{VERSION}.json", smoke_report)
    if gate.source_hash() != app_hash or sha256(exe_dir / CURRENT_EXE.name) != build_record["executable_sha256"]:
        raise RuntimeError("Source or EXE changed while assembling delivery")

    baseline_report = baseline_verification(baseline_dir)
    if baseline_report["status"] != "PASS":
        raise RuntimeError("Frozen baseline copy verification failed")
    (evidence_dir / "frozen-baseline-verification.json").write_text(json.dumps(baseline_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "dependency-versions.txt").write_text(dependency_report(), encoding="utf-8")

    source_hash = sha256(SOURCE_ZIP)
    exe_hash = sha256(CURRENT_EXE)
    assembled_at = datetime.now().astimezone().isoformat(timespec="seconds")
    build_id = f"{VERSION}+{datetime.now().strftime('%Y%m%d%H%M%S')}.{source_hash[:12]}"
    payload = {
        "schema_version": 1, "product": "烘焙软件", "release_version": VERSION, "build_id": build_id, "assembled_at": assembled_at,
        "verified_environment": {"os": platform.platform(), "architecture": platform.machine(), "python": platform.python_version()},
        "provenance": {"method": "hash-bound source archive and application-source aggregate", "git_commit": None, "application_source_sha256": app_hash, "source_archive": f"05-源代码/{SOURCE_ZIP.name}", "source_archive_sha256": source_hash, "executable": f"04-可执行程序/{CURRENT_EXE.name}", "executable_sha256": exe_hash, "test_report": f"06-校验/test-report-{VERSION}.json", "test_report_sha256": sha256(evidence_dir / f"test-report-{VERSION}.json"), "frozen_baseline_verification": "06-校验/frozen-baseline-verification.json"},
        "release_scope": {"hardware_endurance_test": "not completed; requires real HB Model S long-duration field session", "supported_environment": "Windows 11 x86-64 verified; other Windows editions/architectures are not certified by this package"},
    }
    (evidence_dir / "release-metadata.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    traceability = f"""# 制品追溯矩阵

| 对象 | 包内路径 | SHA-256 / 绑定依据 |
|---|---|---|
| 应用源代码集合 | `05-源代码/{SOURCE_ZIP.name}` | 应用文件聚合：`{app_hash}` |
| 完整源码归档 | `05-源代码/{SOURCE_ZIP.name}` | `{source_hash}` |
| Windows EXE | `04-可执行程序/{CURRENT_EXE.name}` | `{exe_hash}` |
| 自动化测试报告 | `06-校验/test-report-{VERSION}.json` | 报告内应用源码哈希等于上述聚合值 |
| 冻结基线 | `02-冻结设计基线-0.0.3-r1/` | `06-校验/frozen-baseline-verification.json` 逐文件校验 |

构建标识：`{build_id}`  
组装时间：`{assembled_at}`
"""
    (evidence_dir / "artifact-traceability.md").write_text(traceability, encoding="utf-8")
    entries = manifest_entries(DELIVERY_DIR)
    (evidence_dir / "MANIFEST.json").write_text(json.dumps({"schema_version": 1, "files": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "MANIFEST.txt").write_text("\n".join(f"{item['bytes']:>12}  {item['sha256']}  {item['path']}" for item in entries) + "\n", encoding="utf-8")
    write_checksums(DELIVERY_DIR)

    validate_build_evidence(CURRENT_EXE)
    gate.validate_test_report(evidence_dir / bound_report.name, build_record["run_id"], gate.source_hash())
    with zipfile.ZipFile(DELIVERY_ZIP, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in DELIVERY_DIR.rglob("*") if p.is_file()):
            archive.write(path, f"{DELIVERY_NAME}/{path.relative_to(DELIVERY_DIR).as_posix()}")
    with zipfile.ZipFile(DELIVERY_ZIP) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC failed: {bad}")
    (RELEASE / f"{DELIVERY_ZIP.name}.sha256").write_text(f"{sha256(DELIVERY_ZIP)}  {DELIVERY_ZIP.name}\n", encoding="utf-8")
    print(json.dumps({"zip": str(DELIVERY_ZIP), "sha256": sha256(DELIVERY_ZIP), "build_id": build_id}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Assemble only a source/version/evidence-matched delivery")
    parser.add_argument("--smoke-exe", action="store_true", help="Require current EXE startup smoke before assembly")
    parser.add_argument("--build-label", help="Unique rebuild label; preserves historical deliveries")
    args = parser.parse_args()
    if args.build_label:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.build_label):
            parser.error("build-label must contain 1-48 ASCII letters, digits, underscores or hyphens")
        DELIVERY_NAME += f"-{args.build_label}"
        DELIVERY_DIR = RELEASE / DELIVERY_NAME
        DELIVERY_ZIP = RELEASE / f"{DELIVERY_NAME}.zip"
        SOURCE_ZIP = RELEASE / f"烘焙软件-{VERSION}-源码-{args.build_label}.zip"
    assemble(args.smoke_exe)
