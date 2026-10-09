"""Field mappings for Quickbase-shaped exports (SPEC §12.3): the model, its closed-world validation, and the
check of a table's field list (``GET /fields``) against it (SPEC §12.5 step 1, §12.3 "Types").

The rules beyond the literal text of §12.3 are its conservative completions, so that every mapping that loads is
meaningful:

* a mapping type must suit the canonical column (``TYPE_COMPATIBILITY``); for example ``file`` is the only
  type for ``relative_path`` and ``relative_path`` the only column for ``file``;
* ``reference`` is allowed only on a link column and must target that column's parent table;
* field 3 (Record ID#) maps only with ``type: recordid``; field 2 (Date Modified) only with ``type: timestamp``;
* ``values`` is required on a multiple-choice field feeding a KIND or enum column, and every canonical value
  in it must satisfy that column's grammar;
* ``scope_filter`` names the obligations table's ``project_id`` field and the mapped project's id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from inspection_reconcile import grammar
from inspection_reconcile import validate as v
from inspection_reconcile.canonical import CanonicalError, digest_of
from inspection_reconcile.errors import RunError
from inspection_reconcile.vocab import ENUMS, SCHEMAS
from inspection_reconcile.yamlsafe import load_yaml

MAPPING_SCHEMA = "inspection-reconcile/mapping/v1"

# SPEC §12.3 "Types": the getFields fieldType each mapping type expects; recordid is field 3 (Record ID#).
EXPECTED_FIELD_TYPE: dict[str, str] = {
    "text": "text",
    "text-multiple-choice": "text-multiple-choice",
    "numeric": "numeric",
    "checkbox": "checkbox",
    "timestamp": "timestamp",
    "user": "user",
    "file": "file",
    "reference": "numeric",
    "recordid": "recordid",
}

# Export table role -> canonical dataset (SPEC §5.5). The obligations table becomes scope.csv.
ROLE_DATASET: dict[str, str] = {
    "obligations": "scope",
    "inspections": "inspections",
    "artifacts": "artifacts",
    "approvals": "approvals",
    "approval_items": "approval_items",
}
REQUIRED_ROLES = ("obligations", "inspections", "artifacts", "approvals")
OPTIONAL_ROLES = ("approval_items",)
ROLES = REQUIRED_ROLES + OPTIONAL_ROLES

# The canonical id a reference resolves to, per target role (SPEC §12.3 "reference").
ROLE_KEY_COLUMN: dict[str, str] = {
    "obligations": "obligation_id",
    "inspections": "inspection_id",
    "artifacts": "artifact_id",
    "approvals": "approval_id",
}

# (role, column) -> the role a reference on that link column must target.
REFERENCE_TARGETS: dict[tuple[str, str], str] = {
    ("inspections", "obligation_id"): "obligations",
    ("artifacts", "inspection_id"): "inspections",
    ("approvals", "inspection_id"): "inspections",
    ("approval_items", "approval_id"): "approvals",
    ("approval_items", "artifact_id"): "artifacts",
}

# Canonical column type (vocab.SCHEMAS) -> the mapping types that can feed it.
TYPE_COMPATIBILITY: dict[str, frozenset[str]] = {
    "ID": frozenset({"text", "numeric", "recordid", "reference", "text-multiple-choice"}),
    "REV": frozenset({"text", "numeric", "recordid", "text-multiple-choice"}),
    "KIND": frozenset({"text", "text-multiple-choice"}),
    "ENUM": frozenset({"text", "text-multiple-choice"}),
    "BOOL": frozenset({"checkbox", "text"}),
    "TS": frozenset({"timestamp", "text"}),
    "DIGEST": frozenset({"text"}),
    "TEXT": frozenset({"text", "user", "text-multiple-choice"}),
    "PATH": frozenset({"file"}),
}
AS_REQUIRED: dict[str, str] = {"numeric": "integer_string", "user": "email"}
MAX_FID = 2**31 - 1
_DBID_RE = re.compile(r"[A-Za-z0-9]{1,64}")


@dataclass(frozen=True)
class FieldMap:
    column: str
    fid: int
    type: str
    values: dict[str, str] | None
    as_: str | None
    target: str | None


@dataclass(frozen=True)
class TableMap:
    role: str
    table_id: str
    fields: dict[str, FieldMap]
    allow_derived: bool

    @property
    def dataset(self) -> str:
        return ROLE_DATASET[self.role]

    def select(self) -> list[int]:
        """The fields every query on this table selects: the mapped fields plus 2 and 3 (SPEC §12.3, §12.5)."""
        return sorted({f.fid for f in self.fields.values()} | {2, 3})


@dataclass(frozen=True)
class Mapping:
    mapping_id: str
    version: str
    source: str
    synthetic: bool
    project: dict[str, Any]
    scope_filter: dict[str, Any]
    tables: dict[str, TableMap]
    sha256: str

    def reference(self) -> dict[str, str]:
        """The ``{mapping_id, version, sha256}`` triple written to normalization.json and the run manifest."""
        return {"mapping_id": self.mapping_id, "version": self.version, "sha256": self.sha256}


def _column_type(dataset: str, column: str) -> tuple[str, bool]:
    for name, type_name, required in SCHEMAS[dataset]:
        if name == column:
            return ("ENUM" if type_name.startswith("ENUM:") else type_name), required
    raise KeyError(column)


def _canonical_ok(dataset: str, column: str, value: str) -> bool:
    """Whether a value-map target satisfies the column's grammar or enum."""
    for name, type_name, _ in SCHEMAS[dataset]:
        if name != column:
            continue
        if type_name.startswith("ENUM:"):
            return value in ENUMS[type_name.split(":", 1)[1]]
        checks = {
            "ID": grammar.is_id,
            "REV": grammar.is_rev,
            "KIND": grammar.is_kind,
            "TEXT": grammar.is_text,
            "DIGEST": grammar.is_digest,
        }
        check = checks.get(type_name)
        return bool(check(value)) if check else False
    return False


def _dbid(value: Any, where: str) -> str:
    s = v.string(value, where)
    if _DBID_RE.fullmatch(s) is None:
        raise v.fail(where, f"{s!r} is not a Quickbase table or app id ([A-Za-z0-9]{{1,64}})")
    return s


def _values_map(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise v.fail(where, "expected a non-empty map of source labels to canonical values")
    out: dict[str, str] = {}
    for label, canonical in value.items():
        if not isinstance(label, str):
            raise v.fail(where, f"label {label!r} must be a string (quote this key)")
        if label == "":
            raise v.fail(where, "an empty label cannot be mapped")
        if not isinstance(canonical, str):
            raise v.fail(f"{where}.{label}", "the canonical value must be a string (quote it)")
        out[label] = canonical
    return out


def _field(role: str, column: str, raw: Any, where: str) -> FieldMap:
    body = v.obj(
        raw,
        where,
        {"fid": v.integer(1, MAX_FID), "type": v.enum(tuple(EXPECTED_FIELD_TYPE))},
        {"values": _values_map, "as": v.string, "target": v.string},
    )
    fid: int = body["fid"]
    type_name: str = body["type"]
    dataset = ROLE_DATASET[role]
    column_type, _required = _column_type(dataset, column)

    if type_name not in TYPE_COMPATIBILITY[column_type]:
        allowed = ", ".join(sorted(TYPE_COMPATIBILITY[column_type]))
        raise v.fail(
            where, f"type {type_name!r} cannot feed column {column} ({column_type}); use one of {allowed}"
        )
    if type_name == "file" and column != "relative_path":
        raise v.fail(where, "type 'file' maps only to relative_path")
    if fid == 3 and type_name != "recordid":
        raise v.fail(where, "field 3 (Record ID#) may be mapped only with type: recordid")
    if type_name == "recordid" and fid != 3:
        raise v.fail(where, "type: recordid is field 3 (Record ID#)")
    if fid == 2 and type_name != "timestamp":
        raise v.fail(where, "field 2 (Date Modified) may be mapped only with type: timestamp")

    as_value = body.get("as")
    expected_as = AS_REQUIRED.get(type_name)
    if expected_as is not None and as_value != expected_as:
        raise v.fail(where, f"type {type_name!r} requires as: {expected_as}")
    if expected_as is None and as_value is not None:
        raise v.fail(where, f"'as' is not allowed with type {type_name!r}")

    target = body.get("target")
    if type_name == "reference":
        needed = REFERENCE_TARGETS.get((role, column))
        if needed is None:
            raise v.fail(where, f"{role}.{column} is not a link column, so it cannot be a reference")
        if target != needed:
            raise v.fail(where, f"a reference on {role}.{column} must have target: {needed}")
    elif target is not None:
        raise v.fail(where, "'target' is allowed only with type: reference")

    values = body.get("values")
    if type_name == "text-multiple-choice":
        if values is None and column_type in ("KIND", "ENUM"):
            raise v.fail(
                where, f"a multiple-choice field feeding {column} ({column_type}) needs a values map"
            )
        for label, canonical in (values or {}).items():
            if not _canonical_ok(dataset, column, canonical):
                raise v.fail(f"{where}.values.{label}", f"{canonical!r} is not a valid {column} value")
    elif values is not None:
        raise v.fail(where, "'values' is allowed only with type: text-multiple-choice")

    return FieldMap(column=column, fid=fid, type=type_name, values=values, as_=as_value, target=target)


def _table(role: str, raw: Any, where: str) -> TableMap:
    body = v.obj(
        raw,
        where,
        {"table_id": _dbid, "fields": v.anything},
        {"allow_derived": v.boolean},
    )
    fields_raw = body["fields"]
    if not isinstance(fields_raw, dict):
        raise v.fail(f"{where}.fields", "expected an object of canonical columns")
    dataset = ROLE_DATASET[role]
    schema_columns = [name for name, _, _ in SCHEMAS[dataset]]
    fields: dict[str, FieldMap] = {}
    used_fids: dict[int, str] = {}
    for column, spec in fields_raw.items():
        if not isinstance(column, str):
            raise v.fail(f"{where}.fields", f"column name {column!r} must be a string (quote it)")
        if column not in schema_columns:
            raise v.fail(f"{where}.fields", f"unknown column {column!r} for {dataset} (SPEC §5.5)")
        fm = _field(role, column, spec, f"{where}.fields.{column}")
        if fm.fid in used_fids:
            raise v.fail(
                f"{where}.fields.{column}", f"field {fm.fid} is already mapped to {used_fids[fm.fid]}"
            )
        used_fids[fm.fid] = column
        fields[column] = fm
    for name, _, required in SCHEMAS[dataset]:
        if required and name not in fields:
            raise v.fail(f"{where}.fields", f"required column {name!r} is not mapped")
    return TableMap(
        role=role, table_id=body["table_id"], fields=fields, allow_derived=body.get("allow_derived", False)
    )


def _tables(value: Any, where: str) -> dict[str, TableMap]:
    if not isinstance(value, dict):
        raise v.fail(where, "expected an object")
    for key in value:
        if key not in ROLES:
            raise v.fail(where, f"unknown table role {key!r}; expected {', '.join(ROLES)}")
    for role in REQUIRED_ROLES:
        if role not in value:
            raise v.fail(where, f"missing table {role!r}")
    tables = {role: _table(role, value[role], f"{where}.{role}") for role in ROLES if role in value}
    seen: dict[str, str] = {}
    for role, table in tables.items():
        if table.table_id in seen:
            raise v.fail(
                f"{where}.{role}", f"table id {table.table_id} is also mapped as {seen[table.table_id]}"
            )
        seen[table.table_id] = role
    return tables


def parse_mapping(raw: Any, source: str) -> Mapping:
    """Validate a parsed mapping document (closed world). Every defect is RunError("CONFIG_INVALID", …)."""
    body = v.obj(
        raw,
        source,
        {
            "schema": v.exact(MAPPING_SCHEMA),
            "mapping_id": v.ident,
            "version": v.string,
            "source": v.exact("quickbase"),
            "synthetic": v.boolean,
            "project": lambda x, w: v.obj(
                x, w, {"project_id": v.ident, "client_id": v.ident, "name": v.text, "synthetic": v.boolean}
            ),
            "scope_filter": lambda x, w: v.obj(x, w, {"fid": v.integer(1, MAX_FID), "value": v.ident}),
            "tables": _tables,
        },
    )
    tables: dict[str, TableMap] = body["tables"]
    scope_filter: dict[str, Any] = body["scope_filter"]
    project_field = tables["obligations"].fields["project_id"]
    if scope_filter["fid"] != project_field.fid:
        raise v.fail(
            f"{source}.scope_filter",
            f"fid {scope_filter['fid']} must be the obligations table's project_id field ({project_field.fid})",
        )
    if scope_filter["value"] != body["project"]["project_id"]:
        raise v.fail(f"{source}.scope_filter", "value must equal project.project_id")
    try:
        sha = digest_of(raw)
    except CanonicalError as exc:  # e.g. a float somewhere in the document
        raise RunError("CONFIG_INVALID", f"{source}: {exc}") from exc
    return Mapping(
        mapping_id=body["mapping_id"],
        version=body["version"],
        source=body["source"],
        synthetic=body["synthetic"],
        project=body["project"],
        scope_filter=scope_filter,
        tables=tables,
        sha256=sha,
    )


def load_mapping(path: Path) -> Mapping:
    return parse_mapping(load_yaml(path), path.name)


def _mismatch(message: str) -> RunError:
    return RunError("QB_SCHEMA_MISMATCH", message)


def verify_fields(mapping: Mapping, role: str, fields: list[dict[str, Any]]) -> None:
    """Check a table's ``GET /fields`` list against the mapping (SPEC §12.3 "Types", §12.5 step 1).

    Every mapped field must exist with the expected ``fieldType`` and a blank ``mode`` (unless the table
    declares ``allow_derived``); field 3 must exist; field 2 must exist with ``fieldType`` ``timestamp``.
    A difference raises RunError("QB_SCHEMA_MISMATCH") naming both values.
    """
    table = mapping.tables.get(role)
    if table is None:
        raise _mismatch(f"the mapping has no table {role!r}")
    where = f"table {role} ({table.table_id})"
    if not isinstance(fields, list):
        raise _mismatch(f"{where}: the field list is not an array")
    by_id: dict[int, dict[str, Any]] = {}
    for item in fields:
        fid = item.get("id") if isinstance(item, dict) else None
        if isinstance(fid, bool) or not isinstance(fid, int):
            raise _mismatch(f"{where}: a field entry has no integer id: {item!r}")
        by_id[fid] = item
    if 3 not in by_id:
        raise _mismatch(f"{where}: field 3 (Record ID#) does not exist")
    date_modified = by_id.get(2)
    if date_modified is None:
        raise _mismatch(f"{where}: field 2 (Date Modified) does not exist")
    if date_modified.get("fieldType") != "timestamp":
        raise _mismatch(
            f"{where}: field 2 has fieldType {date_modified.get('fieldType')!r}, expected 'timestamp'"
        )
    for column, fm in sorted(table.fields.items(), key=lambda kv: kv[1].fid):
        field = by_id.get(fm.fid)
        if field is None:
            raise _mismatch(f"{where}: field {fm.fid} (mapped to {column}) does not exist")
        expected = EXPECTED_FIELD_TYPE[fm.type]
        actual = field.get("fieldType")
        if actual != expected:
            raise _mismatch(
                f"{where}: field {fm.fid} ({column}) has fieldType {actual!r}, but the mapping type "
                f"{fm.type!r} expects {expected!r}"
            )
        mode = field.get("mode")
        if mode not in (None, "") and not table.allow_derived:
            raise _mismatch(
                f"{where}: field {fm.fid} ({column}) has mode {mode!r}; only data-entry fields (blank mode) may "
                "be mapped unless the table declares allow_derived: true"
            )
