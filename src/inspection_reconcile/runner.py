"""The assess pipeline shared by the CLI and the demo: load, evaluate, render (SPEC §7.1, §9)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from inspection_reconcile.adapters.mapping import Mapping

# One input of a run (SPEC §9.3): role, path (None when the file no longer exists), size in bytes, SHA-256 hex.
Input = tuple[str, Path | None, int, str]


@dataclass
class Evaluation:
    assessment: Assessment
    provenance_id: str
    inputs: list[Input]
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


def _input_order(item: Input) -> tuple[str, str]:
    role, path, _, _ = item
    return role, "" if path is None else path.as_posix()


def evaluate(
    snapshot_dir: Path,
    policy_path: Path,
    as_of: datetime | None = None,
    mapping: dict[str, str] | None = None,
    case_sensitive: bool = True,
    *,
    relocate: Callable[[Path], Path | None] | None = None,
    extra_inputs: Sequence[Input] = (),
) -> Evaluation:
    """Assess a canonical snapshot. ``relocate`` rewrites the paths the run manifest records for the snapshot's
    own files, and ``extra_inputs`` adds files read before the snapshot existed (an export's, SPEC §9.3)."""
    snapshot = load_snapshot(snapshot_dir)
    policy: Policy = load_policy(policy_path)
    when = as_of if as_of is not None else snapshot.capture_ended
    evidence = FileSystemEvidence(
        snapshot.evidence_root, policy.max_file_bytes, case_sensitive=case_sensitive
    )
    assessment = assess(snapshot, policy, evidence, when, __version__)
    inputs: list[Input] = [
        (i.role, relocate(i.path) if relocate is not None else i.path, i.size, i.sha256)
        for i in snapshot.inputs
    ]
    inputs.extend(extra_inputs)
    # The digest of the bytes load_policy read: the policy file is not read a second time.
    inputs.append(("policy", policy_path, policy_path.stat().st_size, policy.raw_bytes_sha256))
    inputs.sort(key=_input_order)
    provenance = identity.provenance_id(
        assessment.evaluation_id,
        [(role, sha) for role, _, _, sha in inputs],
        mapping["sha256"] if mapping else None,
    )
    return Evaluation(assessment, provenance, inputs, mapping)


def _adapters() -> tuple[ModuleType, ModuleType]:
    try:
        from inspection_reconcile.adapters import mapping, qb_export
    except ImportError as exc:  # pragma: no cover - present once step B2 is merged
        raise RunError("NOT_AVAILABLE", f"the Quickbase export adapter is not installed: {exc}") from exc
    return mapping, qb_export


@contextmanager
def _normalized(export_dir: Path, mapping: Mapping) -> Iterator[Path]:
    _, qb_export = _adapters()
    with tempfile.TemporaryDirectory(prefix="ir-normalize-") as tmp:
        out = Path(tmp) / "snapshot"
        qb_export.normalize(export_dir, mapping, out)
        yield out


@contextmanager
def normalized_export(export_dir: Path, mapping_path: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    """Normalize a Quickbase-shaped export into a temporary canonical snapshot."""
    mapping = _adapters()[0].load_mapping(mapping_path)
    with _normalized(export_dir, mapping) as out:
        yield out, mapping.reference()


def _read_input(role: str, path: Path) -> Input:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunError("EXPORT_INVALID", f"{path.as_posix()}: {exc.strerror or exc}") from exc
    return role, path, len(data), sha256_hex(data)


def evaluate_export(
    export_dir: Path,
    mapping_path: Path,
    policy_path: Path,
    as_of: datetime | None = None,
    home: Path | None = None,
) -> tuple[Evaluation, dict[str, bytes]]:
    """``assess --export`` and the demo's export scenarios (SPEC §9.3, §11).

    The export is normalized into a temporary directory and assessed there. The run manifest records the
    snapshot's files at ``home``, where the demo writes them, or with a null path (``assess --export`` removes the
    temporary directory). It also records the export files normalize read and the mapping file. The normalized
    snapshot's files are returned, keyed by their path inside it, when ``home`` is given.
    """
    mapping_module, qb_export = _adapters()
    mapping = mapping_module.load_mapping(mapping_path)
    with _normalized(export_dir, mapping) as snap:
        extra = [_read_input(role, path) for role, path in qb_export.export_input_files(export_dir, mapping)]
        extra.append(_read_input("mapping", mapping_path))

        def relocate(path: Path) -> Path | None:
            return home / path.relative_to(snap) if home is not None else None

        evaluation = evaluate(
            snap, policy_path, as_of, mapping=mapping.reference(), relocate=relocate, extra_inputs=extra
        )
        files: dict[str, bytes] = {}
        if home is not None:
            files = {
                p.relative_to(snap).as_posix(): p.read_bytes() for p in sorted(snap.rglob("*")) if p.is_file()
            }
    return evaluation, files
