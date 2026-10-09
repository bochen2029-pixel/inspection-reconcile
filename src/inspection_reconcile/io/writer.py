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
PREVIOUS_PREFIX = ".previous-"  # the previous output, moved aside while a --force swap runs


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
    _swap(out, previous, stage)


def _swap(out: Path, previous: list[Path], stage: Path) -> None:
    """Replace the previous output with the staged one, all or nothing.

    Both phases are renames inside --out: the previous output moves aside into a backup directory, then the staged
    output moves in. If any rename fails (on Windows a file held open by another program, Excel with a CSV for
    example, can be neither renamed nor deleted), every completed rename is undone, so --out is exactly as it was.
    The previous output is deleted only after the new one is in place."""
    aside: list[str] = []
    moved_in: list[str] = []
    try:
        backup = Path(tempfile.mkdtemp(prefix=PREVIOUS_PREFIX, dir=out))
    except OSError as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise RunError(
            "WRITE_FAILED", f"{out.as_posix()}: {exc.strerror or exc}; --out is unchanged"
        ) from exc
    try:
        for entry in previous:
            os.replace(entry, backup / entry.name)
            aside.append(entry.name)
        for entry in sorted(stage.iterdir()):
            os.replace(entry, out / entry.name)
            moved_in.append(entry.name)
    except OSError as exc:
        stuck = [name for name in reversed(moved_in) if not _rename(out / name, stage / name)]
        lost = sorted(name for name in reversed(aside) if not _rename(backup / name, out / name))
        extra = sorted(set(stuck) - set(aside))  # this run's names that the previous output had no entry for
        shutil.rmtree(stage, ignore_errors=True)
        problems: list[str] = []
        if lost:  # the backup is kept and named: it holds the only copy
            problems.append(f"the previous {', '.join(lost)} could not be put back from {backup.as_posix()}")
        else:
            _rmdir(backup)  # empty again
        if extra:
            problems.append(f"this run's {', '.join(extra)} could not be taken out of --out")
        outcome = "; ".join(problems) if problems else "--out is unchanged"
        raise RunError("WRITE_FAILED", f"{out.as_posix()}: {exc.strerror or exc}; {outcome}") from exc
    shutil.rmtree(backup, ignore_errors=True)  # the old output, no longer reachable from --out
    shutil.rmtree(stage, ignore_errors=True)  # empty by now


def _rmdir(directory: Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass


def _rename(src: Path, dst: Path) -> bool:
    try:
        os.replace(src, dst)
    except OSError:
        return False
    return True


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
        hint = ""
        if any(name.startswith(PREVIOUS_PREFIX) for name in foreign):  # never deleted: the only copy, maybe
            hint = f"; {PREVIOUS_PREFIX}* is an earlier output that a failed --force run set aside: restore or delete it"
        raise RunError(
            "OUT_NOT_EMPTY",
            f"{out.as_posix()}: --force replaces only this command's own previous output, and it also holds {shown}"
            + hint,
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
