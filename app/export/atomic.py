"""Stage a group of exports and roll back file replacements on failure."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path


ExportWriter = Callable[[Path], object]
ExportJobs = Mapping[str | Path, ExportWriter] | Iterable[tuple[str | Path, ExportWriter]]


class AtomicExportRollbackError(OSError):
    """A commit failed and some originals require recovery from retained backups."""

    def __init__(self, cause: BaseException, failures: dict[Path, BaseException],
                 backups: dict[Path, Path]) -> None:
        self.original_error = cause
        self.rollback_errors = failures
        self.backup_paths = backups
        details = "; ".join(f"{target}: {error}" for target, error in failures.items())
        recovery = "; ".join(f"{target} <- {backup}" for target, backup in backups.items())
        super().__init__(f"Export rollback incomplete: {details}. Retained backups: {recovery}")


def export_atomic(jobs: ExportJobs) -> tuple[Path, ...]:
    """Export all jobs, returning absolute destination paths in input order.

    ``jobs`` is a mapping or iterable of ``(destination, writer)`` pairs. Each
    synchronous writer receives a temporary Path with the destination's name
    and suffix, on the same filesystem, and must close its output before
    returning. Existing export APIs can be passed through lambdas/partials.
    Parent directories must exist; duplicate destinations and symlinks are
    rejected before any writer runs. Empty files are valid outputs.

    All writers finish and all originals are backed up before replacement.
    A writer/backup/commit error is re-raised after restoring replaced files
    and removing newly created destinations. If restoration also fails,
    AtomicExportRollbackError exposes backup_paths for manual recovery and
    preserves those backups. Temporary cleanup after completion is best effort.

    This provides rollback on caught failures, not a simultaneous multi-file
    filesystem transaction or crash recovery. Callers must serialize exports
    to overlapping destinations and avoid writers that touch final paths.
    """
    pairs = list(jobs.items() if isinstance(jobs, Mapping) else jobs)
    prepared = []
    seen = set()
    for destination, writer in pairs:
        target = Path(destination).absolute()
        identity = os.path.normcase(str(target.resolve()))
        if identity in seen:
            raise ValueError(f"Duplicate export destination: {target}")
        seen.add(identity)
        if not callable(writer):
            raise TypeError(f"Export writer must be callable: {target}")
        if target.is_symlink():
            raise ValueError(f"Export destination must not be a symlink: {target}")
        if target.exists() and not target.is_file():
            raise IsADirectoryError(str(target))
        if not target.parent.is_dir():
            raise FileNotFoundError(str(target.parent))
        prepared.append((target, writer))

    folders: list[Path] = []
    staged: list[tuple[Path, Path, Path | None]] = []
    committed: list[tuple[Path, Path | None]] = []
    retained: set[Path] = set()
    try:
        for target, writer in prepared:
            folder = Path(tempfile.mkdtemp(prefix=".hb-export-", dir=target.parent))
            folders.append(folder)
            stage = folder / target.name
            writer(stage)
            if not stage.is_file() or stage.is_symlink():
                raise FileNotFoundError(f"Export writer did not create a regular file: {stage}")
            backup = stage.with_name(stage.name + ".backup") if target.exists() else None
            if backup is not None:
                shutil.copy2(target, backup)
            staged.append((target, stage, backup))

        for target, stage, backup in staged:
            os.replace(stage, target)
            committed.append((target, backup))
    except BaseException as cause:
        failures = {}
        backups = {}
        for target, backup in reversed(committed):
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
            except BaseException as rollback_error:
                failures[target] = rollback_error
                if backup is not None:
                    retained.add(backup.parent)
                    backups[target] = backup
        if failures:
            raise AtomicExportRollbackError(cause, failures, backups) from cause
        raise
    finally:
        for folder in folders:
            if folder not in retained:
                shutil.rmtree(folder, ignore_errors=True)

    return tuple(target for target, _ in prepared)
