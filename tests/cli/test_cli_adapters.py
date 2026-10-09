"""CLI paths that wrap the adapters (SPEC §11): normalize, assess --export, export-sqlite."""

import json
import sqlite3

from conftest import POLICIES, REPO, SCENARIOS

from inspection_reconcile import cli
from inspection_reconcile.vocab import EXIT_OK, EXIT_RUN_ERROR, EXIT_UNKNOWN

MAPPING = str(REPO / "mappings" / "quickbase-demo.yml")
POLICY = str(POLICIES / "north-creek-demo.yml")


def test_normalize_then_assess(tmp_path):
    snap = tmp_path / "snap"
    assert (
        cli.main(
            [
                "normalize",
                "--export",
                str(SCENARIOS / "S16-quickbase-clean" / "export"),
                "--mapping",
                MAPPING,
                "--out",
                str(snap),
            ]
        )
        == EXIT_OK
    )
    assert (snap / "normalization.json").is_file()
    out = tmp_path / "out"
    assert cli.main(["assess", "--snapshot", str(snap), "--policy", POLICY, "--out", str(out)]) == EXIT_OK


def test_assess_export_matches_canonical_identity(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    export = str(SCENARIOS / "S16-quickbase-clean" / "export")
    assert (
        cli.main(["assess", "--export", export, "--mapping", MAPPING, "--policy", POLICY, "--out", str(a)])
        == EXIT_OK
    )
    assert (
        cli.main(
            [
                "assess",
                "--snapshot",
                str(SCENARIOS / "S01-clean" / "snapshot"),
                "--policy",
                POLICY,
                "--out",
                str(b),
            ]
        )
        == EXIT_OK
    )
    da = json.loads((a / "assessment.json").read_text(encoding="utf-8"))
    db = json.loads((b / "assessment.json").read_text(encoding="utf-8"))
    assert da["evaluation_id"] == db["evaluation_id"]
    assert da["assessment_semantic_sha256"] == db["assessment_semantic_sha256"]
    manifest = json.loads((a / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["mapping"]["mapping_id"] == "quickbase-demo"


def test_assess_export_unmapped_value_is_unknown(tmp_path):
    export = str(SCENARIOS / "S17-quickbase-unmapped-value" / "export")
    out = tmp_path / "o"
    assert (
        cli.main(["assess", "--export", export, "--mapping", MAPPING, "--policy", POLICY, "--out", str(out)])
        == EXIT_UNKNOWN
    )


def test_assess_export_needs_mapping(tmp_path, capsys):
    export = str(SCENARIOS / "S16-quickbase-clean" / "export")
    assert (
        cli.main(["assess", "--export", export, "--policy", POLICY, "--out", str(tmp_path / "o")])
        == EXIT_RUN_ERROR
    )
    assert "--export needs --mapping" in capsys.readouterr().err


def test_export_sqlite(tmp_path):
    out = tmp_path / "db.sqlite"
    args = [
        "export-sqlite",
        "--snapshot",
        str(SCENARIOS / "S15-out-of-scope-record" / "snapshot"),
        "--policy",
        POLICY,
        "--out",
        str(out),
    ]
    assert cli.main(args) == EXIT_OK
    connection = sqlite3.connect(out)
    try:
        rows = connection.execute(
            "SELECT DISTINCT inspection_id FROM inspections WHERE is_current = 1 "
            "AND (obligation_id IS NULL OR obligation_id NOT IN (SELECT obligation_id FROM obligations))"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("INS-099",)]
    assert cli.main(args) == EXIT_RUN_ERROR  # exists, no --force
    assert cli.main([*args, "--force"]) == EXIT_OK
