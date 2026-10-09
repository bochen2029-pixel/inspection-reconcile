"""Regression tests for the spec-only engine review (decision D-009, SPEC §22.11 AM-5). Each failed before its fix."""

import csv
import io

from builder import build, chain


def f(assessment, key):
    return assessment.by_key()[key]


def set_cell(table, column, value, **match):
    def apply(snap):
        snap.find(table, **match)[column] = value

    return apply


def no_scope_file(root):
    (root / "scope.csv").unlink()


def test_scope_member_without_its_file_has_its_own_evaluation_id(tmp_path):
    def no_member(snap):
        snap.tables["scope"] = None
        del snap.manifest["scope"]

    def other_member(snap):
        snap.manifest["scope"]["accepted"] = None
        snap.manifest["scope"]["scope_revision"] = "S9"

    runs = [
        build(tmp_path / "declared", post=no_scope_file),
        build(tmp_path / "undeclared", no_member),
        build(tmp_path / "other", other_member, post=no_scope_file),
    ]
    for a in runs:
        assert f(a, "R1:project:NC-001").reason == "SCOPE_MISSING"
    assert len({a.evaluation_id for a in runs}) == 3


def test_duplicates_that_differ_only_in_x_columns_are_identical(tmp_path):
    def duplicate(snap):
        snap.tables["inspections"].append(dict(snap.find("inspections", inspection_id="INS-004")))

    def add_provenance(root):
        path = root / "inspections.csv"
        rows = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8"), newline="")))
        rows[0].append("x_source")
        for n, row in enumerate(rows[1:], start=1):
            row.append(f"table=bsyn00002;rid={n}")
        buf = io.StringIO(newline="")
        csv.writer(buf, lineterminator="\n").writerows(rows)
        path.write_bytes(buf.getvalue().encode("utf-8"))

    a = build(tmp_path, duplicate, post=add_provenance)
    assert f(a, "R0:inspection:INS-004@1#DUPLICATE_KEY").observed == {"count": 2, "identical": True}


def test_attribution_survives_an_unreadable_inspection_revision(tmp_path):
    a = build(
        tmp_path,
        chain(
            set_cell("inspections", "revision", "1 ", inspection_id="INS-004"),
            set_cell("artifacts", "relative_path", "../O-004/photo.png", artifact_id="ART-004-P"),
        ),
    )
    assert f(a, "R0:artifact:ART-004-P@1#INVALID_PATH").required is True
    r2 = f(a, "R2:obligation:O-004")
    assert r2.outcome == "NOT_EVALUATED"
    assert r2.blocked_by == ["R0:artifact:ART-004-P@1#INVALID_PATH"]
