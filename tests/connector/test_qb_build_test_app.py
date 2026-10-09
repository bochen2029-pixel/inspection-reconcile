"""tools/qb_build_test_app.py (step C3) against a fake Quickbase realm.

The fake implements the builder's write operations as Quickbase's OpenAPI document shapes them, and refuses any
body member the document does not define. Through MockApp's read operations (getFields, runQuery with the keyset
where, downloadFile) it serves back exactly what was written. The end-to-end proof is build -> capture -> normalize
-> assess, which must give S01's findings. Expected verdicts come from S01's own run, never from the builder or the
capture code.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import scenario_support as ss
from conftest import REPO, SCENARIOS
from test_qb_capture import NOW, FakeTime, MockApp, assess_export, triples

from inspection_reconcile.adapters.mapping import load_mapping
from inspection_reconcile.adapters.qb_capture import capture, load_capture_config
from inspection_reconcile.adapters.qb_client import QuickbaseClient

_spec = importlib.util.spec_from_file_location("qb_build_test_app", REPO / "tools" / "qb_build_test_app.py")
assert _spec is not None and _spec.loader is not None
qb = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = qb  # its dataclasses resolve their annotations through sys.modules
_spec.loader.exec_module(qb)

TOKEN = "b7syn_BUILDER_TOKEN_0123456789abcdef"  # synthetic; must never be printed or written anywhere
REALM = "synthetic.quickbase.invalid"
STAMP = "2026-10-09T11:00:00Z"
OWNER = {
    "email": "builder@example.invalid",
    "id": "58123456.syn",
    "name": "Synthetic Builder",
    "userName": "builder",
}
BUILT_IN = (
    (1, "Date Created", "timestamp"),
    (2, "Date Modified", "timestamp"),
    (3, "Record ID#", "recordid"),
)
BUILT_IN += ((4, "Record Owner", "user"), (5, "Last Modified By", "user"))

# OpenAPI: the members each operation's request body may have (createField is additionalProperties: false).
APP_KEYS = {"name", "description", "assignToken", "variables", "securityProperties"}
TABLE_KEYS = {"name", "pluralRecordName", "singleRecordName", "description"}
FIELD_KEYS = {"label", "fieldType", "properties", "addToForms", "appearsByDefault", "audited", "bold"}
FIELD_KEYS |= {"fieldHelp", "findEnabled", "noWrap", "permissions"}
RELATIONSHIP_KEYS = {"parentTableId", "foreignKeyField", "lookupFieldIds", "summaryFields"}
UPSERT_KEYS = {"to", "data", "mergeFieldId", "fieldsToReturn"}
FIELD_TYPES = {
    "text",
    "text-multiple-choice",
    "text-multi-line",
    "rich-text",
    "numeric",
    "currency",
    "rating",
}
FIELD_TYPES |= {
    "percent",
    "multitext",
    "email",
    "url",
    "duration",
    "date",
    "datetime",
    "timestamp",
    "timeofday",
}
FIELD_TYPES |= {"checkbox", "user", "multiuser", "address", "phone", "file"}
ISO_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


class FakeRealm(MockApp):
    """A tiny Quickbase realm: createApp, createTable, createField, createRelationship and upsert, checked against
    the OpenAPI shapes and the field-type write formats, plus MockApp's reads over the stored state."""

    def __init__(self, first_fid: int = 6) -> None:
        super().__init__()
        self.fields, self.records = {}, {}
        self.first_fid = first_fid  # > 6: new tables come with pre-existing fields, so the demo ids shift
        self.apps: dict[str, dict[str, Any]] = {}
        self.tables: dict[str, dict[str, Any]] = {}
        self.references: dict[tuple[str, int], str] = {}  # (child table, field id) -> parent table
        self.relationships: list[tuple[str, str, str, Any]] = []
        self.blobs: dict[tuple[str, int, int, int], tuple[str, bytes]] = {}
        self.users = {OWNER["id"]: OWNER}
        self.serial = 0

    def _dbid(self, prefix: str) -> str:
        self.serial += 1
        return prefix + hashlib.sha256(f"{prefix}{self.serial}".encode()).hexdigest()[:7]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if (
            request.headers.get("QB-Realm-Hostname") != REALM
            or request.headers.get("Authorization") != f"QB-USER-TOKEN {TOKEN}"
        ):
            return httpx.Response(401, json={"message": "Unauthorized"})
        path, method = request.url.path, request.method
        relationship = re.fullmatch(r"/v1/tables/(\w+)/relationship", path)
        write = method == "POST" and (
            path in ("/v1/apps", "/v1/tables", "/v1/fields", "/v1/records") or relationship
        )
        if not write:
            return super().__call__(request)  # the reads, the hooks and the request log are MockApp's
        self.requests.append(request)
        body = json.loads(request.content) if request.content else None
        for hook in self.hooks:
            response = hook(request, body)
            if response is not None:
                return response
        if not isinstance(body, dict):
            return bad("the body must be a JSON object")
        if path == "/v1/apps":
            return self._create_app(body)
        if path == "/v1/tables":
            return self._create_table(request.url.params.get("appId", ""), body)
        if path == "/v1/fields":
            return self._create_field(request.url.params.get("tableId", ""), body)
        if relationship:
            return self._create_relationship(relationship.group(1), body)
        return self._upsert(body)

    def _create_app(self, body: dict[str, Any]) -> httpx.Response:
        if set(body) - APP_KEYS or not isinstance(body.get("name"), str):
            return bad(f"createApp: unexpected body {sorted(body)}")
        app_id = self._dbid("bq")
        self.apps[app_id] = {"name": body["name"], "assignToken": body.get("assignToken") is True}
        return httpx.Response(
            200, json={"id": app_id, "name": body["name"], "created": STAMP, "updated": STAMP}
        )

    def _create_table(self, app_id: str, body: dict[str, Any]) -> httpx.Response:
        if app_id not in self.apps:
            return httpx.Response(404, json={"message": "Not Found", "description": f"no app {app_id}"})
        if set(body) - TABLE_KEYS or not isinstance(body.get("name"), str):
            return bad(f"createTable: unexpected body {sorted(body)}")
        table_id = self._dbid("bt")
        self.tables[table_id] = {
            "app": app_id,
            "name": body["name"],
            "next_fid": self.first_fid,
            "next_rid": 1,
        }
        self.tables[table_id]["single"] = body.get("singleRecordName", "Record")
        self.fields[table_id] = [field(fid, label, kind) for fid, label, kind in BUILT_IN]
        self.fields[table_id] += [
            field(fid, f"Pre-existing {fid}", "text") for fid in range(6, self.first_fid)
        ]
        self.records[table_id] = []
        return httpx.Response(200, json={"id": table_id, "name": body["name"], "nextFieldId": self.first_fid})

    def _new_field(self, table_id: str, label: str, kind: str, properties: dict[str, Any]) -> dict[str, Any]:
        fid = self.tables[table_id]["next_fid"]
        self.tables[table_id]["next_fid"] += 1
        created = field(fid, label, kind, properties)
        self.fields[table_id].append(created)
        return created

    def _create_field(self, table_id: str, body: dict[str, Any]) -> httpx.Response:
        if table_id not in self.tables:
            return httpx.Response(404, json={"message": "Not Found", "description": f"no table {table_id}"})
        if (
            set(body) - FIELD_KEYS
            or not isinstance(body.get("label"), str)
            or body.get("fieldType") not in FIELD_TYPES
        ):
            return bad(f"createField: unexpected body {sorted(body)}")
        properties = body.get("properties", {})
        if set(properties) - {"choices", "allowNewChoices"}:
            return bad(f"createField: unexpected properties {sorted(properties)}")
        return httpx.Response(
            200, json=self._new_field(table_id, body["label"], body["fieldType"], properties)
        )

    def _create_relationship(self, child: str, body: dict[str, Any]) -> httpx.Response:
        parent = body.get("parentTableId")
        if set(body) - RELATIONSHIP_KEYS or child not in self.tables or parent not in self.tables:
            return bad(f"createRelationship: unexpected body or tables {sorted(body)}")
        if self.tables[child]["app"] != self.tables[parent]["app"]:
            return bad("createRelationship: cross-app relationships are not created here")
        label = (body.get("foreignKeyField") or {}).get("label") or f"Related {self.tables[parent]['single']}"
        fk = self._new_field(child, label, "numeric", {})
        self.references[(child, fk["id"])] = parent
        self.relationships.append(
            (child, parent, label, (body.get("lookupFieldIds"), body.get("summaryFields")))
        )
        return httpx.Response(
            200,
            json={
                "id": fk["id"],
                "parentTableId": parent,
                "childTableId": child,
                "foreignKeyField": {"id": fk["id"], "label": label, "type": "numeric"},
                "isCrossApp": False,
                "lookupFields": [],
                "summaryFields": [],
            },
        )

    def _upsert(self, body: dict[str, Any]) -> httpx.Response:
        table_id, data = body.get("to"), body.get("data")
        if set(body) - UPSERT_KEYS or table_id not in self.tables or not isinstance(data, list) or not data:
            return bad("upsert: unexpected body")
        by_id = {f["id"]: f for f in self.fields[table_id]}
        created: list[dict[str, Any]] = []
        line_errors: dict[str, list[str]] = {}
        for n, record in enumerate(data, start=1):
            values: dict[str, Any] = {}
            files: dict[int, tuple[str, bytes]] = {}
            problems = []
            for key, cell in record.items():
                definition = by_id.get(int(key)) if key.isdigit() else None
                if (
                    definition is None
                    or definition["id"] <= 5
                    or not isinstance(cell, dict)
                    or set(cell) != {"value"}
                ):
                    problems.append(f"field {key} cannot be written")
                    continue
                problem = self._accept(table_id, definition, cell["value"], values, files)
                if problem:
                    problems.append(f"field {key}: {problem}")
            if problems:
                line_errors[str(n)] = problems
                continue
            rid = self.tables[table_id]["next_rid"]
            self.tables[table_id]["next_rid"] += 1
            stored = {"1": {"value": STAMP}, "2": {"value": STAMP}, "3": {"value": rid}}
            stored |= {"4": {"value": OWNER}, "5": {"value": OWNER}, **values}
            for fid, (name, content) in files.items():
                self.blobs[(table_id, rid, fid, 1)] = (name, content)
                version = {"versionNumber": 1, "fileName": name, "uploaded": STAMP, "creator": OWNER}
                stored[str(fid)] = {"value": {"url": f"/files/{table_id}/{rid}/{fid}", "versions": [version]}}
            self.records[table_id].append(stored)
            created.append(stored)
        wanted = [3, *(f for f in body.get("fieldsToReturn", []) if f != 3)]
        metadata: dict[str, Any] = {
            "createdRecordIds": [r["3"]["value"] for r in created],
            "updatedRecordIds": [],
            "unchangedRecordIds": [],
            "totalNumberOfRecordsProcessed": len(data),
        }
        if line_errors:
            metadata["lineErrors"] = line_errors
        returned = [{str(f): r[str(f)] for f in wanted if str(f) in r} for r in created]
        return httpx.Response(207 if line_errors else 200, json={"data": returned, "metadata": metadata})

    def _accept(
        self,
        table_id: str,
        definition: dict[str, Any],
        value: Any,
        values: dict[str, Any],
        files: dict[int, tuple[str, bytes]],
    ) -> str | None:
        """The write formats of the "Field type details" page; a problem message, or None when accepted."""
        fid, kind = definition["id"], definition["fieldType"]
        parent = self.references.get((table_id, fid))
        if parent is not None:
            known = {r["3"]["value"] for r in self.records[parent]}
            if isinstance(value, bool) or not isinstance(value, int) or value not in known:
                return f"{value!r} is not a record of the parent table"
        elif kind == "text" and not isinstance(value, str):
            return "text takes a string"
        elif kind == "numeric" and (isinstance(value, bool) or not isinstance(value, int | float)):
            return "numeric takes a number"
        elif kind == "checkbox" and not isinstance(value, bool):
            return "checkbox takes a boolean"
        elif kind == "text-multiple-choice" and value not in definition["properties"].get("choices", []):
            return f"{value!r} is not one of the choices"
        elif kind == "timestamp" and (not isinstance(value, str) or ISO_UTC.fullmatch(value) is None):
            return "a date/time takes an ISO 8601 string"
        elif kind == "user":
            if not isinstance(value, dict) or set(value) != {"id"} or value["id"] not in self.users:
                return 'a user takes {"id": <a realm user id>}'
            value = self.users[value["id"]]
        elif kind == "file":
            if not isinstance(value, dict) or set(value) != {"fileName", "data"}:
                return 'a file takes {"fileName", "data"}'
            try:
                files[fid] = (value["fileName"], base64.b64decode(value["data"], validate=True))
            except (binascii.Error, ValueError, TypeError):
                return "the file data is not base64"
            return None
        values[str(fid)] = {"value": value}
        return None

    def _file(self, table_id: str, rid: int, fid: int, version: int) -> httpx.Response:
        name, content = self.blobs[(table_id, rid, fid, version)]
        return httpx.Response(
            200,
            content=base64.b64encode(content),
            headers={"content-disposition": f'attachment; filename="{name}"'},
        )


def field(fid: int, label: str, kind: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": fid,
        "label": label,
        "fieldType": kind,
        "mode": "",
        "required": False,
        "unique": fid == 3,
        "properties": dict(properties or {}),
    }


def bad(description: str) -> httpx.Response:
    return httpx.Response(400, json={"message": "Bad Request", "description": description})


def run_builder(
    realm: FakeRealm, tmp_path: Path, *extra: str, ft: FakeTime | None = None, env: Any = None
) -> int:
    ft = ft or FakeTime()
    argv = [
        "--realm",
        REALM,
        "--mapping-out",
        (tmp_path / "local" / "quickbase-live.yml").as_posix(),
        "--config-out",
        (tmp_path / "local" / "qb-capture.yml").as_posix(),
        *extra,
    ]
    return qb.main(
        argv,
        transport=httpx.MockTransport(realm),
        environ={"QB_USER_TOKEN": TOKEN} if env is None else env,
        clock=ft.clock,
        sleep=ft.sleep,
        now=lambda: NOW,
    )


def capture_and_assess(realm: FakeRealm, tmp_path: Path) -> Any:
    """Step C3 as the runbook runs it, with the files the builder wrote and the package's read-only client."""
    mapping = load_mapping(tmp_path / "local" / "quickbase-live.yml")
    config = load_capture_config(tmp_path / "local" / "qb-capture.yml")
    ft = FakeTime()
    transport = httpx.MockTransport(realm)
    with QuickbaseClient(
        REALM, TOKEN, user_agent="t/1", transport=transport, clock=ft.clock, sleep=ft.sleep
    ) as c:
        manifest = capture(c, config, mapping, tmp_path / "export", now=lambda: NOW)
    assert len(manifest["files"]) == 80 and all(f["status"] == "captured" for f in manifest["files"])
    return assess_export(tmp_path / "export", mapping, tmp_path)


def s01_files() -> list[str]:
    snap = qb.load_s01()
    return sorted(hashlib.sha256(data).hexdigest() for data in snap.files.values())


# -- the end-to-end proof: build, then capture, normalize and assess give S01's findings -----------------------


def test_build_capture_normalize_assess_gives_the_s01_findings(tmp_path: Path, capsys: Any) -> None:
    realm = FakeRealm()
    assert run_builder(realm, tmp_path, "--yes") == 0
    out = capsys.readouterr().out
    assert "field ids: all 29 equal the demo mapping's" in out

    ((app_id, app),) = realm.apps.items()
    assert app["assignToken"] is True
    assert [t["name"] for t in realm.tables.values()] == [
        "Obligations",
        "Inspections",
        "Artifacts",
        "Approvals",
    ]
    assert all(t["app"] == app_id for t in realm.tables.values())
    names = {table_id: t["name"] for table_id, t in realm.tables.items()}
    assert [(names[c], names[p], label, extras) for c, p, label, extras in realm.relationships] == [
        ("Inspections", "Obligations", "Related Obligation", (None, None)),
        ("Artifacts", "Inspections", "Related Inspection", (None, None)),
        ("Approvals", "Inspections", "Related Inspection", (None, None)),
    ]
    assert [len(realm.records[t]) for t in realm.tables] == [40, 40, 80, 40]
    assert sorted(hashlib.sha256(data).hexdigest() for _, data in realm.blobs.values()) == s01_files()
    approvals = realm.records[list(realm.tables)[3]]
    assert {r["12"]["value"]["email"] for r in approvals} == {OWNER["email"]}  # Decided By: the token's user

    config = load_capture_config(tmp_path / "local" / "qb-capture.yml")
    assert (config.realm_hostname, config.app_id, config.token_env) == (REALM, app_id, "QB_USER_TOKEN")
    assert config.full_read_access and config.attested_by == OWNER["email"]

    result = capture_and_assess(realm, tmp_path)
    s01 = ss.run_scenario("S01-clean")
    assert result.status == "READY_FOR_REVIEW"
    assert triples(result) == triples(s01)


def test_real_field_ids_that_differ_from_the_demo_go_into_the_live_mapping(
    tmp_path: Path, capsys: Any
) -> None:
    realm = FakeRealm(first_fid=7)  # every new table already has a field 6, so each created id shifts by one
    assert run_builder(realm, tmp_path, "--yes") == 0
    assert "29 of 29 differ from the demo mapping" in capsys.readouterr().out
    mapping = load_mapping(tmp_path / "local" / "quickbase-live.yml")
    assert mapping.mapping_id == "quickbase-live"
    assert mapping.tables["obligations"].fields["obligation_id"].fid == 7
    assert mapping.tables["approvals"].fields["decided_by"].fid == 13
    assert mapping.scope_filter["fid"] == mapping.tables["obligations"].fields["project_id"].fid == 8
    assert {t.table_id for t in mapping.tables.values()} == set(realm.tables)
    result = capture_and_assess(realm, tmp_path)
    assert result.status == "READY_FOR_REVIEW"
    assert triples(result) == triples(ss.run_scenario("S01-clean"))


def test_the_plan_is_appendix_d_in_the_order_that_gives_the_demo_field_ids() -> None:
    plan = qb.make_plan(load_mapping(qb.DEMO_MAPPING))
    assert [t.role for t in plan] == ["obligations", "inspections", "artifacts", "approvals"]
    export = SCENARIOS / "S16-quickbase-clean" / "export" / "tables"
    for table in plan:
        demo_fields = json.loads((export / table.role / "fields.json").read_text(encoding="utf-8"))
        expected = {f["id"]: (f["label"], f["fieldType"]) for f in demo_fields if f["id"] >= 6}
        made = {
            fp.demo_fid: (fp.label, "numeric" if fp.type == "reference" else qb.API_FIELD_TYPE[fp.type])
            for fp in table.fields
        }
        assert made == expected, table.role
        assert [fp.demo_fid for fp in table.fields] == list(range(6, 6 + len(table.fields)))


# -- nothing is sent without --yes; the token never leaks --------------------------------------------------------


def test_without_yes_nothing_is_sent_and_nothing_is_written(tmp_path: Path, capsys: Any) -> None:
    realm = FakeRealm()
    assert run_builder(realm, tmp_path) == 0
    assert realm.requests == []
    assert not (tmp_path / "local").exists()
    out = capsys.readouterr().out
    assert "dry run: nothing was sent" in out and "unique and required" in out
    assert TOKEN not in out
    assert run_builder(realm, tmp_path, env={}) == 0  # the plan needs no token
    assert realm.requests == [] and "NOT FOUND" in capsys.readouterr().out


def test_the_token_never_appears_in_the_output_the_files_or_the_logs(
    tmp_path: Path, capsys: Any, caplog: Any
) -> None:
    caplog.set_level(logging.DEBUG)
    realm = FakeRealm()
    state = {"429": False}

    def throttle_once(request: httpx.Request, body: Any) -> httpx.Response | None:
        if not state["429"] and request.url.path == "/v1/fields":
            state["429"] = True
            return httpx.Response(429, json={"message": f"slow down, QB-USER-TOKEN {TOKEN}"})
        return None

    realm.hooks.append(throttle_once)
    assert run_builder(realm, tmp_path, "--yes") == 0
    written = [p.read_bytes() for p in (tmp_path / "local").iterdir()]
    assert len(written) == 2 and all(TOKEN.encode() not in data for data in written)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err and TOKEN not in caplog.text
    assert "retrying" in caplog.text

    failing = FakeRealm()
    failing.hooks.append(
        lambda request, body: bad(f"echo {TOKEN}") if request.url.path == "/v1/apps" else None
    )
    assert run_builder(failing, tmp_path / "second", "--yes") == 2
    err = capsys.readouterr().err
    assert "QB_HTTP_ERROR" in err and TOKEN not in err and "***" in err
    client = qb.BuilderClient(REALM, TOKEN, transport=httpx.MockTransport(failing))
    assert TOKEN not in repr(client)
    with pytest.raises(qb.BuildError) as info:  # the client redacts on its own, not only the command line
        client.call("createApp", body={"name": "x"})
    assert TOKEN not in str(info.value) and "***" in info.value.message


def test_an_interrupted_build_names_the_app_and_deletes_nothing(tmp_path: Path, capsys: Any) -> None:
    realm = FakeRealm()

    def interrupt(request: httpx.Request, body: Any) -> httpx.Response | None:
        if request.url.path == "/v1/records":
            raise KeyboardInterrupt
        return None

    realm.hooks.append(interrupt)
    assert run_builder(realm, tmp_path, "--yes") == 2
    err = capsys.readouterr().err
    (app_id,) = realm.apps
    assert "BUILD_INCOMPLETE: KeyboardInterrupt" in err and app_id in err and "4 of 4 tables" in err
    assert {r.method for r in realm.requests} == {"GET", "POST"} and not (tmp_path / "local").exists()


# -- the allowlist and the ids of this run --------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    [
        "deleteApp",
        "deleteTable",
        "deleteFields",
        "deleteRecords",
        "deleteFile",
        "updateField",
        "updateTable",
        "updateApp",
        "copyApp",
        "runQuery",
        "downloadFile",
        "createSolution",
    ],
)
def test_every_other_operation_is_refused_before_any_io(operation: str) -> None:
    realm = FakeRealm()
    client = qb.BuilderClient(REALM, TOKEN, transport=httpx.MockTransport(realm))
    with pytest.raises(qb.AllowlistError):
        client.call(operation, body={})
    assert realm.requests == [] and client.requests == 0


def test_the_six_operations_send_only_get_and_post_with_exactly_their_parameters() -> None:
    assert set(qb.OPERATIONS) == {
        "createApp",
        "createTable",
        "createField",
        "createRelationship",
        "upsert",
        "getFields",
    }
    assert {method for method, _, _ in qb.OPERATIONS.values()} == {"GET", "POST"}
    realm = FakeRealm()
    client = qb.BuilderClient(REALM, TOKEN, transport=httpx.MockTransport(realm))
    for params in ({}, {"appId": "bq1", "extra": "x"}, {"appId": "../apps"}):
        with pytest.raises(qb.AllowlistError):
            client.call("createTable", params=params, body={"name": "T"})
    with pytest.raises(qb.AllowlistError):
        client.call("createRelationship", path={"tableId": "bt1/../x"}, body={})
    assert realm.requests == []


def test_ids_that_did_not_come_from_this_run_are_refused_before_any_io() -> None:
    ledger = qb.Ledger(realm=REALM, app_name="x")
    for probe in (
        ledger.app,
        lambda: ledger.table("obligations"),
        lambda: ledger.fid("obligations", "obligation_id"),
        lambda: ledger.seen("obligations", 4),
        lambda: ledger.rid("obligations", "O-001"),
    ):
        with pytest.raises(qb.AllowlistError):
            probe()
    # Inspections reference Obligations: without an Obligations table from this run, the relationship is never sent.
    realm = FakeRealm()
    client = qb.BuilderClient(REALM, TOKEN, transport=httpx.MockTransport(realm))
    ledger.app_id = client.call("createApp", body={"name": "x"})["id"]
    plan = qb.make_plan(load_mapping(qb.DEMO_MAPPING))
    with pytest.raises(qb.AllowlistError, match="obligations"):
        qb.create_schema(client, plan[1:2], ledger, lambda line: None)
    assert not any("relationship" in r.url.path for r in realm.requests)


# -- 429, failed writes, refused records, redirects, the rate limit --------------------------------------------


def test_a_429_is_waited_out_for_its_retry_after_and_repeated(tmp_path: Path) -> None:
    realm, ft = FakeRealm(), FakeTime()
    state = {"done": False}

    def once(request: httpx.Request, body: Any) -> httpx.Response | None:
        if not state["done"] and request.url.path == "/v1/tables":
            state["done"] = True
            return httpx.Response(429, headers={"retry-after": "7"}, json={"message": "Too Many Requests"})
        return None

    realm.hooks.append(once)
    assert run_builder(realm, tmp_path, "--yes", ft=ft) == 0
    assert 7.0 in ft.sleeps
    assert sum(1 for r in realm.requests if r.url.path == "/v1/tables") == 5  # 4 tables, one of them twice


def test_a_failed_write_is_not_repeated_and_the_error_names_the_app(tmp_path: Path, capsys: Any) -> None:
    realm = FakeRealm()
    count = {"fields": 0}

    def third_field_fails(request: httpx.Request, body: Any) -> httpx.Response | None:
        if request.method == "POST" and request.url.path == "/v1/fields":
            count["fields"] += 1
            if count["fields"] == 3:
                return httpx.Response(502, json={"message": "Bad Gateway"})
        return None

    realm.hooks.append(third_field_fails)
    assert run_builder(realm, tmp_path, "--yes") == 2
    err = capsys.readouterr().err
    (app_id,) = realm.apps
    assert "BUILD_INCOMPLETE" in err and "QB_WRITE_FAILED" in err and app_id in err
    assert "Nothing was deleted" in err and "1 of 4 tables" in err
    assert count["fields"] == 3  # never repeated
    assert {r.method for r in realm.requests} == {"POST"}
    assert not (tmp_path / "local").exists()


def test_a_5xx_on_getfields_is_repeated(tmp_path: Path) -> None:
    realm, ft = FakeRealm(), FakeTime()
    state = {"done": False}

    def once(request: httpx.Request, body: Any) -> httpx.Response | None:
        if not state["done"] and request.method == "GET" and request.url.path == "/v1/fields":
            state["done"] = True
            return httpx.Response(503, json={"message": "Service Unavailable"})
        return None

    realm.hooks.append(once)
    assert run_builder(realm, tmp_path, "--yes", ft=ft) == 0
    assert sum(1 for r in realm.requests if r.method == "GET") == 5  # 4 tables, one of them twice


def test_records_quickbase_refuses_stop_the_build(tmp_path: Path, capsys: Any) -> None:
    realm = FakeRealm()

    def refuse_inspections(request: httpx.Request, body: Any) -> httpx.Response | None:
        if request.url.path == "/v1/records" and realm.tables[body["to"]]["name"] == "Inspections":
            meta = {"createdRecordIds": [], "lineErrors": {"1": ["Incorrect value for field 9"]}}
            return httpx.Response(207, json={"data": [], "metadata": meta})
        return None

    realm.hooks.append(refuse_inspections)
    assert run_builder(realm, tmp_path, "--yes") == 2
    err = capsys.readouterr().err
    assert "QB_LINE_ERRORS" in err and "Incorrect value for field 9" in err and next(iter(realm.apps)) in err


def test_a_redirect_is_never_followed(tmp_path: Path, capsys: Any) -> None:
    realm = FakeRealm()
    realm.hooks.append(
        lambda request, body: httpx.Response(302, headers={"location": "https://elsewhere.invalid/v1/apps"})
    )
    assert run_builder(realm, tmp_path, "--yes") == 2
    assert "QB_REDIRECT" in capsys.readouterr().err
    assert len(realm.requests) == 1


def test_requests_stay_within_the_rate_limit() -> None:
    realm, ft = FakeRealm(), FakeTime()
    times: list[float] = []
    realm.hooks.append(lambda request, body: times.append(ft.now))  # type: ignore[func-returns-value]
    client = qb.BuilderClient(
        REALM, TOKEN, transport=httpx.MockTransport(realm), requests_per_10s=3, clock=ft.clock, sleep=ft.sleep
    )
    app_id = client.call("createApp", body={"name": "x"})["id"]
    for n in range(8):
        client.call("createTable", params={"appId": app_id}, body={"name": f"T{n}"})
    assert len(times) == 9
    assert all(times[i + 3] - times[i] >= 10.0 for i in range(len(times) - 3))


# -- inputs and outputs ---------------------------------------------------------------------------------------


def test_existing_outputs_refuse_the_build_before_any_request(tmp_path: Path, capsys: Any) -> None:
    (tmp_path / "local").mkdir()
    keep = tmp_path / "local" / "quickbase-live.yml"
    keep.write_text("my edits", encoding="utf-8")
    realm = FakeRealm()
    assert run_builder(realm, tmp_path, "--yes") == 2
    assert "OUT_EXISTS" in capsys.readouterr().err
    assert realm.requests == [] and keep.read_text(encoding="utf-8") == "my edits"
    assert run_builder(realm, tmp_path, "--yes", "--force") == 0
    assert load_mapping(keep).mapping_id == "quickbase-live"


def test_the_token_comes_from_the_named_variable_or_a_file_never_the_command_line(
    tmp_path: Path, capsys: Any
) -> None:
    realm = FakeRealm()
    assert run_builder(realm, tmp_path, "--yes", env={}) == 2
    assert "QB_TOKEN_MISSING" in capsys.readouterr().err and realm.requests == []

    token_file = tmp_path / "qb_token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    assert run_builder(realm, tmp_path / "a", "--yes", "--token-file", token_file.as_posix(), env={}) == 0
    assert (
        run_builder(FakeRealm(), tmp_path / "b", "--yes", "--token-env", "OTHER", env={"OTHER": TOKEN}) == 0
    )
    with pytest.raises(SystemExit):
        qb.parse_args(["--realm", REALM, "--token-env", "X", "--token-file", "f"])
    with pytest.raises(SystemExit):
        qb.parse_args(["--realm", REALM, "--token", TOKEN])
