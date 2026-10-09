"""The oracle is the spec (SPEC §0, Appendix A): verbatim copy and arithmetic self-consistency."""

import re
from collections import Counter

import oracle_util
import pytest
import yaml
from conftest import FIXTURES, REPO

ORACLE = oracle_util.load(FIXTURES / "oracle.yaml")


def test_oracle_equals_spec_appendix_a():
    spec = (REPO / "docs" / "SPEC.md").read_text(encoding="utf-8")
    appendix = spec[spec.index("## Appendix A") :]
    block = re.search(r"```yaml\n(.*?)\n```", appendix, re.S)
    assert block is not None
    assert yaml.safe_load(block.group(1)) == ORACLE


def test_scenario_ids_are_the_spec_set():
    expected = {f"S{n:02d}" for n in range(1, 25)}
    assert {sid[:3] for sid in ORACLE["scenarios"]} == expected


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_oracle_self_consistent(scenario_id):
    scenario = ORACLE["scenarios"][scenario_id]
    entries = oracle_util.expand(scenario["non_pass"])
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys)), "duplicate keys in non_pass"
    assert all(e["outcome"] != "PASS" for e in entries)
    total = oracle_util.expected_total(scenario)
    counts = scenario["counts"]
    assert sum(counts.values()) == total
    by_outcome = Counter(e["outcome"] for e in entries)
    for outcome in ("FAIL", "UNKNOWN", "NOT_EVALUATED"):
        assert counts[outcome] == by_outcome.get(outcome, 0), outcome
    assert counts["PASS"] == total - len(entries)
    for e in entries:
        if e["outcome"] == "NOT_EVALUATED":
            assert e["blocked_by"], e["key"]
            assert e["reason"] is None
        else:
            assert e["reason"], e["key"]
