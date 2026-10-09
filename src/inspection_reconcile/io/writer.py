"""Atomic byte writes and the --out protocol (SPEC §9.6)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from inspection_reconcile.errors import RunError

KNOWN_OUTPUTS = (
    "assessment.json",
    "report.html",
    "run-manifest.json",
    "comparison.json",
    "index.html",
    "evaluation_ids.json",
)
# The output names of the commands whose --out must hold nothing else (SPEC §9.6).
NORMALIZE_OUTPUTS = (
    "manifest.json",
    "project.json",
    "scope.csv",
    "inspections.csv",
    "artifacts.csv",
    "approvals.csv",
    "approval_items.csv",
    "normalization.json",
)
NORMALIZE_DIRS = ("evidence",)
CAPTURE_OUTPUTS = ("capture-manifest.json",)
CAPTURE_DIRS = ("tables", "files")
STAGING_PREFIX = ".staging-"


def json_bytes(value: Any) -> bytes:
    """Pretty JSON for human-facing outputs: 2-space indent, UTF-8, LF, one trailing newline."""
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def prepare_out(out: Path, force: bool, own_dirs: Iterable[str] = ()) -> None:
    """Refuse a non-empty --out without --force; with --force remove only this tool's own outputs."""
    if not out.exists():
        return
    if not out.is_dir():
        raise RunError("OUT_INVALID", f"{out.as_posix()}: exists and is not a directory")
    entries = list(out.iterdir())
    if not entries:
        return
    if not force:
        raise RunError("OUT_NOT_EMPTY", f"{out.as_posix()}: exists and is not empty (use --force)")
    for name in KNOWN_OUTPUTS:
        target = out / name
        if target.is_file():
            target.unlink()
    for name in own_dirs:
        target = out / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)


@contextmanager
def replacing_out(
    out: Path, force: bool, own_files: Iterable[str], own_dirs: Iterable[str]
) -> Iterator[Path]:
    """--out for ``normalize`` and ``capture-quickbase``: yield the empty directory to write into.

    Without --force, --out must be absent or empty. With --force it may hold only this command's own output names,
    checked before anything is touched. The new output is then written into a staging directory inside --out and
    replaces the previous one only when the command succeeds, so a refused or failed run leaves --out as it was.
    """
    previous = _replaceable(out, force, set(own_files), set(own_dirs))
    if not previous:
        yield out
        return
    stage = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=out))
    try:
        yield stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    try:
        for entry in previous:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        for entry in sorted(stage.iterdir()):
            os.replace(entry, out / entry.name)
        stage.rmdir()
    except OSError as exc:
        raise RunError("WRITE_FAILED", f"{out.as_posix()}: {exc.strerror or exc}") from exc


def _replaceable(out: Path, force: bool, files: set[str], dirs: set[str]) -> list[Path]:
    """The previous output that --force may replace. Anything else in --out refuses the run."""
    if not out.exists():
        return []
    if not out.is_dir():
        raise RunError("OUT_INVALID", f"{out.as_posix()}: exists and is not a directory")
    entries = sorted(out.iterdir())
    if not entries:
        return []
    if not force:
        raise RunError("OUT_NOT_EMPTY", f"{out.as_posix()}: exists and is not empty (use --force)")
    foreign = [e.name for e in entries if not _owned(e, files, dirs)]
    if foreign:
        shown = ", ".join(foreign[:5]) + (f" (and {len(foreign) - 5} more)" if len(foreign) > 5 else "")
        raise RunError(
            "OUT_NOT_EMPTY",
            f"{out.as_posix()}: --force replaces only this command's own previous output, and it also holds {shown}",
        )
    return entries


def _owned(entry: Path, files: set[str], dirs: set[str]) -> bool:
    if entry.is_symlink() or entry.is_junction():  # never followed, never deleted
        return False
    if entry.name.startswith(STAGING_PREFIX):  # left behind by an interrupted run
        return entry.is_dir()
    if entry.name in files:
        return entry.is_file()
    return entry.name in dirs and entry.is_dir()


def write_files(out: Path, files: Mapping[str, bytes]) -> list[Path]:
    """Write each file through ``<name>.tmp.<pid>`` + fsync + os.replace. On failure, undo this run's writes: its
    files, then the directories it created that are empty again, deepest first."""
    written: list[Path] = []
    created: list[Path] = []
    tmp: Path | None = None

    def make_dirs(directory: Path) -> None:
        missing = []
        while not directory.exists() and directory != directory.parent:  # a missing drive ends the walk
            missing.append(directory)
            directory = directory.parent
        for d in reversed(missing):
            d.mkdir(exist_ok=True)
            created.append(d)

    try:
        make_dirs(out)
        for name, data in files.items():
            target = out / name
            make_dirs(target.parent)
            tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            tmp = None
            written.append(target)
    except OSError as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        for path in written:
            path.unlink(missing_ok=True)
        for directory in sorted(created, key=lambda d: len(d.parts), reverse=True):
            try:
                directory.rmdir()  # only if empty: a directory another writer filled meanwhile stays
            except OSError:
                pass
        raise RunError("WRITE_FAILED", f"{out.as_posix()}: {exc.strerror or exc}") from exc
    return written
