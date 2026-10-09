"""Fault injection (SPEC §15.1): each deliberate defect must make its target scenarios disagree with the oracle,
which proves the tests can fail for the right reasons."""

import pytest
import scenario_support as ss

from inspection_reconcile.engine import checks, registry
from inspection_reconcile.engine.coverage import Coverage
from inspection_reconcile.vocab import COMPLETE, COVERAGE_DATASETS


def test_controls_match_without_faults():
    for sid in (
        "S02-missing-inspection",
        "S04-revised-evidence",
        "S05-incomplete-capture",
        "S22-case-mismatch",
    ):
        assert ss.mismatches(sid, ss.run_scenario(sid)) == []


def test_fi1_zero_candidates_treated_as_pass(monkeypatch):
    original = registry.CHECKS["R2"]

    def faulty(ctx, obligation):
        result = original(ctx, obligation)
        if result.finding.reason in ("NO_CURRENT_INSPECTION", "ABSENCE_UNCONFIRMED"):
            result.finding.outcome = "PASS"
            result.finding.reason = "SINGLE_CURRENT_COMPLETED"
        return result

    monkeypatch.setitem(registry.CHECKS, "R2", faulty)
    assert ss.mismatches("S02-missing-inspection", ss.run_scenario("S02-missing-inspection"))


@pytest.mark.parametrize("sid", ["S04-revised-evidence", "S08-silent-byte-change"])
def test_fi2_every_digest_matches(monkeypatch, sid):
    monkeypatch.setattr(checks, "digest_matches", lambda approved, current: True)
    assert ss.mismatches(sid, ss.run_scenario(sid))


@pytest.mark.parametrize("sid", ["S05-incomplete-capture", "S12-attachment-not-captured"])
def test_fi3_coverage_forced_complete(monkeypatch, sid):
    def forced(snapshot, policy, integrity):
        return {
            name: Coverage(name, COMPLETE, ("synthetic_universe",), "not_applicable", COMPLETE, ())
            for name in COVERAGE_DATASETS
        }

    monkeypatch.setitem(registry.CHECKS, "EFFECTIVE_COVERAGE", forced)
    assert ss.mismatches(sid, ss.run_scenario(sid))


def test_fi4_case_insensitive_probe():
    assert ss.mismatches("S22-case-mismatch", ss.run_scenario("S22-case-mismatch", case_sensitive=False))


def test_fi5_duplicate_detection_off(monkeypatch):
    monkeypatch.setitem(registry.OPTIONS, "detect_duplicates", False)
    assert ss.mismatches("S23-duplicate-key", ss.run_scenario("S23-duplicate-key"))
