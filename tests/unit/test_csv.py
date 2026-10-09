import pytest

from inspection_reconcile.errors import RunError
from inspection_reconcile.io.snapshot import read_csv

HEADER = "approval_id,inspection_id,inspection_revision,evidence_digest,decision,decided_at,decided_by"
ROW = "APR-001,INS-001,1,,approved,2026-09-03T17:00:00Z,reviewer@example.invalid"


def write(tmp_path, data: bytes):
    p = tmp_path / "approvals.csv"
    p.write_bytes(data)
    return p


def test_reads_rows_and_nulls(tmp_path):
    rows, _ = read_csv(write(tmp_path, f"{HEADER}\n{ROW}\n".encode()), "approvals", "approvals.csv")
    assert len(rows) == 1
    r = rows[0]
    assert r.row_number == 2
    assert r.cells["evidence_digest"] is None
    assert r.cells["approval_id"] == "APR-001"
    assert r.locator == "approvals.csv#row=2"


def test_bom_stripped_and_crlf_accepted(tmp_path):
    data = (chr(0xFEFF) + HEADER + "\r\n" + ROW + "\r\n").encode()
    rows, _ = read_csv(write(tmp_path, data), "approvals", "approvals.csv")
    assert rows[0].cells["decided_by"] == "reviewer@example.invalid"


def test_blank_lines_ignored(tmp_path):
    rows, _ = read_csv(write(tmp_path, f"{HEADER}\n\n{ROW}\n\n".encode()), "approvals", "approvals.csv")
    assert len(rows) == 1
    assert rows[0].row_number == 2


def test_ragged_row_is_malformed_not_an_error(tmp_path):
    rows, _ = read_csv(
        write(tmp_path, f"{HEADER}\nAPR-001,INS-001\n{ROW}\n".encode()), "approvals", "approvals.csv"
    )
    assert rows[0].malformed
    assert rows[0].raw_cells == ("APR-001", "INS-001")
    assert not rows[1].malformed
    assert rows[1].row_number == 3


def test_x_columns_kept_as_extras(tmp_path):
    data = f"x_source,{HEADER}\ntable=b;rid=1,{ROW}\n".encode()
    rows, _ = read_csv(write(tmp_path, data), "approvals", "approvals.csv")
    assert rows[0].extras == {"x_source": "table=b;rid=1"}
    assert rows[0].locator == "table=b;rid=1"


def test_quoted_newline_and_whitespace_preserved(tmp_path):
    data = f'{HEADER}\nAPR-001,INS-001,1,,approved,2026-09-03T17:00:00Z,"two\nlines"\n'.encode()
    rows, _ = read_csv(write(tmp_path, data), "approvals", "approvals.csv")
    assert rows[0].cells["decided_by"] == "two\nlines"


@pytest.mark.parametrize(
    "data, code",
    [
        (b"", "CSV_UNREADABLE"),
        (f"{HEADER},extra\n".encode(), "CSV_HEADER_INVALID"),
        (f"{HEADER},approval_id\n".encode(), "CSV_HEADER_INVALID"),
        (b"approval_id,inspection_id\n", "CSV_HEADER_INVALID"),
        (f"{HEADER}\n{ROW}\x00\n".encode(), "CSV_UNREADABLE"),
        (b"\xff\xfe" + HEADER.encode(), "CSV_UNREADABLE"),
    ],
)
def test_run_errors(tmp_path, data, code):
    with pytest.raises(RunError) as err:
        read_csv(write(tmp_path, data), "approvals", "approvals.csv")
    assert err.value.code == code
