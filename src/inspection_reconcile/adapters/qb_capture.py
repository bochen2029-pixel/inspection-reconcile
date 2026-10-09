"""Live, read-only capture from Quickbase (SPEC §12.5) into the export format of SPEC §12.2.

The capture verifies every mapped table's schema, reads every table in full with keyset pagination on
Record ID# and per-page total accounting, downloads the in-scope current artifacts' files, and only then
re-reads every table (fields 2 and 3) to detect any change made while the capture ran. The coverage it
declares is earned, never assumed: ``complete_for_declared_scope`` needs a complete read, a stable second
pass and the operator's attestation. Nothing the client is given (the token) is ever written or logged.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inspection_reconcile import grammar
from inspection_reconcile import validate as v
from inspection_reconcile.adapters.mapping import Mapping, TableMap, verify_fields
from inspection_reconcile.adapters.qb_client import (
    LOGGER_NAME,
    FileTooLarge,
    QuickbaseClient,
    QuickbaseHTTPError,
)
from inspection_reconcile.adapters.qb_export import (
    CAPTURE_MANIFEST,
    EXPORT_FORMAT,
    SourceRecord,
    convert_records,
    file_relative_path,
    latest_version,
    parse_export_manifest,
    records_from_pages,
)
from inspection_reconcile.canonical import sha256_hex
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.writer import json_bytes, write_files
from inspection_reconcile.yamlsafe import load_yaml

CAPTURE_SCHEMA = "inspection-reconcile/qb-capture/v1"
COMPLETE_BASIS = ["query_total_matched", "two_pass_stable", "operator_attestation"]
DOWNLOAD_FAILURES = ("QB_HTTP_ERROR", "QB_PERMISSION", "QB_RETRIES_EXHAUSTED", "QB_PROTOCOL")

log = logging.getLogger(LOGGER_NAME)

_HOSTNAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_DBID = re.compile(r"[A-Za-z0-9]{1,64}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# A token_env value a message may show. A user token is lower case, and pasting the token where its variable's name
# belongs is the likely mistake, so any other value is never echoed (I-7).
_SHOWN_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")


# ----------------------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class CaptureConfig:
    realm_hostname: str
    app_id: str
    token_env: str
    page_size: int
    capture_files: bool
    scope_revision: str
    scope_accepted: dict[str, str] | None
    max_file_bytes: int
    requests_per_10s: int
    max_attempts: int
    max_retry_wait_s: int
    max_pages: int
    full_read_access: bool
    attested_by: str
    attestation_note: str


def _pattern(regex: re.Pattern[str], label: str) -> v.Check:
    def check(value: Any, where: str) -> str:
        s = v.string(value, where)
        if regex.fullmatch(s) is None:
            raise v.fail(where, f"{s!r} is not a valid {label}")
        return s

    return check


def _env_name(value: Any, where: str) -> str:
    s = v.string(value, where)
    if _ENV_NAME.fullmatch(s) is None:
        raise v.fail(
            where,
            "is not a valid environment variable name (the value is not shown, in case it is the token)",
        )
    return s


def _env_label(name: str) -> str:
    """``name`` for a message, unless it could be the token itself."""
    if _SHOWN_ENV_NAME.fullmatch(name):
        return name
    return "named by token_env (not shown: it is not an upper-case name, so it may be the token itself)"


def _ts_string(value: Any, where: str) -> str:
    s = v.string(value, where)
    if grammar.parse_ts(s) is None:
        raise v.fail(where, f"{s!r} is not an RFC 3339 timestamp with an offset")
    return s


def parse_capture_config(raw: Any, source: str) -> CaptureConfig:
    """Validate ``qb-capture.yml`` (SPEC §12.5), closed world; every defect is RunError("CONFIG_INVALID")."""
    body = v.obj(
        raw,
        source,
        {
            "schema": v.exact(CAPTURE_SCHEMA),
            "realm_hostname": _pattern(_HOSTNAME, "host name"),
            "app_id": _pattern(_DBID, "Quickbase app id"),
            "token_env": _env_name,
            "page_size": v.integer(1, 10_000),
            "capture_files": v.boolean,
            "scope": lambda x, w: v.obj(
                x,
                w,
                {
                    "scope_revision": v.rev,
                    "accepted": v.nullable(
                        lambda y, u: v.obj(y, u, {"by": v.text, "at": _ts_string, "reference": v.text})
                    ),
                },
            ),
            "limits": lambda x, w: v.obj(
                x,
                w,
                {
                    "max_file_bytes": v.integer(1, 2**31 - 1),
                    "requests_per_10s": v.integer(1, 100),
                    "max_attempts": v.integer(1, 20),
                    "max_retry_wait_s": v.integer(0, 3600),
                    "max_pages": v.integer(1, 1_000_000),
                },
            ),
            "operator_attestation": lambda x, w: v.obj(
                x, w, {"full_read_access": v.boolean, "attested_by": v.text, "note": v.text}
            ),
        },
    )
    limits = body["limits"]
    attestation = body["operator_attestation"]
    return CaptureConfig(
        realm_hostname=body["realm_hostname"],
        app_id=body["app_id"],
        token_env=body["token_env"],
        page_size=body["page_size"],
        capture_files=body["capture_files"],
        scope_revision=body["scope"]["scope_revision"],
        scope_accepted=body["scope"]["accepted"],
        max_file_bytes=limits["max_file_bytes"],
        requests_per_10s=limits["requests_per_10s"],
        max_attempts=limits["max_attempts"],
        max_retry_wait_s=limits["max_retry_wait_s"],
        max_pages=limits["max_pages"],
        full_read_access=attestation["full_read_access"],
        attested_by=attestation["attested_by"],
        attestation_note=attestation["note"],
    )


def load_capture_config(path: Path) -> CaptureConfig:
    return parse_capture_config(load_yaml(path), path.name)


def make_client(
    config: CaptureConfig,
    *,
    user_agent: str,
    environ: MappingABC[str, str] | None = None,
    **client_kwargs: Any,
) -> QuickbaseClient:
    """A client for ``config``. The token comes only from the environment variable the config names."""
    env = os.environ if environ is None else environ
    token = env.get(config.token_env, "")
    if not token:
        raise RunError(
            "QB_TOKEN_MISSING", f"the environment variable {_env_label(config.token_env)} is not set or empty"
        )
    return QuickbaseClient(
        config.realm_hostname,
        token,
        user_agent=user_agent,
        requests_per_10s=config.requests_per_10s,
        max_attempts=config.max_attempts,
        max_retry_wait_s=float(config.max_retry_wait_s),
        **client_kwargs,
    )


# ------------------------------------------------------------------------------------------- table reads


@dataclass
class TableRead:
    """The result of one keyset-paginated read of a table (SPEC §12.5 step 2)."""

    paging: str = "keyset"
    pages: list[dict[str, Any]] = field(default_factory=list)
    total0: int | None = None
    retrieved: int = 0
    accounting_ok: bool = True
    ended_on_empty_page: bool = False
    modified: dict[int, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.ended_on_empty_page and self.accounting_ok and self.retrieved == (self.total0 or 0)


def scope_filter_where(mapping: Mapping) -> str:
    sf = mapping.scope_filter
    return "{" + f"{sf['fid']}.EX.'{sf['value']}'" + "}"


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _KeysetRefused(Exception):
    """The source rejected the ``{3.GT.n}`` comparison on a table's first page (an unverified fact, §12.1)."""


def _check_page(
    read: TableRead, page: dict[str, Any], select: list[int], label: str
) -> tuple[int, list[Any], list[int]]:
    """Checks shared by both paging modes: every selected field returned, integer metadata, record ids.
    Returns the page's ``totalRecords``, its records and their Record ID#s."""
    read.pages.append(page)
    returned = {f.get("id") for f in page["fields"] if isinstance(f, dict)}
    missing = [fid for fid in select if fid not in returned]
    if missing:
        raise RunError(
            "FIELD_NOT_RETURNED",
            f"{label}: the query did not return field(s) {missing}; check the token's permissions and the schema",
        )
    meta = page["metadata"]
    total = _int(meta.get("totalRecords"))
    num = _int(meta.get("numRecords"))
    if total is None or num is None:
        raise RunError("QB_PROTOCOL", f"{label}: metadata.totalRecords and numRecords must be integers")
    if read.total0 is None:
        read.total0 = total
    data: list[Any] = page["data"]
    if num != len(data):
        read.accounting_ok = False
    rids: list[int] = []
    for record in data:
        cell = record.get("3") if isinstance(record, dict) else None
        rid = _int(cell.get("value")) if isinstance(cell, dict) else None
        if rid is None:
            raise RunError("QB_PROTOCOL", f"{label}: a record has no integer Record ID# (field 3)")
        rids.append(rid)
        modified = record.get("2")
        read.modified[rid] = modified.get("value") if isinstance(modified, dict) else None
    return total, data, rids


def _ascending(rids: list[int], floor: int) -> bool:
    return bool(rids) and rids[0] > floor and all(b > a for a, b in zip(rids, rids[1:], strict=False))


def _read_keyset(
    client: QuickbaseClient,
    table: TableMap,
    select: list[int],
    base: str | None,
    page_size: int,
    max_pages: int,
    allow_fallback: bool,
) -> TableRead:
    label = f"table {table.role} ({table.table_id})"
    read = TableRead(paging="keyset")
    last = 0
    for page_number in range(max_pages):
        where = (f"{base}AND" if base else "") + "{3.GT." + str(last) + "}"
        body = {
            "from": table.table_id,
            "select": select,
            "where": where,
            "sortBy": [{"fieldId": 3, "order": "ASC"}],
            "options": {"skip": 0, "top": page_size},
        }
        try:
            page = client.run_query(body)
        except QuickbaseHTTPError as exc:
            if exc.status == 400 and page_number == 0 and allow_fallback:
                raise _KeysetRefused() from exc
            raise
        total, data, rids = _check_page(read, page, select, label)
        if total != (read.total0 or 0) - read.retrieved:
            read.accounting_ok = False
            log.warning(
                "%s: page total %d != %d expected; the table changed",
                label,
                total,
                (read.total0 or 0) - read.retrieved,
            )
        if not data:
            read.ended_on_empty_page = True
            return read
        if not _ascending(rids, last):
            read.accounting_ok = False
        read.retrieved += len(data)
        if max(rids) <= last:
            read.accounting_ok = False  # no progress: stop rather than loop
            return read
        last = max(rids)
    raise RunError(
        "QB_MAX_PAGES", f"{label}: more than {max_pages} pages; raise limits.max_pages deliberately"
    )


def _read_skip(
    client: QuickbaseClient,
    table: TableMap,
    select: list[int],
    base: str | None,
    page_size: int,
    max_pages: int,
) -> TableRead:
    """SPEC §12.1 fallback: ``skip`` paging, sorted by Record ID#, with per-page total accounting. Every page
    must report the same total, and record ids must be unique and ascending across pages."""
    label = f"table {table.role} ({table.table_id})"
    read = TableRead(paging="skip")
    seen: set[int] = set()
    floor = 0
    for _ in range(max_pages):
        body: dict[str, Any] = {
            "from": table.table_id,
            "select": select,
            "sortBy": [{"fieldId": 3, "order": "ASC"}],
            "options": {"skip": read.retrieved, "top": page_size},
        }
        if base:
            body["where"] = base
        page = client.run_query(body)
        total, data, rids = _check_page(read, page, select, label)
        if total != read.total0:
            read.accounting_ok = False
            log.warning("%s: page total %d != first total %s; the table changed", label, total, read.total0)
        if not data:
            read.ended_on_empty_page = True
            return read
        if not _ascending(rids, floor) or seen.intersection(rids):
            read.accounting_ok = False
        read.retrieved += len(data)
        if max(rids) <= floor:
            read.accounting_ok = (
                False  # no new record ids (a source ignoring skip): stop rather than loop (C-5)
            )
            return read
        seen.update(rids)
        floor = max(rids)
    raise RunError(
        "QB_MAX_PAGES", f"{label}: more than {max_pages} pages; raise limits.max_pages deliberately"
    )


def read_table(
    client: QuickbaseClient,
    table: TableMap,
    select: list[int],
    base: str | None,
    page_size: int,
    max_pages: int,
    *,
    paging: str = "auto",
) -> TableRead:
    """Read a whole table (SPEC §12.5 step 2): keyset pagination on Record ID#, or — when the source refuses
    the ``{3.GT.n}`` comparison on the first page — ``skip`` paging (SPEC §12.1). ``paging`` forces a mode,
    so that pass 2 uses the mode pass 1 used."""
    if paging not in ("auto", "keyset", "skip"):
        raise ValueError(f"unknown paging mode {paging!r}")
    if paging != "skip":
        try:
            return _read_keyset(
                client, table, select, base, page_size, max_pages, allow_fallback=paging == "auto"
            )
        except _KeysetRefused:
            log.warning(
                "table %s (%s): {3.GT.n} was refused; falling back to skip paging", table.role, table.table_id
            )
    return _read_skip(client, table, select, base, page_size, max_pages)


# ----------------------------------------------------------------------------------------------- capture


def _declaration(complete_read: bool, stable: bool, attested: bool) -> dict[str, Any]:
    consistency = "stable_verified" if stable else "changed_during_capture"
    if complete_read and stable and attested:
        return {
            "coverage": "complete_for_declared_scope",
            "basis": list(COMPLETE_BASIS),
            "consistency": consistency,
        }
    if not complete_read:
        return {"coverage": "partial", "basis": ["pagination_incomplete"], "consistency": consistency}
    earned = ["query_total_matched"]
    if stable:
        earned.append("two_pass_stable")
    if attested:
        earned.append("operator_attestation")
    return {"coverage": "unverified", "basis": earned, "consistency": consistency}


def _in_scope_artifact_rids(
    rows: dict[str, list[tuple[dict[str, str | None], int]]],
) -> set[int]:
    """Current artifact rows whose inspection entity links to a captured obligation (SPEC §12.5 step 3)."""
    obligations = {cells["obligation_id"] for cells, _ in rows.get("scope", []) if cells.get("obligation_id")}
    linked: dict[str, set[str]] = {}
    for cells, _ in rows.get("inspections", []):
        iid, oid = cells.get("inspection_id"), cells.get("obligation_id")
        if iid is not None and oid is not None:
            linked.setdefault(iid, set()).add(oid)
    out: set[int] = set()
    for cells, rid in rows.get("artifacts", []):
        iid = cells.get("inspection_id")
        if cells.get("is_current") == "true" and iid is not None and linked.get(iid, set()) & obligations:
            out.add(rid)
    return out


def _format(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return grammar.format_ts(moment)


def capture(
    client: QuickbaseClient,
    config: CaptureConfig,
    mapping: Mapping,
    out_dir: Path,
    *,
    now: Callable[[], datetime],
) -> dict[str, Any]:
    """Capture every mapped table into ``out_dir`` (absent or empty) and return the capture manifest."""
    out_dir = Path(out_dir)
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise RunError("OUT_NOT_EMPTY", f"{out_dir}: the capture output directory must be absent or empty")
    started = now()
    written: list[Path] = []

    def emit(name: str, data: bytes) -> None:
        written.extend(write_files(out_dir, {name: data}))

    try:
        # 1. schema verification (C2)
        for role, table in mapping.tables.items():
            fields = client.get_fields(table.table_id)
            verify_fields(mapping, role, fields)
            emit(f"tables/{role}/fields.json", json_bytes(fields))
            log.info("verified the schema of %s (%s)", role, table.table_id)

        # 2. pass 1: full-table keyset reads; the obligations table carries the scope filter
        base_where = {
            role: (scope_filter_where(mapping) if role == "obligations" else None) for role in mapping.tables
        }
        reads: dict[str, TableRead] = {}
        for role, table in mapping.tables.items():
            read = read_table(
                client, table, table.select(), base_where[role], config.page_size, config.max_pages
            )
            for number, page in enumerate(read.pages, start=1):
                emit(f"tables/{role}/page-{number:04d}.json", json_bytes(page))
            reads[role] = read
            log.info(
                "read %s: %d of %s record(s), %d page(s)", role, read.retrieved, read.total0, len(read.pages)
            )

        # 3. files of the in-scope current artifacts
        records: dict[str, list[SourceRecord]] = {
            role: records_from_pages(table, reads[role].pages, f"table {role}")
            for role, table in mapping.tables.items()
        }
        rows, _ = convert_records(mapping, records)
        files = _capture_files(client, config, mapping, records, rows, emit)

        # 4. pass 2, only after every table's pass 1: fields 2 and 3 again, compared with pass 1
        two_pass: dict[str, str] = {}
        for role, table in mapping.tables.items():
            second = read_table(
                client,
                table,
                [2, 3],
                base_where[role],
                config.page_size,
                config.max_pages,
                paging=reads[role].paging,
            )
            emit(
                f"tables/{role}/pass2.json",
                json_bytes({str(rid): second.modified[rid] for rid in sorted(second.modified)}),
            )
            stable = reads[role].accounting_ok and second.complete and second.modified == reads[role].modified
            two_pass[role] = "stable" if stable else "changed"
            if not stable:
                log.warning("table %s (%s) changed during the capture", role, table.table_id)

        # 5. expected work must be reliable
        if not (reads["obligations"].complete and two_pass["obligations"] == "stable"):
            raise RunError(
                "QB_SCOPE_UNSTABLE",
                "the obligations table was not read completely and stably; expected work must be reliable",
            )

        # 6. coverage, earned per dataset (approval items share the approvals declaration)
        attested = config.full_read_access
        datasets: dict[str, Any] = {}
        for name, roles in (
            ("inspections", ["inspections"]),
            ("artifacts", ["artifacts"]),
            ("approvals", ["approvals", "approval_items"]),
        ):
            present = [r for r in roles if r in mapping.tables]
            datasets[name] = _declaration(
                all(reads[r].complete for r in present),
                all(two_pass[r] == "stable" for r in present),
                attested,
            )
        datasets["evidence_files"] = _evidence_declaration(datasets["artifacts"], config, files)

        # 7. the manifest, last: its presence marks a finished export
        ended = now()
        manifest: dict[str, Any] = {
            "format": EXPORT_FORMAT,
            "export_id": f"{mapping.project['project_id']}-qb-{_compact(started)}",
            "synthetic": mapping.synthetic,
            "source": {
                "system": "quickbase",
                "realm_hostname": config.realm_hostname,
                "app_id": config.app_id,
                "description": f"Read-only capture of Quickbase app {config.app_id}",
            },
            "capture": {"started_at": _format(started), "ended_at": _format(ended)},
            "scope": {"scope_revision": config.scope_revision, "accepted": config.scope_accepted},
            "tables": {
                role: {
                    "table_id": table.table_id,
                    "select": table.select(),
                    "where": base_where[role],
                    "pages": len(reads[role].pages),
                    "total_records": reads[role].total0 or 0,
                    "retrieved": reads[role].retrieved,
                    "two_pass": two_pass[role],
                    "paging": reads[role].paging,
                }
                for role, table in mapping.tables.items()
            },
            "datasets": datasets,
            "files": files,
        }
        parse_export_manifest(manifest)  # self-check: normalize must accept what capture writes
        emit(CAPTURE_MANIFEST, json_bytes(manifest))
        return manifest
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def _compact(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _capture_files(
    client: QuickbaseClient,
    config: CaptureConfig,
    mapping: Mapping,
    records: dict[str, list[SourceRecord]],
    rows: dict[str, list[tuple[dict[str, str | None], int]]],
    emit: Callable[[str, bytes], None],
) -> list[dict[str, Any]]:
    artifacts = mapping.tables["artifacts"]
    file_field = artifacts.fields.get("relative_path")
    if file_field is None:
        return []
    in_scope = _in_scope_artifact_rids(rows)
    entries: list[dict[str, Any]] = []
    for rec in sorted(records["artifacts"], key=lambda r: r.rid):
        latest = latest_version(rec.values.get(file_field.fid))
        if latest is None:
            continue
        version, name = latest
        entry: dict[str, Any] = {
            "table_id": artifacts.table_id,
            "record_id": rec.rid,
            "field_id": file_field.fid,
            "version": version,
            "file_name": name,
            "path": None,
            "bytes": None,
            "sha256": None,
            "status": "out_of_scope",
        }
        if rec.rid in in_scope:
            entry["status"] = "not_captured"
            if config.capture_files:
                _download_into(
                    client, config, artifacts.table_id, rec.rid, file_field.fid, version, name, entry, emit
                )
        entries.append(entry)
    return entries


def _download_into(
    client: QuickbaseClient,
    config: CaptureConfig,
    table_id: str,
    rid: int,
    fid: int,
    version: int,
    name: str,
    entry: dict[str, Any],
    emit: Callable[[str, bytes], None],
) -> None:
    try:
        content, _ = client.download_file(table_id, rid, fid, version, max_bytes=config.max_file_bytes)
    except FileTooLarge as exc:
        # The size is recorded only when the whole body was read; a download stopped at the limit leaves it null.
        log.warning("file %s/%d/%d/v%d is over max_file_bytes; not captured", table_id, rid, fid, version)
        entry["status"] = "too_large"
        entry["bytes"] = exc.size
        return
    except RunError as exc:
        if exc.code not in DOWNLOAD_FAILURES:
            raise
        log.warning("file %s/%d/%d/v%d was not captured: %s", table_id, rid, fid, version, exc.code)
        entry["status"] = "error"
        return
    path = file_relative_path(table_id, rid, fid, version, name)
    emit(path, content)
    entry.update(path=path, bytes=len(content), sha256=sha256_hex(content), status="captured")


def _evidence_declaration(
    artifacts_decl: dict[str, Any], config: CaptureConfig, files: list[dict[str, Any]]
) -> dict[str, Any]:
    if artifacts_decl["coverage"] != "complete_for_declared_scope":
        return dict(artifacts_decl, basis=list(artifacts_decl["basis"]))
    consistency = artifacts_decl["consistency"]
    if not config.capture_files:
        return {"coverage": "partial", "basis": ["attachment_capture_skipped"], "consistency": consistency}
    if any(f["status"] not in ("captured", "out_of_scope") for f in files):
        return {"coverage": "partial", "basis": ["extraction_interrupted"], "consistency": consistency}
    return dict(artifacts_decl, basis=list(artifacts_decl["basis"]))


__all__ = [
    "CAPTURE_SCHEMA",
    "CaptureConfig",
    "TableRead",
    "capture",
    "load_capture_config",
    "make_client",
    "parse_capture_config",
    "read_table",
    "scope_filter_where",
]
