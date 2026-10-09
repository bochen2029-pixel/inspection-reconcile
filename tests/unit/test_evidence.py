import hashlib
import os
import sys

import pytest

from inspection_reconcile.io.evidence import Absent, FileSystemEvidence, NoPath, Present, TooLarge, Unreadable


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "evidence"
    (r / "O-008").mkdir(parents=True)
    (r / "O-008" / "photo.png").write_bytes(b"photo bytes v1")
    (r / "O-008" / "report.pdf").write_bytes(b"report bytes v2")
    (r / "dir.pdf").mkdir()
    return r


def test_present_hash(root):
    probe = FileSystemEvidence(root, 1000).probe("O-008/photo.png")
    assert probe == Present(hashlib.sha256(b"photo bytes v1").hexdigest(), 14)


def test_case_mismatch_is_absent_with_hint(root):
    probe = FileSystemEvidence(root, 1000).probe("O-008/Photo.png")
    assert isinstance(probe, Absent)
    assert probe.case_hint == "O-008/photo.png"


def test_case_insensitive_mode_finds_it(root):
    probe = FileSystemEvidence(root, 1000, case_sensitive=False).probe("O-008/Photo.png")
    assert isinstance(probe, Present)


def test_absent_and_no_path(root):
    assert FileSystemEvidence(root, 1000).probe("O-009/photo.png") == Absent(None)
    assert FileSystemEvidence(root, 1000).probe(None) == NoPath()
    assert FileSystemEvidence(None, 1000).probe("O-008/photo.png") == Absent(None)


def test_directory_is_not_a_regular_file(root):
    assert FileSystemEvidence(root, 1000).probe("dir.pdf") == Unreadable("not a regular file")


def test_intermediate_file_means_absent(root):
    assert isinstance(FileSystemEvidence(root, 1000).probe("O-008/photo.png/x"), Absent)


def test_too_large(root):
    assert FileSystemEvidence(root, 5).probe("O-008/photo.png") == TooLarge(14)


def test_invalid_path_never_opened(root):
    assert FileSystemEvidence(root, 1000).probe("../evidence/O-008/photo.png") == Unreadable("invalid path")


def test_symlink_not_followed(root):
    target = root / "O-008" / "photo.png"
    link = root / "link.png"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert FileSystemEvidence(root, 1000).probe("link.png") == Unreadable("link not followed")


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junction_not_followed(root):
    import _winapi

    _winapi.CreateJunction(str(root / "O-008"), str(root / "junction"))
    assert FileSystemEvidence(root, 1000).probe("junction/photo.png") == Unreadable("link not followed")


def test_cache_probes_once(root):
    store = FileSystemEvidence(root, 1000)
    first = store.probe("O-008/photo.png")
    (root / "O-008" / "photo.png").write_bytes(b"changed")
    assert store.probe("O-008/photo.png") == first
