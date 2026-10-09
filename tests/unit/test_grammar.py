import pytest

from inspection_reconcile import grammar as g


@pytest.mark.parametrize("value", ["O-001", "a", "A.b:c-d_e", "x" * 128, "INS-012B", "qbrid.bsyn00002.117"])
def test_id_valid(value):
    assert g.is_id(value)


@pytest.mark.parametrize(
    "value", ["", " O-1", "O-1 ", "O 1", "-x", "_x", "é", "x" * 129, "a@b", "a/b", "a#b", "٣"]
)
def test_id_invalid(value):
    assert not g.is_id(value)


@pytest.mark.parametrize("value", ["1", "S1", "2.0", "r_1-a"])
def test_rev_valid(value):
    assert g.is_rev(value)


@pytest.mark.parametrize("value", ["", ":", "1:2", "x" * 33, " 1"])
def test_rev_invalid(value):
    assert not g.is_rev(value)


def test_kind():
    assert g.is_kind("visual_inspection")
    assert not g.is_kind("Visual")
    assert not g.is_kind("1abc")


@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("2026-10-01T18:00:00Z", "2026-10-01T18:00:00.000000Z"),
        ("2026-10-01T18:00:00.5Z", "2026-10-01T18:00:00.500000Z"),
        ("2026-10-01T18:00:00.123456Z", "2026-10-01T18:00:00.123456Z"),
        ("2026-10-01T23:30:00+05:30", "2026-10-01T18:00:00.000000Z"),
        ("2026-10-01T13:00:00-05:00", "2026-10-01T18:00:00.000000Z"),
        ("2026-12-31T23:00:00-02:00", "2027-01-01T01:00:00.000000Z"),
    ],
)
def test_ts_valid(raw, canonical):
    ts = g.parse_ts(raw)
    assert ts is not None
    assert g.format_ts(ts) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "2026-10-01T18:00:00",  # no offset
        "2026-10-01T18:00:00z",  # lowercase z
        "2026-10-01t18:00:00Z",  # lowercase t
        "2026-10-01 18:00:00Z",  # space separator
        "2026-10-01T18:00:00.1234567Z",  # 7 fractional digits
        "2026-10-01T24:00:00Z",
        "2026-10-01T23:59:60Z",  # leap second
        "2026-02-30T00:00:00Z",
        "2026-10-01T18:00:00+24:00",
        "2026-10-01T18:00Z",
        "٢٠٢٦-10-01T18:00:00Z",  # non-ASCII digits
        " 2026-10-01T18:00:00Z",
    ],
)
def test_ts_invalid(raw):
    assert g.parse_ts(raw) is None


def test_date_midnight():
    assert g.format_ts(g.parse_date_midnight_utc("2026-09-01")) == "2026-09-01T00:00:00.000000Z"
    assert g.parse_date_midnight_utc("2026-9-1") is None
    assert g.parse_date_midnight_utc("2026-02-30") is None


def test_text():
    assert g.is_text("reviewer@example.invalid")
    assert not g.is_text("")
    assert not g.is_text("a" * 201)
    assert g.is_text("a" * 200)
    assert not g.is_text("tab\there")
    assert not g.is_text("bad\x7fdel")
    assert not g.is_text("lone\ud800")


def test_digest():
    assert g.is_digest("sha256:" + "0" * 64)
    assert not g.is_digest("sha256:" + "A" * 64)
    assert not g.is_digest("sha1:" + "0" * 40)


@pytest.mark.parametrize(
    "path, rule",
    [
        ("O-001/report.pdf", None),
        ("files/bsyn00003/201/13/v1/report.pdf", None),
        ("/etc/passwd", "path:absolute"),
        ("../O-005/report.pdf", "path:dot-segment"),
        ("O-005/./report.pdf", "path:dot-segment"),
        ("O-005//report.pdf", "path:empty-segment"),
        ("", "path:empty-segment"),
        ("O-005\\report.pdf", "path:forbidden-character"),
        ("C:/x/report.pdf", "path:forbidden-character"),
        ("photo.png:stream", "path:forbidden-character"),
        ("a?b", "path:forbidden-character"),
        ("bad\nname", "path:control-character"),
        ("trailing./x", "path:trailing-dot-or-space"),
        ("trailing /x", "path:trailing-dot-or-space"),
        ("CON", "path:reserved-name"),
        ("dir/nul.txt", "path:reserved-name"),
        ("lpt9.pdf", "path:reserved-name"),
        ("COM10.pdf", None),
        ("x" * 101, "path:segment-too-long"),
        ("/".join(["abcdefghij"] * 25), "path:too-long"),
    ],
)
def test_path_rules(path, rule):
    assert g.path_violation(path) == rule


def test_check_cell():
    assert g.check_cell("BOOL", "true", True) == (True, None)
    assert g.check_cell("BOOL", "TRUE", True) == (None, "grammar:BOOL")
    assert g.check_cell("ID", None, True) == (None, "required")
    assert g.check_cell("ID", None, False) == (None, None)
    assert g.check_cell("ENUM:decision", "approved", True) == ("approved", None)
    assert g.check_cell("ENUM:decision", "Approved", True) == (None, "enum:approved|rejected|revoked")
    assert g.check_cell("PATH", "../x", False) == (None, "path:dot-segment")
    assert g.check_cell("TS", "2026-10-01T18:00:00Z", True) == ("2026-10-01T18:00:00.000000Z", None)
