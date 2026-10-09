"""Run errors of the loaders and of evidence-digest (SPEC §5.1, §5.2, §6, §11): each stops the run (exit 2) before
any evaluation. Found untested in review pass R."""

import os
import shutil
import sys
from pathlib import Path

import pytest
from builder import mf, write
from conftest import POLICIES

from inspection_reconcile.engine.evidence_digest import evidence_digest
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.snapshot import load_snapshot
from inspection_reconcile.policy import load_policy
from inspection_reconcile.yamlsafe import load_yaml

POLICY = POLICIES / "north-creek-demo.yml"


def baseline(tmp_path: Path) -> Path:
    return write(mf.baseline("T00-load"), tmp_path / "snapshot")


def code_of(fn, *args) -> str:
    with pytest.raises(RunError) as err:
        fn(*args)
    return err.value.code


def test_a_missing_snapshot_directory(tmp_path):
    assert code_of(load_snapshot, tmp_path / "absent") == "SNAPSHOT_MISSING"


def test_a_declared_dataset_must_exist_on_disk(tmp_path):
    root = baseline(tmp_path)
    (root / "inspections.csv").unlink()
    assert code_of(load_snapshot, root) == "DATASET_MISSING_ON_DISK"


def test_an_unreadable_manifest(tmp_path):
    root = baseline(tmp_path)
    (root / "manifest.json").unlink()
    (root / "manifest.json").mkdir()  # present, but reading it fails
    assert code_of(load_snapshot, root) == "INPUT_UNREADABLE"


def test_an_unreadable_dataset(tmp_path, monkeypatch):
    root = baseline(tmp_path)
    real_read_bytes = Path.read_bytes

    def failing(self):
        if self.name == "artifacts.csv":
            raise PermissionError(13, "Permission denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", failing)
    assert code_of(load_snapshot, root) == "DATASET_UNREADABLE"


def move_evidence_away(tmp_path: Path, root: Path) -> Path:
    elsewhere = tmp_path / "elsewhere"
    shutil.move(str(root / "evidence"), str(elsewhere))
    return elsewhere


def test_the_evidence_root_must_be_a_directory(tmp_path):
    root = baseline(tmp_path)
    shutil.rmtree(root / "evidence")
    (root / "evidence").write_text("not a directory", encoding="utf-8")
    assert code_of(load_snapshot, root) == "EVIDENCE_ROOT_INVALID"


def test_the_evidence_root_must_not_be_a_symlink(tmp_path):
    root = baseline(tmp_path)
    elsewhere = move_evidence_away(tmp_path, root)
    try:
        os.symlink(elsewhere, root / "evidence", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert code_of(load_snapshot, root) == "EVIDENCE_ROOT_INVALID"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_the_evidence_root_must_not_be_a_junction(tmp_path):
    import _winapi

    root = baseline(tmp_path)
    elsewhere = move_evidence_away(tmp_path, root)
    _winapi.CreateJunction(str(elsewhere), str(root / "evidence"))
    assert code_of(load_snapshot, root) == "EVIDENCE_ROOT_INVALID"


def test_unreadable_policy_and_configuration(tmp_path):
    assert code_of(load_policy, tmp_path / "absent.yml") == "POLICY_UNREADABLE"
    assert code_of(load_yaml, tmp_path / "absent.yml") == "CONFIG_UNREADABLE"


def test_evidence_digest_needs_every_required_file_present(tmp_path):
    root = baseline(tmp_path)
    (root / "evidence" / "O-005" / "report.pdf").unlink()
    snapshot, policy = load_snapshot(root), load_policy(POLICY)
    assert code_of(evidence_digest, snapshot, policy, "INS-005", None) == "EVIDENCE_INCOMPLETE"


def test_evidence_digest_needs_every_required_kind(tmp_path):
    snap = mf.baseline("T00-load")
    snap.drop("artifacts", artifact_id="ART-005-P")
    root = write(snap, tmp_path / "snapshot")
    snapshot, policy = load_snapshot(root), load_policy(POLICY)
    with pytest.raises(RunError) as err:
        evidence_digest(snapshot, policy, "INS-005", None)
    assert err.value.code == "EVIDENCE_INCOMPLETE" and "photo" in err.value.message
