"""R2–R7 (SPEC §7.7–§7.12, §22). Each check is a pure function of the context and one obligation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from inspection_reconcile.canonical import evidence_set_digest, short, sort_canonical
from inspection_reconcile.engine.context import Context
from inspection_reconcile.engine.model import Finding, Row
from inspection_reconcile.io.evidence import FileProbe, Present
from inspection_reconcile.report.templates import first_and_more, render

R3_ORDER = (
    ("inspection", "project_id", "INSPECTION_PROJECT_MISMATCH"),
    ("inspection", "asset_id", "INSPECTION_ASSET_MISMATCH"),
    ("inspection", "activity_kind", "INSPECTION_ACTIVITY_MISMATCH"),
    ("artifact", "project_id", "ARTIFACT_PROJECT_MISMATCH"),
    ("artifact", "asset_id", "ARTIFACT_ASSET_MISMATCH"),
)


def rev_id(row: Row, id_col: str) -> str:
    return f"{row.text(id_col)}@{row.text('revision')}"


def not_evaluated(
    check_id: str, kind: str, subject_id: str, blocked_by: list[str], required: bool = True
) -> Finding:
    blockers = sorted(blocked_by)
    text, res = render("BLOCKED_BY_UPSTREAM", blocked_by=", ".join(blockers))
    return Finding(
        check_id,
        kind,
        subject_id,
        "NOT_EVALUATED",
        "BLOCKED_BY_UPSTREAM",
        required,
        {},
        {},
        text,
        res,
        blockers,
    )


def _finding(
    check_id: str,
    obligation: str,
    outcome: str,
    reason: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    params: dict[str, Any],
    evidence: list[dict[str, str]],
    caused_by: list[str] | None = None,
) -> Finding:
    text, res = render(reason, **params)
    return Finding(
        check_id,
        "obligation",
        obligation,
        outcome,
        reason,
        True,
        expected,
        observed,
        text,
        res,
        [],
        sorted(caused_by or []),
        evidence,
    )


# ------------------------------------------------------------------------------------------------ R2


@dataclass
class R2Result:
    finding: Finding
    candidate: Row | None


def r2_cardinality(ctx: Context, obligation: str) -> R2Result:
    current = sorted(ctx.current_by_obligation.get(obligation, []), key=lambda r: rev_id(r, "inspection_id"))
    superseded = ctx.superseded_by_obligation.get(obligation, [])
    scope_row = ctx.scope_row(obligation)
    complete = ctx.complete("inspections")
    observed = {
        "current_inspections": [rev_id(r, "inspection_id") for r in current],
        "superseded_revisions": len(superseded),
    }
    expected = {"current_completed_inspections": 1}
    evidence = ctx.locators([scope_row, *current, *superseded])
    params: dict[str, Any] = {
        "obligation_id": obligation,
        "activity_kind": scope_row.text("activity_kind"),
        "asset_id": scope_row.text("asset_id"),
        "scope_revision": ctx.scope_revision,
        "cov": ctx.cov_word("inspections"),
    }
    caused = [] if complete else ["R1:dataset:inspections"]
    if len(current) >= 2:
        params.update(
            count=len(current),
            inspections=", ".join(
                f"{r.text('inspection_id')} revision {r.text('revision')}" for r in current
            ),
        )
        f = _finding("R2", obligation, "FAIL", "MULTIPLE_CURRENT", expected, observed, params, evidence)
        return R2Result(f, None)
    if len(current) == 1:
        row = current[0]
        params.update(
            inspection_id=row.text("inspection_id"),
            revision=row.text("revision"),
            completion_status=row.text("completion_status"),
        )
        if row.get("completion_status") != "completed":
            f = _finding("R2", obligation, "FAIL", "NOT_COMPLETED", expected, observed, params, evidence)
            return R2Result(f, None)
        if complete:
            f = _finding(
                "R2", obligation, "PASS", "SINGLE_CURRENT_COMPLETED", expected, observed, params, evidence
            )
        else:
            f = _finding(
                "R2",
                obligation,
                "UNKNOWN",
                "CARDINALITY_UNCONFIRMED",
                expected,
                observed,
                params,
                evidence,
                caused,
            )
        return R2Result(f, row)
    if complete:
        f = _finding("R2", obligation, "FAIL", "NO_CURRENT_INSPECTION", expected, observed, params, evidence)
    else:
        f = _finding(
            "R2", obligation, "UNKNOWN", "ABSENCE_UNCONFIRMED", expected, observed, params, evidence, caused
        )
    return R2Result(f, None)


# ------------------------------------------------------------------------------------------------ R3


def r3_identity(ctx: Context, obligation: str, candidate: Row) -> Finding:
    scope_row = ctx.scope_row(obligation)
    want = {
        "project_id": scope_row.text("project_id"),
        "asset_id": scope_row.text("asset_id"),
        "activity_kind": scope_row.text("activity_kind"),
    }
    artifacts = sorted(
        ctx.current_artifacts_by_inspection.get(candidate.text("inspection_id"), []),
        key=lambda r: rev_id(r, "artifact_id"),
    )
    mismatches: list[dict[str, Any]] = []
    first_reason: str | None = None
    for subject_kind, column, reason in R3_ORDER:
        rows = [candidate] if subject_kind == "inspection" else artifacts
        for row in rows:
            value = row.text(column)
            if value != want[column]:
                id_col = "inspection_id" if subject_kind == "inspection" else "artifact_id"
                mismatches.append(
                    {
                        "subject": f"{subject_kind}:{rev_id(row, id_col)}",
                        "field": column,
                        "expected": want[column],
                        "observed": value,
                    }
                )
                if first_reason is None:
                    first_reason = reason
    mismatches.sort(key=lambda m: (m["subject"], m["field"]))
    evidence = ctx.locators([scope_row, candidate, *artifacts])
    observed = {"mismatches": mismatches}
    params: dict[str, Any] = {
        "obligation_id": obligation,
        "inspection_id": candidate.text("inspection_id"),
        "n": len(artifacts),
        "cov": ctx.cov_word("artifacts"),
        **want,
    }
    if first_reason is not None:
        subject_kind = "inspection" if first_reason.startswith("INSPECTION") else "artifact"
        column = {"PROJECT": "project_id", "ASSET": "asset_id", "ACTIVITY": "activity_kind"}[
            first_reason.split("_")[1]
        ]
        same = [m for m in mismatches if m["subject"].startswith(subject_kind + ":") and m["field"] == column]
        first = same[0]
        params.update(field=column, observed=first["observed"], expected=first["expected"])
        if subject_kind == "artifact":
            ids = [m["subject"].split(":", 1)[1].split("@", 1)[0] for m in same]
            art = next(r for r in artifacts if r.text("artifact_id") == sorted(ids)[0])
            params.update(artifact_id=first_and_more(ids), document_kind=art.text("document_kind"))
        return _finding("R3", obligation, "FAIL", first_reason, want, observed, params, evidence)
    if ctx.complete("artifacts"):
        return _finding("R3", obligation, "PASS", "IDENTITY_CONSISTENT", want, observed, params, evidence)
    return _finding(
        "R3",
        obligation,
        "UNKNOWN",
        "IDENTITY_UNCONFIRMED",
        want,
        observed,
        params,
        evidence,
        ["R1:dataset:artifacts"],
    )


# ------------------------------------------------------------------------------------------------ R4


@dataclass
class R4Result:
    finding: Finding
    required_artifacts: list[Row]


def r4_required_artifacts(ctx: Context, obligation: str, candidate: Row) -> R4Result:
    activity = candidate.text("activity_kind")
    kinds = ctx.policy.requirements.get(activity)
    artifacts = ctx.current_artifacts_by_inspection.get(candidate.text("inspection_id"), [])
    params: dict[str, Any] = {
        "inspection_id": candidate.text("inspection_id"),
        "activity_kind": activity,
        "cov": ctx.cov_word("artifacts"),
    }
    if kinds is None:
        observed: dict[str, Any] = {"kinds": {}, "missing_kinds": []}
        evidence = ctx.locators([candidate])
        f = _finding(
            "R4",
            obligation,
            "UNKNOWN",
            "REQUIREMENT_UNDEFINED",
            {"document_kinds": None},
            observed,
            params,
            evidence,
        )
        return R4Result(f, [])
    required = sorted(
        (r for r in artifacts if r.text("document_kind") in kinds), key=lambda r: rev_id(r, "artifact_id")
    )
    by_kind: dict[str, list[str]] = {}
    for row in required:
        by_kind.setdefault(row.text("document_kind"), []).append(row.text("artifact_id"))
    witnessed = {k: sorted(v) for k, v in sorted(by_kind.items())}
    missing = sorted(k for k in kinds if k not in witnessed)
    observed = {"kinds": witnessed, "missing_kinds": missing}
    expected = {"document_kinds": sorted(kinds)}
    evidence = ctx.locators([candidate, *required])
    params.update(kinds=", ".join(sorted(kinds)), missing_kinds=", ".join(missing))
    if not missing:
        f = _finding("R4", obligation, "PASS", "REQUIRED_KINDS_PRESENT", expected, observed, params, evidence)
    elif ctx.complete("artifacts"):
        f = _finding("R4", obligation, "FAIL", "KIND_MISSING", expected, observed, params, evidence)
    else:
        f = _finding(
            "R4",
            obligation,
            "UNKNOWN",
            "KIND_ABSENCE_UNCONFIRMED",
            expected,
            observed,
            params,
            evidence,
            ["R1:dataset:artifacts"],
        )
    return R4Result(f, required)


# ------------------------------------------------------------------------------------------------ R5


@dataclass
class R5Result:
    finding: Finding
    probes: dict[str, FileProbe]


def r5_availability(ctx: Context, obligation: str, candidate: Row, required: list[Row]) -> R5Result:
    evidence_complete = ctx.complete("evidence_files")
    probes: dict[str, FileProbe] = {}
    files: list[dict[str, Any]] = []
    absent: list[Row] = []
    unknown: dict[str, list[Row]] = {"NOT_CAPTURED": [], "FILE_UNREADABLE": [], "FILE_TOO_LARGE": []}
    for row in required:
        rid = rev_id(row, "artifact_id")
        path = row.get("relative_path")
        probe = ctx.evidence.probe(path if isinstance(path, str) else None)
        probes[rid] = probe
        entry: dict[str, Any] = {"artifact": rid, "status": probe.status}
        if isinstance(probe, Present):
            entry["sha256"] = probe.sha256
        files.append(entry)
        if probe.status in ("absent", "no_path"):
            if evidence_complete:
                absent.append(row)
            else:
                unknown["NOT_CAPTURED"].append(row)
        elif probe.status == "unreadable":
            unknown["FILE_UNREADABLE"].append(row)
        elif probe.status == "too_large":
            unknown["FILE_TOO_LARGE"].append(row)
    files.sort(key=lambda f: f["artifact"])
    observed = {"files": files}
    expected = {"files": len(required)}
    evidence = ctx.locators([candidate, *required])
    params: dict[str, Any] = {
        "inspection_id": candidate.text("inspection_id"),
        "n": len(required),
        "limit": ctx.policy.max_file_bytes,
    }

    def named(rows: list[Row]) -> str:
        return first_and_more([r.text("artifact_id") for r in rows])

    if absent:
        first = sorted(absent, key=lambda r: rev_id(r, "artifact_id"))[0]
        probe = probes[rev_id(first, "artifact_id")]
        hint = getattr(probe, "case_hint", None)
        path = first.get("relative_path")
        params.update(
            artifact_id=named(absent),
            path=path if isinstance(path, str) else "no path recorded",
            case_hint=f" A file differing only in letter case exists: {hint}." if hint else "",
        )
        f = _finding("R5", obligation, "FAIL", "FILE_ABSENT", expected, observed, params, evidence)
        return R5Result(f, probes)
    for code in ("NOT_CAPTURED", "FILE_UNREADABLE", "FILE_TOO_LARGE"):
        rows = unknown[code]
        if not rows:
            continue
        first = sorted(rows, key=lambda r: rev_id(r, "artifact_id"))[0]
        probe = probes[rev_id(first, "artifact_id")]
        params.update(
            artifact_id=named(rows),
            detail=getattr(probe, "detail", ""),
            cov=ctx.cov_word("evidence_files"),
        )
        caused = ["R1:dataset:evidence_files"] if code == "NOT_CAPTURED" else []
        f = _finding("R5", obligation, "UNKNOWN", code, expected, observed, params, evidence, caused)
        return R5Result(f, probes)
    if not ctx.complete("artifacts"):
        params.update(cov=ctx.cov_word("artifacts"))
        f = _finding(
            "R5",
            obligation,
            "UNKNOWN",
            "AVAILABILITY_UNCONFIRMED",
            expected,
            observed,
            params,
            evidence,
            ["R1:dataset:artifacts"],
        )
        return R5Result(f, probes)
    f = _finding("R5", obligation, "PASS", "EVIDENCE_AVAILABLE", expected, observed, params, evidence)
    return R5Result(f, probes)


# ------------------------------------------------------------------------------------------------ R6


def digest_matches(approval_digest: str, current: str) -> bool:
    """The comparison seam used by fault injection FI-2."""
    return approval_digest == current


def r6_approval(
    ctx: Context, obligation: str, candidate: Row, required: list[Row], probes: dict[str, FileProbe]
) -> Finding:
    entries = []
    for row in required:
        probe = probes[rev_id(row, "artifact_id")]
        assert isinstance(probe, Present)
        entries.append(
            {
                "artifact_id": row.text("artifact_id"),
                "document_kind": row.text("document_kind"),
                "revision": row.text("revision"),
                "sha256": probe.sha256,
            }
        )
    d = evidence_set_digest(entries)
    current_revs = sort_canonical(
        {"artifact_id": r.text("artifact_id"), "revision": r.text("revision")} for r in required
    )
    required_ids = {r.text("artifact_id") for r in required}
    r_set = {(r.text("artifact_id"), r.text("revision")) for r in required}
    inspection_id = candidate.text("inspection_id")
    revision = candidate.text("revision")
    approvals = sorted(
        ctx.approvals_by_inspection.get(inspection_id, []), key=lambda r: r.text("approval_id")
    )
    item_rows = [i for a in approvals for i in ctx.items_by_approval.get(a.text("approval_id"), [])]
    evidence = ctx.locators([candidate, *approvals, *item_rows])
    params: dict[str, Any] = {
        "inspection_id": inspection_id,
        "revision": revision,
        "digest_short": short(d),
        "cov": ctx.cov_word("approvals"),
        "binding": ctx.policy.required_binding,
        "binding_note": "",
    }
    observed: dict[str, Any] = {
        "binding": ctx.policy.required_binding,
        "current_evidence_digest": d,
        "current_artifact_revisions": current_revs,
        "decision_approvals": [],
    }
    expected: dict[str, Any] = {"approved_evidence_digests": [], "approved_artifact_revisions": []}

    def done(outcome: str, reason: str, caused: list[str] | None = None) -> Finding:
        return _finding("R6", obligation, outcome, reason, expected, observed, params, evidence, caused)

    if not ctx.complete("approvals"):
        return done("UNKNOWN", "APPROVAL_UNCONFIRMED", ["R1:dataset:approvals"])
    if not approvals:
        return done("FAIL", "NO_APPROVAL")
    acur = [a for a in approvals if a.text("inspection_revision") == revision]
    if not acur:
        revs = sorted({a.text("inspection_revision") for a in approvals})
        observed["approved_revisions"] = revs
        params.update(approved_revisions=", ".join(revs))
        return done("FAIL", "REVISION_NOT_APPROVED")

    match: list[Row] = []
    mismatch: list[tuple[Row, str, Any]] = []
    undetermined: list[tuple[Row, str]] = []
    for a in acur:
        digest = a.get("evidence_digest")
        items = ctx.items_by_approval.get(a.text("approval_id"), [])
        if isinstance(digest, str):
            if digest_matches(digest, d):
                match.append(a)
            else:
                mismatch.append((a, "digest", digest))
        elif items:
            if ctx.policy.required_binding == "digest":
                undetermined.append((a, "BINDING_STRENGTH_INSUFFICIENT"))
            else:
                j = {
                    (i.text("artifact_id"), i.text("artifact_revision"))
                    for i in items
                    if i.text("artifact_id") in required_ids
                }
                if j == r_set:
                    match.append(a)
                else:
                    revs = sort_canonical({"artifact_id": x, "revision": y} for x, y in j)
                    mismatch.append((a, "revisions", revs))
        else:
            undetermined.append((a, "BINDING_UNAVAILABLE"))

    def binding_of(a: Row) -> str:
        return "digest" if isinstance(a.get("evidence_digest"), str) else "revisions"

    classified = {binding_of(a) for a in match} | {b for _, b, _ in mismatch}
    if len(classified) == 1:
        observed["binding"] = next(iter(classified))
    elif len(classified) > 1:
        observed["binding"] = "mixed"
    params["binding"] = observed["binding"]

    def order(a: Row) -> tuple[str, str]:
        return (a.text("decided_at"), a.text("approval_id"))

    if not match:
        if undetermined:
            first, code = sorted(undetermined, key=lambda u: order(u[0]))[0]
            observed["decision_approvals"] = [first.text("approval_id")]
            params.update(approval_id=first.text("approval_id"))
            return done("UNKNOWN", code)
        expected["approved_evidence_digests"] = sorted({v for _, b, v in mismatch if b == "digest"})
        expected["approved_artifact_revisions"] = sort_canonical(
            v for _, b, v in mismatch if b == "revisions"
        )
        ids = sorted(a.text("approval_id") for a, _, _ in mismatch)
        observed["decision_approvals"] = ids
        params.update(approval_ids=", ".join(ids))
        return done("FAIL", "EVIDENCE_CHANGED_SINCE_APPROVAL")
    t = max(a.text("decided_at") for a in match)
    later = sorted((u for u in undetermined if u[0].text("decided_at") > t), key=lambda u: order(u[0]))
    if later:
        first, code = later[0]
        observed["decision_approvals"] = [first.text("approval_id")]
        params.update(approval_id=first.text("approval_id"))
        return done("UNKNOWN", code)
    latest = sorted((a for a in match if a.text("decided_at") == t), key=lambda a: a.text("approval_id"))
    ids = [a.text("approval_id") for a in latest]
    observed["decision_approvals"] = ids
    decisions = {a.text("decision") for a in latest}
    params.update(approval_ids=", ".join(ids), decided_at=t, approval_id=first_and_more(ids))
    if len(decisions) > 1:
        return done("UNKNOWN", "CONFLICTING_DECISIONS")
    decision = decisions.pop()
    params.update(decision=decision)
    if decision == "approved":
        if observed["binding"] == "revisions":
            params["binding_note"] = (
                " Byte changes under an unchanged revision label are undetectable in revision binding."
            )
        return done("PASS", "APPROVAL_COVERS_CURRENT")
    return done("FAIL", "APPROVAL_REJECTED" if decision == "rejected" else "APPROVAL_REVOKED")


# ------------------------------------------------------------------------------------------------ R7


def r7_unmatched(ctx: Context) -> list[Finding]:
    obligations = ctx.integrity.obligations
    entities: dict[str, list[Row]] = {}
    for row in ctx.integrity.rows["inspections"]:
        if row.quarantined or not row.is_current:
            continue
        ob = row.values.get("obligation_id")
        if isinstance(ob, str) and ob in obligations:
            continue
        entities.setdefault(row.text("inspection_id"), []).append(row)
    if not entities:
        text, res = render("NO_UNMATCHED_RECORDS")
        return [Finding("R7", "snapshot", "all", "PASS", "NO_UNMATCHED_RECORDS", False, {}, {}, text, res)]
    findings = []
    for entity in sorted(entities):
        rows = sorted(entities[entity], key=lambda r: r.text("revision"))
        obligation_ids = sort_canonical({r.values.get("obligation_id") for r in rows})
        observed = {"current_revisions": [r.text("revision") for r in rows], "obligation_ids": obligation_ids}
        text, res = render(
            "UNMATCHED_INSPECTION",
            inspection_id=entity,
            obligation_ids=", ".join("none" if o is None else str(o) for o in obligation_ids),
            scope_revision=ctx.scope_revision,
        )
        findings.append(
            Finding(
                "R7",
                "inspection",
                entity,
                "FAIL",
                "UNMATCHED_INSPECTION",
                False,
                {},
                observed,
                text,
                res,
                evidence=ctx.locators(rows),
            )
        )
    return findings
