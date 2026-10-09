"""Build the SPEC Appendix D test app in your own Quickbase realm through the JSON API (step C3).

    uv run python tools/qb_build_test_app.py --realm HOST [--token-file PATH | --token-env NAME] [--yes]

Without --yes it prints the plan and sends nothing. With --yes it creates ONE new app:
- the four Appendix D tables, with their fields and relationships created in the order that reproduces the demo
  field ids;
- every S01 record, with its 80 evidence files.
It then writes local/quickbase-live.yml (the real table and field ids) and local/qb-capture.yml. After that, step C3
is just capture-quickbase, assess and compare.

This is the only code in the repository that writes to Quickbase, and it only creates:
- six operations are allowed, checked before any I/O: createApp, createTable, createField, createRelationship,
  upsert and getFields. Nothing can update or delete;
- every app, table, field and record id it sends came from a response in this same run, so it writes only into the
  app it has just created;
- a failure after createApp stops the build and names the app, for you to delete by hand. It never deletes.

The package never imports this file, so the read-only client and its allowlist (I-7) are unchanged. Request shapes
follow Quickbase's OpenAPI document (operationIds cited below) and its "Field type details" page.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import logging
import math
import os
import re
import sys
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from inspection_reconcile.adapters.mapping import (
    REQUIRED_ROLES,
    ROLE_DATASET,
    ROLE_KEY_COLUMN,
    load_mapping,
    parse_mapping,
    verify_fields,
)
from inspection_reconcile.adapters.mapping import Mapping as FieldMapping
from inspection_reconcile.adapters.qb_capture import parse_capture_config
from inspection_reconcile.adapters.qb_client import BASE_URL, TokenRedactionFilter, redact
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.writer import write_files
from inspection_reconcile.yamlsafe import loads_yaml

REPO = Path(__file__).resolve().parents[1]
DEMO_MAPPING = REPO / "mappings" / "quickbase-demo.yml"
USER_AGENT = "inspection-reconcile-builder/0.1.0"
LOGGER_NAME = "inspection_reconcile.builder"
WINDOW_SECONDS = 10.0
BACKOFF_CAP_SECONDS = 30.0
MAX_UPSERT_RECORDS = 500
MAX_UPSERT_BYTES = 10 * 1024 * 1024  # upsert documents a 40 MB payload limit; stay well below it
RECORD_OWNER_FID = 4  # built into every table: a user field, the user who created the record

log = logging.getLogger(LOGGER_NAME)

# operationId -> (method, path template, the exact query parameter names). Nothing else can be sent: there is no
# update and no delete. Shapes from Quickbase's OpenAPI document:
#   createApp           POST /apps                            {name*, description, assignToken, ...}
#   createTable         POST /tables?appId=                   {name*, singleRecordName, pluralRecordName, description}
#   createField         POST /fields?tableId=                 {label*, fieldType*, properties{choices, ...}, addToForms,
#                                                              ...}; closed (additionalProperties: false), and it has
#                                                              no "required" or "unique": only updateField does
#   createRelationship  POST /tables/{childTableId}/relationship  {parentTableId*, foreignKeyField{label},
#                                                              lookupFieldIds, summaryFields}; the response's
#                                                              foreignKeyField.id is the new reference field
#   upsert              POST /records                         {to*, data*, fieldsToReturn, mergeFieldId}; 200 or 207
#                                                              with metadata.createdRecordIds and lineErrors
#   getFields           GET  /fields?tableId=                 every field, with fieldType and mode
OPERATIONS: dict[str, tuple[str, str, frozenset[str]]] = {
    "createApp": ("POST", "/apps", frozenset()),
    "createTable": ("POST", "/tables", frozenset({"appId"})),
    "createField": ("POST", "/fields", frozenset({"tableId"})),
    "createRelationship": ("POST", "/tables/{tableId}/relationship", frozenset()),
    "upsert": ("POST", "/records", frozenset()),
    "getFields": ("GET", "/fields", frozenset({"tableId"})),
}
READS = frozenset({"getFields"})  # the only operation a 5xx or a transport error may repeat

# The mapping type -> the createField fieldType. "reference" fields come from createRelationship instead.
API_FIELD_TYPE: dict[str, str] = {
    "text": "text",
    "numeric": "numeric",
    "checkbox": "checkbox",
    "text-multiple-choice": "text-multiple-choice",
    "timestamp": "timestamp",
    "user": "user",
    "file": "file",
}

# SPEC Appendix D: the table names and the field labels, keyed by the demo mapping's roles and columns.
TABLE_NAMES: dict[str, tuple[str, str]] = {
    "obligations": ("Obligations", "Obligation"),
    "inspections": ("Inspections", "Inspection"),
    "artifacts": ("Artifacts", "Artifact"),
    "approvals": ("Approvals", "Approval"),
}
LABELS: dict[str, dict[str, str]] = {
    "obligations": {
        "obligation_id": "Obligation ID",
        "project_id": "Project ID",
        "scope_revision": "Scope Revision",
        "asset_id": "Asset ID",
        "activity_kind": "Activity",
    },
    "inspections": {
        "inspection_id": "Inspection ID",
        "revision": "Revision",
        "is_current": "Is Current",
        "obligation_id": "Related Obligation",
        "project_id": "Project ID",
        "asset_id": "Asset ID",
        "activity_kind": "Activity",
        "completion_status": "Status",
        "completed_at": "Completed At",
    },
    "artifacts": {
        "artifact_id": "Artifact ID",
        "revision": "Revision",
        "is_current": "Is Current",
        "inspection_id": "Related Inspection",
        "project_id": "Project ID",
        "asset_id": "Asset ID",
        "document_kind": "Document Kind",
        "relative_path": "File",
    },
    "approvals": {
        "approval_id": "Approval ID",
        "inspection_id": "Related Inspection",
        "inspection_revision": "Inspection Revision",
        "evidence_digest": "Evidence Digest",
        "decision": "Decision",
        "decided_at": "Decided At",
        "decided_by": "Decided By",
    },
}

_HOSTNAME_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_DBID_RE = re.compile(r"[A-Za-z0-9]{1,64}")
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,512}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_USER_ID_RE = re.compile(r"[0-9]{1,20}\.[A-Za-z0-9]{1,16}")
_FIELD_KEY_RE = re.compile(r"[1-9][0-9]{0,9}")
# Failures in which Quickbase answered and created nothing. After any other failure of createApp (a 5xx, a lost
# connection, an unreadable 2xx, Ctrl+C in flight) an app may exist without the build knowing its id.
REFUSED = frozenset({"QB_HTTP_ERROR", "QB_PERMISSION", "QB_RATE_LIMITED", "QB_REDIRECT", "NOT_ALLOWED"})


class BuildError(RunError):
    """A refused or failed build: exit 2, one diagnostic line on stderr, never the token."""


class AllowlistError(BuildError):
    """An operation outside the six, or an id that did not come from this run. Raised before any I/O."""

    def __init__(self, message: str) -> None:
        super().__init__("NOT_ALLOWED", message)


# ----------------------------------------------------------------------------------------------- the plan


@dataclass(frozen=True)
class FieldPlan:
    column: str
    label: str
    demo_fid: int
    type: str  # the mapping type
    values: dict[str, str]  # multiple choice: display label -> canonical value
    target: str | None  # reference: the parent role


@dataclass(frozen=True)
class TablePlan:
    role: str
    name: str
    single: str
    fields: tuple[FieldPlan, ...]


def make_plan(mapping: FieldMapping) -> tuple[TablePlan, ...]:
    """The Appendix D tables in creation order, each with its fields in demo-fid order (that order reproduces the
    demo field ids: new fields take the next free id from 6). Types and choices come from the demo mapping."""
    plan = []
    for role in REQUIRED_ROLES:
        table = mapping.tables[role]
        fields = tuple(
            FieldPlan(
                column=column,
                label=LABELS[role][column],
                demo_fid=fm.fid,
                type=fm.type,
                values=dict(fm.values or {}),
                target=fm.target,
            )
            for column, fm in sorted(table.fields.items(), key=lambda kv: kv[1].fid)
        )
        plan.append(TablePlan(role, *TABLE_NAMES[role], fields))
    return tuple(plan)


def load_s01() -> Any:
    """The S01 baseline from the fixture generator, imported the way tests/builder.py does."""
    spec = importlib.util.spec_from_file_location("make_fixtures", REPO / "tools" / "make_fixtures.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.baseline("S01-clean")


# ------------------------------------------------------------------------------------------------ the client


class BuilderClient:
    """The builder's own small client: the six operations, sequential, rate-limited, no redirects.

    A 429 is waited out (Retry-After) and repeated: Quickbase did not process the request. A 5xx or a transport
    error is repeated only for getFields. A failed write may or may not have taken effect, so repeating it could
    create a duplicate; the build stops instead. The token never appears in a message, a repr or a log record."""

    def __init__(
        self,
        realm: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        requests_per_10s: int = 90,
        max_attempts: int = 5,
        max_retry_wait_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if _HOSTNAME_RE.fullmatch(realm) is None:
            raise BuildError("USAGE", "--realm must be a plain host name such as example.quickbase.com")
        if _TOKEN_RE.fullmatch(token) is None:
            raise BuildError("QB_TOKEN_MALFORMED", "the user token is empty or malformed (value not shown)")
        self._realm = realm
        self._token = token
        self._limit = requests_per_10s
        self._max_attempts = max_attempts
        self._max_retry_wait_s = max_retry_wait_s
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self.requests = 0
        self._filter = TokenRedactionFilter(token)
        log.addFilter(self._filter)
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(120.0, connect=10.0),
            follow_redirects=False,
            headers={
                "QB-Realm-Hostname": realm,
                "Authorization": f"QB-USER-TOKEN {token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )

    def __repr__(self) -> str:
        return f"BuilderClient(realm={self._realm!r})"

    def close(self) -> None:
        self._http.close()
        log.removeFilter(self._filter)

    def call(
        self,
        operation: str,
        *,
        params: Mapping[str, str] | None = None,
        path: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        """One allowed operation; the decoded JSON response. Every check happens before any I/O."""
        if operation not in OPERATIONS:
            raise AllowlistError(f"{operation} is not one of the builder's six operations")
        method, template, query = OPERATIONS[operation]
        if method not in ("GET", "POST"):  # unreachable by construction: nothing in OPERATIONS deletes
            raise AllowlistError(f"{operation}: {method} is never sent")
        params = dict(params or {})
        if set(params) != query:
            raise AllowlistError(f"{operation} takes exactly the query parameters {sorted(query)}")
        for value in [*params.values(), *(path or {}).values()]:
            if not isinstance(value, str) or _DBID_RE.fullmatch(value) is None:
                raise AllowlistError(f"{operation}: {value!r} is not a Quickbase id")
        if operation == "upsert" and not only_adds(body):
            raise AllowlistError("upsert only adds records: no mergeFieldId and no Record ID#")
        url = BASE_URL + template.format(**(path or {}))
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            self.requests += 1
            try:
                response = self._http.request(method, url, params=params or None, json=body)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {self._redact(str(exc))}"
                if operation in READS and attempt < self._max_attempts:
                    self._wait(operation, last, self._backoff(attempt), attempt)
                    continue
                raise self._failed(operation, last) from None
            status = response.status_code
            if 200 <= status < 300:
                try:
                    return json.loads(response.content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise BuildError("QB_PROTOCOL", f"{operation} returned a body that is not JSON") from None
            if status == 429 and attempt < self._max_attempts:
                self._wait(operation, "HTTP 429", self._retry_after(response, attempt), attempt)
                continue
            if 500 <= status < 600 and operation in READS and attempt < self._max_attempts:
                self._wait(operation, f"HTTP {status}", self._backoff(attempt), attempt)
                continue
            detail = self._error_detail(response)
            if 300 <= status < 400:
                raise BuildError(
                    "QB_REDIRECT", f"{operation} answered HTTP {status}; redirects are never followed"
                )
            if status in (401, 403):
                raise BuildError("QB_PERMISSION", f"{operation} was refused with HTTP {status}: {detail}")
            if status == 429:
                raise BuildError(
                    "QB_RATE_LIMITED", f"{operation} was still refused with HTTP 429 after {attempt} attempts"
                )
            if status >= 500:
                raise self._failed(operation, f"HTTP {status}: {detail}")
            raise BuildError("QB_HTTP_ERROR", f"{operation} failed with HTTP {status}: {detail}")

    def _failed(self, operation: str, last: str) -> BuildError:
        if operation in READS:
            return BuildError("QB_RETRIES_EXHAUSTED", f"{operation} failed; last error: {last}")
        return BuildError(
            "QB_WRITE_FAILED",
            f"{operation} failed ({last}). A failed write may or may not have taken effect, so it is never repeated",
        )

    def _wait(self, operation: str, last: str, delay: float, attempt: int) -> None:
        log.warning(
            "%s: %s; retrying in %.3f s (attempt %d of %d)",
            operation,
            last,
            delay,
            attempt,
            self._max_attempts,
        )
        self._sleep(delay)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** (attempt - 1), BACKOFF_CAP_SECONDS)

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        try:
            wait = float(response.headers.get("retry-after", "nan"))
        except ValueError:
            wait = math.nan
        if not math.isfinite(wait):
            wait = self._backoff(attempt)
        return min(max(wait, 0.0), self._max_retry_wait_s)

    def _throttle(self) -> None:
        now = self._clock()
        while self._sent and now - self._sent[0] >= WINDOW_SECONDS:
            self._sent.popleft()
        if len(self._sent) >= self._limit:
            self._sleep(WINDOW_SECONDS - (now - self._sent[0]))
            now = self._clock()
            while self._sent and now - self._sent[0] >= WINDOW_SECONDS:
                self._sent.popleft()
        self._sent.append(now)

    def _error_detail(self, response: httpx.Response) -> str:
        try:
            body = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        text = ""
        if isinstance(body, dict):
            text = " - ".join(
                str(body[k]) for k in ("message", "description") if body.get(k) not in (None, "")
            )
        if not text:
            text = response.content[:200].decode("utf-8", errors="replace").strip() or "(no body)"
        return self._redact(text)

    def _redact(self, text: str) -> str:
        return redact(text, self._token)


def only_adds(body: Any) -> bool:
    """Whether an upsert body can only add records. upsert also updates: a data item that carries the table's
    key field (Record ID#, field 3, on every table this run creates) or a mergeFieldId (OpenAPI upsert) turns
    an add into an update. Every field key must be a plain field id other than 3."""
    if not isinstance(body, dict) or "mergeFieldId" in body or not isinstance(body.get("data"), list):
        return False
    return all(
        isinstance(item, dict)
        and all(isinstance(key, str) and _FIELD_KEY_RE.fullmatch(key) and key != "3" for key in item)
        for item in body["data"]
    )


# ------------------------------------------------------------------------------------------------ the ledger


@dataclass
class Ledger:
    """Every id this run has received from Quickbase. A request may carry only these."""

    realm: str
    app_name: str
    app_id: str | None = None
    tables: dict[str, str] = field(default_factory=dict)  # role -> table id
    fields: dict[str, dict[str, int]] = field(default_factory=dict)  # role -> column -> field id
    seen_fields: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)  # role -> getFields
    records: dict[str, dict[str, int]] = field(default_factory=dict)  # role -> key -> record id
    files: int = 0
    owner: dict[str, Any] | None = None  # the token's user, as the Record Owner of a record this run created

    def app(self) -> str:
        if self.app_id is None:
            raise AllowlistError("no app was created in this run")
        return self.app_id

    def table(self, role: str) -> str:
        if role not in self.tables:
            raise AllowlistError(f"table {role!r} was not created in this run")
        return self.tables[role]

    def fid(self, role: str, column: str) -> int:
        fid = self.fields.get(role, {}).get(column)
        if fid is None:
            raise AllowlistError(f"field {role}.{column} was not created in this run")
        return fid

    def seen(self, role: str, fid: int) -> int:
        if fid not in self.seen_fields.get(role, {}):
            raise AllowlistError(f"field {fid} of {role} was not reported by getFields in this run")
        return fid

    def rid(self, role: str, key: str) -> int:
        rid = self.records.get(role, {}).get(key)
        if rid is None:
            raise AllowlistError(f"record {role} {key!r} was not created in this run")
        return rid

    def state(self) -> str:
        parts = [f"{len(self.tables)} of 4 tables", f"{sum(len(f) for f in self.fields.values())} fields"]
        parts.append(f"{sum(len(r) for r in self.records.values())} records")
        return ", ".join(parts)


# ------------------------------------------------------------------------------------------------- the build


def cell(fp: FieldPlan, row: dict[str, str], files: Mapping[str, bytes], ledger: Ledger, user_id: str) -> Any:
    """A canonical S01 value in the form the field type takes on write ("Field type details"): text and
    timestamps as strings, numbers, booleans, a choice's display label, {"id"} for a user, {"fileName", "data"}
    (base64) for a file, and a parent's Record ID# for a reference."""
    raw = row[fp.column]
    if fp.type == "reference":
        assert fp.target is not None
        return ledger.rid(fp.target, raw)
    if fp.type == "numeric":
        return int(raw)
    if fp.type == "checkbox":
        return raw == "true"
    if fp.type == "text-multiple-choice":
        labels = [label for label, canonical in fp.values.items() if canonical == raw]
        if len(labels) != 1:
            raise BuildError(
                "INTERNAL", f"{fp.column}: {raw!r} has no single choice label in the demo mapping"
            )
        return labels[0]
    if fp.type == "user":
        return {"id": user_id}
    if fp.type == "file":
        return {"fileName": PurePosixPath(raw).name, "data": base64.b64encode(files[raw]).decode("ascii")}
    return raw  # text, timestamp


def batches(records: list[dict[str, Any]]) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """(offset, batch) pairs of at most MAX_UPSERT_RECORDS records and about MAX_UPSERT_BYTES of JSON."""
    start, size = 0, 0
    batch: list[dict[str, Any]] = []
    for n, record in enumerate(records):
        length = len(json.dumps(record))
        if batch and (len(batch) >= MAX_UPSERT_RECORDS or size + length > MAX_UPSERT_BYTES):
            yield start, batch
            start, size, batch = n, 0, []
        batch.append(record)
        size += length
    if batch:
        yield start, batch


def create_schema(
    client: BuilderClient, plan: tuple[TablePlan, ...], ledger: Ledger, say: Callable[[str], None]
) -> None:
    for table in plan:
        created = client.call(
            "createTable",
            params={"appId": ledger.app()},
            body={
                "name": table.name,
                "singleRecordName": table.single,
                "pluralRecordName": table.name,
                "description": f"SPEC Appendix D: {table.name.lower()} (synthetic North Creek data)",
            },
        )
        table_id = _string(created, "id", "createTable")
        ledger.tables[table.role] = table_id
        ledger.fields[table.role] = {}
        key_fid = created.get("keyFieldId", 3)  # OpenAPI: "usually the Quickbase Record ID"
        if key_fid != 3:  # upsert updates on the key field, and only_adds() refuses field 3 only
            raise BuildError(
                "QB_UNEXPECTED", f"{table.name}: the key field is {key_fid!r}, not Record ID# (3)"
            )
        made = []
        for fp in table.fields:
            if fp.type == "reference":
                assert fp.target is not None
                rel = client.call(
                    "createRelationship",
                    path={"tableId": ledger.table(table.role)},
                    body={"parentTableId": ledger.table(fp.target), "foreignKeyField": {"label": fp.label}},
                )
                fk = rel.get("foreignKeyField") if isinstance(rel, dict) else None
                fid = _int(fk, "id", "createRelationship foreignKeyField")
                ends = (rel.get("childTableId"), rel.get("parentTableId"))
                if ends != (ledger.table(table.role), ledger.table(fp.target)):
                    raise BuildError(
                        "QB_UNEXPECTED", f"{table.name}: the relationship joins {ends}, not the tables asked"
                    )
                if rel.get("lookupFields") or rel.get("summaryFields"):
                    raise BuildError(
                        "QB_UNEXPECTED", f"{table.name}: the relationship came with lookup or summary fields"
                    )
            else:
                body: dict[str, Any] = {
                    "label": fp.label,
                    "fieldType": API_FIELD_TYPE[fp.type],
                    "addToForms": True,
                }
                if fp.type == "text-multiple-choice":
                    body["properties"] = {"choices": list(fp.values), "allowNewChoices": False}
                response = client.call("createField", params={"tableId": ledger.table(table.role)}, body=body)
                fid = _int(response, "id", "createField")
                actual = response.get("fieldType")
                if actual is not None and actual != body["fieldType"]:
                    raise BuildError(
                        "QB_UNEXPECTED",
                        f"{table.name}.{fp.label} was created as fieldType {actual!r}, not {body['fieldType']!r}",
                    )
            ledger.fields[table.role][fp.column] = fid
            made.append(f"{fid} {fp.label}")
        say(f"  {table.name}: table {table_id}; fields {', '.join(made)}")


def verify_schema(client: BuilderClient, mapping: FieldMapping, ledger: Ledger) -> None:
    """getFields on every table, checked exactly as capture-quickbase will check it (SPEC §12.5 step 1)."""
    for role in REQUIRED_ROLES:
        fields = client.call("getFields", params={"tableId": ledger.table(role)})
        if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
            raise BuildError("QB_PROTOCOL", "getFields did not return a JSON array of field objects")
        verify_fields(mapping, role, fields)
        ledger.seen_fields[role] = {f["id"]: f for f in fields if isinstance(f.get("id"), int)}
        owner = ledger.seen_fields[role].get(RECORD_OWNER_FID)
        if owner is None or owner.get("fieldType") != "user":
            raise BuildError(
                "QB_UNEXPECTED", f"{role}: field {RECORD_OWNER_FID} (Record Owner) is not a user field"
            )


def load_records(
    client: BuilderClient,
    plan: tuple[TablePlan, ...],
    snap: Any,
    ledger: Ledger,
    decided_by_id: str | None,
    say: Callable[[str], None],
) -> None:
    for table in plan:
        role = table.role
        key_column = ROLE_KEY_COLUMN[role]
        key_fid = ledger.fid(role, key_column)
        rows = snap.tables[ROLE_DATASET[role]]
        user_id = decided_by_id or (ledger.owner or {}).get("id") or ""
        records = [
            {
                str(ledger.fid(role, fp.column)): {"value": cell(fp, row, snap.files, ledger, user_id)}
                for fp in table.fields
            }
            for row in rows
        ]
        ledger.records[role] = {}
        for offset, batch in batches(records):
            keys = [row[key_column] for row in rows[offset : offset + len(batch)]]
            response = client.call(
                "upsert",
                body={
                    "to": ledger.table(role),
                    "data": batch,
                    "fieldsToReturn": [key_fid, ledger.seen(role, RECORD_OWNER_FID)],
                },
            )
            _check_upsert(response, table, keys, key_fid, ledger)
        ledger.files += sum(fp.type == "file" for fp in table.fields) * len(rows)
        if ledger.owner is None and decided_by_id is None:
            raise BuildError(
                "QB_UNEXPECTED",
                f"{table.name}: the response did not name the Record Owner (field 4); pass --decided-by-id",
            )
        say(f"  {table.name}: {len(rows)} records")


def _check_upsert(response: Any, table: TablePlan, keys: list[str], key_fid: int, ledger: Ledger) -> None:
    where = f"upsert into {table.name}"
    meta = response.get("metadata") if isinstance(response, dict) else None
    if not isinstance(meta, dict):
        raise BuildError("QB_PROTOCOL", f"{where}: the response has no metadata")
    if meta.get("lineErrors"):
        shown = json.dumps(meta["lineErrors"])[:600]
        raise BuildError(
            "QB_LINE_ERRORS", f"{where}: Quickbase refused records (lineErrors, 1-based): {shown}"
        )
    created = meta.get("createdRecordIds")
    data = response.get("data")
    if not isinstance(created, list) or len(created) != len(keys) or not isinstance(data, list):
        raise BuildError("QB_PROTOCOL", f"{where}: expected {len(keys)} created records, got {created!r}")
    returned: dict[str, int] = {}
    for item in data:
        rid = (item.get("3") or {}).get("value") if isinstance(item, dict) else None
        key = (item.get(str(key_fid)) or {}).get("value") if isinstance(item, dict) else None
        if not isinstance(rid, int) or not isinstance(key, str):
            raise BuildError(
                "QB_PROTOCOL", f"{where}: a returned record lacks its Record ID# or key: {item!r}"
            )
        returned[key] = rid
        owner = (item.get(str(RECORD_OWNER_FID)) or {}).get("value")
        if ledger.owner is None and isinstance(owner, dict) and isinstance(owner.get("id"), str):
            ledger.owner = owner
    # Reference values come from these record ids, matched by key, never assumed to be 1..N.
    if sorted(returned) != sorted(keys) or sorted(returned.values()) != sorted(created):
        raise BuildError("QB_PROTOCOL", f"{where}: the returned records do not match the records sent")
    ledger.records[table.role].update(returned)


def _string(value: Any, key: str, where: str) -> str:
    item = value.get(key) if isinstance(value, dict) else None
    if not isinstance(item, str) or _DBID_RE.fullmatch(item) is None:
        raise BuildError("QB_PROTOCOL", f"{where} returned no valid {key}")
    return item


def _int(value: Any, key: str, where: str) -> int:
    item = value.get(key) if isinstance(value, dict) else None
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise BuildError("QB_PROTOCOL", f"{where} returned no valid {key}")
    return item


# --------------------------------------------------------------------------------------------- the outputs


def live_mapping_text(demo_text: str, ledger: Ledger, stamp: str) -> str:
    """The demo mapping with this app's table and field ids (and mapping_id quickbase-live), layout kept."""
    lines = [
        f"# Written by tools/qb_build_test_app.py at {stamp} for app {ledger.app()} in {ledger.realm}.",
        "# It holds real table and field ids: keep it in the gitignored local/ directory (SPEC Appendix D).",
    ]
    role: str | None = None
    for line in demo_text.splitlines():
        if line.startswith("mapping_id:"):
            line = "mapping_id: quickbase-live"
        elif m := re.fullmatch(r"scope_filter: \{fid: \d+, value: ([^}]+)\}.*", line):
            fid = ledger.fid("obligations", "project_id")
            line = f"scope_filter: {{fid: {fid}, value: {m.group(1)}}}           # obligations table only"
        elif m := re.fullmatch(r"  ([a-z_]+):", line):
            role = m.group(1)
        elif role and (m := re.fullmatch(r"(    table_id: )\S+(.*)", line)):
            line = f"{m.group(1)}{json.dumps(ledger.table(role))}{m.group(2)}"  # quoted: an id is a string
        elif role and (m := re.fullmatch(r"(      ([a-z_]+): +\{fid: )(\d+)(,.*)", line)):
            line = f"{m.group(1)}{ledger.fid(role, m.group(2))}{m.group(4)}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def capture_config_text(ledger: Ledger, token_env: str, accepted: Mapping[str, str], stamp: str) -> str:
    """local/qb-capture.yml for this app (SPEC §12.5). Strings are JSON-quoted, which YAML reads verbatim."""
    owner = ledger.owner or {}
    attested_by = str(
        owner.get("email") or owner.get("name") or owner.get("id") or "the builder's token user"
    )
    note = (
        f"App {ledger.app()} was built by tools/qb_build_test_app.py with this token's user, who owns it and can "
        "read every record. Re-attest if you capture with another token."
    )
    q = json.dumps
    return "\n".join(
        [
            f"# Written by tools/qb_build_test_app.py at {stamp} for app {ledger.app()} in {ledger.realm}.",
            "# The user token is read only from the environment variable named in token_env; never put it here.",
            "schema: inspection-reconcile/qb-capture/v1",
            f"realm_hostname: {q(ledger.realm)}",
            f"app_id: {q(ledger.app())}",
            f"token_env: {q(token_env)}",
            "page_size: 1000",
            "capture_files: true",
            "scope:",
            "  scope_revision: S1",
            f"  accepted: {{by: {q(accepted['by'])}, at: {q(accepted['at'])}, reference: {q(accepted['reference'])}}}",
            "limits:",
            "  max_file_bytes: 104857600",
            "  requests_per_10s: 90",
            "  max_attempts: 5",
            "  max_retry_wait_s: 60",
            "  max_pages: 10000",
            "operator_attestation:",
            "  full_read_access: true",
            f"  attested_by: {q(attested_by)}",
            f"  note: {q(note)}",
            "",
        ]
    )


def check_outputs(ledger: Ledger, mapping_text: str, config_text: str) -> None:
    """Both files must load with the package's own validators and carry exactly this run's ids."""
    live = parse_mapping(loads_yaml(mapping_text, "quickbase-live.yml"), "quickbase-live.yml")
    for role in REQUIRED_ROLES:
        table = live.tables[role]
        if table.table_id != ledger.table(role) or any(
            fm.fid != ledger.fid(role, column) for column, fm in table.fields.items()
        ):
            raise BuildError("INTERNAL", f"the live mapping does not carry this run's ids for {role}")
    if live.mapping_id != "quickbase-live" or live.scope_filter.get("fid") != ledger.fid(
        "obligations", "project_id"
    ):
        raise BuildError("INTERNAL", "the live mapping's mapping_id or scope_filter is wrong")
    config = parse_capture_config(loads_yaml(config_text, "qb-capture.yml"), "qb-capture.yml")
    if (config.realm_hostname, config.app_id) != (ledger.realm, ledger.app()):
        raise BuildError("INTERNAL", "the capture configuration does not name this run's realm and app")


# ---------------------------------------------------------------------------------------------------- the CLI


def read_token(args: argparse.Namespace, environ: Mapping[str, str]) -> tuple[str | None, str]:
    """(token or None, where it comes from). The token is never taken from the command line."""
    if args.token_file:
        path = Path(args.token_file)
        source = f"the file {path.as_posix()}"
        try:
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None, source
        return token or None, source
    return environ.get(args.token_env) or None, f"the environment variable {args.token_env}"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qb_build_test_app.py",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--realm", required=True, help="your realm's host name, e.g. example.quickbase.com")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--token-env", default="QB_USER_TOKEN", help="environment variable holding the user token"
    )
    source.add_argument("--token-file", help="file holding the user token, e.g. local/qb_token (gitignored)")
    parser.add_argument("--app-name", help="the new app's name (default: a dated name)")
    parser.add_argument(
        "--decided-by-id",
        help="Quickbase user id for the approvals' Decided By (default: the token's own user)",
    )
    parser.add_argument("--mapping-out", default="local/quickbase-live.yml")
    parser.add_argument("--config-out", default="local/qb-capture.yml")
    parser.add_argument("--force", action="store_true", help="replace existing output files")
    parser.add_argument("--yes", action="store_true", help="build it; without --yes nothing is sent")
    return parser.parse_args(argv)


def describe(plan: tuple[TablePlan, ...]) -> list[str]:
    lines = []
    for n, table in enumerate(plan, start=2):
        parts = []
        for fp in table.fields:
            if fp.type == "reference":
                parts.append(
                    f"{fp.demo_fid} {fp.label} (relationship from {TABLE_NAMES[fp.target or ''][0]})"
                )
            elif fp.values:
                parts.append(f"{fp.demo_fid} {fp.label} ({API_FIELD_TYPE[fp.type]}: {', '.join(fp.values)})")
            else:
                parts.append(f"{fp.demo_fid} {fp.label} ({API_FIELD_TYPE[fp.type]})")
        lines.append(f"  {n}. {table.name}: createTable, then " + "; ".join(parts))
    return lines


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = parse_args(argv)
    env = os.environ if environ is None else environ
    token: str | None = None
    try:
        token, source = read_token(args, env)
        return build(args, token, source, transport=transport, clock=clock, sleep=sleep, now=now)
    except RunError as exc:
        print(f"error: {exc.code}: {redact(exc.message, token)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # before createApp; once it is sent, build() reports what may exist
        print("error: INTERRUPTED: stopped by Ctrl+C before anything was created", file=sys.stderr)
        return 2
    except Exception as exc:  # a defect here must not print a traceback, or anything holding the token
        print(f"error: INTERNAL: {type(exc).__name__}: {redact(str(exc), token)}", file=sys.stderr)
        return 2


def build(
    args: argparse.Namespace,
    token: str | None,
    source: str,
    *,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
) -> int:
    if _HOSTNAME_RE.fullmatch(args.realm) is None:
        raise BuildError("USAGE", "--realm must be a plain host name such as example.quickbase.com")
    if args.token_file is None and _ENV_NAME_RE.fullmatch(args.token_env) is None:
        raise BuildError("USAGE", "--token-env must be an environment variable name")
    if args.decided_by_id is not None and _USER_ID_RE.fullmatch(args.decided_by_id) is None:
        raise BuildError("USAGE", "--decided-by-id must be a Quickbase user id such as 123456.ab1s")
    started = now().astimezone(UTC)
    stamp = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    app_name = args.app_name or f"inspection-reconcile C3 test {started.strftime('%Y-%m-%d %H:%M')} UTC"
    demo_text = DEMO_MAPPING.read_text(encoding="utf-8")
    plan = make_plan(load_mapping(DEMO_MAPPING))
    snap = load_s01()
    counts = {t.role: len(snap.tables[ROLE_DATASET[t.role]]) for t in plan}
    file_bytes = sum(len(data) for data in snap.files.values())
    outputs = [Path(args.mapping_out), Path(args.config_out)]
    taken = [p.as_posix() for p in outputs if p.exists()]
    if taken and not args.force:
        raise BuildError("OUT_EXISTS", f"{', '.join(taken)}: exists (use --force to replace)")

    print("Appendix D test app (SPEC Appendix D): the plan")
    print(f"  realm   {args.realm}")
    print(f'  app     "{app_name}" (new; assignToken: true, so the token can read it)')
    print(f"  token   from {source}: {'found' if token else 'NOT FOUND'} (the value is never shown)")
    print("  1. createApp")
    print("\n".join(describe(plan)))
    print("  6. getFields on every table, checked exactly as capture-quickbase will check it")
    print(
        f"  7. upsert the S01 records, parents first: {counts['obligations']} obligations, "
        f"{counts['inspections']} inspections, {counts['artifacts']} artifacts with {len(snap.files)} files "
        f"({file_bytes:,} bytes), {counts['approvals']} approvals"
    )
    print(
        f"  Decided By: {args.decided_by_id or 'the token user (the Record Owner of the first record created)'}"
    )
    print("  at most 90 requests per 10 s; a 429 is waited out; a failed write is never repeated")
    print(f"  then writes {outputs[0].as_posix()} and {outputs[1].as_posix()}")
    print(
        "  not done here: Obligation ID unique and required (createField cannot set them), and the read-only"
        " Reconcile Reader role and its token (Appendix D, by hand)"
    )
    if not args.yes:
        print("dry run: nothing was sent. Add --yes to build the app.")
        return 0
    if not token:
        raise BuildError("QB_TOKEN_MISSING", f"no user token in {source}")

    client = BuilderClient(args.realm, token, transport=transport, clock=clock, sleep=sleep)
    ledger = Ledger(realm=args.realm, app_name=app_name)
    try:
        app = client.call(
            "createApp",
            body={
                "name": app_name,
                "description": "Synthetic test app for inspection-reconcile step C3 (SPEC Appendix D). "
                "Built by tools/qb_build_test_app.py; safe to delete.",
                "assignToken": True,
            },
        )
        ledger.app_id = _string(app, "id", "createApp")
        print(f"created app {ledger.app_id}")
        create_schema(client, plan, ledger, print)
        mapping_text = live_mapping_text(demo_text, ledger, stamp)
        live = parse_mapping(loads_yaml(mapping_text, "quickbase-live.yml"), "quickbase-live.yml")
        verify_schema(client, live, ledger)
        load_records(client, plan, snap, ledger, args.decided_by_id, print)
        accepted = snap.manifest["scope"]["accepted"]
        config_text = capture_config_text(ledger, args.token_env, accepted, stamp)
        check_outputs(ledger, mapping_text, config_text)
        for path, text in zip(outputs, (mapping_text, config_text), strict=True):
            write_files(path.parent, {path.name: text.encode("utf-8")})
    except (Exception, KeyboardInterrupt) as exc:
        if ledger.app_id is None:  # createApp itself failed: say when an app may exist anyway
            if isinstance(exc, RunError) and exc.code in REFUSED:
                raise
            if isinstance(exc, RunError):
                code, detail = exc.code, exc.message
            elif isinstance(exc, KeyboardInterrupt):
                code, detail = "INTERRUPTED", "interrupted while createApp was in flight"
            else:
                code, detail = "INTERNAL", type(exc).__name__
            raise BuildError(
                code,
                f"{detail}. An app named {app_name!r} may have been created in {args.realm}; "
                "check for it before running again.",
            ) from None
        reason = f"{exc.code}: {exc.message}" if isinstance(exc, RunError) else type(exc).__name__
        raise BuildError(
            "BUILD_INCOMPLETE",
            f"{reason}. App {ledger.app_id} ({app_name!r}) in {args.realm} was created and is incomplete "
            f"({ledger.state()}). Nothing was deleted: delete that app in Quickbase, then run again.",
        ) from None
    finally:
        client.close()

    differ = [
        f"{t.role}.{fp.column} {fp.demo_fid}->{ledger.fid(t.role, fp.column)}"
        for t in plan
        for fp in t.fields
        if ledger.fid(t.role, fp.column) != fp.demo_fid
    ]
    total = sum(len(t.fields) for t in plan)
    print(f"built app {ledger.app_id} in {args.realm} with {client.requests} requests")
    print("  tables: " + ", ".join(f"{role} {table_id}" for role, table_id in ledger.tables.items()))
    if differ:
        print(
            f"  field ids: {len(differ)} of {total} differ from the demo mapping (the live mapping has the real ones): {', '.join(differ)}"
        )
    else:
        print(f"  field ids: all {total} equal the demo mapping's")
    owner = ledger.owner or {}
    print(
        f"  Decided By: {args.decided_by_id or owner.get('id')}; Record Owner: {owner.get('email') or owner.get('id')}"
    )
    print(f"wrote {outputs[0].as_posix()} and {outputs[1].as_posix()}")
    print(
        "next, step C3 (docs/c3-runbook.md, step 7); capture-quickbase reads the token from $"
        + args.token_env
        + ":"
    )
    print(
        f"  uv run inspection-reconcile capture-quickbase --config {outputs[1].as_posix()} --mapping {outputs[0].as_posix()} --out local/c3/export"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
