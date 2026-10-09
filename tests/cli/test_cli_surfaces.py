"""Output-surface regressions from the T5 review (SPEC §9.6, §11): --force for normalize and capture-quickbase,
the demo's normalized snapshot, --log-json for library log records, index links, compare's input checks."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import POLICIES, REPO, SCENARIOS

from inspection_reconcile import cli, demo
from inspection_reconcile.adapters import qb_capture
from inspection_reconcile.errors import RunError
from inspection_reconcile.oracle import load_oracle
from inspection_reconcile.runner import evaluate
from inspection_reconcile.vocab import EXIT_DEMO_MISMATCH, EXIT_OK, EXIT_RUN_ERROR

MAPPING = str(REPO / "mappings" / "quickbase-demo.yml")
POLICY = POLICIES / "north-creek-demo.yml"
S16 = SCENARIOS / "S16-quickbase-clean" / "export"
EXAMPLE = REPO / "docs" / "qb-capture.example.yml"


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def normalize(out: Path, *extra: str, export: Path = S16) -> int:
    return cli.main(["normalize", "--export", str(export), "--mapping", MAPPING, "--out", str(out), *extra])


# -- --force for normalize and capture-quickbase (SPEC §9.6) --------------------------------------------------


def test_normalize_force_replaces_its_own_previous_output(tmp_path):
    fresh, out = tmp_path / "fresh", tmp_path / "out"
    assert normalize(fresh) == EXIT_OK
    assert normalize(out) == EXIT_OK
    (out / "evidence" / "stale.bin").write_bytes(b"left over from an earlier export")
    assert normalize(out, "--force") == EXIT_OK
    assert tree(out) == tree(fresh)


def test_a_refused_force_run_deletes_nothing(tmp_path, capsys):
    out = tmp_path / "out"
    assert normalize(out) == EXIT_OK
    (out / "index.html").write_text("an earlier demo index", encoding="utf-8")
    (out / "notes.txt").write_text("the operator's notes", encoding="utf-8")
    before = tree(out)
    capsys.readouterr()
    assert normalize(out, "--force") == EXIT_RUN_ERROR
    err = capsys.readouterr().err
    assert "OUT_NOT_EMPTY" in err and "index.html" in err and "notes.txt" in err
    assert tree(out) == before


def test_a_failed_force_run_keeps_the_previous_output(tmp_path):
    out, broken = tmp_path / "out", tmp_path / "broken"
    assert normalize(out) == EXIT_OK
    before = sorted(p.name for p in out.iterdir()), tree(out)
    shutil.copytree(S16, broken)
    (broken / "capture-manifest.json").write_text("{", encoding="utf-8")
    assert normalize(out, "--force", export=broken) == EXIT_RUN_ERROR
    assert (sorted(p.name for p in out.iterdir()), tree(out)) == before


class NullClient:
    def close(self) -> None:
        pass


def fake_capture(files: dict[str, bytes], fail: bool = False):
    def capture(client, config, mapping, out_dir, *, now):
        out_dir = Path(out_dir)
        if out_dir.exists() and any(out_dir.iterdir()):
            raise RunError("OUT_NOT_EMPTY", "the capture output directory must be absent or empty")
        for name, data in files.items():
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if fail:
            raise RunError("QB_HTTP_ERROR", "HTTP 503 after 5 attempts")
        return {}

    return capture


def capture_cli(out: Path, *extra: str) -> int:
    return cli.main(
        ["capture-quickbase", "--config", str(EXAMPLE), "--mapping", MAPPING, "--out", str(out), *extra]
    )


def test_capture_force_replaces_its_own_previous_output(tmp_path, monkeypatch):
    monkeypatch.setattr(qb_capture, "make_client", lambda config, **kw: NullClient())
    out = tmp_path / "cap"
    first = {
        "capture-manifest.json": b"1",
        "tables/inspections/page-0001.json": b"1",
        "files/b/1/7/v1/a.pdf": b"%",
    }
    monkeypatch.setattr(qb_capture, "capture", fake_capture(first))
    assert capture_cli(out) == EXIT_OK
    second = {"capture-manifest.json": b"2", "tables/inspections/page-0001.json": b"2"}
    monkeypatch.setattr(qb_capture, "capture", fake_capture(second))
    assert capture_cli(out, "--force") == EXIT_OK
    assert tree(out) == second


def test_a_failed_capture_keeps_the_previous_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(qb_capture, "make_client", lambda config, **kw: NullClient())
    out = tmp_path / "cap"
    first = {"capture-manifest.json": b"1", "tables/inspections/page-0001.json": b"1"}
    monkeypatch.setattr(qb_capture, "capture", fake_capture(first))
    assert capture_cli(out) == EXIT_OK
    monkeypatch.setattr(
        qb_capture, "capture", fake_capture({"tables/x/page-0001.json": b"partial"}, fail=True)
    )
    assert capture_cli(out, "--force") == EXIT_RUN_ERROR
    assert sorted(p.name for p in out.iterdir()) == ["capture-manifest.json", "tables"]
    assert tree(out) == first


# -- demo: an export scenario is normalized into <out>/<scenario>/snapshot (SPEC §11) --------------------------


def test_demo_writes_the_normalized_snapshot_of_an_export_scenario(tmp_path):
    out = tmp_path / "d"
    code = cli.main(
        ["demo", "--scenario", "S16-quickbase-clean", "--out", str(out), "--fixtures", str(SCENARIOS)]
    )
    assert code == EXIT_OK
    home = out / "S16-quickbase-clean"
    snapshot = home / "snapshot"
    assert (snapshot / "manifest.json").is_file() and (snapshot / "normalization.json").is_file()
    assessment = json.loads((home / "assessment.json").read_text(encoding="utf-8"))
    assert evaluate(snapshot, POLICY).assessment.evaluation_id == assessment["evaluation_id"]
    inputs = json.loads((home / "run-manifest.json").read_text(encoding="utf-8"))["inputs"]
    assert {i["role"] for i in inputs} >= {"manifest", "inspections", "normalization", "policy"}
    for item in inputs:  # every recorded input still exists after the run (SPEC §9.3)
        assert Path(item["path"]).is_file(), item


def test_index_links_only_reports_that_exist(tmp_path, monkeypatch):
    oracle = load_oracle(REPO / "fixtures" / "oracle.yaml")
    small = {
        "scenarios": {"S01-clean": oracle["scenarios"]["S01-clean"], "S98-absent": {"status": "BLOCKED"}}
    }
    monkeypatch.setattr(demo, "load_oracle", lambda path: small)
    out = tmp_path / "d"
    assert demo.run_demo(SCENARIOS, out, None, False, say=lambda line: None) == EXIT_DEMO_MISMATCH
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="S01-clean/report.html"' in index and (out / "S01-clean" / "report.html").is_file()
    assert 'href="S98-absent/report.html"' not in index
    assert not (out / "S98-absent").exists()


# -- --log-json covers library log records (SPEC §11) ---------------------------------------------------------

PROBE = (
    "import logging, sys\n"
    "from inspection_reconcile import cli\n"
    "def probe(args):\n"
    "    logging.getLogger('inspection_reconcile.qb').warning('table %s: probe', 'inspections')\n"
    "    return 0\n"
    "cli.cmd_validate = probe\n"
    "sys.exit(cli.main(sys.argv[1:]))\n"
)


def run_probe(*argv: str) -> tuple[int, list[str]]:
    # A fresh interpreter: under pytest the root logger already has handlers, so basicConfig would do nothing.
    done = subprocess.run([sys.executable, "-c", PROBE, *argv], capture_output=True, timeout=120, check=False)
    return done.returncode, done.stderr.decode("utf-8").splitlines()


def test_log_json_turns_library_log_records_into_json_lines():
    code, lines = run_probe("--log-json", "validate", "--snapshot", "s", "--policy", "p")
    assert code == EXIT_OK
    assert [json.loads(line) for line in lines] == [
        {
            "level": "warning",
            "code": "LOG",
            "logger": "inspection_reconcile.qb",
            "message": "table inspections: probe",
        }
    ]


def test_without_log_json_library_records_stay_plain_text():
    code, lines = run_probe("validate", "--snapshot", "s", "--policy", "p")
    assert code == EXIT_OK
    assert lines == ["WARNING: inspection_reconcile.qb: table inspections: probe"]


# -- compare refuses an input it cannot read as an assessment (SPEC §9.5, §11) --------------------------------


def test_compare_refuses_a_malformed_assessment(tmp_path, capsys):
    good = tmp_path / "good"
    snapshot = SCENARIOS / "S01-clean" / "snapshot"
    assert (
        cli.main(["assess", "--snapshot", str(snapshot), "--policy", str(POLICY), "--out", str(good)])
        == EXIT_OK
    )
    bad = tmp_path / "bad.json"
    doc = {"schema": "inspection-reconcile/assessment/v1", "evaluation_id": "sha256:00", "status": "BLOCKED"}
    bad.write_text(json.dumps(doc), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["compare", "--before", str(bad), "--after", str(good)]) == EXIT_RUN_ERROR
    err = capsys.readouterr().err
    assert "COMPARE_INPUT_INVALID" in err and "INTERNAL_ERROR" not in err
    doc = json.loads((good / "assessment.json").read_text(encoding="utf-8"))
    del doc["findings"][3]["expected"]
    bad.write_text(json.dumps(doc), encoding="utf-8")
    assert cli.main(["compare", "--before", str(good), "--after", str(bad)]) == EXIT_RUN_ERROR
    err = capsys.readouterr().err
    assert "COMPARE_INPUT_INVALID" in err and "INTERNAL_ERROR" not in err
