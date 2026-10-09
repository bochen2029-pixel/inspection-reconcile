"""The evidence probe (SPEC §7.10): exact-case resolution from directory listings on every operating system,
no links or reparse points followed, streamed SHA-256 with a size limit."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from inspection_reconcile.grammar import path_violation

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Present:
    sha256: str
    size: int
    status: str = "present"


@dataclass(frozen=True)
class Absent:
    case_hint: str | None = None
    status: str = "absent"


@dataclass(frozen=True)
class NoPath:
    status: str = "no_path"


@dataclass(frozen=True)
class Unreadable:
    detail: str
    status: str = "unreadable"


@dataclass(frozen=True)
class TooLarge:
    size: int
    status: str = "too_large"


FileProbe = Present | Absent | NoPath | Unreadable | TooLarge


class EvidenceStore(Protocol):
    def probe(self, relative_path: str | None) -> FileProbe: ...


def _is_link(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _entries(directory: Path) -> list[os.DirEntry[str]]:
    with os.scandir(directory) as it:
        return list(it)


@dataclass
class FileSystemEvidence:
    """Probe files under one evidence root. ``case_sensitive=False`` exists only for fault injection (FI-4)."""

    root: Path | None
    max_bytes: int
    case_sensitive: bool = True
    _cache: dict[str | None, FileProbe] = field(default_factory=dict)

    def probe(self, relative_path: str | None) -> FileProbe:
        if relative_path in self._cache:
            return self._cache[relative_path]
        result = self._probe(relative_path)
        self._cache[relative_path] = result
        return result

    def _match(
        self, entries: list[os.DirEntry[str]], segment: str, sensitive: bool
    ) -> os.DirEntry[str] | None:
        if sensitive:
            for e in entries:
                if e.name == segment:
                    return e
            return None
        folded = segment.casefold()
        candidates = sorted((e for e in entries if e.name.casefold() == folded), key=lambda e: e.name)
        return candidates[0] if candidates else None

    def _resolve(self, relative_path: str, sensitive: bool) -> tuple[str, os.DirEntry[str] | None, list[str]]:
        """Walk the segments. Returns (state, final entry, actual names); state is ok|absent|link|notfile."""
        assert self.root is not None
        current = self.root
        actual: list[str] = []
        segments = relative_path.split("/")
        for i, segment in enumerate(segments):
            try:
                entries = _entries(current)
            except OSError:
                return "absent", None, actual
            entry = self._match(entries, segment, sensitive)
            if entry is None:
                return "absent", None, actual
            if _is_link(entry):
                return "link", entry, actual
            actual.append(entry.name)
            last = i == len(segments) - 1
            try:
                if not last:
                    if not entry.is_dir(follow_symlinks=False):
                        return "absent", None, actual
                    current = current / entry.name
                    continue
                if not entry.is_file(follow_symlinks=False):
                    return "notfile", entry, actual
            except OSError:
                return "absent", None, actual
            return "ok", entry, actual
        return "absent", None, actual

    def _hash(self, path: Path, size: int) -> FileProbe:
        if size > self.max_bytes:
            return TooLarge(size)
        digest = hashlib.sha256()
        total = 0
        try:
            with open(path, "rb") as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        return TooLarge(total)
                    digest.update(chunk)
        except PermissionError:
            return Unreadable("permission denied")
        except OSError:
            return Unreadable("read error")
        return Present(digest.hexdigest(), total)

    def _probe(self, relative_path: str | None) -> FileProbe:
        if relative_path is None:
            return NoPath()
        if self.root is None:
            return Absent(None)
        if path_violation(relative_path) is not None:
            return Unreadable("invalid path")
        state, entry, actual = self._resolve(relative_path, self.case_sensitive)
        if state == "link":
            return Unreadable("link not followed")
        if state == "notfile":
            return Unreadable("not a regular file")
        if state == "absent" or entry is None:
            hint = None
            if self.case_sensitive:
                hstate, _, hactual = self._resolve(relative_path, False)
                if hstate == "ok":
                    hint = "/".join(hactual)
            return Absent(hint)
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            return Unreadable("read error")
        return self._hash(self.root.joinpath(*actual), size)
