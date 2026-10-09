"""Coverage added during review pass R (SPEC §16.3)."""

import csv
import io
import subprocess
import sys

from builder import build
from conftest import REPO

from inspection_reconcile.report.templates import TEMPLATE_ALIAS, TEMPLATES


def test_r3_inspection_asset_mismatch_alone(tmp_path):
    def mutate(snap):
        snap.find("inspections", inspection_id="INS-005")["asset_id"] = "A-999"

    a = build(tmp_path, mutate)
    f = a.by_key()["R3:obligation:O-005"]
    assert (f.outcome, f.reason) == ("FAIL", "INSPECTION_ASSET_MISMATCH")
    assert "names asset_id A-999" in f.explanation


def test_every_template_alias_points_at_a_template():
    for code, target in TEMPLATE_ALIAS.items():
        assert target in TEMPLATES, code


def quickbase_import(dest, *extra):
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools" / "make_fixtures.py"), "--quickbase-import", str(dest), *extra],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr


def dict_rows(path):
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8"), newline="")))


def test_generator_quickbase_import(tmp_path):
    quickbase_import(tmp_path / "qb")
    names = sorted(p.name for p in (tmp_path / "qb").glob("*.csv"))
    assert names == ["approvals.csv", "artifacts.csv", "inspections.csv", "obligations.csv"]
    rows = list(csv.reader(io.StringIO((tmp_path / "qb" / "inspections.csv").read_text(encoding="utf-8"))))
    assert rows[0][:4] == ["Inspection ID", "Revision", "Is Current", "Related Obligation"]
    assert len(rows) == 41
    assert (tmp_path / "qb" / "files" / "O-001" / "report.pdf").is_file()


def test_quickbase_import_references_resolve_in_fresh_tables(tmp_path):
    """Appendix D: a new table numbers its records 1, 2, … in import order, so reference n is the n-th parent row.
    Every reference must land on the parent that S01's canonical snapshot names."""
    quickbase_import(tmp_path / "qb", "--decided-by", "reviewer@your-realm.example")
    qb = {name: dict_rows(tmp_path / "qb" / f"{name}.csv") for name in ("obligations", "inspections")}
    qb["artifacts"] = dict_rows(tmp_path / "qb" / "artifacts.csv")
    qb["approvals"] = dict_rows(tmp_path / "qb" / "approvals.csv")
    canonical = REPO / "fixtures" / "scenarios" / "S01-clean" / "snapshot"
    obligation_of = {r["inspection_id"]: r["obligation_id"] for r in dict_rows(canonical / "inspections.csv")}
    inspection_of_artifact = {
        r["artifact_id"]: r["inspection_id"] for r in dict_rows(canonical / "artifacts.csv")
    }
    inspection_of_approval = {
        r["approval_id"]: r["inspection_id"] for r in dict_rows(canonical / "approvals.csv")
    }

    def parent(table, ref, id_column):
        assert ref.isdigit() and 1 <= int(ref) <= len(qb[table]), ref
        return qb[table][int(ref) - 1][id_column]

    for r in qb["inspections"]:
        assert (
            parent("obligations", r["Related Obligation"], "Obligation ID")
            == obligation_of[r["Inspection ID"]]
        )
    for r in qb["artifacts"]:
        expected = inspection_of_artifact[r["Artifact ID"]]
        assert parent("inspections", r["Related Inspection"], "Inspection ID") == expected
    for r in qb["approvals"]:
        expected = inspection_of_approval[r["Approval ID"]]
        assert parent("inspections", r["Related Inspection"], "Inspection ID") == expected
        assert r["Decided By"] == "reviewer@your-realm.example"
