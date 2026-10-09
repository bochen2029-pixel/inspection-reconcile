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


def test_generator_quickbase_import(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools" / "make_fixtures.py"), "--quickbase-import", str(tmp_path / "qb")],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr
    names = sorted(p.name for p in (tmp_path / "qb").glob("*.csv"))
    assert names == ["approvals.csv", "artifacts.csv", "inspections.csv", "obligations.csv"]
    rows = list(csv.reader(io.StringIO((tmp_path / "qb" / "inspections.csv").read_text(encoding="utf-8"))))
    assert rows[0][:4] == ["Inspection ID", "Revision", "Is Current", "Related Obligation"]
    assert len(rows) == 41
    assert (tmp_path / "qb" / "files" / "O-001" / "report.pdf").is_file()
