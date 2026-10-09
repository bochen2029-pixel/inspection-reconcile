"""Reason-code catalog: explanation and resolution templates (SPEC §10, §22.4)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

TEMPLATES: dict[str, tuple[str, str]] = {
    # R0
    "INTEGRITY_OK": (
        "No duplicate keys, conflicting current revisions, invalid values or dangling references were found.",
        "",
    ),
    "DUPLICATE_KEY": (
        "{dataset} key {key} appears {count} times ({identical_note}).",
        "Remove or correct the duplicate records in the source.",
    ),
    "MULTIPLE_CURRENT_REVISIONS": (
        "{entity} {id} has {count} revisions marked current: {revisions}.",
        "Mark exactly one revision current.",
    ),
    "COMPLETED_WITHOUT_TIMESTAMP": (
        "Inspection {id} revision {revision} is current and completed but has no completion time.",
        "Record the completion time.",
    ),
    "INVALID_VALUE": ("{subject}: {violations}.", "Correct the value(s) in the source, or the mapping."),
    "UNMAPPED_VALUE": (
        "{subject}: {violations}; the mapping has no entry for these source values.",
        "Confirm the values' meaning and extend the mapping's value map.",
    ),
    "INVALID_PATH": ("{subject}: {violations}; the file was not opened.", "Correct the inventory path."),
    "MALFORMED_ROW": (
        "A row in {dataset} has {field_count} fields but the header has {header_count} ({count} such row(s)).",
        "Repair the row in the source export.",
    ),
    "TIMESTAMP_AFTER_AS_OF": (
        "{subject}: {field} {value} is later than the evaluation time {as_of}.",
        "Check the time zone mapping, the source value, or the capture time.",
    ),
    "DANGLING_REFERENCE": (
        "{subject} references {missing}, absent from a dataset declared complete.",
        "Capture the referenced record(s), or correct the reference.",
    ),
    # R1
    "SCOPE_ESTABLISHED": (
        "Accepted scope {scope_revision} for project {project_id} lists {count} obligations.",
        "",
    ),
    "SCOPE_MISSING": (
        "No accepted scope was supplied, so readiness cannot be assessed.",
        "Supply the accepted scope for this project.",
    ),
    "SCOPE_INVALID": (
        "The scope contains rows whose obligation could not be identified.",
        "Correct the scope rows.",
    ),
    "SCOPE_EMPTY": (
        "The scope lists no obligations; an empty scope cannot be ready.",
        "Supply the scope's obligations.",
    ),
    "SCOPE_NOT_ACCEPTED": (
        "Scope {scope_revision} has no acceptance record.",
        "Record who accepted the scope, and when.",
    ),
    "SCOPE_REVISION_MISMATCH": (
        "The scope revisions disagree: manifest {m}, rows {r}, policy {p}.",
        "Align the scope revision with the policy.",
    ),
    "SCOPE_PROJECT_MISMATCH": (
        "Scope rows name project(s) {projects}; expected {project_id}.",
        "Correct the scope rows.",
    ),
    "COVERAGE_COMPLETE": ("Dataset {dataset} is complete for the declared scope (basis: {basis}).", ""),
    "DATASET_MISSING": ("Dataset {dataset} was not supplied.", "Capture {dataset} for the declared scope."),
    "COVERAGE_PARTIAL": (
        "Dataset {dataset} is declared partial (basis: {basis}).",
        "Capture {dataset} completely for the declared scope.",
    ),
    "COVERAGE_UNVERIFIED": (
        "The completeness of dataset {dataset} is unverified (basis: {basis}).",
        "Establish completeness with a basis the policy accepts.",
    ),
    "COVERAGE_BASIS_NOT_ACCEPTED": (
        "Dataset {dataset} is declared complete on basis {basis}, which the policy does not accept.",
        "Re-capture with an accepted basis, or amend the policy deliberately.",
    ),
    "CHANGED_DURING_CAPTURE": (
        "Records in {dataset} changed while it was being captured.",
        "Re-capture when the source is stable.",
    ),
    "COVERAGE_CONTRADICTED": (
        "Dataset {dataset} is declared complete, but {n} reference(s) point to records it does not contain.",
        "Capture the missing records, or correct the references.",
    ),
    "UNATTRIBUTABLE_RECORDS": (
        "Dataset {dataset} contains {n} row(s) that cannot be attributed to an obligation.",
        "Correct those rows.",
    ),
    # R2
    "SINGLE_CURRENT_COMPLETED": (
        "Obligation {obligation_id} has exactly one current completed inspection: {inspection_id} revision {revision}.",
        "",
    ),
    "NO_CURRENT_INSPECTION": (
        "Obligation {obligation_id} requires a {activity_kind} inspection of asset {asset_id}. No current inspection "
        "references it in the inspections dataset, which is complete for scope {scope_revision}.",
        "Record or link the inspection for {obligation_id}, or remove {obligation_id} from the scope through an "
        "accepted scope revision.",
    ),
    "MULTIPLE_CURRENT": (
        "Obligation {obligation_id} has {count} current inspections ({inspections}); exactly one is required.",
        "Mark superseded inspections not current, or correct their obligation links.",
    ),
    "NOT_COMPLETED": (
        "The current inspection {inspection_id} for obligation {obligation_id} has status {completion_status}.",
        "Complete the inspection, or correct its status.",
    ),
    "ABSENCE_UNCONFIRMED": (
        "No current inspection for obligation {obligation_id} was captured, but the inspections dataset is {cov}; "
        "the record may exist uncaptured.",
        "Capture the complete inspections dataset, or confirm the absence in the source.",
    ),
    "CARDINALITY_UNCONFIRMED": (
        "One current completed inspection ({inspection_id}) was captured for obligation {obligation_id}, but the "
        "inspections dataset is {cov}; another current inspection could exist.",
        "Capture the complete inspections dataset.",
    ),
    # R3
    "IDENTITY_CONSISTENT": (
        "Inspection {inspection_id} and its {n} current artifact(s) match obligation {obligation_id}: project "
        "{project_id}, asset {asset_id}, activity {activity_kind}.",
        "",
    ),
    "INSPECTION_MISMATCH": (
        "Inspection {inspection_id} names {field} {observed}, but obligation {obligation_id} requires {expected}.",
        "Correct the inspection's {field}, or link it to the correct obligation.",
    ),
    "ARTIFACT_MISMATCH": (
        "Artifact {artifact_id} ({document_kind}) attached to inspection {inspection_id} names {field} {observed}, "
        "but obligation {obligation_id} requires {expected}.",
        "Attach the correct document, or correct the artifact's {field}.",
    ),
    "IDENTITY_UNCONFIRMED": (
        "No identity conflict was found among the captured records for obligation {obligation_id}, but the "
        "artifacts dataset is {cov}.",
        "Capture the complete artifacts dataset.",
    ),
    # R4
    "REQUIRED_KINDS_PRESENT": (
        "Inspection {inspection_id} has current artifacts of every required kind: {kinds}.",
        "",
    ),
    "KIND_MISSING": (
        "Inspection {inspection_id} has no current artifact of the required kind(s) {missing_kinds}; the artifacts "
        "dataset is complete.",
        "Attach the missing document(s), or correct their kind or current flag.",
    ),
    "KIND_ABSENCE_UNCONFIRMED": (
        "No current {missing_kinds} artifact was captured for inspection {inspection_id}, but the artifacts dataset "
        "is {cov}.",
        "Capture the complete artifacts dataset.",
    ),
    "REQUIREMENT_UNDEFINED": (
        "The requirement pack defines no document requirements for activity {activity_kind}.",
        "Add requirements for {activity_kind} to the pack.",
    ),
    # R5
    "EVIDENCE_AVAILABLE": (
        "All {n} required artifact files for inspection {inspection_id} were read and hashed.",
        "",
    ),
    "FILE_ABSENT": (
        'The file for artifact {artifact_id} ("{path}") is not in the evidence set, which is complete for this '
        "snapshot.{case_hint}",
        "Restore the file, or correct the inventory path.",
    ),
    "NOT_CAPTURED": (
        "The file for artifact {artifact_id} was not captured; the evidence set is {cov}.",
        "Capture the file, or confirm its absence in the source.",
    ),
    "FILE_UNREADABLE": (
        "The file for artifact {artifact_id} exists but could not be read ({detail}).",
        "Fix the file's permissions or format, then re-run.",
    ),
    "FILE_TOO_LARGE": (
        "The file for artifact {artifact_id} exceeds the {limit}-byte limit.",
        "Raise the limit deliberately, or review the file separately.",
    ),
    "AVAILABILITY_UNCONFIRMED": (
        "Every captured required file was available, but the artifacts dataset is {cov}; other required artifacts "
        "could exist.",
        "Capture the complete artifacts dataset.",
    ),
    # R6
    "APPROVAL_COVERS_CURRENT": (
        "Approval {approval_id} ({decided_at}) covers inspection {inspection_id} revision {revision} and its current "
        "required evidence {digest_short} (binding: {binding}).{binding_note}",
        "",
    ),
    "NO_APPROVAL": (
        "No approval decision exists for inspection {inspection_id}; the approvals dataset is complete.",
        "Obtain a review decision.",
    ),
    "REVISION_NOT_APPROVED": (
        "Approvals exist for inspection {inspection_id} revision(s) {approved_revisions}, but not for the current "
        "revision {revision}.",
        "Obtain approval for revision {revision}.",
    ),
    "EVIDENCE_CHANGED_SINCE_APPROVAL": (
        "No decision on inspection {inspection_id} revision {revision} covers its current required evidence "
        "{digest_short}; decision(s) {approval_ids} bind different evidence. The evidence changed after approval.",
        "Re-review the current evidence, or restore the approved version.",
    ),
    "APPROVAL_DECISION": (
        "The latest decision on the current evidence for inspection {inspection_id} is {decision} ({approval_id}, "
        "{decided_at}).",
        "Resolve the reviewer's decision.",
    ),
    "APPROVAL_UNCONFIRMED": (
        "The approvals dataset is {cov}; a later decision could exist.",
        "Capture the complete approvals dataset.",
    ),
    "BINDING_UNAVAILABLE": (
        "Approval {approval_id} does not record which evidence it covers.",
        "Record the evidence digest (see `evidence-digest`) or the approved artifact revisions.",
    ),
    "BINDING_STRENGTH_INSUFFICIENT": (
        "Approval {approval_id} binds artifact revisions only; the pack requires a content-digest binding.",
        "Record the evidence digest (see `evidence-digest`), or use a pack that accepts revision binding.",
    ),
    "CONFLICTING_DECISIONS": (
        "Decisions {approval_ids} on the current evidence share the timestamp {decided_at} but disagree.",
        "Correct the decision records.",
    ),
    # R7
    "NO_UNMATCHED_RECORDS": (
        "Every captured current inspection references an obligation in the accepted scope.",
        "",
    ),
    "UNMATCHED_INSPECTION": (
        "Current inspection {inspection_id} references {obligation_ids}, which is not in accepted scope "
        "{scope_revision}. (Advisory.)",
        "Link the inspection to an in-scope obligation, or review whether the scope is stale.",
    ),
    # any
    "BLOCKED_BY_UPSTREAM": ("Not evaluated because {blocked_by} did not pass.", "Resolve {blocked_by}."),
}

# Reason codes whose template is shared (SPEC §10 rows that list several codes).
TEMPLATE_ALIAS = {
    "INSPECTION_PROJECT_MISMATCH": "INSPECTION_MISMATCH",
    "INSPECTION_ASSET_MISMATCH": "INSPECTION_MISMATCH",
    "INSPECTION_ACTIVITY_MISMATCH": "INSPECTION_MISMATCH",
    "ARTIFACT_PROJECT_MISMATCH": "ARTIFACT_MISMATCH",
    "ARTIFACT_ASSET_MISMATCH": "ARTIFACT_MISMATCH",
    "APPROVAL_REJECTED": "APPROVAL_DECISION",
    "APPROVAL_REVOKED": "APPROVAL_DECISION",
}


def render(code: str, **params: Any) -> tuple[str, str]:
    explanation, resolution = TEMPLATES[TEMPLATE_ALIAS.get(code, code)]
    return explanation.format(**params), resolution.format(**params)


def first_and_more(items: Sequence[str]) -> str:
    """SPEC §22.4: the first qualifying subject in sorted order, then ' (and N more)'."""
    ordered = sorted(items)
    if not ordered:
        return ""
    rest = len(ordered) - 1
    return ordered[0] + (f" (and {rest} more)" if rest else "")


def join(items: Sequence[str]) -> str:
    return ", ".join(items)
