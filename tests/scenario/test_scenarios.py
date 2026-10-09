"""Every canonical scenario against the hand-written oracle (SPEC Appendix A)."""

import json

import pytest
import scenario_support as ss
from conftest import SCENARIOS

CANONICAL = sorted(sid for sid in ss.ORACLE["scenarios"] if not sid.startswith(("S16", "S17")))

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("scenario_id", CANONICAL)
def test_scenario_matches_oracle(scenario_id):
    assessment = ss.run_scenario(scenario_id)
    assert ss.mismatches(scenario_id, assessment) == []


def _finding(assessment, key):
    return assessment.by_key()[key]


def test_s02_headline_wording():
    a = ss.run_scenario("S02-missing-inspection")
    f = _finding(a, "R2:obligation:O-017")
    for fragment in ("O-017", "A-017", "visual_inspection", "scope S1"):
        assert fragment in f.explanation
    assert {"system": "fixture", "dataset": "scope", "locator": "scope.csv#row=18"} in f.evidence


def test_s03_mismatch_detail():
    f = _finding(ss.run_scenario("S03-wrong-project-evidence"), "R3:obligation:O-023")
    assert f.expected["project_id"] == "NC-001"
    assert {
        "subject": "artifact:ART-023-P@1",
        "field": "project_id",
        "expected": "NC-001",
        "observed": "NC-002",
    } in f.observed["mismatches"]


@pytest.mark.parametrize(
    "scenario_id, obligation", [("S04-revised-evidence", "O-031"), ("S08-silent-byte-change", "O-009")]
)
def test_changed_evidence_digests(scenario_id, obligation):
    extra = json.loads((SCENARIOS / scenario_id / "expected.json").read_text(encoding="utf-8"))
    f = _finding(ss.run_scenario(scenario_id), f"R6:obligation:{obligation}")
    assert f.expected["approved_evidence_digests"] == [extra["approved_digest"]]
    assert f.observed["current_evidence_digest"] == extra["current_digest"]
    assert extra["approved_digest"] != extra["current_digest"]


def test_s18_revision_binding_limitation_is_stated():
    a = ss.run_scenario("S18-revision-binding")
    assert "undetectable" in _finding(a, "R6:obligation:O-009").explanation
    assert _finding(a, "R6:obligation:O-031").observed["binding"] == "revisions"


def test_s20_contradiction_reason():
    a = ss.run_scenario("S20-dangling-reference")
    assert _finding(a, "R1:dataset:inspections").observed["reasons"] == ["COVERAGE_CONTRADICTED"]


def test_s22_case_hint():
    f = _finding(ss.run_scenario("S22-case-mismatch"), "R5:obligation:O-008")
    assert "O-008/photo.png" in f.explanation


def test_s23_duplicate_observed():
    f = _finding(ss.run_scenario("S23-duplicate-key"), "R0:inspection:INS-004@1#DUPLICATE_KEY")
    assert f.observed == {"count": 2, "identical": True}
