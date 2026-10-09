"""Orchestration (SPEC §7.1): R0, probes, R1, per-obligation R2–R6 with dependencies, R7, status, identities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from inspection_reconcile.canonical import digest_of
from inspection_reconcile.engine import identity
from inspection_reconcile.engine.checks import not_evaluated
from inspection_reconcile.engine.context import Context
from inspection_reconcile.engine.coverage import Coverage, r1_dataset_findings, r1_project_finding
from inspection_reconcile.engine.integrity import Integrity
from inspection_reconcile.engine.model import Finding
from inspection_reconcile.engine.registry import CHECKS, OPTIONS
from inspection_reconcile.errors import RunError
from inspection_reconcile.grammar import format_ts
from inspection_reconcile.io.evidence import EvidenceStore
from inspection_reconcile.io.snapshot import LoadedSnapshot
from inspection_reconcile.policy import Policy
from inspection_reconcile.vocab import COVERAGE_DATASETS, OUTCOMES


@dataclass
class Assessment:
    status: str
    as_of: str
    findings: list[Finding]
    coverage: dict[str, Coverage]
    snapshot_semantic_sha256: str
    evaluation_id: str
    assessment_semantic_sha256: str
    obligation_count: int
    engine_version: str
    snapshot: LoadedSnapshot
    policy: Policy

    def counts(self) -> dict[str, dict[str, int]]:
        def tally(findings: list[Finding]) -> dict[str, int]:
            c = Counter(f.outcome for f in findings)
            return {o: c.get(o, 0) for o in OUTCOMES}

        required = [f for f in self.findings if f.required]
        advisory = [f for f in self.findings if not f.required]
        roots = [f for f in required if f.outcome in ("FAIL", "UNKNOWN") and not f.caused_by]
        root_counts = Counter(f.outcome for f in roots)
        return {
            "by_outcome": tally(self.findings),
            "required": tally(required),
            "advisory": tally(advisory),
            "roots": {"FAIL": root_counts.get("FAIL", 0), "UNKNOWN": root_counts.get("UNKNOWN", 0)},
        }

    def by_key(self) -> dict[str, Finding]:
        return {f.key: f for f in self.findings}


def project_status(findings: list[Finding]) -> str:
    required = [f for f in findings if f.required]
    if any(f.outcome == "FAIL" for f in required):
        return "BLOCKED"
    if any(f.outcome in ("UNKNOWN", "NOT_EVALUATED") for f in required):
        return "UNKNOWN"
    return "READY_FOR_REVIEW"


def assess(
    snapshot: LoadedSnapshot,
    policy: Policy,
    evidence: EvidenceStore,
    as_of: datetime,
    engine_version: str,
) -> Assessment:
    if snapshot.project["project_id"] != policy.project_id:
        raise RunError(
            "PROJECT_MISMATCH",
            f"project.json names {snapshot.project['project_id']}, the policy names {policy.project_id}",
        )
    if as_of < policy.effective_from_dt:
        raise RunError(
            "POLICY_NOT_EFFECTIVE",
            f"as_of {format_ts(as_of)} is before the pack's effective_from {policy.effective_from}",
        )

    integrity = Integrity(snapshot, as_of, detect_duplicates=OPTIONS["detect_duplicates"]).run()
    coverage: dict[str, Coverage] = CHECKS["EFFECTIVE_COVERAGE"](snapshot, policy, integrity)
    ctx = Context(snapshot, policy, evidence, as_of, integrity, coverage)
    ctx.build_indexes()

    findings: list[Finding] = list(integrity.findings)
    r1_project = r1_project_finding(snapshot, policy, integrity)
    findings.append(r1_project)
    findings.extend(r1_dataset_findings(coverage, integrity, snapshot.source["system"]))

    for obligation in sorted(integrity.obligations):
        findings.extend(_obligation_chain(ctx, obligation, r1_project))

    if r1_project.outcome != "PASS":
        findings.append(not_evaluated("R7", "snapshot", "all", [r1_project.key], required=False))
    else:
        findings.extend(CHECKS["R7"](ctx))

    findings.sort(key=lambda f: f.sort_key())
    keys = [f.key for f in findings]
    if len(keys) != len(set(keys)):  # I-13: a duplicate key is an engine defect, never a valid assessment
        raise RunError("ENGINE_DUPLICATE_KEY", "two findings share a key")

    semantic = identity.snapshot_semantic(snapshot, integrity, evidence)
    semantic_sha = digest_of(semantic)
    as_of_text = format_ts(as_of)
    evaluation = identity.evaluation_id(engine_version, policy.sha256, semantic_sha, as_of_text)
    return Assessment(
        status=project_status(findings),
        as_of=as_of_text,
        findings=findings,
        coverage=coverage,
        snapshot_semantic_sha256=semantic_sha,
        evaluation_id=evaluation,
        assessment_semantic_sha256=identity.assessment_semantic(findings),
        obligation_count=len(integrity.obligations),
        engine_version=engine_version,
        snapshot=snapshot,
        policy=policy,
    )


def _obligation_chain(ctx: Context, obligation: str, r1_project: Finding) -> list[Finding]:
    """SPEC §7.4: R2 → R3 → (R4, R5) → R6 for one obligation."""
    attributed = ctx.integrity.attributed.get(obligation, [])
    if r1_project.outcome != "PASS":
        r2 = not_evaluated("R2", "obligation", obligation, [r1_project.key])
        candidate = None
    elif attributed:
        r2 = not_evaluated("R2", "obligation", obligation, attributed)
        candidate = None
    else:
        result: Any = CHECKS["R2"](ctx, obligation)
        r2, candidate = result.finding, result.candidate
        if not (r2.outcome == "PASS" or (r2.outcome == "UNKNOWN" and r2.reason == "CARDINALITY_UNCONFIRMED")):
            candidate = None

    if candidate is None:
        r3 = not_evaluated("R3", "obligation", obligation, [r2.key])
    else:
        r3 = CHECKS["R3"](ctx, obligation, candidate)
    r3_ok = r3.outcome in ("PASS", "UNKNOWN")

    required: list[Any] = []
    if r3_ok and candidate is not None:
        r4_result: Any = CHECKS["R4"](ctx, obligation, candidate)
        r4 = r4_result.finding
        required = r4_result.required_artifacts
    else:
        r4 = not_evaluated("R4", "obligation", obligation, [r3.key])

    probes: dict[str, Any] = {}
    if r3_ok and candidate is not None and required:
        r5_result: Any = CHECKS["R5"](ctx, obligation, candidate, required)
        r5 = r5_result.finding
        probes = r5_result.probes
    else:
        r5 = not_evaluated("R5", "obligation", obligation, [r3.key] if not r3_ok else [r4.key])

    if r4.outcome == "PASS" and r5.outcome == "PASS" and candidate is not None:
        r6 = CHECKS["R6"](ctx, obligation, candidate, required, probes)
    else:
        r6 = not_evaluated("R6", "obligation", obligation, [f.key for f in (r4, r5) if f.outcome != "PASS"])
    return [r2, r3, r4, r5, r6]


def coverage_table(assessment: Assessment) -> dict[str, dict[str, Any]]:
    return {name: assessment.coverage[name].observed() for name in COVERAGE_DATASETS}
