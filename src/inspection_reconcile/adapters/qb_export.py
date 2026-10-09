"""Normalize a Quickbase-shaped export (SPEC §12.2) into a canonical snapshot (SPEC §5) through a field
mapping (SPEC §12.3).

Nothing is guessed: an unmapped multiple-choice label is written raw and listed in ``normalization.json``
(R0 then reports ``UNMAPPED_VALUE``); a reference to an uncaptured record becomes the placeholder
``qbrid.<table>.<rid>``; a value of an unexpected JSON type becomes its canonical JSON text and is listed as
unmapped too (AM-7). Files are copied under the evidence root only when the export captured them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from inspection_reconcile import grammar
from inspection_reconcile import validate as v
from inspection_reconcile.adapters.mapping import (
    MAX_FID,
    ROLE_DATASET,
    ROLE_KEY_COLUMN,
    FieldMap,
    Mapping,
    TableMap,
    verify_fields,
)
from inspection_reconcile.canonical import sha256_hex, sort_canonical
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.snapshot import NORMALIZATION_FORMAT, SNAPSHOT_FORMAT, read_json
from inspection_reconcile.io.writer import json_bytes, write_files
from inspection_reconcile.vocab import CONSISTENCY_VALUES, COVERAGE_VALUES, KEY_FIELDS, SCHEMAS

EXPORT_FORMAT = "inspection-reconcile/qb-export/v1"
CAPTURE_MANIFEST = "capture-manifest.json"
FILE_STATUSES = ("captured", "not_captured", "out_of_scope", "too_large", "error")
TWO_PASS_VALUES = ("not_run", "stable", "changed")
EVIDENCE_DIR = "evidence"
NORMALIZATION_FILE = "normalization.json"
DATASET_FILE = {
    "scope": "scope.csv",
    "inspections": "inspections.csv",
    "artifacts": "artifacts.csv",
    "approvals": "approvals.csv",
    "approval_items": "approval_items.csv",
}
COVERAGE_DATASETS = ("inspections", "artifacts", "approvals", "evidence_files")
MAX_FILE_NAME = 100
_UNSAFE_NAME_CHAR = re.compile(r"[^A-Za-z0-9._-]")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DBID = re.compile(r"[A-Za-z0-9]{1,64}")
_PAGE_FILE = re.compile(r"page-([0-9]{4,})\.json")


def _invalid(message: str) -> RunError:
    return RunError("EXPORT_INVALID", message)


# ------------------------------------------------------------------------------------- export manifest


def _dbid(value: Any, where: str) -> str:
    s = v.string(value, where)
    if _DBID.fullmatch(s) is None:
        raise v.fail(where, f"{s!r} is not a Quickbase id")
    return s


def _ts_string(value: Any, where: str) -> str:
    s = v.string(value, where)
    if grammar.parse_ts(s) is None:
        raise v.fail(where, f"{s!r} is not an RFC 3339 timestamp with an offset")
    return s


def _basis_token(value: Any, where: str) -> str:
    s = v.string(value, where)
    if not grammar.is_kind(s):
        raise v.fail(where, f"{s!r} is not a basis token")
    return s


def _coverage_decl(value: Any, where: str) -> dict[str, Any]:
    return v.obj(
        value,
        where,
        {
            "coverage": v.enum(COVERAGE_VALUES),
            "basis": v.listof(_basis_token, min_len=1, unique=True),
            "consistency": v.enum(CONSISTENCY_VALUES),
        },
    )


def _hex64(value: Any, where: str) -> str:
    s = v.string(value, where)
    if _HEX64.fullmatch(s) is None:
        raise v.fail(where, "expected 64 lowercase hex digits")
    return s


def _table_entry(value: Any, where: str) -> dict[str, Any]:
    return v.obj(
        value,
        where,
        {
            "table_id": _dbid,
            "select": v.listof(v.integer(1, MAX_FID), min_len=1, unique=True),
            "where": v.nullable(v.string),
            "pages": v.integer(0, 10**7),
            "total_records": v.integer(0, 2**53 - 1),
            "retrieved": v.integer(0, 2**53 - 1),
            "two_pass": v.enum(TWO_PASS_VALUES),
        },
        {"paging": v.enum(("keyset", "skip"))},
    )


def _file_entry(value: Any, where: str) -> dict[str, Any]:
    body = v.obj(
        value,
        where,
        {
            "table_id": _dbid,
            "record_id": v.integer(1, 2**53 - 1),
            "field_id": v.integer(1, MAX_FID),
            "version": v.integer(1, 2**53 - 1),
            "file_name": v.string,
            "path": v.nullable(v.string),
            "bytes": v.nullable(v.integer(0, 2**53 - 1)),
            "sha256": v.nullable(_hex64),
            "status": v.enum(FILE_STATUSES),
        },
    )
    if body["status"] == "captured":
        if body["path"] is None or body["bytes"] is None or body["sha256"] is None:
            raise v.fail(where, "a captured file needs path, bytes and sha256")
        if grammar.path_violation(body["path"]) is not None:
            raise v.fail(where, f"path {body['path']!r} is not a safe relative path")
    elif body["path"] is not None or body["sha256"] is not None:
        raise v.fail(where, f"a {body['status']} file has no path or sha256 (AM-4)")
    elif body["bytes"] is not None and body["status"] != "too_large":
        raise v.fail(where, f"a {body['status']} file has no bytes; only too_large records the size (AM-4)")
    return body


def _accepted(value: Any, where: str) -> dict[str, str]:
    return v.obj(value, where, {"by": v.text, "at": _ts_string, "reference": v.text})


def parse_export_manifest(raw: Any, label: str = CAPTURE_MANIFEST) -> dict[str, Any]:
    """Validate ``capture-manifest.json`` (SPEC §12.2). Unknown members and wrong types are run errors."""
    body = v.obj(
        raw,
        label,
        {
            "format": v.exact(EXPORT_FORMAT),
            "export_id": v.ident,
            "synthetic": v.boolean,
            "source": lambda x, w: v.obj(
                x,
                w,
                {
                    "system": v.exact("quickbase"),
                    "realm_hostname": v.string,
                    "app_id": _dbid,
                    "description": v.text,
                },
            ),
            "capture": lambda x, w: v.obj(x, w, {"started_at": _ts_string, "ended_at": _ts_string}),
            "tables": lambda x, w: v.obj(
                x,
                w,
                {},
                {role: _table_entry for role in ROLE_DATASET},
            ),
            "datasets": lambda x, w: v.obj(x, w, {}, {name: _coverage_decl for name in COVERAGE_DATASETS}),
            "files": v.listof(_file_entry),
        },
        {"scope": lambda x, w: v.obj(x, w, {"scope_revision": v.rev, "accepted": v.nullable(_accepted)})},
    )
    started = grammar.parse_ts(body["capture"]["started_at"])
    ended = grammar.parse_ts(body["capture"]["ended_at"])
    if started is not None and ended is not None and started > ended:
        raise v.fail(f"{label}.capture", "started_at is after ended_at")
    return body


# ------------------------------------------------------------------------------------------ file names


def sanitize_file_name(name: str) -> str:
    """SPEC §12.3 "Name sanitization": a safe single path segment for an attachment's file name."""
    s = _UNSAFE_NAME_CHAR.sub("_", name).rstrip(".")
    if not s:
        s = "file"
    if s.split(".", 1)[0].upper() in grammar.RESERVED_NAMES:
        s = "_" + s
    if len(s) > MAX_FILE_NAME:
        dot = s.rfind(".")
        ext = s[dot:] if dot > 0 else ""
        digest = hashlib.sha256(name.encode("utf-8", errors="surrogatepass")).hexdigest()[:8]
        s = (s[:83] + "~" + digest + ext)[:MAX_FILE_NAME]
    return s


# ----------------------------------------------------------------------------------------- conversions


def json_text(value: Any) -> str:
    """The canonical JSON text of a value of an unexpected type."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unexpected(value: Any) -> Converted:
    """A JSON type the §12.3 table does not accept for its mapping type. The cell is its canonical JSON text, and
    the cell is listed as unmapped, so R0 reports it whether or not that text satisfies the column grammar
    (``1.5`` is a valid TEXT, ``123`` a valid ID): it is never guessed (I-9, AM-7)."""
    text = json_text(value)
    return Converted(text, unmapped_label=text)


def _integer(value: Any) -> int | None:
    """An integer, or a float with an integral value; anything else (including booleans) is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


@dataclass(frozen=True)
class Converted:
    cell: str | None
    unmapped_label: str | None = None


def convert_value(fm: FieldMap, value: Any) -> Converted:
    """SPEC §12.3 "Conversions" for every type except ``reference`` and ``file`` (which need context)."""
    if value is None:
        return Converted(None)
    t = fm.type
    if t in ("text", "timestamp"):
        if isinstance(value, str):
            return Converted(value if value != "" else None)
        return _unexpected(value)
    if t == "text-multiple-choice":
        label: str | None
        if isinstance(value, str):
            label = value
        elif isinstance(value, list) and len(value) == 0:
            label = ""
        elif isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            label = value[0]
        else:
            return _unexpected(value)
        if label == "":
            return Converted(None)
        if fm.values is None:
            return Converted(label)
        mapped = fm.values.get(label)
        if mapped is None:
            return Converted(label, unmapped_label=label)
        return Converted(mapped)
    if t in ("numeric", "recordid"):
        n = _integer(value)
        return Converted(str(n)) if n is not None else _unexpected(value)
    if t == "checkbox":
        if isinstance(value, bool):
            return Converted("true" if value else "false")
        return _unexpected(value)
    if t == "user":
        if isinstance(value, dict) and isinstance(value.get("email"), str):
            return Converted(value["email"] if value["email"] != "" else None)
        return _unexpected(value)
    raise ValueError(f"convert_value does not handle type {t!r}")  # reference and file are contextual


def reference_rid(value: Any) -> int | None:
    """The Record ID# a reference value names, or None when the value is not a record id."""
    n = _integer(value)
    return n if n is not None and n >= 1 else None


def placeholder(target_table_id: str, rid: int) -> str:
    """SPEC §12.3: an unresolved reference becomes ``qbrid.<target_table_id>.<rid>`` (a valid ID)."""
    return f"qbrid.{target_table_id}.{rid}"


def latest_version(value: Any) -> tuple[int, str] | None:
    """The highest ``versionNumber`` of a file attachment value and its file name; None when there is none."""
    if not isinstance(value, dict):
        return None
    versions = value.get("versions")
    if not isinstance(versions, list) or not versions:
        return None
    best: tuple[int, str] | None = None
    for item in versions:
        if not isinstance(item, dict):
            continue
        number = _integer(item.get("versionNumber"))
        name = item.get("fileName")
        if number is None or number < 1 or not isinstance(name, str):
            continue
        if best is None or number > best[0]:
            best = (number, name)
    return best


def file_relative_path(table_id: str, rid: int, fid: int, version: int, file_name: str) -> str:
    """SPEC §12.3 "Path": ``files/<table_id>/<rid>/<fid>/v<n>/<sanitized>``."""
    return f"files/{table_id}/{rid}/{fid}/v{version}/{sanitize_file_name(file_name)}"


# ------------------------------------------------------------------------------------------------ pages


@dataclass(frozen=True)
class SourceRecord:
    rid: int
    values: dict[int, Any]


def records_from_pages(table: TableMap, pages: list[Any], label: str) -> list[SourceRecord]:
    """Flatten ``runQuery`` pages into records keyed by field id. Every mapped field must be present."""
    out: list[SourceRecord] = []
    needed = {f.fid for f in table.fields.values()} | {3}
    for p, page in enumerate(pages, start=1):
        where = f"{label} page {p}"
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise _invalid(f"{where}: not a runQuery response with a data array")
        for record in page["data"]:
            if not isinstance(record, dict):
                raise _invalid(f"{where}: a record is not an object")
            values: dict[int, Any] = {}
            for key, cell in record.items():
                if not isinstance(key, str) or not key.isdigit():
                    raise _invalid(f"{where}: field key {key!r} is not a field id")
                if not isinstance(cell, dict) or "value" not in cell:
                    raise _invalid(f"{where}: field {key} is not a {{'value': ...}} object")
                values[int(key)] = cell["value"]
            missing = sorted(needed - set(values))
            if missing:
                raise _invalid(f"{where}: a record lacks field(s) {missing}")
            rid = reference_rid(values[3])
            if rid is None:
                raise _invalid(f"{where}: field 3 (Record ID#) is not a positive integer: {values[3]!r}")
            out.append(SourceRecord(rid, values))
    return out


def unique_records(records: list[SourceRecord]) -> list[SourceRecord]:
    """The first occurrence of each Record ID#, in page order."""
    seen: set[int] = set()
    out: list[SourceRecord] = []
    for rec in records:
        if rec.rid not in seen:
            seen.add(rec.rid)
            out.append(rec)
    return out


# -------------------------------------------------------------------------------------------- normalize


@dataclass
class _Notes:
    unmapped: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, str]] = field(default_factory=list)
    copies: list[tuple[str, dict[str, Any]]] = field(
        default_factory=list
    )  # (relative_path, export file entry)


def _sort_key(cells: dict[str, str | None], key_fields: tuple[str, ...], rid: int) -> tuple[Any, ...]:
    parts: list[Any] = []
    for name in key_fields:
        value = cells.get(name)
        parts.append((value is None, value or ""))
    parts.append(rid)
    return tuple(parts)


def _csv_bytes(columns: list[str], rows: list[list[str | None]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buf.getvalue().encode("utf-8")


def _read_pages(table_dir: Path, label: str) -> list[Any]:
    names = sorted(
        (p.name for p in table_dir.iterdir() if _PAGE_FILE.fullmatch(p.name)), key=lambda n: int(n[5:-5])
    )
    pages: list[Any] = []
    for i, name in enumerate(names, start=1):
        if int(name[5:-5]) != i:
            raise _invalid(f"{label}: page files are not numbered 1..{len(names)} without gaps ({name})")
        page, _ = read_json(table_dir / name, f"{label}/{name}")
        pages.append(page)
    return pages


def convert_export(
    export_dir: Path, mapping: Mapping
) -> tuple[dict[str, Any], dict[str, list[tuple[dict[str, str | None], int]]], _Notes]:
    """Read and convert an export: (validated capture manifest, rows per dataset as (cells, rid), notes)."""
    raw_manifest, _ = read_json(export_dir / CAPTURE_MANIFEST, CAPTURE_MANIFEST)
    manifest = parse_export_manifest(raw_manifest)

    for role in manifest["tables"]:
        if role not in mapping.tables:
            raise _invalid(f"the export contains table {role!r}, which the mapping does not map")
    records: dict[str, list[SourceRecord]] = {}
    for role, table in mapping.tables.items():
        entry = manifest["tables"].get(role)
        if entry is None:
            raise _invalid(f"the mapping maps table {role!r}, which the export does not contain")
        if entry["table_id"] != table.table_id:
            raise _invalid(
                f"table {role}: export table id {entry['table_id']} != mapping table id {table.table_id}"
            )
        table_dir = export_dir / "tables" / role
        fields, _ = read_json(table_dir / "fields.json", f"tables/{role}/fields.json")
        verify_fields(mapping, role, fields)
        pages = _read_pages(table_dir, f"tables/{role}")
        if len(pages) != entry["pages"]:
            raise _invalid(
                f"table {role}: the manifest lists {entry['pages']} page(s) but {len(pages)} exist"
            )
        records[role] = records_from_pages(table, pages, f"tables/{role}")
        if len(records[role]) != entry["retrieved"]:
            raise _invalid(
                f"table {role}: the manifest says {entry['retrieved']} record(s) were retrieved but the pages hold "
                f"{len(records[role])}"
            )
        rids = [rec.rid for rec in records[role]]
        if len(set(rids)) != len(rids):
            # Record IDs are unique within a Quickbase table, so a repeat is a capture artifact: a page that
            # re-delivered a record. It is tolerated only where the manifest already reports a failed read.
            if entry["retrieved"] == entry["total_records"] and entry["two_pass"] != "changed":
                raise _invalid(f"table {role}: a Record ID# repeats in a read the manifest declares clean")
            records[role] = unique_records(records[role])

    files_index: dict[tuple[str, int, int, int], dict[str, Any]] = {}
    for item in manifest["files"]:
        files_index[(item["table_id"], item["record_id"], item["field_id"], item["version"])] = item
    rows, notes = convert_records(mapping, records, files_index)
    return manifest, rows, notes


def convert_records(
    mapping: Mapping,
    records: dict[str, list[SourceRecord]],
    files_index: dict[tuple[str, int, int, int], dict[str, Any]] | None = None,
) -> tuple[dict[str, list[tuple[dict[str, str | None], int]]], _Notes]:
    """Convert source records into canonical rows per dataset, as (cells with x_source, Record ID#), sorted by
    key then Record ID#. ``files_index`` maps (table, rid, fid, version) to the export's file entries; files
    whose entry is ``captured`` are scheduled for copying in the returned notes. With an index (normalize),
    every record's latest file version must have an entry: the export's file list is closed-world."""
    require_entries = files_index is not None
    files_index = files_index or {}
    # Each reference target: Record ID# -> the canonical id its key column converts to.
    key_maps: dict[str, dict[int, str | None]] = {}
    for role, key_column in ROLE_KEY_COLUMN.items():
        target = mapping.tables.get(role)
        if target is None or key_column not in target.fields:
            continue
        key_field = target.fields[key_column]
        key_maps[role] = {
            rec.rid: convert_value(key_field, rec.values[key_field.fid]).cell for rec in records.get(role, [])
        }

    notes = _Notes()
    rows: dict[str, list[tuple[dict[str, str | None], int]]] = {}
    for role, table in mapping.tables.items():
        dataset = ROLE_DATASET[role]
        out_rows: list[tuple[dict[str, str | None], int]] = []
        for rec in records.get(role, []):
            source = f"table={table.table_id};rid={rec.rid}"
            cells: dict[str, str | None] = {}
            for column, _type, _req in SCHEMAS[dataset]:
                mapped = table.fields.get(column)
                if mapped is None:
                    cells[column] = None
                    continue
                fm = mapped
                value = rec.values[fm.fid]
                if fm.type == "reference":
                    cells[column] = _resolve_reference(fm, value, mapping, key_maps, notes, dataset, source)
                elif fm.type == "file":
                    cells[column] = _file_cell(table, fm, rec, files_index, notes, require_entries)
                else:
                    converted = convert_value(fm, value)
                    cells[column] = converted.cell
                    if converted.unmapped_label is not None:
                        notes.unmapped.append(
                            {
                                "dataset": dataset,
                                "source": source,
                                "field": column,
                                "raw": converted.unmapped_label,
                            }
                        )
            cells["x_source"] = source
            out_rows.append((cells, rec.rid))
        out_rows.sort(key=lambda item: _sort_key(item[0], KEY_FIELDS[dataset], item[1]))
        rows[dataset] = out_rows
    return rows, notes


def _resolve_reference(
    fm: FieldMap,
    value: Any,
    mapping: Mapping,
    key_maps: dict[str, dict[int, str | None]],
    notes: _Notes,
    dataset: str,
    source: str,
) -> str | None:
    if value is None:
        return None
    rid = reference_rid(value)
    if rid is None:
        return json_text(value)
    assert fm.target is not None  # guaranteed by mapping validation
    targets = key_maps.get(fm.target, {})
    if rid in targets:
        return targets[rid]
    target_table = mapping.tables[fm.target].table_id
    notes.unresolved.append(
        {"dataset": dataset, "source": source, "field": fm.column, "target_table": target_table, "rid": rid}
    )
    return placeholder(target_table, rid)


def _file_cell(
    table: TableMap,
    fm: FieldMap,
    rec: SourceRecord,
    files_index: dict[tuple[str, int, int, int], dict[str, Any]],
    notes: _Notes,
    require_entries: bool = False,
) -> str | None:
    value = rec.values[fm.fid]
    if value is None:
        return None
    latest = latest_version(value)
    if latest is None:
        if isinstance(value, dict) and isinstance(value.get("versions"), list) and not value["versions"]:
            return None  # an empty versions list means no file
        return json_text(value)
    version, original = latest
    relative = file_relative_path(table.table_id, rec.rid, fm.fid, version, original)
    notes.files.append({"relative_path": relative, "original_name": original})
    entry = files_index.get((table.table_id, rec.rid, fm.fid, version))
    if entry is None and require_entries:
        raise _invalid(
            f"table {table.role}: record {rec.rid} has file version v{version} in field {fm.fid}, "
            "but the capture manifest lists no files[] entry for it"
        )
    if entry is not None and entry["status"] == "captured":
        notes.copies.append((relative, entry))
    return relative


def normalize(export_dir: Path, mapping: Mapping, out_dir: Path) -> None:
    """Write the canonical snapshot for ``export_dir`` into ``out_dir`` (absent or empty; SPEC §12.3 "Output")."""
    export_dir = Path(export_dir)
    out_dir = Path(out_dir)
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise RunError("OUT_NOT_EMPTY", f"{out_dir}: the normalize output directory must be absent or empty")
    manifest, rows, notes = convert_export(export_dir, mapping)

    files: dict[str, bytes] = {}
    for dataset, dataset_rows in rows.items():
        columns = [name for name, _, _ in SCHEMAS[dataset]] + ["x_source"]
        files[DATASET_FILE[dataset]] = _csv_bytes(
            columns, [[cells[c] for c in columns] for cells, _ in dataset_rows]
        )
    files["project.json"] = json_bytes(dict(mapping.project))
    files[NORMALIZATION_FILE] = json_bytes(
        {
            "format": NORMALIZATION_FORMAT,
            "mapping": mapping.reference(),
            "unmapped_values": sort_canonical(notes.unmapped),
            "unresolved_references": sort_canonical(notes.unresolved),
            "files": sort_canonical(notes.files),
        }
    )
    files["manifest.json"] = json_bytes(_snapshot_manifest(manifest, mapping))

    written: list[Path] = []
    try:
        (out_dir / EVIDENCE_DIR).mkdir(parents=True, exist_ok=True)
        for relative, entry in sorted(notes.copies, key=lambda item: item[0]):
            data = _read_captured_file(export_dir, entry)
            written += write_files(out_dir / EVIDENCE_DIR, {relative: data})
        written += write_files(out_dir, files)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def _read_captured_file(export_dir: Path, entry: dict[str, Any]) -> bytes:
    path = export_dir / entry["path"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise _invalid(f"{entry['path']}: listed as captured but unreadable ({exc.strerror or exc})") from exc
    if len(data) != entry["bytes"] or sha256_hex(data) != entry["sha256"]:
        raise _invalid(f"{entry['path']}: the bytes do not match the size and sha256 in the capture manifest")
    return data


def _snapshot_manifest(export: dict[str, Any], mapping: Mapping) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    for name in COVERAGE_DATASETS:
        decl = export["datasets"].get(name)
        if decl is None:
            continue
        location = {"dir": EVIDENCE_DIR} if name == "evidence_files" else {"file": DATASET_FILE[name]}
        datasets[name] = {**location, **decl}
    manifest: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "snapshot_id": export["export_id"],
        "synthetic": export["synthetic"],
        "source": {"system": "quickbase", "description": export["source"]["description"]},
        "capture": {"started_at": export["capture"]["started_at"], "ended_at": export["capture"]["ended_at"]},
    }
    if "scope" in export:
        manifest["scope"] = {
            "file": DATASET_FILE["scope"],
            "scope_revision": export["scope"]["scope_revision"],
            "accepted": export["scope"]["accepted"],
        }
    manifest["datasets"] = datasets
    manifest["optional_datasets"] = {
        "approval_items": {"file": DATASET_FILE["approval_items"]}
        if "approval_items" in mapping.tables
        else None
    }
    manifest["normalization"] = NORMALIZATION_FILE
    return manifest


# Exported for the capture step, which writes the export with the same names and conversions.
__all__ = [
    "CAPTURE_MANIFEST",
    "EXPORT_FORMAT",
    "Converted",
    "SourceRecord",
    "convert_export",
    "convert_records",
    "convert_value",
    "file_relative_path",
    "json_text",
    "latest_version",
    "normalize",
    "parse_export_manifest",
    "placeholder",
    "records_from_pages",
    "reference_rid",
    "sanitize_file_name",
]
