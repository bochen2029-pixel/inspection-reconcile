"""capture-quickbase (SPEC §12.5) against a mock Quickbase app built from the S16 export, then normalize and
assess (step C2, "Checkpoint C-mock"). Expected verdicts come from S01's run, never from capture code."""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import scenario_support as ss
from conftest import POLICIES, REPO, SCENARIOS

from inspection_reconcile.adapters.mapping import Mapping, load_mapping
from inspection_reconcile.adapters.qb_capture import (
    CaptureConfig,
    capture,
    load_capture_config,
    make_client,
    parse_capture_config,
)
from inspection_reconcile.adapters.qb_client import LOGGER_NAME, QuickbaseClient
from inspection_reconcile.adapters.qb_export import normalize
from inspection_reconcile.errors import RunError

S16 = SCENARIOS / "S16-quickbase-clean" / "export"
DEMO = REPO / "mappings" / "quickbase-demo.yml"
TOKEN = "b7syn_CAPTURE_TOKEN_fedcba9876543210"  # synthetic; must never be written anywhere
REALM = "synthetic.quickbase.invalid"
NOW = datetime(2026, 10, 1, 18, 0, 0, tzinfo=UTC)

Hook = Callable[[httpx.Request, Any], "httpx.Response | None"]


class MockApp:
    """A tiny Quickbase: getFields, runQuery (EX/GT conditions, sort by Record ID#, top) and downloadFile."""

    def __init__(self, export: Path = S16) -> None:
        manifest = json.loads((export / "capture-manifest.json").read_text(encoding="utf-8"))
        self.export = export
        self.fields: dict[str, list[dict[str, Any]]] = {}
        self.records: dict[str, list[dict[str, Any]]] = {}
        for role, entry in manifest["tables"].items():
            table_id = entry["table_id"]
            self.fields[table_id] = json.loads(
                (export / "tables" / role / "fields.json").read_text(encoding="utf-8")
            )
            page = json.loads((export / "tables" / role / "page-0001.json").read_text(encoding="utf-8"))
            self.records[table_id] = page["data"]
        self.hooks: list[Hook] = []
        self.requests: list[httpx.Request] = []
        self.queries: list[dict[str, Any]] = []
        self.query_count: dict[str, int] = {}
        self.short_pages: dict[tuple[str, int], int] = {}
        self.total_bias: dict[tuple[str, int], int] = {}
        self.drop_field: dict[str, int] = {}
        self.refuse_gt = False  # a source that rejects the {3.GT.n} comparison (SPEC §12.1, unverified)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content) if request.content else None
        for hook in self.hooks:
            response = hook(request, body)
            if response is not None:
                return response
        path = request.url.path
        if request.method == "GET" and path == "/v1/fields":
            return httpx.Response(200, json=self.fields[request.url.params["tableId"]])
        if request.method == "POST" and path == "/v1/records/query":
            self.queries.append(body)
            return self._query(body)
        m = re.fullmatch(r"/v1/files/(\w+)/(\d+)/(\d+)/(\d+)", path)
        if request.method == "GET" and m:
            return self._file(m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        return httpx.Response(404, json={"message": "Not Found", "description": path})

    def _query(self, body: dict[str, Any]) -> httpx.Response:
        table_id, select, top = body["from"], body["select"], body["options"]["top"]
        skip = body["options"].get("skip", 0)
        where = body.get("where") or ""
        if self.refuse_gt and ".GT." in where:
            return httpx.Response(400, json={"message": "Bad Request", "description": "Invalid query"})
        conditions = re.findall(r"\{(\d+)\.(EX|GT)\.([^}]*)\}", where)

        def matches(record: dict[str, Any]) -> bool:
            for fid, op, value in conditions:
                cell = record.get(fid, {}).get("value")
                if op == "EX" and str(cell) != value.strip("'"):
                    return False
                if op == "GT" and not (isinstance(cell, int) and cell > int(value)):
                    return False
            return True

        hits = sorted((r for r in self.records[table_id] if matches(r)), key=lambda r: r["3"]["value"])
        n = self.query_count[table_id] = self.query_count.get(table_id, 0) + 1
        page = hits[skip : skip + min(top, self.short_pages.get((table_id, n), top))]
        labels = {f["id"]: f for f in self.fields[table_id]}
        shown = [fid for fid in select if self.drop_field.get(table_id) != fid]
        return httpx.Response(
            200,
            json={
                "data": [{str(fid): rec[str(fid)] for fid in shown if str(fid) in rec} for rec in page],
                "fields": [
                    {"id": fid, "label": labels[fid]["label"], "type": labels[fid]["fieldType"]}
                    for fid in shown
                ],
                "metadata": {
                    "totalRecords": len(hits) + self.total_bias.get((table_id, n), 0),
                    "numRecords": len(page),
                    "numFields": len(shown),
                    "skip": 0,
                    "top": top,
                },
            },
        )

    def _file(self, table_id: str, rid: int, fid: int, version: int) -> httpx.Response:
        record = next(r for r in self.records[table_id] if r["3"]["value"] == rid)
        name = next(
            v["fileName"] for v in record[str(fid)]["value"]["versions"] if v["versionNumber"] == version
        )
        data = (self.export / "files" / table_id / str(rid) / str(fid) / f"v{version}" / name).read_bytes()
        return httpx.Response(
            200,
            content=base64.b64encode(data),
            headers={"content-disposition": f'attachment; filename="{name}"'},
        )


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


CONFIG: dict[str, Any] = {
    "schema": "inspection-reconcile/qb-capture/v1",
    "realm_hostname": REALM,
    "app_id": "bsyn00000",
    "token_env": "QB_USER_TOKEN",
    "page_size": 15,
    "capture_files": True,
    "scope": {
        "scope_revision": "S1",
        "accepted": {
            "by": "Synthetic Client Records Dept",
            "at": "2026-08-15T14:00:00Z",
            "reference": "SYN-SCOPE-S1",
        },
    },
    "limits": {
        "max_file_bytes": 104857600,
        "requests_per_10s": 90,
        "max_attempts": 3,
        "max_retry_wait_s": 60,
        "max_pages": 100,
    },
    "operator_attestation": {
        "full_read_access": True,
        "attested_by": "Synthetic Operator",
        "note": "Synthetic mock app; the token's role reads every record",
    },
}


def config_with(**changes: Any) -> CaptureConfig:
    raw = json.loads(json.dumps(CONFIG))
    for key, value in changes.items():
        node = raw
        parts = key.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return parse_capture_config(raw, "qb-capture.yml")


@pytest.fixture(scope="module")
def mapping() -> Mapping:
    return load_mapping(DEMO)


@pytest.fixture
def ft() -> FakeTime:
    return FakeTime()


@pytest.fixture
def app() -> MockApp:
    return MockApp()


@pytest.fixture
def client(app: MockApp, ft: FakeTime) -> Iterator[QuickbaseClient]:
    c = QuickbaseClient(
        REALM,
        TOKEN,
        user_agent="inspection-reconcile/0.1.0",
        transport=httpx.MockTransport(app),
        max_attempts=3,
        clock=ft.clock,
        sleep=ft.sleep,
    )
    yield c
    c.close()


def run_capture(
    client: QuickbaseClient, mapping: Mapping, out: Path, config: CaptureConfig | None = None
) -> dict:
    return capture(client, config or config_with(), mapping, out, now=lambda: NOW)


def assess_export(export: Path, mapping: Mapping, tmp_path: Path) -> Any:
    snapshot = tmp_path / "snapshot"
    normalize(export, mapping, snapshot)
    return ss.run_snapshot(snapshot, POLICIES / "north-creek-demo.yml")


def triples(assessment: Any) -> set[tuple[str, str, str]]:
    return {(f.key, f.outcome, f.reason) for f in assessment.findings}


# -- Checkpoint C-mock: capture -> normalize -> assess gives S01's findings -----------------------------------


def test_capture_normalize_assess_gives_the_s01_findings(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.short_pages[("bsyn00002", 2)] = 7  # an "intelligent" short page: fewer records than top
    export = tmp_path / "export"
    manifest = run_capture(client, mapping, export)

    obligations = manifest["tables"]["obligations"]
    assert obligations["where"] == "{7.EX.'NC-001'}"
    assert (obligations["pages"], obligations["total_records"], obligations["retrieved"]) == (4, 40, 40)
    assert manifest["tables"]["inspections"]["pages"] == 5  # 15, 7, 15, 3, then the empty page
    assert all(t["two_pass"] == "stable" for t in manifest["tables"].values())
    for name in ("inspections", "artifacts", "approvals", "evidence_files"):
        assert manifest["datasets"][name] == {
            "coverage": "complete_for_declared_scope",
            "basis": ["query_total_matched", "two_pass_stable", "operator_attestation"],
            "consistency": "stable_verified",
        }, name
    assert len(manifest["files"]) == 80 and all(f["status"] == "captured" for f in manifest["files"])
    assert (export / "tables" / "approvals" / "pass2.json").is_file()

    result = assess_export(export, mapping, tmp_path)
    s01 = ss.run_scenario("S01-clean")
    assert result.status == "READY_FOR_REVIEW"
    assert triples(result) == triples(s01)
    # D-005: the identities differ only because the declared coverage basis differs.
    assert result.evaluation_id != s01.evaluation_id


def test_queries_use_keyset_pagination_on_record_id(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    run_capture(client, mapping, tmp_path / "export")
    obligation_queries = [q for q in app.queries if q["from"] == "bsyn00001" and q["select"] != [2, 3]]
    assert [q["where"] for q in obligation_queries] == [
        "{7.EX.'NC-001'}AND{3.GT.0}",
        "{7.EX.'NC-001'}AND{3.GT.15}",
        "{7.EX.'NC-001'}AND{3.GT.30}",
        "{7.EX.'NC-001'}AND{3.GT.40}",
    ]
    for q in app.queries:
        assert q["sortBy"] == [{"fieldId": 3, "order": "ASC"}]
        assert q["options"] == {"skip": 0, "top": 15}
    assert obligation_queries[0]["select"] == [2, 3, 6, 7, 8, 9, 10]
    child = [q for q in app.queries if q["from"] == "bsyn00003"]
    assert (
        child[0]["where"] == "{3.GT.0}"
    )  # dependent tables are read in full, never by their own project field
    pass2 = [q for q in app.queries if q["select"] == [2, 3]]
    assert {q["from"] for q in pass2} == {"bsyn00001", "bsyn00002", "bsyn00003", "bsyn00004"}
    first_pass2 = app.queries.index(pass2[0])
    assert all(q["select"] != [2, 3] for q in app.queries[:first_pass2])  # pass 2 starts after every pass 1


def test_the_captured_bytes_are_the_decoded_files(
    tmp_path: Path, client: QuickbaseClient, mapping: Mapping
) -> None:
    export = tmp_path / "export"
    manifest = run_capture(client, mapping, export)
    s16 = {
        (f["record_id"], f["version"]): f
        for f in json.loads((S16 / "capture-manifest.json").read_text())["files"]
    }
    for entry in manifest["files"]:
        original = s16[(entry["record_id"], entry["version"])]
        assert (entry["sha256"], entry["bytes"]) == (original["sha256"], original["bytes"])
        assert (export / entry["path"]).read_bytes() == (S16 / original["path"]).read_bytes()


# -- accounting, change detection and coverage -----------------------------------------------------------------


def test_inconsistent_page_totals_make_the_read_incomplete(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.total_bias[("bsyn00003", 2)] = 5  # the second artifacts page reports a total that does not account
    manifest = run_capture(client, mapping, tmp_path / "export")
    assert manifest["tables"]["artifacts"]["two_pass"] == "changed"
    assert manifest["datasets"]["artifacts"] == {
        "coverage": "partial",
        "basis": ["pagination_incomplete"],
        "consistency": "changed_during_capture",
    }
    assert manifest["datasets"]["evidence_files"]["coverage"] == "partial"
    assert manifest["datasets"]["inspections"]["coverage"] == "complete_for_declared_scope"


def test_a_change_seen_by_pass_2_gives_changed_during_capture(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    def touch(request: httpx.Request, body: Any) -> None:
        if body and body.get("from") == "bsyn00004" and body.get("select") == [2, 3]:
            app.records["bsyn00004"][0]["2"] = {"value": "2026-10-01T12:30:00Z"}
        return None

    app.hooks.append(touch)
    export = tmp_path / "export"
    manifest = run_capture(client, mapping, export)
    assert manifest["tables"]["approvals"]["two_pass"] == "changed"
    assert manifest["datasets"]["approvals"] == {
        "coverage": "unverified",
        "basis": ["query_total_matched", "operator_attestation"],
        "consistency": "changed_during_capture",
    }
    result = assess_export(export, mapping, tmp_path)
    assert result.status == "UNKNOWN"
    by_key = {f.key: f for f in result.findings}
    assert (by_key["R1:dataset:approvals"].outcome, by_key["R1:dataset:approvals"].reason) == (
        "UNKNOWN",
        "COVERAGE_UNVERIFIED",
    )


def test_without_attestation_the_coverage_is_unverified(
    tmp_path: Path, client: QuickbaseClient, mapping: Mapping
) -> None:
    config = config_with(operator_attestation__full_read_access=False)
    manifest = run_capture(client, mapping, tmp_path / "export", config)
    assert manifest["datasets"]["inspections"] == {
        "coverage": "unverified",
        "basis": ["query_total_matched", "two_pass_stable"],
        "consistency": "stable_verified",
    }


def test_the_obligations_table_must_be_complete_and_stable(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.total_bias[("bsyn00001", 2)] = 1
    out = tmp_path / "export"
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, out)
    assert info.value.code == "QB_SCOPE_UNSTABLE"
    assert not any(p.is_file() for p in out.rglob("*"))


def test_exceeding_max_pages_is_a_run_error(
    tmp_path: Path, client: QuickbaseClient, mapping: Mapping
) -> None:
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, tmp_path / "export", config_with(limits__max_pages=2))
    assert info.value.code == "QB_MAX_PAGES"


# -- files: scope, size limit and skipped capture --------------------------------------------------------------


def test_files_over_the_size_limit_are_not_captured(
    tmp_path: Path, client: QuickbaseClient, mapping: Mapping
) -> None:
    limit = 700
    manifest = run_capture(client, mapping, tmp_path / "export", config_with(limits__max_file_bytes=limit))
    sizes = {
        f["record_id"]: f["bytes"] for f in json.loads((S16 / "capture-manifest.json").read_text())["files"]
    }
    for entry in manifest["files"]:
        expected = "too_large" if sizes[entry["record_id"]] > limit else "captured"
        assert entry["status"] == expected
    assert any(f["status"] == "too_large" for f in manifest["files"])
    assert manifest["datasets"]["evidence_files"] == {
        "coverage": "partial",
        "basis": ["extraction_interrupted"],
        "consistency": "stable_verified",
    }


def test_skipping_file_capture_downloads_nothing(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    manifest = run_capture(client, mapping, tmp_path / "export", config_with(capture_files=False))
    assert {f["status"] for f in manifest["files"]} == {"not_captured"}
    assert not any("/files/" in r.url.path for r in app.requests)
    assert manifest["datasets"]["evidence_files"]["basis"] == ["attachment_capture_skipped"]


def test_out_of_scope_artifacts_are_listed_but_not_downloaded(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    obligation = json.loads(json.dumps(app.records["bsyn00001"][0]))
    obligation["3"] = {"value": 41}
    obligation["6"] = {"value": "O-901"}
    obligation["7"] = {"value": "NC-002"}  # another project: the scope filter excludes it
    inspection = json.loads(json.dumps(app.records["bsyn00002"][0]))
    inspection.update({"3": {"value": 141}, "6": {"value": "INS-901"}, "9": {"value": 41}})
    artifact = json.loads(json.dumps(app.records["bsyn00003"][0]))
    artifact.update({"3": {"value": 281}, "6": {"value": "ART-901-R"}, "9": {"value": 141}})
    app.records["bsyn00001"].append(obligation)
    app.records["bsyn00002"].append(inspection)
    app.records["bsyn00003"].append(artifact)

    manifest = run_capture(client, mapping, tmp_path / "export")
    entry = next(f for f in manifest["files"] if f["record_id"] == 281)
    assert entry["status"] == "out_of_scope" and entry["path"] is None
    assert not any(r.url.path.startswith("/v1/files/bsyn00003/281/") for r in app.requests)
    assert manifest["tables"]["obligations"]["retrieved"] == 40
    assert manifest["datasets"]["evidence_files"]["coverage"] == "complete_for_declared_scope"


# -- transport failures ----------------------------------------------------------------------------------------


def test_a_429_is_waited_out(
    tmp_path: Path, app: MockApp, ft: FakeTime, client: QuickbaseClient, mapping: Mapping
) -> None:
    state = {"sent": False}

    def once(request: httpx.Request, body: Any) -> httpx.Response | None:
        if request.url.path == "/v1/records/query" and not state["sent"]:
            state["sent"] = True
            return httpx.Response(429, headers={"retry-after": "2"}, json={"message": "Too Many Requests"})
        return None

    app.hooks.append(once)
    manifest = run_capture(client, mapping, tmp_path / "export")
    assert 2.0 in ft.sleeps
    assert manifest["datasets"]["inspections"]["coverage"] == "complete_for_declared_scope"


def test_exhausted_retries_fail_the_capture_and_leave_nothing(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.hooks.append(
        lambda request, body: httpx.Response(503) if request.url.path == "/v1/records/query" else None
    )
    out = tmp_path / "export"
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, out)
    assert info.value.code == "QB_RETRIES_EXHAUSTED"
    assert not any(p.is_file() for p in out.rglob("*"))


@pytest.mark.parametrize("status", [401, 403])
def test_permission_errors_fail_the_capture(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping, status: int
) -> None:
    app.hooks.append(
        lambda request, body: (
            httpx.Response(status, json={"message": "Access denied"})
            if request.url.path == "/v1/fields"
            else None
        )
    )
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, tmp_path / "export")
    assert info.value.code == "QB_PERMISSION"
    assert "getFields" in info.value.message


def test_a_missing_selected_field_is_a_run_error(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.drop_field["bsyn00002"] = 13
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, tmp_path / "export")
    assert info.value.code == "FIELD_NOT_RETURNED"
    assert "13" in info.value.message


def test_a_schema_mismatch_is_a_run_error(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    for f in app.fields["bsyn00002"]:
        if f["id"] == 8:
            f["fieldType"] = "text"
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, tmp_path / "export")
    assert info.value.code == "QB_SCHEMA_MISMATCH"


def test_a_failed_download_marks_the_file_and_the_evidence(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.hooks.append(
        lambda request, body: (
            httpx.Response(404, json={"message": "No such version"})
            if request.url.path == "/v1/files/bsyn00003/201/13/1"
            else None
        )
    )
    manifest = run_capture(client, mapping, tmp_path / "export")
    assert next(f for f in manifest["files"] if f["record_id"] == 201)["status"] == "error"
    assert manifest["datasets"]["evidence_files"]["coverage"] == "partial"


# -- the token never leaks -------------------------------------------------------------------------------------


def test_the_token_is_absent_from_every_written_file_and_the_logs(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    app.total_bias[("bsyn00003", 2)] = 3  # provoke warnings too
    export = tmp_path / "export"
    run_capture(client, mapping, export)
    files = [p for p in export.rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert TOKEN.encode() not in path.read_bytes(), path
    assert caplog.records
    assert TOKEN not in caplog.text


# -- configuration ---------------------------------------------------------------------------------------------


def test_the_capture_config_loads(tmp_path: Path) -> None:
    path = tmp_path / "qb-capture.yml"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")  # JSON is YAML
    config = load_capture_config(path)
    assert (config.realm_hostname, config.app_id, config.page_size, config.max_pages) == (
        REALM,
        "bsyn00000",
        15,
        100,
    )
    assert config.scope_accepted == CONFIG["scope"]["accepted"]


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "inspection-reconcile/qb-capture/v2"},
        {"realm_hostname": "bad host"},
        {"app_id": "bad/app"},
        {"token_env": "1BAD"},
        {"page_size": 0},
        {"capture_files": "yes"},
        {"limits__requests_per_10s": 101},
        {"limits__max_attempts": 0},
        {"limits__max_retry_wait_s": 60.5},
        {"scope__accepted__at": "yesterday"},
        {"operator_attestation__full_read_access": "true"},
        {"token": "never-in-config"},
    ],
)
def test_bad_capture_configs_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(RunError) as info:
        config_with(**changes)
    assert info.value.code == "CONFIG_INVALID"


def test_make_client_reads_the_token_only_from_the_environment(app: MockApp) -> None:
    config = config_with()
    with pytest.raises(RunError) as info:
        make_client(config, user_agent="inspection-reconcile/0.1.0", environ={})
    assert info.value.code == "QB_TOKEN_MISSING"
    client = make_client(
        config,
        user_agent="inspection-reconcile/0.1.0",
        environ={"QB_USER_TOKEN": TOKEN},
        transport=httpx.MockTransport(app),
    )
    try:
        assert client.get_fields("bsyn00001")
        assert app.requests[-1].headers["Authorization"] == f"QB-USER-TOKEN {TOKEN}"
        assert TOKEN not in repr(client)
    finally:
        client.close()


def test_the_output_directory_must_be_absent_or_empty(
    tmp_path: Path, client: QuickbaseClient, mapping: Mapping
) -> None:
    out = tmp_path / "export"
    out.mkdir()
    (out / "stray").write_text("x", encoding="utf-8")
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, out)
    assert info.value.code == "OUT_NOT_EMPTY"


# -- SPEC §12.1: skip paging when the source refuses {3.GT.n} --------------------------------------------------


def test_a_refused_keyset_comparison_falls_back_to_skip_paging(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.refuse_gt = True
    export = tmp_path / "export"
    manifest = run_capture(client, mapping, export)
    assert {t["paging"] for t in manifest["tables"].values()} == {"skip"}
    assert all(t["two_pass"] == "stable" for t in manifest["tables"].values())
    assert manifest["datasets"]["inspections"]["coverage"] == "complete_for_declared_scope"
    accepted = [q for q in app.queries if ".GT." not in (q.get("where") or "")]
    obligations = [q for q in accepted if q["from"] == "bsyn00001" and q["select"] != [2, 3]]
    assert [q["options"]["skip"] for q in obligations] == [0, 15, 30, 40]
    assert {q["where"] for q in obligations} == {"{7.EX.'NC-001'}"}
    assert all("where" not in q for q in accepted if q["from"] == "bsyn00003")
    result = assess_export(export, mapping, tmp_path)
    assert result.status == "READY_FOR_REVIEW"
    assert triples(result) == triples(ss.run_scenario("S01-clean"))


def test_skip_paging_detects_an_insert_during_the_read(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.refuse_gt = True
    extra = json.loads(json.dumps(app.records["bsyn00002"][0]))
    extra.update({"3": {"value": 150}, "6": {"value": "INS-950"}})
    state = {"skip_pages": 0}

    def insert_after_first_page(request: httpx.Request, body: Any) -> None:
        if body and body.get("from") == "bsyn00002" and "where" not in body and body["select"] != [2, 3]:
            state["skip_pages"] += 1
            if state["skip_pages"] == 2:
                app.records["bsyn00002"].append(extra)
        return None

    app.hooks.append(insert_after_first_page)
    manifest = run_capture(client, mapping, tmp_path / "export")
    assert manifest["tables"]["inspections"]["paging"] == "skip"
    assert manifest["tables"]["inspections"]["two_pass"] == "changed"
    assert manifest["datasets"]["inspections"] == {
        "coverage": "partial",
        "basis": ["pagination_incomplete"],
        "consistency": "changed_during_capture",
    }


def test_skip_paging_detects_a_delete_during_the_read(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.refuse_gt = True
    state = {"skip_pages": 0}

    def delete_after_first_page(request: httpx.Request, body: Any) -> None:
        if body and body.get("from") == "bsyn00002" and "where" not in body and body["select"] != [2, 3]:
            state["skip_pages"] += 1
            if state["skip_pages"] == 2:  # a record already read disappears: later offsets shift
                app.records["bsyn00002"] = [r for r in app.records["bsyn00002"] if r["3"]["value"] != 101]
        return None

    app.hooks.append(delete_after_first_page)
    manifest = run_capture(client, mapping, tmp_path / "export")
    assert manifest["tables"]["inspections"]["paging"] == "skip"
    assert manifest["datasets"]["inspections"] == {
        "coverage": "partial",
        "basis": ["pagination_incomplete"],
        "consistency": "changed_during_capture",
    }


def test_skip_paging_reads_through_short_intelligent_pages(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.refuse_gt = True
    app.short_pages[("bsyn00002", 2)] = 7  # the refused keyset query is not counted by the mock
    manifest = run_capture(client, mapping, tmp_path / "export")
    inspections = manifest["tables"]["inspections"]
    assert (inspections["paging"], inspections["pages"], inspections["retrieved"]) == ("skip", 5, 40)
    skips = [
        q["options"]["skip"]
        for q in app.queries
        if q["from"] == "bsyn00002" and "where" not in q and q["select"] != [2, 3]
    ]
    assert skips == [0, 15, 22, 37, 40]
    assert manifest["datasets"]["inspections"]["coverage"] == "complete_for_declared_scope"


def test_a_refusal_after_the_first_page_is_a_run_error(
    tmp_path: Path, app: MockApp, client: QuickbaseClient, mapping: Mapping
) -> None:
    app.hooks.append(
        lambda request, body: (
            httpx.Response(400, json={"message": "Bad Request"})
            if body and body.get("where") == "{7.EX.'NC-001'}AND{3.GT.15}"
            else None
        )
    )
    with pytest.raises(RunError) as info:
        run_capture(client, mapping, tmp_path / "export")
    assert info.value.code == "QB_HTTP_ERROR"


def test_keyset_is_recorded_when_it_works(tmp_path: Path, client: QuickbaseClient, mapping: Mapping) -> None:
    manifest = run_capture(client, mapping, tmp_path / "export")
    assert {t["paging"] for t in manifest["tables"].values()} == {"keyset"}
