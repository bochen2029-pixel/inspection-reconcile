"""Regression tests from the adapter review: a re-delivered record, a grammar-valid unmapped label and a
file version without a files[] entry. Expected verdicts come from SPEC §12.2-§12.5 and AM-3/AM-4, never from src/."""

import json
import shutil
from pathlib import Path

import httpx
import pytest
from test_qb_capture import DEMO, NOW, REALM, S16, TOKEN, FakeTime, MockApp, assess_export, config_with

from inspection_reconcile.adapters.mapping import load_mapping
from inspection_reconcile.adapters.qb_capture import capture
from inspection_reconcile.adapters.qb_client import QuickbaseClient
from inspection_reconcile.adapters.qb_export import normalize
from inspection_reconcile.errors import RunError

MAPPING = load_mapping(DEMO)
INSPECTIONS, ARTIFACTS = "bsyn00002", "bsyn00003"


def capture_with(app: MockApp, tmp_path: Path) -> tuple[Path, dict]:
    ft = FakeTime()
    transport = httpx.MockTransport(app)
    with QuickbaseClient(
        REALM, TOKEN, user_agent="t/1", transport=transport, max_attempts=3, clock=ft.clock, sleep=ft.sleep
    ) as client:
        manifest = capture(client, config_with(), MAPPING, tmp_path / "export", now=lambda: NOW)
    return tmp_path / "export", manifest


def copy_export(tmp_path: Path) -> Path:
    export = tmp_path / "export"
    shutil.copytree(S16, export)
    return export


def edit_json(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def duplicate_keys(assessment) -> list[str]:
    return [f.key for f in assessment.findings if f.reason == "DUPLICATE_KEY"]


def test_a_re_delivered_record_does_not_become_a_duplicate_key(tmp_path: Path) -> None:
    """A keyset page re-delivers an already-read record. The capture reports the failed read (partial), so the
    repeat is a capture artifact: no DUPLICATE_KEY FAIL, and the status is UNKNOWN, not BLOCKED."""
    app = MockApp()
    plain = app._query

    def redeliver(body):
        response = plain(body)
        if body["from"] == ARTIFACTS and body["select"] != [2, 3] and app.query_count[ARTIFACTS] == 2:
            payload = json.loads(response.content)
            first = min(app.records[ARTIFACTS], key=lambda r: r["3"]["value"])
            payload["data"].insert(0, {k: v for k, v in first.items() if int(k) in body["select"]})
            payload["metadata"]["numRecords"] += 1
            return httpx.Response(200, json=payload)
        return response

    app._query = redeliver
    export, manifest = capture_with(app, tmp_path)
    assert manifest["datasets"]["artifacts"]["coverage"] == "partial"
    assessment = assess_export(export, MAPPING, tmp_path)
    assert duplicate_keys(assessment) == []
    assert assessment.status == "UNKNOWN"


def test_a_source_that_ignores_skip_leaves_no_duplicate_key(tmp_path: Path) -> None:
    """The stall guard's second, identical skip page stays in the export (retrieved 30 of 40); its 15 records
    were already read, so they must not become 15 DUPLICATE_KEY FAILs."""
    app = MockApp()
    app.refuse_gt = True

    def ignore_skip(request, body):
        if body and body.get("from") == INSPECTIONS and "where" not in body:
            body["options"]["skip"] = 0
        return None

    app.hooks.append(ignore_skip)
    export, manifest = capture_with(app, tmp_path)
    assert manifest["tables"]["inspections"]["retrieved"] == 30
    assessment = assess_export(export, MAPPING, tmp_path)
    assert duplicate_keys(assessment) == []
    assert assessment.status == "UNKNOWN"


def test_a_repeated_record_id_in_a_read_declared_clean_is_refused(tmp_path: Path) -> None:
    export = copy_export(tmp_path)
    edit_json(export / "tables" / "artifacts" / "page-0001.json", lambda p: p["data"].append(p["data"][0]))

    def recount(m):
        m["tables"]["artifacts"]["retrieved"] += 1
        m["tables"]["artifacts"]["total_records"] += 1

    edit_json(export / "capture-manifest.json", recount)
    with pytest.raises(RunError) as info:
        normalize(export, MAPPING, tmp_path / "snapshot")
    assert info.value.code == "EXPORT_INVALID"


def test_an_unmapped_label_that_satisfies_the_grammar_is_still_unmapped(tmp_path: Path) -> None:
    """§12.3: a label missing from the map is never guessed; R0 reports UNMAPPED_VALUE even for "photo"."""
    export = copy_export(tmp_path)

    def relabel(page):
        record = next(r for r in page["data"] if r["6"]["value"] == "ART-007-P")
        record["12"]["value"] = "photo"  # the map knows "Photo" only

    edit_json(export / "tables" / "artifacts" / "page-0001.json", relabel)
    assessment = assess_export(export, MAPPING, tmp_path)
    finding = assessment.by_key()["R0:artifact:ART-007-P@1#UNMAPPED_VALUE"]
    assert (finding.outcome, finding.required) == ("UNKNOWN", True)
    assert assessment.by_key()["R2:obligation:O-007"].outcome == "NOT_EVALUATED"
    assert assessment.status == "UNKNOWN"


def test_an_unexpected_json_type_is_never_guessed(tmp_path: Path) -> None:
    """AM-7 (review F4): a number where the field type promises text is listed as unmapped, so R0 reports it. Its
    text "123" satisfies the ID grammar, and before AM-7 it read as a wrong asset and failed R3."""
    export = copy_export(tmp_path)

    def retype(page):
        record = next(r for r in page["data"] if r["6"]["value"] == "INS-005")
        record["11"]["value"] = 123  # asset_id is a text field (fid 11)

    edit_json(export / "tables" / "inspections" / "page-0001.json", retype)
    assessment = assess_export(export, MAPPING, tmp_path)
    finding = assessment.by_key()["R0:inspection:INS-005@1#UNMAPPED_VALUE"]
    assert (finding.outcome, finding.required) == ("UNKNOWN", True)
    assert assessment.by_key()["R3:obligation:O-005"].outcome == "NOT_EVALUATED"
    assert assessment.status == "UNKNOWN"


def test_a_file_version_without_a_files_entry_is_refused(tmp_path: Path) -> None:
    """The export's file list is closed-world (AM-4): every artifact file has an entry with some status."""
    export = copy_export(tmp_path)
    edit_json(
        export / "capture-manifest.json",
        lambda m: m.update(files=[f for f in m["files"] if f["record_id"] != 202]),
    )
    with pytest.raises(RunError) as info:
        normalize(export, MAPPING, tmp_path / "snapshot")
    assert info.value.code == "EXPORT_INVALID"
    assert "files[]" in info.value.message
