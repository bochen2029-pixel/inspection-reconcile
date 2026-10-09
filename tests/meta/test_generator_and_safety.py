"""Generator reproducibility, generator independence, and public safety (SPEC §15.1 meta)."""

import subprocess
import sys

import pytest
from conftest import REPO


def test_generator_reproduces_committed_fixtures():
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools" / "make_fixtures.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]


def test_generator_never_imports_src():
    text = (REPO / "tools" / "make_fixtures.py").read_text(encoding="utf-8")
    assert "inspection_reconcile" not in text


def tracked_files():
    proc = subprocess.run(["git", "ls-files"], capture_output=True, text=True, timeout=120, cwd=REPO)
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    return [REPO / line for line in proc.stdout.splitlines() if line]


def test_no_private_terms_in_tracked_files():
    terms_file = REPO / "local" / "private_terms.txt"
    if not terms_file.is_file():
        pytest.skip("no local private-terms list")
    terms = [t.strip().lower() for t in terms_file.read_text(encoding="utf-8").splitlines() if t.strip()]
    hits = []
    for path in tracked_files():
        if not path.is_file():
            continue
        data = path.read_bytes()
        try:
            text = data.decode("utf-8").lower()
        except UnicodeDecodeError:
            continue
        for term in terms:
            if term in text:
                hits.append(f"{path.relative_to(REPO).as_posix()}: {term}")
    assert not hits, hits


def test_public_spec_has_no_private_preface():
    spec = (REPO / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert "Private preface" not in spec
    assert "BEGIN docs/SPEC.md -->" not in spec
