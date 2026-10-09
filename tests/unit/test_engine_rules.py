"""Rule-level tests for SPEC §7 truth tables and §22 values. Each test mutates the generator's baseline and states
the expected finding from the spec text; nothing here computes a verdict with src/."""

from datetime import UTC, datetime

import pytest
from builder import build, chain, custom_policy, edit_manifest, partial

from inspection_reconcile import __version__
from inspection_reconcile.engine.assess import assess
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.evidence import FileSystemEvidence
from inspection_reconcile.io.snapshot import load_snapshot
from inspection_reconcile.policy import load_policy


def f(assessment, key):
    return assessment.by_key()[key]


def outcome(assessment, key):
    x = f(assessment, key)
    return x.outcome, x.reason


def set_cell(table, column, value, **match):
    def apply(snap):
        snap.find(table, **match)[column] = value

    return apply


def drop(table, **match):
    def apply(snap):
        snap.drop(table, **match)

    return apply


# ---------------------------------------------------------------------------------------------- R2


def test_r2_not_completed_blocks_chain(tmp_path):
    a = build(tmp_path, set_cell("inspections", "completion_status", "in_progress", inspection_id="INS-005"))
    assert outcome(a, "R2:obligation:O-005") == ("FAIL", "NOT_COMPLETED")
    assert f(a, "R3:obligation:O-005").blocked_by == ["R2:obligation:O-005"]
    assert a.status == "BLOCKED"


def test_r2_not_completed_holds_under_partial_coverage(tmp_path):
    a = build(
        tmp_path,
        chain(
            set_cell("inspections", "completion_status", "cancelled", inspection_id="INS-005"),
            partial("inspections"),
        ),
    )
    assert outcome(a, "R2:obligation:O-005") == ("FAIL", "NOT_COMPLETED")


def test_r2_absence_unconfirmed_under_partial(tmp_path):
    a = build(tmp_path, chain(drop("inspections", inspection_id="INS-005"), partial("inspections")))
    x = f(a, "R2:obligation:O-005")
    assert (x.outcome, x.reason, x.caused_by) == (
        "UNKNOWN",
        "ABSENCE_UNCONFIRMED",
        ["R1:dataset:inspections"],
    )


def test_r2_multiple_current_fails_even_under_partial(tmp_path):
    def add(snap):
        row = dict(snap.find("inspections", inspection_id="INS-005"))
        row["inspection_id"] = "INS-005B"
        snap.tables["inspections"].append(row)

    a = build(tmp_path, chain(add, partial("inspections")))
    x = f(a, "R2:obligation:O-005")
    assert (x.outcome, x.reason) == ("FAIL", "MULTIPLE_CURRENT")
    assert x.observed["current_inspections"] == ["INS-005@1", "INS-005B@1"]


# ---------------------------------------------------------------------------------------------- R3


def test_r3_reason_order_and_all_mismatches(tmp_path):
    a = build(
        tmp_path,
        chain(
            set_cell("inspections", "asset_id", "A-999", inspection_id="INS-005"),
            set_cell("inspections", "project_id", "NC-002", inspection_id="INS-005"),
        ),
    )
    x = f(a, "R3:obligation:O-005")
    assert (x.outcome, x.reason) == ("FAIL", "INSPECTION_PROJECT_MISMATCH")
    assert [(m["subject"], m["field"]) for m in x.observed["mismatches"]] == [
        ("inspection:INS-005@1", "asset_id"),
        ("inspection:INS-005@1", "project_id"),
    ]


def test_r3_activity_mismatch(tmp_path):
    a = build(tmp_path, set_cell("inspections", "activity_kind", "x_ray", inspection_id="INS-005"))
    assert outcome(a, "R3:obligation:O-005") == ("FAIL", "INSPECTION_ACTIVITY_MISMATCH")


def test_r3_artifact_asset_mismatch(tmp_path):
    a = build(tmp_path, set_cell("artifacts", "asset_id", "A-999", artifact_id="ART-005-R"))
    x = f(a, "R3:obligation:O-005")
    assert (x.outcome, x.reason) == ("FAIL", "ARTIFACT_ASSET_MISMATCH")
    assert "ART-005-R" in x.explanation


def test_r3_unconfirmed_without_counterexample_under_partial(tmp_path):
    a = build(tmp_path, partial("artifacts"))
    x = f(a, "R3:obligation:O-005")
    assert (x.outcome, x.reason, x.caused_by) == ("UNKNOWN", "IDENTITY_UNCONFIRMED", ["R1:dataset:artifacts"])
    assert x.observed == {"mismatches": []}


def test_r3_counterexample_fails_under_partial(tmp_path):
    a = build(
        tmp_path,
        chain(set_cell("artifacts", "project_id", "NC-002", artifact_id="ART-005-P"), partial("artifacts")),
    )
    assert outcome(a, "R3:obligation:O-005") == ("FAIL", "ARTIFACT_PROJECT_MISMATCH")


# ---------------------------------------------------------------------------------------------- R4


def test_r4_requirement_undefined(tmp_path):
    a = build(
        tmp_path,
        chain(
            set_cell("scope", "activity_kind", "x_ray", obligation_id="O-005"),
            set_cell("inspections", "activity_kind", "x_ray", inspection_id="INS-005"),
        ),
    )
    x = f(a, "R4:obligation:O-005")
    assert (x.outcome, x.reason) == ("UNKNOWN", "REQUIREMENT_UNDEFINED")
    assert x.expected == {"document_kinds": None}
    assert f(a, "R5:obligation:O-005").blocked_by == ["R4:obligation:O-005"]
    assert f(a, "R6:obligation:O-005").blocked_by == ["R4:obligation:O-005", "R5:obligation:O-005"]


def test_r4_kind_absence_unconfirmed_under_partial(tmp_path):
    a = build(tmp_path, chain(drop("artifacts", artifact_id="ART-005-P"), partial("artifacts")))
    x = f(a, "R4:obligation:O-005")
    assert (x.outcome, x.reason, x.caused_by) == (
        "UNKNOWN",
        "KIND_ABSENCE_UNCONFIRMED",
        ["R1:dataset:artifacts"],
    )
    assert x.observed["missing_kinds"] == ["photo"]
    assert outcome(a, "R5:obligation:O-005") == ("UNKNOWN", "AVAILABILITY_UNCONFIRMED")


# ---------------------------------------------------------------------------------------------- R5


def test_r5_directory_is_unreadable(tmp_path):
    def post(root):
        target = root / "evidence" / "O-005" / "photo.png"
        target.unlink()
        target.mkdir()

    a = build(tmp_path, post=post)
    x = f(a, "R5:obligation:O-005")
    assert (x.outcome, x.reason) == ("UNKNOWN", "FILE_UNREADABLE")
    assert "not a regular file" in x.explanation


def test_r5_too_large(tmp_path):
    policy = custom_policy(tmp_path, **{"max_file_bytes: 104857600": "max_file_bytes: 100"})
    a = build(tmp_path, policy=policy)
    assert outcome(a, "R5:obligation:O-005") == ("UNKNOWN", "FILE_TOO_LARGE")


def test_r5_null_path_is_absent_under_complete_evidence(tmp_path):
    a = build(tmp_path, set_cell("artifacts", "relative_path", "", artifact_id="ART-005-P"))
    x = f(a, "R5:obligation:O-005")
    assert (x.outcome, x.reason) == ("FAIL", "FILE_ABSENT")
    assert "no path recorded" in x.explanation


def test_r5_not_captured_takes_priority(tmp_path):
    def post(root):
        (root / "evidence" / "O-005" / "report.pdf").unlink()
        target = root / "evidence" / "O-005" / "photo.png"
        target.unlink()
        target.mkdir()

    a = build(tmp_path, partial("evidence_files", basis="attachment_capture_skipped"), post=post)
    x = f(a, "R5:obligation:O-005")
    assert (x.outcome, x.reason, x.caused_by) == ("UNKNOWN", "NOT_CAPTURED", ["R1:dataset:evidence_files"])


# ---------------------------------------------------------------------------------------------- R6


def test_r6_unconfirmed_when_approvals_partial(tmp_path):
    a = build(tmp_path, partial("approvals"))
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason, x.caused_by) == ("UNKNOWN", "APPROVAL_UNCONFIRMED", ["R1:dataset:approvals"])


def test_r6_no_approval(tmp_path):
    assert outcome(build(tmp_path, drop("approvals", approval_id="APR-005")), "R6:obligation:O-005") == (
        "FAIL",
        "NO_APPROVAL",
    )


def test_r6_revision_not_approved(tmp_path):
    a = build(tmp_path, set_cell("approvals", "inspection_revision", "0", approval_id="APR-005"))
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason) == ("FAIL", "REVISION_NOT_APPROVED")
    assert x.observed["approved_revisions"] == ["0"]


def test_r6_binding_unavailable(tmp_path):
    a = build(tmp_path, set_cell("approvals", "evidence_digest", "", approval_id="APR-005"))
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason) == ("UNKNOWN", "BINDING_UNAVAILABLE")
    assert x.observed["decision_approvals"] == ["APR-005"]


def test_r6_rejected(tmp_path):
    a = build(tmp_path, set_cell("approvals", "decision", "rejected", approval_id="APR-005"))
    assert outcome(a, "R6:obligation:O-005") == ("FAIL", "APPROVAL_REJECTED")


def add_approval(approval_id, decided_at, decision="approved", digest_from="APR-005", digest=None):
    def apply(snap):
        base = snap.find("approvals", approval_id=digest_from)
        row = dict(base)
        row.update(approval_id=approval_id, decided_at=decided_at, decision=decision)
        if digest is not None:
            row["evidence_digest"] = digest
        snap.tables["approvals"].append(row)

    return apply


def test_r6_conflicting_decisions(tmp_path):
    a = build(tmp_path, add_approval("APR-005-B", "2026-09-07T17:00:00Z", decision="rejected"))
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason) == ("UNKNOWN", "CONFLICTING_DECISIONS")
    assert x.observed["decision_approvals"] == ["APR-005", "APR-005-B"]


def test_r6_later_undeterminable_decision(tmp_path):
    a = build(tmp_path, add_approval("APR-005-L", "2026-09-20T17:00:00Z", decision="revoked", digest=""))
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason) == ("UNKNOWN", "BINDING_UNAVAILABLE")
    assert x.observed["decision_approvals"] == ["APR-005-L"]


def test_r6_latest_match_wins(tmp_path):
    a = build(tmp_path, add_approval("APR-005-Z", "2026-09-20T17:00:00Z", decision="revoked"))
    assert outcome(a, "R6:obligation:O-005") == ("FAIL", "APPROVAL_REVOKED")


def test_r6_mismatch_after_match_is_ignored(tmp_path):
    a = build(
        tmp_path,
        add_approval("APR-005-Z", "2026-09-20T17:00:00Z", decision="revoked", digest="sha256:" + "0" * 64),
    )
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason) == ("PASS", "APPROVAL_COVERS_CURRENT")


def test_r6_revision_mode_ignores_items_outside_required_set(tmp_path):
    def items(snap):
        snap.manifest["optional_datasets"]["approval_items"] = {"file": "approval_items.csv"}
        snap.items_declared = True
        for apr in snap.tables["approvals"]:
            apr["evidence_digest"] = ""
            n = int(apr["approval_id"][4:])
            snap.row(
                "approval_items",
                approval_id=apr["approval_id"],
                artifact_id=f"ART-{n:03d}-R",
                artifact_revision="1",
            )
            snap.row(
                "approval_items",
                approval_id=apr["approval_id"],
                artifact_id=f"ART-{n:03d}-P",
                artifact_revision="1",
            )
        snap.row(
            "artifacts",
            artifact_id="ART-005-S",
            revision="1",
            is_current="true",
            inspection_id="INS-005",
            project_id="NC-001",
            asset_id="A-005",
            document_kind="sketch",
            relative_path="O-005/report.pdf",
        )
        snap.row("approval_items", approval_id="APR-005", artifact_id="ART-005-S", artifact_revision="1")

    a = build(tmp_path, items, policy="north-creek-revisions")
    x = f(a, "R6:obligation:O-005")
    assert (x.outcome, x.reason) == ("PASS", "APPROVAL_COVERS_CURRENT")
    assert x.observed["binding"] == "revisions"
    assert "undetectable" in x.explanation


# ---------------------------------------------------------------------------------------------- R0


def test_r0_invalid_value_quarantines_and_blocks(tmp_path):
    a = build(tmp_path, set_cell("inspections", "activity_kind", "Visual", inspection_id="INS-005"))
    x = f(a, "R0:inspection:INS-005@1#INVALID_VALUE")
    assert (x.outcome, x.required) == ("UNKNOWN", True)
    assert x.observed == {
        "violations": [{"field": "activity_kind", "value": "Visual", "rule": "grammar:KIND"}],
        "count": 1,
    }
    assert f(a, "R2:obligation:O-005").blocked_by == ["R0:inspection:INS-005@1#INVALID_VALUE"]


def test_r0_unreadable_key_is_unattributable_and_downgrades_dataset(tmp_path):
    a = build(tmp_path, set_cell("inspections", "revision", " 1", inspection_id="INS-005"))
    r0 = [x for x in a.findings if x.check_id == "R0"]
    assert len(r0) == 1 and r0[0].subject_kind == "unattributable_row" and r0[0].required
    cov = f(a, "R1:dataset:inspections")
    assert (cov.outcome, cov.reason) == ("UNKNOWN", "UNATTRIBUTABLE_RECORDS")
    assert outcome(a, "R2:obligation:O-005") == ("UNKNOWN", "ABSENCE_UNCONFIRMED")
    assert outcome(a, "R2:obligation:O-006") == ("UNKNOWN", "CARDINALITY_UNCONFIRMED")


def test_r0_unreadable_link_is_unattributable(tmp_path):
    a = build(tmp_path, set_cell("artifacts", "inspection_id", "bad id", artifact_id="ART-005-R"))
    assert f(a, "R0:artifact:ART-005-R@1#INVALID_VALUE").required
    assert outcome(a, "R1:dataset:artifacts") == ("UNKNOWN", "UNATTRIBUTABLE_RECORDS")


def test_r0_malformed_row(tmp_path):
    def post(root):
        with open(root / "inspections.csv", "ab") as handle:
            handle.write(b"INS-900,1\n")

    a = build(tmp_path, post=post)
    x = [x for x in a.findings if x.reason == "MALFORMED_ROW"][0]
    assert x.observed == {"dataset": "inspections", "field_count": 2, "header_count": 9, "count": 1}
    assert outcome(a, "R1:dataset:inspections") == ("UNKNOWN", "UNATTRIBUTABLE_RECORDS")


def test_r0_completed_without_timestamp(tmp_path):
    a = build(tmp_path, set_cell("inspections", "completed_at", "", inspection_id="INS-005"))
    assert outcome(a, "R0:inspection:INS-005@1#COMPLETED_WITHOUT_TIMESTAMP") == (
        "FAIL",
        "COMPLETED_WITHOUT_TIMESTAMP",
    )
    assert a.status == "BLOCKED"


def test_r0_timestamp_after_as_of(tmp_path):
    a = build(tmp_path, set_cell("approvals", "decided_at", "2026-10-05T00:00:00Z", approval_id="APR-005"))
    x = f(a, "R0:approval:APR-005#TIMESTAMP_AFTER_AS_OF")
    assert x.observed == {"field": "decided_at", "value": "2026-10-05T00:00:00.000000Z", "count": 1}
    assert f(a, "R2:obligation:O-005").outcome == "NOT_EVALUATED"


def test_r0_out_of_scope_defect_is_advisory(tmp_path):
    def add(snap):
        snap.row(
            "inspections",
            inspection_id="INS-099",
            revision="1",
            is_current="true",
            obligation_id="O-099",
            project_id="NC-001",
            asset_id="A-099",
            activity_kind="Visual",
            completion_status="completed",
            completed_at="2026-09-20T15:00:00Z",
        )

    a = build(tmp_path, add)
    x = f(a, "R0:inspection:INS-099@1#INVALID_VALUE")
    assert x.required is False
    assert a.status == "READY_FOR_REVIEW"


def test_r0_dangling_item_reference(tmp_path):
    def items(snap):
        snap.manifest["optional_datasets"]["approval_items"] = {"file": "approval_items.csv"}
        snap.items_declared = True
        snap.row("approval_items", approval_id="APR-005", artifact_id="ART-005-R", artifact_revision="9")

    a = build(tmp_path, items)
    x = f(a, "R0:approval_item:APR-005/ART-005-R#DANGLING_REFERENCE")
    assert x.observed["missing"] == [
        {"field": "artifact_id", "target": "artifacts", "target_key": "ART-005-R@9"}
    ]
    assert x.required
    assert outcome(a, "R1:dataset:artifacts") == ("UNKNOWN", "COVERAGE_CONTRADICTED")


def test_r0_duplicate_rows_that_differ(tmp_path):
    def dup(snap):
        row = dict(snap.find("approvals", approval_id="APR-005"))
        row["decision"] = "rejected"
        snap.tables["approvals"].append(row)

    a = build(tmp_path, dup)
    assert f(a, "R0:approval:APR-005#DUPLICATE_KEY").observed == {"count": 2, "identical": False}


# ---------------------------------------------------------------------------------------------- R1


def test_r1_scope_empty(tmp_path):
    a = build(tmp_path, lambda s: s.tables.update(scope=[]))
    assert outcome(a, "R1:project:NC-001") == ("UNKNOWN", "SCOPE_EMPTY")
    assert a.obligation_count == 0


def test_r1_scope_revision_mismatch(tmp_path):
    a = build(tmp_path, lambda s: s.manifest["scope"].update(scope_revision="S2"))
    assert outcome(a, "R1:project:NC-001") == ("UNKNOWN", "SCOPE_REVISION_MISMATCH")
    assert f(a, "R2:obligation:O-001").blocked_by == ["R1:project:NC-001"]


def test_r1_scope_project_mismatch(tmp_path):
    a = build(tmp_path, set_cell("scope", "project_id", "NC-002", obligation_id="O-005"))
    assert outcome(a, "R1:project:NC-001") == ("UNKNOWN", "SCOPE_PROJECT_MISMATCH")


def test_r1_scope_invalid(tmp_path):
    a = build(tmp_path, set_cell("scope", "obligation_id", "bad id", obligation_id="O-005"))
    assert outcome(a, "R1:project:NC-001") == ("UNKNOWN", "SCOPE_INVALID")


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"basis": ["ui_export"]}, "COVERAGE_BASIS_NOT_ACCEPTED"),
        ({"consistency": "changed_during_capture"}, "CHANGED_DURING_CAPTURE"),
        ({"coverage": "unverified", "basis": ["operator_attestation"]}, "COVERAGE_UNVERIFIED"),
    ],
)
def test_r1_dataset_reasons(tmp_path, change, reason):
    a = build(tmp_path, lambda s: s.manifest["datasets"]["inspections"].update(change))
    assert outcome(a, "R1:dataset:inspections") == ("UNKNOWN", reason)


def test_r1_dataset_missing(tmp_path):
    def post(root):
        (root / "approvals.csv").unlink()
        edit_manifest(root, lambda m: m["datasets"].pop("approvals"))

    a = build(tmp_path, post=post)
    assert outcome(a, "R1:dataset:approvals") == ("UNKNOWN", "DATASET_MISSING")
    assert outcome(a, "R6:obligation:O-001") == ("UNKNOWN", "APPROVAL_UNCONFIRMED")


def test_r1_accepted_basis_set_must_be_a_subset(tmp_path):
    a = build(
        tmp_path,
        lambda s: s.manifest["datasets"]["inspections"].update(
            basis=["query_total_matched", "two_pass_stable", "operator_attestation"]
        ),
    )
    assert outcome(a, "R1:dataset:inspections") == ("PASS", "COVERAGE_COMPLETE")


# ------------------------------------------------------------------------------------------ run errors


def test_project_mismatch_is_a_run_error(tmp_path):
    with pytest.raises(RunError) as err:
        build(tmp_path, lambda s: s.project.update(project_id="NC-002"))
    assert err.value.code == "PROJECT_MISMATCH"


def test_policy_not_effective_is_a_run_error(tmp_path):
    from builder import mf, write
    from conftest import POLICIES

    root = write(mf.baseline("T00-unit"), tmp_path / "snap")
    snapshot = load_snapshot(root)
    policy = load_policy(POLICIES / "north-creek-demo.yml")
    with pytest.raises(RunError) as err:
        assess(
            snapshot,
            policy,
            FileSystemEvidence(snapshot.evidence_root, policy.max_file_bytes),
            datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
            __version__,
        )
    assert err.value.code == "POLICY_NOT_EFFECTIVE"
