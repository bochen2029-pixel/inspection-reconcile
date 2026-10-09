"""The field mapping (SPEC §12.3): the demo mapping, every validation rule, and the field-list check."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO, SCENARIOS

from inspection_reconcile.adapters.mapping import (
    EXPECTED_FIELD_TYPE,
    Mapping,
    load_mapping,
    parse_mapping,
    verify_fields,
)
from inspection_reconcile.errors import RunError
from inspection_reconcile.yamlsafe import load_yaml, loads_yaml

DEMO = REPO / "mappings" / "quickbase-demo.yml"
S16_TABLES = SCENARIOS / "S16-quickbase-clean" / "export" / "tables"


@pytest.fixture
def raw() -> dict[str, Any]:
    return copy.deepcopy(load_yaml(DEMO))


def s16_fields(role: str) -> list[dict[str, Any]]:
    return json.loads((S16_TABLES / role / "fields.json").read_text(encoding="utf-8"))


# -- the demo mapping ------------------------------------------------------------------------------------------


def test_the_demo_mapping_loads_with_the_spec_field_ids() -> None:
    m = load_mapping(DEMO)
    assert (m.mapping_id, m.version, m.source, m.synthetic) == ("quickbase-demo", "3.0.0", "quickbase", True)
    assert m.project == {
        "project_id": "NC-001",
        "client_id": "CL-SYN-01",
        "name": "North Creek (synthetic)",
        "synthetic": True,
    }
    assert m.scope_filter == {"fid": 7, "value": "NC-001"}
    assert {role: t.table_id for role, t in m.tables.items()} == {
        "obligations": "bsyn00001",
        "inspections": "bsyn00002",
        "artifacts": "bsyn00003",
        "approvals": "bsyn00004",
    }
    insp = m.tables["inspections"]
    assert insp.fields["obligation_id"].type == "reference"
    assert insp.fields["obligation_id"].target == "obligations"
    assert insp.fields["revision"].as_ == "integer_string"
    assert insp.fields["completion_status"].values == {
        "Complete": "completed",
        "In Progress": "in_progress",
        "Not Started": "not_started",
        "Cancelled": "cancelled",
    }
    assert m.tables["approvals"].fields["decided_by"].as_ == "email"
    assert m.tables["artifacts"].fields["relative_path"].type == "file"
    assert m.tables["obligations"].select() == [2, 3, 6, 7, 8, 9, 10]
    assert m.tables["inspections"].select() == [2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    assert m.sha256.startswith("sha256:") and len(m.sha256) == 71
    assert m.reference() == {"mapping_id": "quickbase-demo", "version": "3.0.0", "sha256": m.sha256}


def test_the_mapping_digest_ignores_comments_and_layout(tmp_path: Path) -> None:
    text = DEMO.read_text(encoding="utf-8")
    reformatted = "# a leading comment\n" + text.replace("{fid: 6,  type: text}", "{type: text, fid: 6}")
    path = tmp_path / "m.yml"
    path.write_text(reformatted, encoding="utf-8")
    assert load_mapping(path).sha256 == load_mapping(DEMO).sha256


def test_expected_field_types_follow_the_spec_table() -> None:
    assert EXPECTED_FIELD_TYPE == {
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


# -- every validation rule -------------------------------------------------------------------------------------


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    keys = path.split(".")

    def apply(doc: dict[str, Any]) -> None:
        node = doc
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value

    return apply


def _drop(path: str) -> Callable[[dict[str, Any]], None]:
    keys = path.split(".")

    def apply(doc: dict[str, Any]) -> None:
        node = doc
        for key in keys[:-1]:
            node = node[key]
        del node[keys[-1]]

    return apply


INSP = "tables.inspections.fields"
ART = "tables.artifacts.fields"
APR = "tables.approvals.fields"
OBL = "tables.obligations.fields"

RULES: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("unknown top-level member", _set("extra", 1), "unknown member 'extra'"),
    ("wrong schema", _set("schema", "inspection-reconcile/mapping/v2"), "schema"),
    ("source must be quickbase", _set("source", "salesforce"), "source"),
    ("mapping_id grammar", _set("mapping_id", "bad id"), "violates grammar ID"),
    ("version must be a string", _set("version", 3), "expected a string"),
    ("synthetic must be boolean", _set("synthetic", "yes"), "expected true or false"),
    ("project member missing", _drop("project.client_id"), "missing member 'client_id'"),
    ("project unknown member", _set("project.owner", "x"), "unknown member 'owner'"),
    ("missing required table", _drop("tables.approvals"), "missing table 'approvals'"),
    (
        "unknown table role",
        _set("tables.payroll", {"table_id": "bsyn00009", "fields": {}}),
        "unknown table role",
    ),
    ("table id grammar", _set("tables.obligations.table_id", "bad/id"), "not a Quickbase table or app id"),
    ("duplicate table id", _set("tables.approvals.table_id", "bsyn00002"), "is also mapped as"),
    ("table unknown member", _set("tables.artifacts.extra", True), "unknown member 'extra'"),
    (
        "allow_derived must be boolean",
        _set("tables.artifacts.allow_derived", "yes"),
        "expected true or false",
    ),
    ("unknown column", _set(f"{INSP}.color", {"fid": 30, "type": "text"}), "unknown column 'color'"),
    ("required column not mapped", _drop(f"{INSP}.completion_status"), "required column 'completion_status'"),
    ("fid must be positive", _set(f"{INSP}.project_id.fid", 0), "expected an integer in"),
    ("fid must be an integer", _set(f"{INSP}.project_id.fid", "10"), "expected an integer"),
    ("fid mapped twice", _set(f"{INSP}.asset_id.fid", 10), "is already mapped to project_id"),
    ("unknown type", _set(f"{INSP}.project_id.type", "rich-text"), "is not one of"),
    (
        "type must suit the column",
        _set(f"{INSP}.is_current.type", "numeric"),
        "cannot feed column is_current",
    ),
    (
        "file only for relative_path",
        _set(f"{ART}.project_id", {"fid": 10, "type": "file"}),
        "cannot feed column",
    ),
    (
        "relative_path only from file",
        _set(f"{ART}.relative_path.type", "text"),
        "cannot feed column relative_path",
    ),
    ("field 3 needs recordid", _set(f"{INSP}.project_id", {"fid": 3, "type": "text"}), "field 3"),
    (
        "recordid is field 3",
        _set(f"{INSP}.inspection_id", {"fid": 20, "type": "recordid"}),
        "type: recordid is field 3",
    ),
    ("field 2 needs timestamp", _set(f"{INSP}.project_id", {"fid": 2, "type": "text"}), "field 2"),
    ("numeric needs as", _drop(f"{INSP}.revision.as"), "requires as: integer_string"),
    (
        "numeric as must be integer_string",
        _set(f"{INSP}.revision.as", "email"),
        "requires as: integer_string",
    ),
    ("user needs as email", _drop(f"{APR}.decided_by.as"), "requires as: email"),
    ("as not allowed on text", _set(f"{INSP}.project_id.as", "email"), "'as' is not allowed"),
    (
        "reference needs a link column",
        _set(f"{INSP}.asset_id", {"fid": 11, "type": "reference", "target": "obligations"}),
        "is not a link column",
    ),
    (
        "reference on a non-link ID column",
        _set(f"{OBL}.obligation_id", {"fid": 6, "type": "reference", "target": "obligations"}),
        "is not a link column",
    ),
    (
        "reference target must be the parent",
        _set(f"{INSP}.obligation_id.target", "inspections"),
        "must have target: obligations",
    ),
    ("reference needs a target", _drop(f"{INSP}.obligation_id.target"), "must have target: obligations"),
    (
        "target only on reference",
        _set(f"{INSP}.project_id.target", "obligations"),
        "'target' is allowed only",
    ),
    ("values required for an enum", _drop(f"{INSP}.completion_status.values"), "needs a values map"),
    (
        "values only on multiple choice",
        _set(f"{INSP}.project_id.values", {"A": "b"}),
        "'values' is allowed only",
    ),
    ("values must be a non-empty map", _set(f"{INSP}.completion_status.values", {}), "non-empty map"),
    (
        "value-map target must be in the enum",
        _set(f"{INSP}.completion_status.values", {"Complete": "done"}),
        "is not a valid completion_status value",
    ),
    (
        "value-map target must be a KIND",
        _set(f"{OBL}.activity_kind.values", {"Visual Inspection": "Visual"}),
        "is not a valid activity_kind value",
    ),
    (
        "value-map label must be a string",
        _set(f"{INSP}.completion_status.values", {True: "completed"}),
        "quote this key",
    ),
    (
        "value-map target must be a string",
        _set(f"{INSP}.completion_status.values", {"Complete": True}),
        "must be a string",
    ),
    (
        "scope filter must be the project field",
        _set("scope_filter.fid", 8),
        "must be the obligations table's project_id field",
    ),
    (
        "scope filter value must be the project",
        _set("scope_filter.value", "NC-002"),
        "must equal project.project_id",
    ),
    ("scope filter unknown member", _set("scope_filter.op", "EX"), "unknown member 'op'"),
]


@pytest.mark.parametrize(("label", "mutate", "message"), RULES, ids=[r[0] for r in RULES])
def test_every_rule_is_a_config_error(
    raw: dict[str, Any], label: str, mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    mutate(raw)
    with pytest.raises(RunError) as info:
        parse_mapping(raw, "test.yml")
    assert info.value.code == "CONFIG_INVALID"
    assert message in info.value.message, f"{label}: {info.value.message}"


def test_a_nullable_column_may_be_omitted(raw: dict[str, Any]) -> None:
    del raw["tables"]["inspections"]["fields"]["completed_at"]
    del raw["tables"]["approvals"]["fields"]["evidence_digest"]
    m = parse_mapping(raw, "test.yml")
    assert "completed_at" not in m.tables["inspections"].fields


def test_an_optional_approval_items_table(raw: dict[str, Any]) -> None:
    raw["tables"]["approval_items"] = {
        "table_id": "bsyn00005",
        "fields": {
            "approval_id": {"fid": 6, "type": "reference", "target": "approvals"},
            "artifact_id": {"fid": 7, "type": "reference", "target": "artifacts"},
            "artifact_revision": {"fid": 8, "type": "numeric", "as": "integer_string"},
        },
    }
    m = parse_mapping(raw, "test.yml")
    assert m.tables["approval_items"].dataset == "approval_items"
    assert m.tables["approval_items"].select() == [2, 3, 6, 7, 8]


def test_text_and_record_ids_can_feed_id_columns(raw: dict[str, Any]) -> None:
    raw["tables"]["inspections"]["fields"]["inspection_id"] = {"fid": 3, "type": "recordid"}
    raw["tables"]["inspections"]["fields"]["obligation_id"] = {"fid": 9, "type": "text"}
    m = parse_mapping(raw, "test.yml")
    assert m.tables["inspections"].fields["inspection_id"].type == "recordid"


def test_a_multiple_choice_text_column_needs_no_values(raw: dict[str, Any]) -> None:
    raw["tables"]["inspections"]["fields"]["project_id"] = {"fid": 10, "type": "text-multiple-choice"}
    assert parse_mapping(raw, "test.yml").tables["inspections"].fields["project_id"].values is None


def test_unquoted_yes_label_is_rejected_by_the_loader_and_validator() -> None:
    text = DEMO.read_text(encoding="utf-8").replace('{"Complete": completed,', "{Yes: completed,")
    doc = loads_yaml(text, "yes.yml")
    with pytest.raises(RunError) as info:
        parse_mapping(doc, "yes.yml")
    assert "quote this key" in info.value.message


def test_floats_in_the_document_are_rejected(raw: dict[str, Any]) -> None:
    raw["tables"]["inspections"]["fields"]["project_id"]["fid"] = 10.0
    with pytest.raises(RunError) as info:
        parse_mapping(raw, "test.yml")
    assert info.value.code == "CONFIG_INVALID"


# -- verify_fields ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo() -> Mapping:
    return load_mapping(DEMO)


@pytest.mark.parametrize("role", ["obligations", "inspections", "artifacts", "approvals"])
def test_the_s16_field_lists_verify(demo: Mapping, role: str) -> None:
    verify_fields(demo, role, s16_fields(role))


def _mutated(role: str, fid: int, **changes: Any) -> list[dict[str, Any]]:
    fields = s16_fields(role)
    for f in fields:
        if f["id"] == fid:
            f.update(changes)
    return fields


def test_a_field_type_mismatch_names_both_values(demo: Mapping) -> None:
    with pytest.raises(RunError) as info:
        verify_fields(demo, "inspections", _mutated("inspections", 13, fieldType="text"))
    assert info.value.code == "QB_SCHEMA_MISMATCH"
    assert "'text'" in info.value.message and "'text-multiple-choice'" in info.value.message
    assert "completion_status" in info.value.message


def test_a_reference_must_be_a_numeric_field(demo: Mapping) -> None:
    with pytest.raises(RunError) as info:
        verify_fields(demo, "artifacts", _mutated("artifacts", 9, fieldType="text"))
    assert "expects 'numeric'" in info.value.message


def test_a_missing_mapped_field(demo: Mapping) -> None:
    fields = [f for f in s16_fields("approvals") if f["id"] != 12]
    with pytest.raises(RunError) as info:
        verify_fields(demo, "approvals", fields)
    assert "field 12 (mapped to decided_by) does not exist" in info.value.message


@pytest.mark.parametrize("mode", ["lookup", "summary", "formula"])
def test_derived_fields_are_refused_unless_allowed(raw: dict[str, Any], mode: str) -> None:
    m = parse_mapping(raw, "test.yml")
    fields = _mutated("inspections", 10, mode=mode)
    with pytest.raises(RunError) as info:
        verify_fields(m, "inspections", fields)
    assert f"mode {mode!r}" in info.value.message
    raw["tables"]["inspections"]["allow_derived"] = True
    verify_fields(parse_mapping(raw, "test.yml"), "inspections", fields)


def test_fields_2_and_3_are_required(demo: Mapping) -> None:
    without3 = [f for f in s16_fields("obligations") if f["id"] != 3]
    with pytest.raises(RunError) as info:
        verify_fields(demo, "obligations", without3)
    assert "field 3" in info.value.message
    without2 = [f for f in s16_fields("obligations") if f["id"] != 2]
    with pytest.raises(RunError) as info:
        verify_fields(demo, "obligations", without2)
    assert "field 2" in info.value.message
    with pytest.raises(RunError) as info:
        verify_fields(demo, "obligations", _mutated("obligations", 2, fieldType="date"))
    assert "expected 'timestamp'" in info.value.message


def test_malformed_field_lists(demo: Mapping) -> None:
    with pytest.raises(RunError):
        verify_fields(demo, "obligations", [{"label": "no id"}])
    with pytest.raises(RunError):
        verify_fields(demo, "payroll", s16_fields("obligations"))
