"""Atomic byte writes and the --out protocol (SPEC §9.6)."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable, Mapping
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


def json_bytes(value: Any) -> bytes:
    """Pretty JSON for human-facing outputs: 2-space indent, UTF-8, LF, one trailing newline."""
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def prepare_out(out: Path, force: bool, own_dirs: Iterable[str] = ()) -> None:
    """Refuse a non-empty --out without --force; with --force remove only this tool's own outputs."""
    if not out.exists():
        return
    if not out.is_dir():
        raise RunError("OUT_INVALID", f"{out}: exists and is not a directory")
    entries = list(out.iterdir())
    if not entries:
        return
    if not force:
        raise RunError("OUT_NOT_EMPTY", f"{out}: exists and is not empty (use --force)")
    for name in KNOWN_OUTPUTS:
        target = out / name
        if target.is_file():
            target.unlink()
    for name in own_dirs:
        target = out / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)


def write_files(out: Path, files: Mapping[str, bytes]) -> list[Path]:
    """Write each file through ``<name>.tmp.<pid>`` + fsync + os.replace; undo this run's writes on failure."""
    written: list[Path] = []
    tmp: Path | None = None
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
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
        raise RunError("WRITE_FAILED", f"{out}: {exc.strerror or exc}") from exc
    return written
