"""assessment.json and run-manifest.json (SPEC §9.1, §9.3)."""

from __future__ import annotations

import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inspection_reconcile import ENGINE_NAME
from inspection_reconcile.engine.assess import Assessment, coverage_table
from inspection_reconcile.grammar import format_ts
from inspection_reconcile.io.writer import json_bytes

ASSESSMENT_SCHEMA = "inspection-reconcile/assessment/v1"
RUN_MANIFEST_SCHEMA = "inspection-reconcile/run-manifest/v1"


def assessment_document(assessment: Assessment, provenance_id: str) -> dict[str, Any]:
    snapshot = assessment.snapshot
    policy = assessment.policy
    scope = snapshot.scope
    return {
        "schema": ASSESSMENT_SCHEMA,
        "evaluation_id": assessment.evaluation_id,
        "assessment_semantic_sha256": assessment.assessment_semantic_sha256,
        "provenance_id": provenance_id,
        "status": assessment.status,
        "as_of": assessment.as_of,
        "engine": {"name": ENGINE_NAME, "version": assessment.engine_version},
        "project": dict(snapshot.project),
        "scope": {
            "present": snapshot.scope_present,
            "scope_revision": scope.scope_revision if scope is not None else None,
            "obligation_count": assessment.obligation_count,
            "accepted": scope.accepted if scope is not None else None,
        },
        "policy": {
            "pack_id": policy.pack_id,
            "version": policy.version,
            "sha256": policy.sha256,
            "required_binding": policy.required_binding,
        },
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "synthetic": snapshot.synthetic,
            "semantic_sha256": assessment.snapshot_semantic_sha256,
        },
        "coverage": coverage_table(assessment),
        "counts": assessment.counts(),
        "findings": [f.to_json() for f in assessment.findings],
    }


def assessment_bytes(assessment: Assessment, provenance_id: str) -> bytes:
    return json_bytes(assessment_document(assessment, provenance_id))


def relative_or_absolute(path: Path) -> str:
    try:
        return Path(os.path.relpath(path)).as_posix()
    except ValueError:  # another drive on Windows
        return path.resolve().as_posix()


def run_manifest_bytes(
    assessment: Assessment,
    provenance_id: str,
    inputs: list[tuple[str, Path, int, str]],
    mapping: dict[str, str] | None = None,
) -> bytes:
    policy = assessment.policy
    doc = {
        "schema": RUN_MANIFEST_SCHEMA,
        "evaluation_id": assessment.evaluation_id,
        "provenance_id": provenance_id,
        "engine": {"name": ENGINE_NAME, "version": assessment.engine_version},
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "generated_at": format_ts(datetime.now(UTC)),
        "as_of": assessment.as_of,
        "policy": {"pack_id": policy.pack_id, "version": policy.version, "sha256": policy.sha256},
        "inputs": [
            {"role": role, "path": relative_or_absolute(path), "bytes": size, "sha256": sha}
            for role, path, size, sha in inputs
        ],
        "mapping": mapping,
    }
    return json_bytes(doc)
