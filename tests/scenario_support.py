"""Run a fixture scenario through the engine and compare it with the oracle (expected values come only from
fixtures/oracle.yaml; nothing here computes a verdict)."""

from __future__ import annotations

from pathlib import Path

import oracle_util
from conftest import FIXTURES, POLICIES, SCENARIOS

from inspection_reconcile import __version__
from inspection_reconcile.engine.assess import Assessment, assess
from inspection_reconcile.io.evidence import FileSystemEvidence
from inspection_reconcile.io.snapshot import load_snapshot
from inspection_reconcile.policy import load_policy

ORACLE = oracle_util.load(FIXTURES / "oracle.yaml")


def scenario_paths(scenario_id: str) -> tuple[Path, Path]:
    spec = ORACLE["scenarios"][scenario_id]
    fixture = spec.get("fixture", scenario_id)
    policy = spec.get("policy", "north-creek-demo")
    return SCENARIOS / fixture / "snapshot", POLICIES / f"{policy}.yml"


def run_snapshot(snapshot_dir: Path, policy_path: Path, case_sensitive: bool = True) -> Assessment:
    snapshot = load_snapshot(snapshot_dir)
    policy = load_policy(policy_path)
    evidence = FileSystemEvidence(
        snapshot.evidence_root, policy.max_file_bytes, case_sensitive=case_sensitive
    )
    return assess(snapshot, policy, evidence, snapshot.capture_ended, __version__)


def run_scenario(scenario_id: str, case_sensitive: bool = True) -> Assessment:
    snapshot_dir, policy_path = scenario_paths(scenario_id)
    return run_snapshot(snapshot_dir, policy_path, case_sensitive)


def mismatches(scenario_id: str, assessment: Assessment) -> list[str]:
    """Every way the assessment departs from the oracle entry (empty list = match)."""
    spec = ORACLE["scenarios"][scenario_id]
    problems: list[str] = []
    if assessment.status != spec["status"]:
        problems.append(f"status {assessment.status} != {spec['status']}")
    expected = {e["key"]: e for e in oracle_util.expand(spec["non_pass"])}
    actual = {f.key: f for f in assessment.findings}
    for key, e in expected.items():
        f = actual.get(key)
        if f is None:
            problems.append(f"missing finding {key}")
            continue
        got = {
            "outcome": f.outcome,
            "reason": f.reason,
            "blocked_by": f.blocked_by,
            "caused_by": f.caused_by,
            "required": f.required,
        }
        want = {
            "outcome": e["outcome"],
            "reason": e["reason"] or "BLOCKED_BY_UPSTREAM",
            "blocked_by": e["blocked_by"],
            "caused_by": e["caused_by"],
            "required": e["required"],
        }
        if got != want:
            problems.append(f"{key}: got {got} want {want}")
    for key, f in actual.items():
        if key not in expected and f.outcome != "PASS":
            problems.append(f"unexpected non-PASS {key}: {f.outcome} {f.reason}")
    if len(actual) != oracle_util.expected_total(spec):
        problems.append(f"finding count {len(actual)} != {oracle_util.expected_total(spec)}")
    counts = assessment.counts()["by_outcome"]
    if counts != spec["counts"]:
        problems.append(f"counts {counts} != {spec['counts']}")
    return problems
