"""Snapshot semantic digest and identities (SPEC §8.4, §8.5, §22.5)."""

from __future__ import annotations

from typing import Any

from inspection_reconcile.canonical import digest_of, sort_canonical
from inspection_reconcile.engine.integrity import IntegrityResult
from inspection_reconcile.engine.model import Finding, Row
from inspection_reconcile.io.evidence import EvidenceStore, Present
from inspection_reconcile.io.snapshot import LoadedSnapshot
from inspection_reconcile.vocab import SCHEMAS

SNAPSHOT_SCHEME = "inspection-reconcile/snapshot-semantic/v1"
EVALUATION_SCHEME = "inspection-reconcile/evaluation/v1"
PROVENANCE_SCHEME = "inspection-reconcile/provenance/v1"


def _row_object(row: Row, evidence: EvidenceStore) -> dict[str, Any]:
    raw = row.raw
    if raw.cells is None:
        return {"quarantined": ["MALFORMED_ROW"], "raw_cells": list(raw.raw_cells)}
    columns = [c for c, _, _ in SCHEMAS[row.dataset]]
    if row.quarantined:
        return {"quarantined": sorted(row.quarantine), "raw": {c: raw.cells[c] for c in columns}}
    out: dict[str, Any] = {c: row.values.get(c) for c in columns}
    if row.dataset == "artifacts":
        path = out.pop("relative_path")
        if path is None:
            out["evidence"] = {"status": "no_path"}
        else:
            probe = evidence.probe(str(path))
            if isinstance(probe, Present):
                out["evidence"] = {"status": "present", "sha256": probe.sha256}
            else:
                out["evidence"] = {"status": probe.status}
    return out


def snapshot_semantic(
    snapshot: LoadedSnapshot, integrity: IntegrityResult, evidence: EvidenceStore
) -> dict[str, Any]:
    def declared(name: str) -> dict[str, Any] | None:
        decl = snapshot.datasets.get(name)
        if decl is None:
            return None
        return {"basis": sorted(decl.basis), "consistency": decl.consistency, "coverage": decl.coverage}

    def rows(dataset: str) -> list[Any]:
        return sort_canonical(_row_object(r, evidence) for r in integrity.rows[dataset])

    scope: dict[str, Any] | None = None
    if snapshot.scope is not None:  # a member whose file is absent still identifies the evaluation (AM-5)
        scope = {
            "accepted": snapshot.scope.accepted,
            "rows": rows("scope") if snapshot.scope_present else None,
            "scope_revision": snapshot.scope.scope_revision,
        }
    project = snapshot.project
    return {
        "scheme": SNAPSHOT_SCHEME,
        "project": {
            "client_id": project["client_id"],
            "name": project["name"],
            "project_id": project["project_id"],
            "synthetic": project["synthetic"],
        },
        "scope": scope,
        "datasets": {
            "inspections": {"declared": declared("inspections"), "rows": rows("inspections")},
            "artifacts": {"declared": declared("artifacts"), "rows": rows("artifacts")},
            "approvals": {"declared": declared("approvals"), "rows": rows("approvals")},
            "approval_items": {"rows": rows("approval_items")},
            "evidence_files": {"declared": declared("evidence_files")},
        },
    }


def evaluation_id(engine_version: str, policy_sha256: str, snapshot_semantic_sha256: str, as_of: str) -> str:
    return digest_of(
        {
            "scheme": EVALUATION_SCHEME,
            "engine_version": engine_version,
            "policy_sha256": policy_sha256,
            "snapshot_semantic_sha256": snapshot_semantic_sha256,
            "as_of": as_of,
        }
    )


def assessment_semantic(findings: list[Finding]) -> str:
    return digest_of([f.semantic() for f in findings])


def provenance_id(evaluation: str, inputs: list[tuple[str, str]], mapping_sha256: str | None) -> str:
    return digest_of(
        {
            "scheme": PROVENANCE_SCHEME,
            "evaluation_id": evaluation,
            "inputs": sort_canonical({"role": role, "sha256": sha} for role, sha in inputs),
            "mapping_sha256": mapping_sha256,
        }
    )
