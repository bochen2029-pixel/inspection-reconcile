"""SQLite export for the independent SQL cross-check (SPEC Appendix G, step A6).

``export_sqlite`` writes the snapshot's **step-1-valid rows** -- rows that are not malformed and have no
§7.5.1 cell violation -- together with the pack's requirements. Rows with duplicate keys are deliberately
included, because ``sql/r0_duplicate_keys.sql`` must find them on its own. The queries in ``sql/`` re-derive R2,
R3, R4, R7 and duplicate keys from these tables, and ``tests/sql`` compares them with the engine.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from inspection_reconcile.engine.integrity import validate_row
from inspection_reconcile.engine.model import Row
from inspection_reconcile.errors import RunError
from inspection_reconcile.grammar import CellValue
from inspection_reconcile.io.snapshot import LoadedSnapshot, RawRow
from inspection_reconcile.policy import Policy
from inspection_reconcile.vocab import SCHEMAS

# Appendix G, verbatim.
SCHEMA_SQL = """\
CREATE TABLE obligations    (obligation_id TEXT, project_id TEXT, scope_revision TEXT, asset_id TEXT, activity_kind TEXT);
CREATE TABLE inspections    (inspection_id TEXT, revision TEXT, is_current INTEGER, obligation_id TEXT, project_id TEXT,
                             asset_id TEXT, activity_kind TEXT, completion_status TEXT, completed_at TEXT);
CREATE TABLE artifacts      (artifact_id TEXT, revision TEXT, is_current INTEGER, inspection_id TEXT, project_id TEXT,
                             asset_id TEXT, document_kind TEXT, relative_path TEXT);
CREATE TABLE approvals      (approval_id TEXT, inspection_id TEXT, inspection_revision TEXT, evidence_digest TEXT,
                             decision TEXT, decided_at TEXT, decided_by TEXT);
CREATE TABLE approval_items (approval_id TEXT, artifact_id TEXT, artifact_revision TEXT);
CREATE TABLE required_kinds (activity_kind TEXT, document_kind TEXT);
"""

# Each dataset's table; the table's columns are the dataset's schema columns (SPEC §5.5), in schema order.
TABLE_FOR_DATASET: dict[str, str] = {
    "scope": "obligations",
    "inspections": "inspections",
    "artifacts": "artifacts",
    "approvals": "approvals",
    "approval_items": "approval_items",
}


def _raw_rows(snapshot: LoadedSnapshot, dataset: str) -> tuple[RawRow, ...]:
    if dataset == "scope":
        if snapshot.scope is None or snapshot.scope.rows is None:
            return ()
        return snapshot.scope.rows
    return snapshot.rows[dataset]


def step1_valid_rows(snapshot: LoadedSnapshot) -> dict[str, list[Row]]:
    """Per dataset, the rows that pass §7.5.1: not malformed and without any cell violation. Duplicates stay."""
    unmapped = snapshot.normalization.unmapped if snapshot.normalization is not None else frozenset()
    out: dict[str, list[Row]] = {}
    for dataset in TABLE_FOR_DATASET:
        rows = [validate_row(raw, unmapped) for raw in _raw_rows(snapshot, dataset)]
        out[dataset] = [row for row in rows if not row.malformed and not row.violations]
    return out


def required_kinds(policy: Policy) -> list[tuple[str, str]]:
    """The pack's requirements as (activity_kind, document_kind) pairs, sorted."""
    return sorted((activity, kind) for activity, kinds in policy.requirements.items() for kind in kinds)


def _sql_value(value: CellValue) -> str | int | None:
    """Booleans as 0/1; strings (timestamps are already canonical UTC) and nulls unchanged."""
    if isinstance(value, bool):
        return int(value)
    return value


def _write_database(path: Path, rows: dict[str, list[Row]], kinds: list[tuple[str, str]]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_SQL)
        for dataset, table in TABLE_FOR_DATASET.items():
            columns = [column for column, _, _ in SCHEMAS[dataset]]
            statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
            connection.executemany(
                statement, ([_sql_value(row.values.get(c)) for c in columns] for row in rows[dataset])
            )
        connection.executemany(
            "INSERT INTO required_kinds (activity_kind, document_kind) VALUES (?, ?)", kinds
        )
        connection.commit()
    finally:
        connection.close()  # an open handle would block os.replace on Windows


def _discard(tmp: Path) -> None:
    for leftover in (tmp, tmp.with_name(f"{tmp.name}-journal")):
        leftover.unlink(missing_ok=True)


def export_sqlite(snapshot: LoadedSnapshot, policy: Policy, out_file: Path) -> None:
    """Write the Appendix G database to ``out_file`` atomically: a temporary file in the same directory, then
    ``os.replace``. On any failure the temporary file is removed and an existing ``out_file`` is left untouched."""
    out_file = Path(out_file)
    rows = step1_valid_rows(snapshot)
    kinds = required_kinds(policy)
    tmp = out_file.with_name(f"{out_file.name}.tmp.{os.getpid()}")
    try:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        _discard(tmp)  # a stale file would make CREATE TABLE fail
        _write_database(tmp, rows, kinds)
        os.replace(tmp, out_file)
    except BaseException as exc:
        _discard(tmp)
        if isinstance(exc, OSError | sqlite3.Error):
            raise RunError("WRITE_FAILED", f"{out_file}: {exc}") from exc
        raise
