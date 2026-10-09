"""The assess pipeline shared by the CLI and the demo: load, evaluate, render (SPEC §7.1, §9)."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inspection_reconcile import __version__
from inspection_reconcile.canonical import sha256_hex
from inspection_reconcile.engine import identity
from inspection_reconcile.engine.assess import Assessment, assess
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.evidence import FileSystemEvidence
from inspection_reconcile.io.snapshot import load_snapshot
from inspection_reconcile.policy import Policy, load_policy
from inspection_reconcile.report.html import render_html
from inspection_reconcile.report.json_out import assessment_bytes, run_manifest_bytes


@dataclass
class Evaluation:
    assessment: Assessment
    provenance_id: str
    inputs: list[tuple[str, Path, int, str]]
    mapping: dict[str, str] | None

    def outputs(self) -> dict[str, bytes]:
        digests = [(role, sha) for role, _, _, sha in self.inputs]
        return {
            "assessment.json": assessment_bytes(self.assessment, self.provenance_id),
            "report.html": render_html(self.assessment, self.provenance_id, digests),
            "run-manifest.json": run_manifest_bytes(
                self.assessment, self.provenance_id, self.inputs, self.mapping
            ),
        }


def evaluate(
    snapshot_dir: Path,
    policy_path: Path,
    as_of: datetime | None = None,
    mapping: dict[str, str] | None = None,
    case_sensitive: bool = True,
) -> Evaluation:
    snapshot = load_snapshot(snapshot_dir)
    policy: Policy = load_policy(policy_path)
    when = as_of if as_of is not None else snapshot.capture_ended
    evidence = FileSystemEvidence(
        snapshot.evidence_root, policy.max_file_bytes, case_sensitive=case_sensitive
    )
    assessment = assess(snapshot, policy, evidence, when, __version__)
    policy_bytes = policy_path.read_bytes()
    inputs = [(i.role, i.path, i.size, i.sha256) for i in snapshot.inputs]
    inputs.append(("policy", policy_path, len(policy_bytes), sha256_hex(policy_bytes)))
    provenance = identity.provenance_id(
        assessment.evaluation_id,
        [(role, sha) for role, _, _, sha in inputs],
        mapping["sha256"] if mapping else None,
    )
    return Evaluation(assessment, provenance, inputs, mapping)


@contextmanager
def normalized_export(export_dir: Path, mapping_path: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    """Normalize a Quickbase-shaped export into a temporary canonical snapshot (``assess --export``)."""
    try:
        from inspection_reconcile.adapters.mapping import load_mapping
        from inspection_reconcile.adapters.qb_export import normalize
    except ImportError as exc:  # pragma: no cover - present once step B2 is merged
        raise RunError("NOT_AVAILABLE", f"the Quickbase export adapter is not installed: {exc}") from exc
    mapping = load_mapping(mapping_path)
    with tempfile.TemporaryDirectory(prefix="ir-normalize-") as tmp:
        out = Path(tmp) / "snapshot"
        normalize(export_dir, mapping, out)
        info = {"mapping_id": mapping.mapping_id, "version": mapping.version, "sha256": mapping.sha256}
        yield out, info
