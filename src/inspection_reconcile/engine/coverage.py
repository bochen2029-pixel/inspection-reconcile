"""Effective coverage (SPEC §5.8) and R1 · scope and coverage (SPEC §7.6, §22.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from inspection_reconcile.engine.integrity import IntegrityResult
from inspection_reconcile.engine.model import Finding
from inspection_reconcile.io.snapshot import LoadedSnapshot
from inspection_reconcile.policy import Policy
from inspection_reconcile.report.templates import render
from inspection_reconcile.vocab import (
    COMPLETE,
    COVERAGE_DATASETS,
    COVERAGE_REASON_EFFECT,
    COVERAGE_REASON_ORDER,
)


@dataclass(frozen=True)
class Coverage:
    dataset: str
    declared: str | None
    basis: tuple[str, ...]
    consistency: str | None
    effective: str
    reasons: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.effective == COMPLETE

    def observed(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "basis": sorted(self.basis),
            "consistency": self.consistency,
            "effective": self.effective,
            "reasons": list(self.reasons),
        }


def effective_coverage(
    snapshot: LoadedSnapshot, policy: Policy, integrity: IntegrityResult
) -> dict[str, Coverage]:
    """Every applicable reason is collected; the first in the fixed order decides the effective value."""
    out: dict[str, Coverage] = {}
    for name in COVERAGE_DATASETS:
        decl = snapshot.datasets.get(name)
        reasons: set[str] = set()
        if decl is None:
            reasons.add("DATASET_MISSING")
        else:
            if decl.coverage == "partial":
                reasons.add("COVERAGE_PARTIAL")
            elif decl.coverage == "unverified":
                reasons.add("COVERAGE_UNVERIFIED")
            elif not any(accepted <= set(decl.basis) for accepted in policy.accepted_bases):
                reasons.add("COVERAGE_BASIS_NOT_ACCEPTED")
            if decl.consistency == "changed_during_capture":
                reasons.add("CHANGED_DURING_CAPTURE")
        if integrity.contradicted.get(name, 0) > 0:
            reasons.add("COVERAGE_CONTRADICTED")
        if integrity.unattributable.get(name, 0) > 0:
            reasons.add("UNATTRIBUTABLE_RECORDS")
        ordered = tuple(r for r in COVERAGE_REASON_ORDER if r in reasons)
        effective = COMPLETE if not ordered else COVERAGE_REASON_EFFECT[ordered[0]]
        out[name] = Coverage(
            dataset=name,
            declared=decl.coverage if decl else None,
            basis=decl.basis if decl else (),
            consistency=decl.consistency if decl else None,
            effective=effective,
            reasons=ordered,
        )
    return out


def r1_dataset_findings(
    coverage: dict[str, Coverage], integrity: IntegrityResult, system: str
) -> list[Finding]:
    findings = []
    for name in COVERAGE_DATASETS:
        cov = coverage[name]
        basis = ", ".join(sorted(cov.basis)) or "none"
        params = {
            "dataset": name,
            "basis": basis,
            "n": integrity.contradicted.get(name, 0)
            if cov.reasons and cov.reasons[0] == "COVERAGE_CONTRADICTED"
            else integrity.unattributable.get(name, 0),
        }
        if cov.complete:
            outcome, reason = "PASS", "COVERAGE_COMPLETE"
        else:
            outcome, reason = "UNKNOWN", cov.reasons[0]
        text, res = render(reason, **params)
        findings.append(
            Finding(
                check_id="R1",
                subject_kind="dataset",
                subject_id=name,
                outcome=outcome,
                reason=reason,
                required=True,
                expected={"effective": COMPLETE},
                observed=cov.observed(),
                explanation=text,
                resolution=res,
            )
        )
    return findings


def r1_project_finding(snapshot: LoadedSnapshot, policy: Policy, integrity: IntegrityResult) -> Finding:
    project_id = snapshot.project["project_id"]
    scope = snapshot.scope
    scope_rows = integrity.rows["scope"]
    live = [r for r in scope_rows if not r.quarantined]
    row_revisions = sorted({r.text("scope_revision") for r in live})
    row_projects = sorted({r.text("project_id") for r in live})
    present = snapshot.scope_present
    manifest_revision = scope.scope_revision if scope is not None else None
    accepted = scope.accepted if scope is not None else None
    count = len(integrity.obligations)
    observed = {
        "scope_present": present,
        "accepted": accepted is not None,
        "obligation_count": count,
        "manifest_scope_revision": manifest_revision,
        "row_scope_revisions": row_revisions,
        "row_project_ids": row_projects,
    }
    expected = {"project_id": policy.project_id, "scope_revision": policy.scope_revision}
    params: dict[str, Any] = {"project_id": project_id, "scope_revision": manifest_revision, "count": count}
    if not present:
        reason = "SCOPE_MISSING"
    elif integrity.scope_unattributable:
        reason = "SCOPE_INVALID"
    elif count == 0:
        reason = "SCOPE_EMPTY"
    elif accepted is None:
        reason = "SCOPE_NOT_ACCEPTED"
    elif manifest_revision != policy.scope_revision or any(r != manifest_revision for r in row_revisions):
        reason = "SCOPE_REVISION_MISMATCH"
        params.update(m=manifest_revision, r=", ".join(row_revisions) or "none", p=policy.scope_revision)
    elif any(p != project_id for p in row_projects):
        reason = "SCOPE_PROJECT_MISMATCH"
        params.update(projects=", ".join(row_projects))
    else:
        reason = "SCOPE_ESTABLISHED"
    outcome = "PASS" if reason == "SCOPE_ESTABLISHED" else "UNKNOWN"
    text, res = render(reason, **params)
    evidence = sorted({("scope", r.raw.locator) for r in scope_rows})
    return Finding(
        check_id="R1",
        subject_kind="project",
        subject_id=project_id,
        outcome=outcome,
        reason=reason,
        required=True,
        expected=expected,
        observed=observed,
        explanation=text,
        resolution=res,
        evidence=[{"system": snapshot.source["system"], "dataset": d, "locator": loc} for d, loc in evidence],
    )
