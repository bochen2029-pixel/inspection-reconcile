"""Foundations review: a defective CSV record is never a run error (SPEC §5.4, §7.14).

Only bad UTF-8, a NUL byte and header violations make a CSV unreadable. A record that the csv module cannot parse
is R0 MALFORMED_ROW (unattributable, so its dataset becomes partial), and an over-long cell fails its grammar.
Expected verdicts come from the spec text, never from src/.
"""

import csv
from pathlib import Path

import pytest
from builder import mf, write
from conftest import POLICIES
from scenario_support import run_snapshot

from inspection_reconcile.errors import RunError
from inspection_reconcile.io.snapshot import load_snapshot

POLICY = POLICIES / "north-creek-demo.yml"


def snapshot_with(tmp_path: Path, edit) -> Path:
    """The baseline with approvals.csv's fifth data record (APR-005) edited as text."""
    root = write(mf.baseline("T00-csv"), tmp_path / "snapshot")
    path = root / "approvals.csv"
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[5].startswith("APR-005,")
    lines[5] = edit(lines[5])
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return root


def non_pass(assessment) -> list[tuple[str, str, str]]:
    return [(f.key, f.outcome, f.reason) for f in assessment.findings if f.outcome != "PASS"]


def test_a_quote_error_in_one_record_is_a_malformed_row(tmp_path: Path) -> None:
    broken = None

    def edit(line: str) -> str:
        nonlocal broken
        broken = line.replace(",approved,", ',"approved"x,', 1)
        return broken

    root = snapshot_with(tmp_path, edit)
    snapshot = load_snapshot(root)
    malformed = [r for r in snapshot.rows["approvals"] if r.malformed]
    assert [r.raw_cells for r in malformed] == [(broken,)]
    assert len(snapshot.rows["approvals"]) == 40  # reading resumed after the defective record
    assessment = run_snapshot(root, POLICY)
    r0 = [k for k in non_pass(assessment) if k[0].startswith("R0:")]
    assert len(r0) == 1 and r0[0][0].startswith("R0:unattributable_row:approvals.")
    assert r0[0][1:] == ("UNKNOWN", "MALFORMED_ROW")
    assert assessment.by_key()["R1:dataset:approvals"].reason == "UNATTRIBUTABLE_RECORDS"
    assert assessment.status == "UNKNOWN"
    assert not [k for k in non_pass(assessment) if k[1] == "FAIL"]


def test_an_unterminated_quote_swallows_the_rest_into_one_malformed_row(tmp_path: Path) -> None:
    root = snapshot_with(tmp_path, lambda line: line.replace(",approved,", ',"approved,', 1))
    snapshot = load_snapshot(root)
    approvals = snapshot.rows["approvals"]
    assert [r.cells["approval_id"] for r in approvals[:4]] == ["APR-001", "APR-002", "APR-003", "APR-004"]
    assert len(approvals) == 5 and approvals[4].malformed
    assert "APR-040" in approvals[4].raw_cells[0]
    assessment = run_snapshot(root, POLICY)
    assert assessment.status == "UNKNOWN"
    assert not [k for k in non_pass(assessment) if k[1] == "FAIL"]


def test_an_over_long_cell_fails_its_grammar_instead_of_the_reader(tmp_path: Path) -> None:
    limit = csv.field_size_limit()
    root = snapshot_with(tmp_path, lambda line: line.rsplit(",", 1)[0] + "," + "y" * 140_000)
    assessment = run_snapshot(root, POLICY)
    finding = assessment.by_key()["R0:approval:APR-005#INVALID_VALUE"]
    assert finding.observed["violations"][0]["rule"] == "grammar:TEXT"
    assert finding.required is True
    assert assessment.status == "UNKNOWN"
    assert csv.field_size_limit() == limit  # the reader's limit is restored


def test_a_quote_error_in_the_header_is_still_a_run_error(tmp_path: Path) -> None:
    root = write(mf.baseline("T00-csv-header"), tmp_path / "snapshot")
    path = root / "approvals.csv"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("approval_id,", '"approval_id"x,', 1), encoding="utf-8", newline="")
    with pytest.raises(RunError) as info:
        load_snapshot(root)
    assert info.value.code == "CSV_UNREADABLE"
