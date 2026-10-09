"""`demo` (SPEC §11): run scenarios against the oracle; `--all` also writes index.html, the S02 → S06 comparison
and evaluation_ids.json (decision D-002)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import jinja2

from inspection_reconcile.canonical import sha256_hex
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.writer import json_bytes, prepare_out, write_files
from inspection_reconcile.oracle import check, load_oracle
from inspection_reconcile.report.compare import compare
from inspection_reconcile.runner import Evaluation, evaluate, normalized_export
from inspection_reconcile.vocab import EXIT_DEMO_MISMATCH, EXIT_OK, STATUS_EXIT

COMPARISON_DIR = "compare-S02-S06"
# An export scenario's normalized snapshot goes into <out>/<scenario>/snapshot (SPEC §11).
SNAPSHOT_DIR = "snapshot"

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>inspection-reconcile demo</title>
<style>
body { margin: 0; padding: 24px 16px; font: 15px/1.5 system-ui, sans-serif; color: #1d2330; background: #fff; }
main { max-width: 1000px; margin: 0 auto; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #d9dee7; padding: 4px 8px; text-align: left; }
th { background: #f4f6f9; }
.ok { color: #1f7a3f; font-weight: 700; } .bad { color: #b3261e; font-weight: 700; }
.banner { background: #fff4d6; border: 1px solid #d9dee7; padding: 8px 12px; border-radius: 6px; font-weight: 600; }
code { font-family: ui-monospace, Consolas, monospace; }
</style>
</head>
<body>
<main>
<div class="banner">SYNTHETIC DATA: fictional project (North Creek)</div>
<h1>inspection-reconcile: scenario demonstration</h1>
<p>Each scenario was assessed by the engine and checked against the hand-written oracle in <code>fixtures/oracle.yaml</code>.</p>
<table>
<tr><th>scenario</th><th>oracle status</th><th>actual status</th><th>matches oracle</th><th>report</th></tr>
{% for r in rows %}<tr><td><code>{{ r.id }}</code></td><td>{{ r.expected }}</td><td>{{ r.actual }}</td><td class="{{ 'ok' if r.match else 'bad' }}">{{ 'yes' if r.match else 'NO' }}</td><td>{% if r.report %}<a href="{{ r.id }}/report.html">report</a>{% else %}none (run error){% endif %}</td></tr>
{% endfor %}</table>
{% if comparison %}<h2>The corrected rerun: S02 → S06</h2>
<p>Status {{ comparison.before.status }} → {{ comparison.after.status }}; {{ comparison.changed|length }} finding(s) changed, {{ comparison.unchanged_count }} unchanged.</p>
<ul>{% for c in comparison.changed %}<li><code>{{ c.key }}</code>: {{ c.before.outcome }} {{ c.before.reason }} → {{ c.after.outcome }} {{ c.after.reason }}</li>{% endfor %}</ul>
{% endif %}
</main>
</body>
</html>
"""


@dataclass
class ScenarioResult:
    scenario_id: str
    evaluation: Evaluation | None
    problems: list[str]
    snapshot: dict[str, bytes] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return not self.problems

    @property
    def status(self) -> str:
        return self.evaluation.assessment.status if self.evaluation is not None else "RUN_ERROR"


def _evaluate(
    repo: Path, fixtures: Path, scenario_id: str, spec: dict[str, Any], home: Path
) -> tuple[Evaluation, dict[str, bytes]]:
    """Assess one scenario. An export scenario is normalized first (SPEC §11); its snapshot is returned as bytes,
    to be written under ``home`` (``<out>/<scenario>/snapshot``) with the other outputs, and the run manifest
    records the inputs at those final paths."""
    fixture_dir = fixtures / spec.get("fixture", scenario_id)
    policy = repo / "policies" / f"{spec.get('policy', 'north-creek-demo')}.yml"
    if not (fixture_dir / "export").is_dir():
        return evaluate(fixture_dir / "snapshot", policy), {}
    with normalized_export(fixture_dir / "export", repo / "mappings" / "quickbase-demo.yml") as (snap, info):
        evaluation = evaluate(snap, policy, mapping=info)
        files = {
            p.relative_to(snap).as_posix(): p.read_bytes() for p in sorted(snap.rglob("*")) if p.is_file()
        }
        inputs = [
            (role, home / path.relative_to(snap) if path.is_relative_to(snap) else path, size, sha)
            for role, path, size, sha in evaluation.inputs
        ]
    return replace(evaluation, inputs=inputs), files


def run_demo(
    fixtures: Path,
    out: Path,
    scenario: str | None,
    force: bool,
    say: Callable[[str], None] = print,
) -> int:
    fixtures = fixtures.resolve()
    root = fixtures.parent
    repo = root.parent
    oracle = load_oracle(root / "oracle.yaml")
    scenarios: dict[str, Any] = oracle["scenarios"]
    if scenario is not None and scenario not in scenarios:
        raise RunError("UNKNOWN_SCENARIO", f"{scenario} is not in the oracle")
    ids = [scenario] if scenario else sorted(scenarios)
    own_dirs = sorted(scenarios) + [COMPARISON_DIR]
    if not force:
        prepare_out(out, force=False)  # refuse a non-empty --out before any work

    results: dict[str, ScenarioResult] = {}
    cache: dict[str, tuple[Evaluation, dict[str, bytes]]] = {}

    def run(sid: str) -> tuple[Evaluation, dict[str, bytes]]:
        if sid not in cache:
            cache[sid] = _evaluate(repo, fixtures, sid, scenarios[sid], out / sid / SNAPSHOT_DIR)
        return cache[sid]

    for sid in ids:
        try:
            evaluation, snapshot = run(sid)
        except RunError as exc:  # one scenario's run error is a mismatch, not the end of the demo
            results[sid] = ScenarioResult(sid, None, [f"run error {exc.code}: {exc.message}"])
            continue
        problems = check(scenarios[sid], evaluation.assessment)
        twin = scenarios[sid].get("equivalent_to")
        if twin:
            try:
                b = run(twin)[0].assessment
                a = evaluation.assessment
                if (a.evaluation_id, a.assessment_semantic_sha256) != (
                    b.evaluation_id,
                    b.assessment_semantic_sha256,
                ):
                    problems.append(f"not equivalent to {twin}")
            except RunError as exc:
                problems.append(f"{twin} could not run: {exc.code}")
        results[sid] = ScenarioResult(sid, evaluation, problems, snapshot)

    files: dict[str, bytes] = {}
    ids_doc: dict[str, Any] = {}
    for sid, result in results.items():
        if result.evaluation is None:
            ids_doc[sid] = {"status": "RUN_ERROR", "matches_oracle": False}
            continue
        for name, data in result.snapshot.items():
            files[f"{sid}/{SNAPSHOT_DIR}/{name}"] = data
        outputs = result.evaluation.outputs()
        for name, data in outputs.items():
            files[f"{sid}/{name}"] = data
        a = result.evaluation.assessment
        ids_doc[sid] = {
            "evaluation_id": a.evaluation_id,
            "assessment_semantic_sha256": a.assessment_semantic_sha256,
            "report_sha256": "sha256:" + sha256_hex(outputs["report.html"]),
            "status": a.status,
            "matches_oracle": result.matched,
        }
    comparison = None
    if (
        scenario is None
        and "S02-missing-inspection/assessment.json" in files
        and "S06-corrected/assessment.json" in files
    ):
        before = json.loads(files["S02-missing-inspection/assessment.json"])
        after = json.loads(files["S06-corrected/assessment.json"])
        comparison = compare(before, after)
        files[f"{COMPARISON_DIR}/comparison.json"] = json_bytes(comparison)
    if scenario is None:
        rows = [
            {
                "id": sid,
                "expected": scenarios[sid]["status"],
                "actual": r.status,
                "match": r.matched,
                "report": r.evaluation is not None,
            }
            for sid, r in results.items()
        ]
        env = jinja2.Environment(
            autoescape=True, keep_trailing_newline=True, undefined=jinja2.StrictUndefined
        )
        index = env.from_string(INDEX_TEMPLATE).render(rows=rows, comparison=comparison)
        files["index.html"] = index.encode("utf-8")
        files["evaluation_ids.json"] = json_bytes(ids_doc)

    prepare_out(out, force, own_dirs)
    write_files(out, files)
    for sid, r in results.items():
        verdict = "matches oracle" if r.matched else "DOES NOT MATCH ORACLE: " + "; ".join(r.problems[:3])
        say(f"{sid:38s} {r.status:17s} {verdict}")
    if scenario is None:
        return EXIT_OK if all(r.matched for r in results.values()) else EXIT_DEMO_MISMATCH
    only = results[scenario]
    if not only.matched or only.evaluation is None:
        return EXIT_DEMO_MISMATCH
    return STATUS_EXIT[only.evaluation.assessment.status]
