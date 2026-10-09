"""The `evidence-digest` command (SPEC §11): the digest a review process should store as `evidence_digest`."""

from __future__ import annotations

from typing import Any

from inspection_reconcile.canonical import evidence_set_digest
from inspection_reconcile.engine.integrity import Integrity
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.evidence import FileSystemEvidence, Present
from inspection_reconcile.io.snapshot import LoadedSnapshot
from inspection_reconcile.policy import Policy


def evidence_digest(
    snapshot: LoadedSnapshot, policy: Policy, inspection_id: str, revision: str | None = None
) -> tuple[str, list[dict[str, Any]]]:
    integrity = Integrity(snapshot, snapshot.capture_ended).run()
    inspections = [
        r
        for r in integrity.rows["inspections"]
        if not r.quarantined and r.values.get("inspection_id") == inspection_id
    ]
    if revision is None:
        chosen = [r for r in inspections if r.is_current]
    else:
        chosen = [r for r in inspections if r.values.get("revision") == revision]
    if len(chosen) != 1:
        which = f"revision {revision}" if revision else "the current revision"
        raise RunError("INSPECTION_NOT_FOUND", f"{inspection_id}: {which} is not exactly one valid row")
    inspection = chosen[0]
    kinds = policy.requirements.get(inspection.text("activity_kind"))
    if kinds is None:
        raise RunError(
            "REQUIREMENT_UNDEFINED", f"the pack has no requirements for {inspection.text('activity_kind')}"
        )
    artifacts = [
        r
        for r in integrity.rows["artifacts"]
        if not r.quarantined
        and r.is_current
        and r.values.get("inspection_id") == inspection_id
        and r.values.get("document_kind") in kinds
    ]
    evidence = FileSystemEvidence(snapshot.evidence_root, policy.max_file_bytes)
    entries: list[dict[str, Any]] = []
    for row in sorted(artifacts, key=lambda r: (r.text("artifact_id"), r.text("revision"))):
        path = row.values.get("relative_path")
        probe = evidence.probe(path if isinstance(path, str) else None)
        if not isinstance(probe, Present):
            raise RunError(
                "EVIDENCE_INCOMPLETE",
                f"artifact {row.text('artifact_id')} revision {row.text('revision')}: file {probe.status}",
            )
        entries.append(
            {
                "artifact_id": row.text("artifact_id"),
                "document_kind": row.text("document_kind"),
                "revision": row.text("revision"),
                "sha256": probe.sha256,
            }
        )
    missing = sorted(set(kinds) - {e["document_kind"] for e in entries})
    if missing:
        raise RunError("EVIDENCE_INCOMPLETE", f"no current artifact of kind(s) {', '.join(missing)}")
    return evidence_set_digest(entries), entries
