"""Closed vocabularies used across the engine (SPEC §5–§7, §9–§11)."""

from __future__ import annotations

OUTCOMES = ("PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED")
STATUSES = ("READY_FOR_REVIEW", "BLOCKED", "UNKNOWN")

# (id, type, required) — fixed by SPEC §6.1; R0's per-finding requiredness follows §7.5.7.
CHECKS: tuple[tuple[str, str, bool], ...] = (
    ("R0", "input_integrity", True),
    ("R1", "scope_and_coverage", True),
    ("R2", "inspection_cardinality", True),
    ("R3", "identity_consistency", True),
    ("R4", "required_artifacts", True),
    ("R5", "artifact_availability", True),
    ("R6", "approval_binding", True),
    ("R7", "unmatched_records", False),
)
CHECK_TYPES = {cid: ctype for cid, ctype, _ in CHECKS}
CHECK_REQUIRED = {cid: req for cid, _, req in CHECKS}
CHECK_ORDER = {cid: i for i, (cid, _, _) in enumerate(CHECKS)}
PER_OBLIGATION_CHECKS = ("R2", "R3", "R4", "R5", "R6")

COVERAGE_DATASETS = ("inspections", "artifacts", "approvals", "evidence_files")
CSV_DATASETS = ("inspections", "artifacts", "approvals")
COVERAGE_VALUES = ("complete_for_declared_scope", "partial", "unverified")
COMPLETE = "complete_for_declared_scope"
CONSISTENCY_VALUES = ("not_applicable", "stable_verified", "changed_during_capture")
COMPLETION_STATUS = ("completed", "in_progress", "not_started", "cancelled")
DECISIONS = ("approved", "rejected", "revoked")
BINDINGS = ("digest", "revisions")

SUBJECT_KINDS = (
    "snapshot",
    "project",
    "dataset",
    "obligation",
    "scope_row",
    "inspection",
    "artifact",
    "approval",
    "approval_item",
    "unattributable_row",
)
SUBJECT_KIND_RANK = {k: i for i, k in enumerate(SUBJECT_KINDS)}

COVERAGE_REASON_ORDER = (
    "DATASET_MISSING",
    "COVERAGE_PARTIAL",
    "COVERAGE_UNVERIFIED",
    "COVERAGE_BASIS_NOT_ACCEPTED",
    "CHANGED_DURING_CAPTURE",
    "COVERAGE_CONTRADICTED",
    "UNATTRIBUTABLE_RECORDS",
)
COVERAGE_REASON_EFFECT = {
    "DATASET_MISSING": "unverified",
    "COVERAGE_PARTIAL": "partial",
    "COVERAGE_UNVERIFIED": "unverified",
    "COVERAGE_BASIS_NOT_ACCEPTED": "unverified",
    "CHANGED_DURING_CAPTURE": "unverified",
    "COVERAGE_CONTRADICTED": "partial",
    "UNATTRIBUTABLE_RECORDS": "partial",
}

EXIT_OK = 0
EXIT_RUN_ERROR = 2
EXIT_BLOCKED = 10
EXIT_UNKNOWN = 11
EXIT_COMPARE_DIFF = 20
EXIT_DEMO_MISMATCH = 30
STATUS_EXIT = {"READY_FOR_REVIEW": EXIT_OK, "BLOCKED": EXIT_BLOCKED, "UNKNOWN": EXIT_UNKNOWN}

ENUMS: dict[str, tuple[str, ...]] = {"completion_status": COMPLETION_STATUS, "decision": DECISIONS}

# Entity schemas (SPEC §5.5): (column, type, required). Types: ID REV KIND BOOL TS DIGEST TEXT PATH ENUM:<name>.
SCHEMAS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "scope": (
        ("obligation_id", "ID", True),
        ("project_id", "ID", True),
        ("scope_revision", "REV", True),
        ("asset_id", "ID", True),
        ("activity_kind", "KIND", True),
    ),
    "inspections": (
        ("inspection_id", "ID", True),
        ("revision", "REV", True),
        ("is_current", "BOOL", True),
        ("obligation_id", "ID", False),
        ("project_id", "ID", True),
        ("asset_id", "ID", True),
        ("activity_kind", "KIND", True),
        ("completion_status", "ENUM:completion_status", True),
        ("completed_at", "TS", False),
    ),
    "artifacts": (
        ("artifact_id", "ID", True),
        ("revision", "REV", True),
        ("is_current", "BOOL", True),
        ("inspection_id", "ID", True),
        ("project_id", "ID", True),
        ("asset_id", "ID", True),
        ("document_kind", "KIND", True),
        ("relative_path", "PATH", False),
    ),
    "approvals": (
        ("approval_id", "ID", True),
        ("inspection_id", "ID", True),
        ("inspection_revision", "REV", True),
        ("evidence_digest", "DIGEST", False),
        ("decision", "ENUM:decision", True),
        ("decided_at", "TS", True),
        ("decided_by", "TEXT", True),
    ),
    "approval_items": (
        ("approval_id", "ID", True),
        ("artifact_id", "ID", True),
        ("artifact_revision", "REV", True),
    ),
}
KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "scope": ("obligation_id",),
    "inspections": ("inspection_id", "revision"),
    "artifacts": ("artifact_id", "revision"),
    "approvals": ("approval_id",),
    "approval_items": ("approval_id", "artifact_id"),
}
# The cell that attributes a row to its parent (SPEC §7.5.7); scope rows attribute to themselves.
LINK_FIELD: dict[str, str | None] = {
    "scope": None,
    "inspections": "obligation_id",
    "artifacts": "inspection_id",
    "approvals": "inspection_id",
    "approval_items": "approval_id",
}
SUBJECT_KIND_FOR_DATASET = {
    "scope": "scope_row",
    "inspections": "inspection",
    "artifacts": "artifact",
    "approvals": "approval",
    "approval_items": "approval_item",
}
# approval_items share the approvals coverage declaration (SPEC §5.2, §5.8).
COVERAGE_DATASET_OF = {
    "inspections": "inspections",
    "artifacts": "artifacts",
    "approvals": "approvals",
    "approval_items": "approvals",
}

BASIS_TOKENS_DOCUMENTED = (
    "synthetic_universe",
    "query_total_matched",
    "two_pass_stable",
    "operator_attestation",
    "ui_export",
    "extraction_interrupted",
    "attachment_capture_skipped",
    "pagination_incomplete",
)
