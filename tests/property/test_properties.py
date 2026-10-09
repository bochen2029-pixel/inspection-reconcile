"""Properties (SPEC §15.1): P1 order invariance, P2 idempotence, P3 coverage monotonicity."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest
import scenario_support as ss
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.slow

CSV_FILES = ("scope.csv", "inspections.csv", "artifacts.csv", "approvals.csv", "approval_items.csv")


def copy_snapshot(scenario_id: str, dest: Path) -> Path:
    src, _ = ss.scenario_paths(scenario_id)
    target = dest / "snapshot"
    shutil.copytree(src, target)
    return target


@pytest.mark.parametrize(
    "scenario_id",
    [
        "S01-clean",
        "S02-missing-inspection",
        "S05-incomplete-capture",
        "S13-conflicting-current-revisions",
        "S18-revision-binding",
        "S20-dangling-reference",
        "S23-duplicate-key",
    ],
)
@settings(max_examples=6, derandomize=True, deadline=None)
@given(seed=st.randoms(use_true_random=False))
def test_p1_row_order_invariance(scenario_id, seed):
    baseline = ss.run_scenario(scenario_id)
    _, policy = ss.scenario_paths(scenario_id)
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_snapshot(scenario_id, Path(tmp))
        for name in CSV_FILES:
            path = root / name
            if not path.is_file():
                continue
            lines = path.read_bytes().split(b"\n")
            header, rows = lines[0], [line for line in lines[1:] if line]
            seed.shuffle(rows)
            path.write_bytes(b"\n".join([header, *rows]) + b"\n")
        result = ss.run_snapshot(root, policy)
    assert result.evaluation_id == baseline.evaluation_id
    assert result.assessment_semantic_sha256 == baseline.assessment_semantic_sha256


@pytest.mark.parametrize(
    "scenario_id", ["S01-clean", "S05-incomplete-capture", "S17-quickbase-unmapped-value"]
)
def test_p2_idempotence(scenario_id):
    """Two complete runs give byte-identical assessment.json and report.html (for S17 each run normalizes the
    export afresh, so this also proves normalize is deterministic)."""
    from conftest import REPO, SCENARIOS

    from inspection_reconcile.runner import evaluate, normalized_export

    _, policy = ss.scenario_paths(scenario_id)

    def run() -> dict[str, bytes]:
        export = SCENARIOS / scenario_id / "export"
        if export.is_dir():
            with normalized_export(export, REPO / "mappings" / "quickbase-demo.yml") as (snap, info):
                return evaluate(snap, policy, mapping=info).outputs()
        return evaluate(SCENARIOS / scenario_id / "snapshot", policy).outputs()

    first, second = run(), run()
    assert first["assessment.json"] == second["assessment.json"]
    assert first["report.html"] == second["report.html"]
    assert json.loads(first["assessment.json"])["status"] == ss.ORACLE["scenarios"][scenario_id]["status"]


def complete_coverage_scenarios() -> list[str]:
    out = []
    for sid in ss.ORACLE["scenarios"]:
        if sid.startswith(("S16", "S17")):
            continue
        snapshot_dir, _ = ss.scenario_paths(sid)
        manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
        if all(d["coverage"] == "complete_for_declared_scope" for d in manifest["datasets"].values()):
            out.append(sid)
    return sorted(set(out))


@pytest.mark.parametrize("scenario_id", complete_coverage_scenarios())
@pytest.mark.parametrize("dataset", ["inspections", "artifacts", "approvals", "evidence_files"])
def test_p3_coverage_monotonicity(scenario_id, dataset):
    baseline = ss.run_scenario(scenario_id)
    _, policy = ss.scenario_paths(scenario_id)
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_snapshot(scenario_id, Path(tmp))
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["datasets"][dataset]["coverage"] = "partial"
        manifest["datasets"][dataset]["basis"] = ["extraction_interrupted"]
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        downgraded = ss.run_snapshot(root, policy)
    before = baseline.by_key()
    for key, finding in downgraded.by_key().items():
        if key in before and before[key].outcome != "PASS":
            assert finding.outcome != "PASS", key
    assert downgraded.status != "READY_FOR_REVIEW"
