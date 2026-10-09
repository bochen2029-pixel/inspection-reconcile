"""normalize (SPEC §12.2, §12.3): export -> canonical snapshot, every conversion, files, and equivalence (AC-14)."""

from __future__ import annotations

import csv
import io
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import scenario_support as ss
from conftest import POLICIES, REPO, SCENARIOS

from inspection_reconcile import grammar
from inspection_reconcile.adapters.mapping import FieldMap, Mapping, load_mapping
from inspection_reconcile.adapters.qb_export import (
    convert_value,
    file_relative_path,
    json_text,
    latest_version,
    normalize,
    parse_export_manifest,
    placeholder,
    reference_rid,
    sanitize_file_name,
)
from inspection_reconcile.errors import RunError

DEMO = REPO / "mappings" / "quickbase-demo.yml"
POLICY = POLICIES / "north-creek-demo.yml"
S16 = SCENARIOS / "S16-quickbase-clean" / "export"
S17 = SCENARIOS / "S17-quickbase-unmapped-value" / "export"


@pytest.fixture(scope="module")
def mapping() -> Mapping:
    return load_mapping(DEMO)


def copy_export(tmp_path: Path, source: Path = S16) -> Path:
    target = tmp_path / "export"
    shutil.copytree(source, target)
    return target


def edit_json(path: Path, fn: Callable[[Any], None]) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def edit_record(export: Path, role: str, rid: int, fid: int, value: Any) -> None:
    def apply(page: dict[str, Any]) -> None:
        for record in page["data"]:
            if record["3"]["value"] == rid:
                record[str(fid)] = {"value": value}
                return
        raise AssertionError(f"rid {rid} not found in {role}")

    edit_json(export / "tables" / role / "page-0001.json", apply)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))


def run(out: Path) -> Any:
    return ss.run_snapshot(out, POLICY)


# -- equivalence and the oracle --------------------------------------------------------------------------------


def test_normalized_s16_has_the_identities_of_s01(tmp_path: Path, mapping: Mapping) -> None:
    """AC-14: the canonical fixture and the equivalent export give identical identities."""
    out = tmp_path / "snapshot"
    normalize(S16, mapping, out)
    s16 = run(out)
    s01 = ss.run_scenario("S01-clean")
    assert s16.evaluation_id == s01.evaluation_id
    assert s16.assessment_semantic_sha256 == s01.assessment_semantic_sha256
    assert s16.status == "READY_FOR_REVIEW"
    assert ss.mismatches("S16-quickbase-clean", s16) == []


def test_normalized_s17_matches_its_oracle(tmp_path: Path, mapping: Mapping) -> None:
    out = tmp_path / "snapshot"
    normalize(S17, mapping, out)
    assert ss.mismatches("S17-quickbase-unmapped-value", run(out)) == []
    norm = json.loads((out / "normalization.json").read_text(encoding="utf-8"))
    assert norm["unmapped_values"] == [
        {
            "dataset": "inspections",
            "source": "table=bsyn00002;rid=106",
            "field": "completion_status",
            "raw": "Completed - pending QA",
        }
    ]
    rows = {r["inspection_id"]: r for r in read_csv_rows(out / "inspections.csv")}
    assert rows["INS-006"]["completion_status"] == "Completed - pending QA"


def test_normalize_is_deterministic_and_writes_lf_bytes(tmp_path: Path, mapping: Mapping) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    normalize(S16, mapping, a)
    normalize(S16, mapping, b)
    files_a = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    assert files_a == files_b
    for name in files_a:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
        if name.endswith((".csv", ".json")):
            assert b"\r\n" not in (a / name).read_bytes(), name
    assert not any(".tmp." in name for name in files_a)


def test_the_snapshot_layout_and_manifest(tmp_path: Path, mapping: Mapping) -> None:
    out = tmp_path / "snapshot"
    normalize(S16, mapping, out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == "NC-001-S16-quickbase-clean"
    assert manifest["source"] == {
        "system": "quickbase",
        "description": "North Creek synthetic Quickbase-shaped export",
    }
    assert manifest["scope"]["file"] == "scope.csv"
    assert manifest["scope"]["scope_revision"] == "S1"
    assert manifest["datasets"]["evidence_files"]["dir"] == "evidence"
    assert manifest["datasets"]["inspections"]["file"] == "inspections.csv"
    assert manifest["datasets"]["inspections"]["basis"] == ["synthetic_universe"]
    assert manifest["optional_datasets"] == {"approval_items": None}
    assert manifest["normalization"] == "normalization.json"
    assert json.loads((out / "project.json").read_text(encoding="utf-8")) == mapping.project
    norm = json.loads((out / "normalization.json").read_text(encoding="utf-8"))
    assert norm["mapping"] == mapping.reference()
    assert len(norm["files"]) == 80
    assert (out / "evidence" / "files" / "bsyn00003" / "201" / "13" / "v1" / "report.pdf").is_file()


def test_every_row_carries_x_source_and_rows_sort_by_key(tmp_path: Path, mapping: Mapping) -> None:
    out = tmp_path / "snapshot"
    normalize(S16, mapping, out)
    for name, key in [("scope.csv", ["obligation_id"]), ("artifacts.csv", ["artifact_id", "revision"])]:
        rows = read_csv_rows(out / name)
        assert rows and all(
            r["x_source"].startswith("table=bsyn0000") and ";rid=" in r["x_source"] for r in rows
        )
        keys = [tuple(r[k] for k in key) for r in rows]
        assert keys == sorted(keys)
    artifacts = read_csv_rows(out / "artifacts.csv")
    assert artifacts[0]["artifact_id"] == "ART-001-P"
    assert artifacts[0]["relative_path"] == "files/bsyn00003/202/13/v1/photo.png"
    assert artifacts[0]["inspection_id"] == "INS-001"


def test_the_output_directory_must_be_absent_or_empty(tmp_path: Path, mapping: Mapping) -> None:
    out = tmp_path / "snapshot"
    out.mkdir()
    (out / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RunError) as info:
        normalize(S16, mapping, out)
    assert info.value.code == "OUT_NOT_EMPTY"


# -- conversions -----------------------------------------------------------------------------------------------


def fm(type_name: str, values: dict[str, str] | None = None, as_: str | None = None) -> FieldMap:
    return FieldMap(column="x", fid=20, type=type_name, values=values, as_=as_, target=None)


STATUS = {"Complete": "completed", "In Progress": "in_progress"}

CONVERSIONS: list[tuple[str, FieldMap, Any, str | None, str | None]] = [
    ("text", fm("text"), "INS-001", "INS-001", None),
    ("text empty is null", fm("text"), "", None, None),
    ("text null", fm("text"), None, None, None),
    ("text from a number", fm("text"), 5, "5", "5"),
    ("text from an object", fm("text"), {"b": 1, "a": 2}, '{"a":2,"b":1}', '{"a":2,"b":1}'),
    ("choice string", fm("text-multiple-choice", STATUS), "Complete", "completed", None),
    ("choice one-element list", fm("text-multiple-choice", STATUS), ["In Progress"], "in_progress", None),
    ("choice empty string", fm("text-multiple-choice", STATUS), "", None, None),
    ("choice empty list", fm("text-multiple-choice", STATUS), [], None, None),
    ("choice unmapped", fm("text-multiple-choice", STATUS), "Done-ish", "Done-ish", "Done-ish"),
    ("choice case-sensitive", fm("text-multiple-choice", STATUS), "complete", "complete", "complete"),
    (
        "choice two-element list",
        fm("text-multiple-choice", STATUS),
        ["Complete", "x"],
        '["Complete","x"]',
        '["Complete","x"]',
    ),
    ("choice number", fm("text-multiple-choice", STATUS), 3, "3", "3"),
    ("choice without a value map", fm("text-multiple-choice"), "Anything", "Anything", None),
    ("numeric integer", fm("numeric", as_="integer_string"), 2, "2", None),
    ("numeric integral float", fm("numeric", as_="integer_string"), 2.0, "2", None),
    ("numeric fraction", fm("numeric", as_="integer_string"), 2.5, "2.5", "2.5"),
    ("numeric boolean", fm("numeric", as_="integer_string"), True, "true", "true"),
    ("numeric string", fm("numeric", as_="integer_string"), "2", '"2"', '"2"'),
    ("checkbox true", fm("checkbox"), True, "true", None),
    ("checkbox false", fm("checkbox"), False, "false", None),
    ("checkbox string", fm("checkbox"), "yes", '"yes"', '"yes"'),
    ("timestamp", fm("timestamp"), "2026-09-01T15:00:00Z", "2026-09-01T15:00:00Z", None),
    ("timestamp empty", fm("timestamp"), "", None, None),
    ("timestamp number", fm("timestamp"), 20260901, "20260901", "20260901"),
    (
        "user email",
        fm("user", as_="email"),
        {"email": "reviewer@example.invalid", "name": "R"},
        "reviewer@example.invalid",
        None,
    ),
    ("user without email", fm("user", as_="email"), {"name": "R"}, '{"name":"R"}', '{"name":"R"}'),
    (
        "user as a string",
        fm("user", as_="email"),
        "reviewer@example.invalid",
        '"reviewer@example.invalid"',
        '"reviewer@example.invalid"',
    ),
    ("recordid", fm("recordid"), 101, "101", None),
]


@pytest.mark.parametrize(
    ("label", "field_map", "value", "cell", "unmapped"), CONVERSIONS, ids=[c[0] for c in CONVERSIONS]
)
def test_conversions(
    label: str, field_map: FieldMap, value: Any, cell: str | None, unmapped: str | None
) -> None:
    converted = convert_value(field_map, value)
    assert (converted.cell, converted.unmapped_label) == (cell, unmapped), label


def test_reference_and_file_types_are_contextual() -> None:
    with pytest.raises(ValueError):
        convert_value(fm("reference"), 1)
    with pytest.raises(ValueError):
        convert_value(fm("file"), {})


def test_reference_ids_and_placeholders() -> None:
    assert reference_rid(101) == 101
    assert reference_rid(101.0) == 101
    assert reference_rid(0) is None and reference_rid(-1) is None
    assert reference_rid(True) is None and reference_rid("101") is None and reference_rid(1.5) is None
    assert placeholder("bsyn00002", 999) == "qbrid.bsyn00002.999"
    assert grammar.is_id(placeholder("bsyn00002", 999))


def test_json_text_is_canonical() -> None:
    assert json_text({"b": [1, "é"], "a": None}) == '{"a":null,"b":[1,"é"]}'


# -- file names and versions -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("my report (v2).pdf", "my_report__v2_.pdf"),
        ("résumé.pdf", "r_sum_.pdf"),
        ("name...", "name"),
        ("...", "file"),
        ("", "file"),
        ("con.txt", "_con.txt"),
        ("CON", "_CON"),
        ("Lpt1.log", "_Lpt1.log"),
        ("console.txt", "console.txt"),
        ("../../etc/passwd", ".._.._etc_passwd"),
        ("a:b|c?.png", "a_b_c_.png"),
    ],
)
def test_file_name_sanitization(name: str, expected: str) -> None:
    assert sanitize_file_name(name) == expected
    assert grammar.path_violation(file_relative_path("bsyn00003", 201, 13, 1, name)) is None


def test_long_file_names_are_shortened_with_a_digest() -> None:
    name = "x" * 150 + ".pdf"
    out = sanitize_file_name(name)
    assert len(out) <= 100
    assert out.startswith("x" * 83 + "~")
    assert out.endswith(".pdf")
    assert len(out) == 83 + 1 + 8 + 4
    assert sanitize_file_name("y" * 150 + ".pdf") != out


def test_latest_version_picks_the_highest() -> None:
    value = {
        "url": "/files/t/1/13",
        "versions": [
            {"versionNumber": 1, "fileName": "a.pdf"},
            {"versionNumber": 3, "fileName": "c.pdf"},
            {"versionNumber": 2, "fileName": "b.pdf"},
            {"versionNumber": "9", "fileName": "bad.pdf"},
        ],
    }
    assert latest_version(value) == (3, "c.pdf")
    assert latest_version({"versions": []}) is None
    assert latest_version(None) is None
    assert latest_version("not a file value") is None


# -- end to end through mutated exports ------------------------------------------------------------------------


def test_an_unresolved_reference_becomes_a_placeholder_and_a_dangling_reference(
    tmp_path: Path, mapping: Mapping
) -> None:
    export = copy_export(tmp_path)
    edit_record(export, "approvals", 301, 7, 999)  # APR-001 -> an inspection record that was not captured
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    approvals = {r["approval_id"]: r for r in read_csv_rows(out / "approvals.csv")}
    assert approvals["APR-001"]["inspection_id"] == "qbrid.bsyn00002.999"
    norm = json.loads((out / "normalization.json").read_text(encoding="utf-8"))
    assert norm["unresolved_references"] == [
        {
            "dataset": "approvals",
            "source": "table=bsyn00004;rid=301",
            "field": "inspection_id",
            "target_table": "bsyn00002",
            "rid": 999,
        }
    ]
    keys = {f.key: f for f in run(out).findings}
    assert keys["R0:approval:APR-001#DANGLING_REFERENCE"].outcome == "UNKNOWN"


def test_empty_versions_mean_no_file(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)
    edit_record(export, "artifacts", 202, 13, {"url": "/files/bsyn00003/202/13", "versions": []})
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    artifacts = {r["artifact_id"]: r for r in read_csv_rows(out / "artifacts.csv")}
    assert artifacts["ART-001-P"]["relative_path"] == ""
    assert not (out / "evidence" / "files" / "bsyn00003" / "202").exists()
    keys = {f.key: f for f in run(out).findings}
    assert keys["R5:obligation:O-001"].reason == "FILE_ABSENT"


def test_a_null_file_value_means_no_file(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)
    edit_record(export, "artifacts", 202, 13, None)
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    assert {r["artifact_id"]: r for r in read_csv_rows(out / "artifacts.csv")}["ART-001-P"][
        "relative_path"
    ] == ""


def test_list_form_choices_and_integral_floats_normalize_like_s16(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)
    edit_record(export, "inspections", 106, 13, ["Complete"])
    edit_record(export, "inspections", 107, 7, 1.0)
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    assert run(out).assessment_semantic_sha256 == ss.run_scenario("S01-clean").assessment_semantic_sha256


def test_a_wrong_json_type_is_listed_as_unmapped(tmp_path: Path, mapping: Mapping) -> None:
    """AM-7: the cell holds the value's canonical JSON text, normalization.json lists it, and R0 reports
    UNMAPPED_VALUE only (§22.3 exclusive classification), whatever the column grammar says."""
    export = copy_export(tmp_path)
    edit_record(export, "inspections", 107, 8, "yes")  # a checkbox holding a string
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    rows = {r["inspection_id"]: r for r in read_csv_rows(out / "inspections.csv")}
    assert rows["INS-007"]["is_current"] == '"yes"'
    norm = json.loads((out / "normalization.json").read_text(encoding="utf-8"))
    assert {
        "dataset": "inspections",
        "source": "table=bsyn00002;rid=107",
        "field": "is_current",
        "raw": '"yes"',
    } in norm["unmapped_values"]
    findings = {f.key: f for f in run(out).findings}
    assert findings["R0:inspection:INS-007@1#UNMAPPED_VALUE"].outcome == "UNKNOWN"
    assert "R0:inspection:INS-007@1#INVALID_VALUE" not in findings


def test_file_names_are_sanitized_and_the_original_kept(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)
    edit_record(
        export,
        "artifacts",
        202,
        13,
        {
            "url": "/files/bsyn00003/202/13",
            "versions": [{"versionNumber": 1, "fileName": "Photo (final).PNG"}],
        },
    )
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    relative = "files/bsyn00003/202/13/v1/Photo__final_.PNG"
    assert {r["artifact_id"]: r for r in read_csv_rows(out / "artifacts.csv")}["ART-001-P"][
        "relative_path"
    ] == relative
    assert (out / "evidence" / relative).read_bytes() == (
        export / "files/bsyn00003/202/13/v1/photo.png"
    ).read_bytes()
    norm = json.loads((out / "normalization.json").read_text(encoding="utf-8"))
    assert {"relative_path": relative, "original_name": "Photo (final).PNG"} in norm["files"]


def test_an_uncaptured_file_is_not_copied(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)

    def mark(manifest: dict[str, Any]) -> None:
        for entry in manifest["files"]:
            if entry["record_id"] == 201:
                entry.update(status="not_captured", path=None, bytes=None, sha256=None)
        manifest["datasets"]["evidence_files"] = {
            "coverage": "partial",
            "basis": ["attachment_capture_skipped"],
            "consistency": "not_applicable",
        }

    edit_json(export / "capture-manifest.json", mark)
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    assert not (out / "evidence" / "files" / "bsyn00003" / "201").exists()
    keys = {f.key: f for f in run(out).findings}
    assert keys["R5:obligation:O-001"].reason == "NOT_CAPTURED"


@pytest.mark.parametrize(
    ("status", "change", "accepted"),
    [
        ("not_captured", {"sha256": "0" * 64}, False),
        ("out_of_scope", {"path": "files/bsyn00003/201/13/v1/report.pdf"}, False),
        ("error", {"bytes": 10}, False),
        ("too_large", {"bytes": 10}, True),
    ],
)
def test_an_uncaptured_entry_carries_no_capture_values(
    status: str, change: dict[str, Any], accepted: bool
) -> None:
    manifest = json.loads((S16 / "capture-manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0].update({"status": status, "path": None, "bytes": None, "sha256": None, **change})
    if accepted:
        parse_export_manifest(manifest)
        return
    with pytest.raises(RunError) as info:
        parse_export_manifest(manifest)
    assert "(AM-4)" in info.value.message


def test_corrupted_captured_bytes_are_refused(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)
    (export / "files/bsyn00003/201/13/v1/report.pdf").write_bytes(b"tampered")
    with pytest.raises(RunError) as info:
        normalize(export, mapping, tmp_path / "snapshot")
    assert info.value.code == "EXPORT_INVALID"
    assert not (tmp_path / "snapshot" / "manifest.json").exists()


EXPORT_DEFECTS: list[tuple[str, Callable[[Path], None], str]] = [
    (
        "unknown manifest member",
        lambda e: edit_json(e / "capture-manifest.json", lambda m: m.update(extra=1)),
        "CONFIG_INVALID",
    ),
    (
        "wrong format",
        lambda e: edit_json(e / "capture-manifest.json", lambda m: m.update(format="x")),
        "CONFIG_INVALID",
    ),
    (
        "pages count",
        lambda e: edit_json(
            e / "capture-manifest.json", lambda m: m["tables"]["inspections"].update(pages=2)
        ),
        "EXPORT_INVALID",
    ),
    (
        "retrieved count",
        lambda e: edit_json(
            e / "capture-manifest.json", lambda m: m["tables"]["artifacts"].update(retrieved=79)
        ),
        "EXPORT_INVALID",
    ),
    (
        "table id",
        lambda e: edit_json(
            e / "capture-manifest.json", lambda m: m["tables"]["approvals"].update(table_id="bsyn00009")
        ),
        "EXPORT_INVALID",
    ),
    (
        "captured file without a hash",
        lambda e: edit_json(e / "capture-manifest.json", lambda m: m["files"][0].update(sha256=None)),
        "CONFIG_INVALID",
    ),
    (
        "unsafe captured file path",
        lambda e: edit_json(
            e / "capture-manifest.json", lambda m: m["files"][0].update(path="../outside.pdf")
        ),
        "CONFIG_INVALID",
    ),
    (
        "a record lacks a mapped field",
        lambda e: edit_json(
            e / "tables" / "inspections" / "page-0001.json", lambda p: p["data"][0].pop("13")
        ),
        "EXPORT_INVALID",
    ),
    (
        "a field type mismatch",
        lambda e: edit_json(
            e / "tables" / "inspections" / "fields.json",
            lambda fs: [f.update(fieldType="text") for f in fs if f["id"] == 8],
        ),
        "QB_SCHEMA_MISMATCH",
    ),
]


@pytest.mark.parametrize(("label", "damage", "code"), EXPORT_DEFECTS, ids=[d[0] for d in EXPORT_DEFECTS])
def test_export_defects_are_run_errors(
    tmp_path: Path, mapping: Mapping, label: str, damage: Callable[[Path], None], code: str
) -> None:
    export = copy_export(tmp_path)
    damage(export)
    with pytest.raises(RunError) as info:
        normalize(export, mapping, tmp_path / "snapshot")
    assert info.value.code == code, f"{label}: {info.value}"


def test_a_table_the_mapping_does_not_map_is_refused(tmp_path: Path, mapping: Mapping) -> None:
    export = copy_export(tmp_path)

    def add(m: dict[str, Any]) -> None:
        m["tables"]["approval_items"] = dict(m["tables"]["approvals"], table_id="bsyn00005")

    edit_json(export / "capture-manifest.json", add)
    with pytest.raises(RunError) as info:
        normalize(export, mapping, tmp_path / "snapshot")
    assert info.value.code == "EXPORT_INVALID"


@pytest.mark.parametrize("paging", [None, "keyset", "skip"])
def test_tables_paging_is_optional_and_semantically_ignored(
    tmp_path: Path, mapping: Mapping, paging: str | None
) -> None:
    """§12.1 fallback: capture records tables[].paging; fixture exports omit it; normalize ignores it."""
    export = copy_export(tmp_path)
    if paging is not None:
        edit_json(
            export / "capture-manifest.json",
            lambda m: [t.update(paging=paging) for t in m["tables"].values()],
        )
    manifest = json.loads((export / "capture-manifest.json").read_text(encoding="utf-8"))
    assert parse_export_manifest(manifest)["tables"]["inspections"].get("paging") == paging
    out = tmp_path / "snapshot"
    normalize(export, mapping, out)
    assert run(out).assessment_semantic_sha256 == ss.run_scenario("S01-clean").assessment_semantic_sha256


@pytest.mark.parametrize("bad", ["offset", "", "KEYSET", 1, None])
def test_an_unknown_paging_value_is_refused(bad: Any) -> None:
    manifest = json.loads((S16 / "capture-manifest.json").read_text(encoding="utf-8"))
    manifest["tables"]["obligations"]["paging"] = bad
    with pytest.raises(RunError) as info:
        parse_export_manifest(manifest)
    assert info.value.code == "CONFIG_INVALID"
