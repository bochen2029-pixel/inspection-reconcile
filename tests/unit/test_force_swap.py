"""G1 review (uylmncsh): a --force run that fails while swapping outputs leaves --out exactly as it was (SPEC §9.6).

Before the fix, the previous output was deleted first and the staged output moved in afterwards, so a failure in
between (on Windows, a file held open by another program) left --out with neither. Expectations come from §9.6's
text, never from src/.
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO

from inspection_reconcile.cli import main
from inspection_reconcile.errors import RunError
from inspection_reconcile.io import writer
from inspection_reconcile.io.writer import replacing_out

OWN_FILES = {"a.json", "b.json"}
OWN_DIRS = {"tables"}
OLD = {
    "a.json": b"old a",
    "b.json": b"old b",
    "tables/x/page-0001.json": b"old page",
    "tables/y.json": b"old y",
}
NEW = {"a.json": b"new a", "b.json": b"new b", "tables/x/page-0001.json": b"new page"}


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def put(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def run_force(out: Path) -> None:
    with replacing_out(out, True, OWN_FILES, OWN_DIRS) as stage:
        put(stage, NEW)


def test_a_successful_force_run_replaces_the_previous_output_exactly(tmp_path: Path) -> None:
    out = tmp_path / "out"
    put(out, OLD)
    run_force(out)
    expected = tmp_path / "expected"
    put(expected, NEW)
    assert tree(out) == tree(expected)
    assert sorted(p.name for p in out.iterdir()) == [
        "a.json",
        "b.json",
        "tables",
    ]  # no staging or backup left


def test_a_failure_at_any_rename_of_the_swap_leaves_out_unchanged(tmp_path: Path, monkeypatch) -> None:
    real_replace = os.replace
    renames = 3 + 3  # the previous output's three top-level entries aside, then the new output's three in
    for failing in range(1, renames + 1):
        out = tmp_path / f"out{failing}"
        put(out, OLD)
        before = tree(out)
        calls = {"n": 0}

        def flaky(src, dst, failing=failing, calls=calls):
            calls["n"] += 1
            if calls["n"] == failing:
                raise PermissionError(13, "simulated: the file is in use by another process")
            return real_replace(src, dst)

        monkeypatch.setattr(writer.os, "replace", flaky)
        with pytest.raises(RunError) as info:
            run_force(out)
        monkeypatch.setattr(writer.os, "replace", real_replace)
        assert info.value.code == "WRITE_FAILED", failing
        assert info.value.message.endswith("--out is unchanged"), failing
        assert tree(out) == before, f"--out changed when rename {failing} failed"
        assert sorted(p.name for p in out.iterdir()) == ["a.json", "b.json", "tables"], failing


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to rename or delete a file held open")
def test_normalize_force_with_a_locked_previous_file_keeps_the_previous_snapshot(tmp_path: Path) -> None:
    export = REPO / "fixtures" / "scenarios" / "S16-quickbase-clean" / "export"
    mapping = REPO / "mappings" / "quickbase-demo.yml"
    args = ["normalize", "--export", str(export), "--mapping", str(mapping), "--out", str(tmp_path / "out")]
    assert main(args) == 0
    before = tree(tmp_path / "out")
    with open(tmp_path / "out" / "scope.csv", "rb"):  # what Excel does to a CSV it has open
        assert main([*args, "--force"]) == 2
    assert tree(tmp_path / "out") == before
    assert not [p.name for p in (tmp_path / "out").iterdir() if p.name.startswith(".")]
    assert main([*args, "--force"]) == 0  # once the file is closed, the same run succeeds


def test_when_the_previous_output_cannot_be_put_back_the_error_says_where_it_is(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "out"
    put(out, OLD)
    real_replace = os.replace

    def flaky(src, dst):  # the second rename fails, and so does putting a.json back
        if Path(dst).name == "b.json" or Path(src).parent.name.startswith(".previous-"):
            raise PermissionError(13, "simulated: the file is in use by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(writer.os, "replace", flaky)
    with pytest.raises(RunError) as info:
        run_force(out)
    monkeypatch.setattr(writer.os, "replace", real_replace)
    assert info.value.code == "WRITE_FAILED"
    assert "unchanged" not in info.value.message
    backups = [p for p in out.iterdir() if p.name.startswith(".previous-")]
    assert len(backups) == 1 and f"could not be put back from {backups[0].as_posix()}" in info.value.message
    assert (backups[0] / "a.json").read_bytes() == b"old a"  # the only copy, kept and named


def test_a_force_run_refuses_to_delete_a_previous_output_set_aside_by_a_failed_run(tmp_path: Path) -> None:
    out = tmp_path / "out"
    put(out, OLD)
    put(out, {".previous-crashed/a.json": b"the only copy of the previous a.json"})
    before = tree(out)
    with pytest.raises(RunError) as info:
        run_force(out)
    assert info.value.code == "OUT_NOT_EMPTY"
    assert ".previous-crashed" in info.value.message and "restore or delete it" in info.value.message
    assert tree(out) == before


def test_a_staging_directory_left_by_an_interrupted_run_is_removed_by_the_next_force_run(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    put(out, OLD)
    put(out, {".staging-crashed/a.json": b"half-written"})
    run_force(out)
    assert sorted(p.name for p in out.iterdir()) == ["a.json", "b.json", "tables"]
