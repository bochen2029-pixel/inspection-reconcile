"""Snapshot loading (SPEC §5): manifest, project, scope, CSV datasets, normalization file, evidence root.

Configuration defects raise RunError. Record defects are preserved as raw rows for R0 (SPEC §7.5).
"""

from __future__ import annotations

import csv
import io
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from inspection_reconcile import validate as v
from inspection_reconcile.canonical import sha256_hex
from inspection_reconcile.errors import RunError
from inspection_reconcile.grammar import format_ts, is_kind
from inspection_reconcile.vocab import CONSISTENCY_VALUES, COVERAGE_DATASETS, COVERAGE_VALUES, SCHEMAS

SNAPSHOT_FORMAT = "inspection-reconcile/snapshot/v1"
NORMALIZATION_FORMAT = "inspection-reconcile/normalization/v1"


@dataclass(frozen=True)
class InputFile:
    role: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class RawRow:
    dataset: str
    file: str
    row_number: int
    cells: Mapping[str, str | None] | None
    extras: Mapping[str, str | None]
    raw_cells: tuple[str, ...]
    header_count: int

    @property
    def malformed(self) -> bool:
        return self.cells is None

    @property
    def source(self) -> str | None:
        return self.extras.get("x_source")

    @property
    def locator(self) -> str:
        src = self.source
        return src if src else f"{self.file}#row={self.row_number}"


@dataclass(frozen=True)
class DatasetDecl:
    name: str
    location: str
    coverage: str
    basis: tuple[str, ...]
    consistency: str


@dataclass(frozen=True)
class ScopeInfo:
    file: str
    scope_revision: str
    accepted: dict[str, str] | None
    rows: tuple[RawRow, ...] | None


@dataclass(frozen=True)
class Normalization:
    mapping: dict[str, str]
    unmapped: frozenset[tuple[str, str, str]]
    unresolved: tuple[dict[str, Any], ...]
    files: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class LoadedSnapshot:
    root: Path
    snapshot_id: str
    synthetic: bool
    source: dict[str, str]
    capture_started: datetime
    capture_ended: datetime
    project: dict[str, Any]
    scope: ScopeInfo | None
    datasets: dict[str, DatasetDecl | None]
    rows: dict[str, tuple[RawRow, ...]]
    approval_items_declared: bool
    evidence_root: Path | None
    normalization: Normalization | None
    inputs: tuple[InputFile, ...]

    @property
    def scope_present(self) -> bool:
        return self.scope is not None and self.scope.rows is not None


# --------------------------------------------------------------------------------------------- JSON


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate member {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def read_json(path: Path, label: str) -> tuple[Any, bytes]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunError("INPUT_UNREADABLE", f"{label}: {exc.strerror or exc}") from exc
    try:
        text = data.decode("utf-8-sig")  # strips a leading byte-order mark
        value = json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RunError("CONFIG_INVALID", f"{label}: invalid JSON ({exc})") from exc
    return value, data


# ---------------------------------------------------------------------------------------------- CSV


def read_csv(path: Path, dataset: str, file_name: str) -> tuple[tuple[RawRow, ...], bytes]:
    """Read one dataset CSV per SPEC §5.4."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunError("DATASET_UNREADABLE", f"{file_name}: {exc.strerror or exc}") from exc
    if b"\x00" in data:
        raise RunError("CSV_UNREADABLE", f"{file_name}: contains a NUL byte")
    try:
        text = data.decode("utf-8-sig")  # strips a leading byte-order mark
    except UnicodeDecodeError as exc:
        raise RunError("CSV_UNREADABLE", f"{file_name}: not valid UTF-8") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise RunError("CSV_UNREADABLE", f"{file_name}: empty file (no header)")
        schema_cols = [c for c, _, _ in SCHEMAS[dataset]]
        seen: set[str] = set()
        for col in header:
            if col in seen:
                raise RunError("CSV_HEADER_INVALID", f"{file_name}: duplicated column {col!r}")
            seen.add(col)
            if col not in schema_cols and not col.startswith("x_"):
                raise RunError("CSV_HEADER_INVALID", f"{file_name}: unknown column {col!r}")
        missing = [c for c in schema_cols if c not in seen]
        if missing:
            raise RunError("CSV_HEADER_INVALID", f"{file_name}: missing column(s) {', '.join(missing)}")
        rows: list[RawRow] = []
        number = 1
        for fields in reader:
            if not fields:
                continue  # a blank line is not a record (SPEC §5.4)
            number += 1
            if len(fields) != len(header):
                rows.append(RawRow(dataset, file_name, number, None, {}, tuple(fields), len(header)))
                continue
            values = {h: (f if f != "" else None) for h, f in zip(header, fields, strict=True)}
            cells = {c: values[c] for c in schema_cols}
            extras = {h: values[h] for h in header if h.startswith("x_")}
            rows.append(RawRow(dataset, file_name, number, cells, extras, tuple(fields), len(header)))
    except csv.Error as exc:
        raise RunError("CSV_UNREADABLE", f"{file_name}: {exc}") from exc
    return tuple(rows), data


# ---------------------------------------------------------------------------------------- manifest


def _basis_token(value: Any, where: str) -> str:
    s = v.string(value, where)
    if not is_kind(s):
        raise v.fail(where, f"{s!r} is not a basis token")
    return s


def _dataset_decl(location_key: str) -> v.Check:
    def check(value: Any, where: str) -> dict[str, Any]:
        return v.obj(
            value,
            where,
            {
                location_key: v.single_segment_path,
                "coverage": v.enum(COVERAGE_VALUES),
                "basis": v.listof(_basis_token, min_len=1, unique=True),
                "consistency": v.enum(CONSISTENCY_VALUES),
            },
        )

    return check


def _datasets(value: Any, where: str) -> dict[str, Any]:
    return v.obj(
        value,
        where,
        {},
        {
            "inspections": _dataset_decl("file"),
            "artifacts": _dataset_decl("file"),
            "approvals": _dataset_decl("file"),
            "evidence_files": _dataset_decl("dir"),
        },
    )


def _accepted(value: Any, where: str) -> dict[str, str]:
    body = v.obj(value, where, {"by": v.text, "at": v.timestamp, "reference": v.text})
    return {"by": body["by"], "at": format_ts(body["at"]), "reference": body["reference"]}


def _scope_member(value: Any, where: str) -> dict[str, Any]:
    return v.obj(
        value,
        where,
        {"file": v.single_segment_path, "scope_revision": v.rev, "accepted": v.nullable(_accepted)},
    )


def parse_manifest(raw: Any) -> dict[str, Any]:
    body = v.obj(
        raw,
        "manifest.json",
        {
            "format": v.exact(SNAPSHOT_FORMAT),
            "snapshot_id": v.ident,
            "synthetic": v.boolean,
            "source": lambda x, w: v.obj(x, w, {"system": v.text, "description": v.text}),
            "capture": lambda x, w: v.obj(x, w, {"started_at": v.timestamp, "ended_at": v.timestamp}),
            "datasets": _datasets,
            "optional_datasets": lambda x, w: v.obj(
                x,
                w,
                {"approval_items": v.nullable(lambda y, u: v.obj(y, u, {"file": v.single_segment_path}))},
            ),
        },
        {"scope": _scope_member, "normalization": v.nullable(v.single_segment_path)},
    )
    if body["capture"]["started_at"] > body["capture"]["ended_at"]:
        raise v.fail("manifest.json.capture", "started_at is after ended_at")
    if body["optional_datasets"]["approval_items"] is not None and "approvals" not in body["datasets"]:
        raise v.fail(
            "manifest.json.optional_datasets", "approval_items is declared without an approvals dataset"
        )
    return body


def parse_project(raw: Any) -> dict[str, Any]:
    return v.obj(
        raw,
        "project.json",
        {"project_id": v.ident, "client_id": v.ident, "name": v.text, "synthetic": v.boolean},
    )


def parse_normalization(raw: Any, label: str) -> Normalization:
    datasets = ("scope", "inspections", "artifacts", "approvals", "approval_items")
    body = v.obj(
        raw,
        label,
        {
            "format": v.exact(NORMALIZATION_FORMAT),
            "mapping": lambda x, w: v.obj(
                x, w, {"mapping_id": v.ident, "version": v.string, "sha256": v.digest}
            ),
            "unmapped_values": v.listof(
                lambda x, w: v.obj(
                    x,
                    w,
                    {"dataset": v.enum(datasets), "source": v.string, "field": v.string, "raw": v.string},
                )
            ),
            "unresolved_references": v.listof(
                lambda x, w: v.obj(
                    x,
                    w,
                    {
                        "dataset": v.enum(datasets),
                        "source": v.string,
                        "field": v.string,
                        "target_table": v.string,
                        "rid": v.integer(0, 2**53 - 1),
                    },
                )
            ),
            "files": v.listof(
                lambda x, w: v.obj(x, w, {"relative_path": v.string, "original_name": v.string})
            ),
        },
    )
    unmapped = frozenset((u["dataset"], u["source"], u["field"]) for u in body["unmapped_values"])
    return Normalization(
        mapping=body["mapping"],
        unmapped=unmapped,
        unresolved=tuple(body["unresolved_references"]),
        files=tuple(body["files"]),
    )


def is_link_or_reparse(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def load_snapshot(root: Path) -> LoadedSnapshot:
    root = Path(root)
    if not root.is_dir():
        raise RunError("SNAPSHOT_MISSING", f"{root.as_posix()}: not a directory")
    inputs: list[InputFile] = []

    def record(role: str, path: Path, data: bytes) -> None:
        inputs.append(InputFile(role, path, len(data), sha256_hex(data)))

    raw_manifest, data = read_json(root / "manifest.json", "manifest.json")
    record("manifest", root / "manifest.json", data)
    manifest = parse_manifest(raw_manifest)

    raw_project, data = read_json(root / "project.json", "project.json")
    record("project", root / "project.json", data)
    project = parse_project(raw_project)

    scope: ScopeInfo | None = None
    if "scope" in manifest:
        sm = manifest["scope"]
        scope_path = root / sm["file"]
        scope_rows: tuple[RawRow, ...] | None = None
        if scope_path.is_file():
            scope_rows, data = read_csv(scope_path, "scope", sm["file"])
            record("scope", scope_path, data)
        scope = ScopeInfo(sm["file"], sm["scope_revision"], sm["accepted"], scope_rows)

    datasets: dict[str, DatasetDecl | None] = {name: None for name in COVERAGE_DATASETS}
    rows: dict[str, tuple[RawRow, ...]] = {
        "inspections": (),
        "artifacts": (),
        "approvals": (),
        "approval_items": (),
    }
    for name, decl in manifest["datasets"].items():
        location = decl["dir"] if name == "evidence_files" else decl["file"]
        datasets[name] = DatasetDecl(
            name, location, decl["coverage"], tuple(decl["basis"]), decl["consistency"]
        )
        if name == "evidence_files":
            continue
        path = root / location
        if not path.is_file():
            raise RunError("DATASET_MISSING_ON_DISK", f"{location}: declared in the manifest but absent")
        rows[name], data = read_csv(path, name, location)
        record(name, path, data)

    items_decl = manifest["optional_datasets"]["approval_items"]
    if items_decl is not None:
        path = root / items_decl["file"]
        if not path.is_file():
            raise RunError(
                "DATASET_MISSING_ON_DISK", f"{items_decl['file']}: declared in the manifest but absent"
            )
        rows["approval_items"], data = read_csv(path, "approval_items", items_decl["file"])
        record("approval_items", path, data)

    evidence_root: Path | None = None
    evidence_decl = datasets["evidence_files"]
    if evidence_decl is not None:
        evidence_root = root / evidence_decl.location
        if is_link_or_reparse(evidence_root) or not evidence_root.is_dir():
            raise RunError(
                "EVIDENCE_ROOT_INVALID",
                f"{evidence_decl.location}: the evidence root must be a real directory (not a link or junction)",
            )

    normalization: Normalization | None = None
    if manifest.get("normalization"):
        name = manifest["normalization"]
        raw_norm, data = read_json(root / name, name)
        record("normalization", root / name, data)
        normalization = parse_normalization(raw_norm, name)

    return LoadedSnapshot(
        root=root,
        snapshot_id=manifest["snapshot_id"],
        synthetic=manifest["synthetic"],
        source=manifest["source"],
        capture_started=manifest["capture"]["started_at"],
        capture_ended=manifest["capture"]["ended_at"],
        project=project,
        scope=scope,
        datasets=datasets,
        rows=rows,
        approval_items_declared=items_decl is not None,
        evidence_root=evidence_root,
        normalization=normalization,
        inputs=tuple(inputs),
    )
