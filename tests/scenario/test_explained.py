"""I-4: every finding is explained and traceable (SPEC §3, §9.2, §10, §22.2)."""

import pytest
import scenario_support as ss

CANONICAL = sorted(sid for sid in ss.ORACLE["scenarios"] if not sid.startswith(("S16", "S17")))

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("scenario_id", CANONICAL)
def test_every_finding_is_explained_and_traceable(scenario_id):
    for f in ss.run_scenario(scenario_id).findings:
        assert f.reason and f.explanation, f.key
        assert "{" not in f.explanation + f.resolution, f"{f.key}: an unrendered placeholder"
        if f.outcome != "PASS":  # PASS templates have no resolution (§10)
            assert f.resolution, f.key
        if f.outcome in ("FAIL", "UNKNOWN") and f.check_id != "R1":  # §22.2: R1 may cite no rows
            assert f.evidence, f"{f.key}: no source locators"
