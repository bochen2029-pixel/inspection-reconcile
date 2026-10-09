"""CLI contract (SPEC §11): exit codes, outputs, the --out protocol, run errors, evidence-digest, compare."""

import json

import pytest
from conftest import POLICIES, SCENARIOS

from inspection_reconcile import cli
from inspection_reconcile.vocab import EXIT_BLOCKED, EXIT_COMPARE_DIFF, EXIT_OK, EXIT_RUN_ERROR, EXIT_UNKNOWN

DEMO_POLICY = str(POLICIES / "north-creek-demo.yml")


def snap(sid):
    return str(SCENARIOS / sid / "snapshot")


def assess(sid, out, *extra):
    return cli.main(["assess", "--snapshot", snap(sid), "--policy", DEMO_POLICY, "--out", str(out), *extra])


@pytest.mark.parametrize(
    "sid, code",
    [
        ("S01-clean", EXIT_OK),
        ("S02-missing-inspection", EXIT_BLOCKED),
        ("S05-incomplete-capture", EXIT_UNKNOWN),
    ],
)
def test_assess_exit_codes_and_outputs(tmp_path, capsys, sid, code):
    out = tmp_path / "out"
    assert assess(sid, out) == code
    assert sorted(p.name for p in out.iterdir()) == ["assessment.json", "report.html", "run-manifest.json"]
    doc = json.loads((out / "assessment.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "inspection-reconcile/assessment/v1"
    assert doc["status"] in capsys.readouterr().out


def test_outputs_are_lf_utf8_bytes(tmp_path):
    out = tmp_path / "out"
    assess("S02-missing-inspection", out)
    for name in ("assessment.json", "report.html", "run-manifest.json"):
        data = (out / name).read_bytes()
        assert b"\r\n" not in data
        assert data.endswith(b"\n") and not data.endswith(b"\n\n")
        data.decode("utf-8")


def test_assessment_has_no_runtime_values(tmp_path):
    out = tmp_path / "out"
    assess("S01-clean", out)
    text = (out / "assessment.json").read_text(encoding="utf-8")
    assert "generated_at" not in text
    assert str(tmp_path).replace("\\", "/") not in text.replace("\\", "/")


def test_non_empty_out_without_force_is_refused(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("user data")
    assert assess("S01-clean", out) == EXIT_RUN_ERROR
    assert "OUT_NOT_EMPTY" in capsys.readouterr().err
    assert assess("S01-clean", out, "--force") == EXIT_OK
    assert (out / "keep.txt").read_text() == "user data"


def test_malformed_policy_exit_2_and_no_outputs(tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("schema: inspection-reconcile/policy/v1\npack_id: x\n", encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(["assess", "--snapshot", snap("S01-clean"), "--policy", str(bad), "--out", str(out)])
    assert code == EXIT_RUN_ERROR
    assert not out.exists()
    assert "CONFIG_INVALID" in capsys.readouterr().err


def test_internal_exception_exit_2_without_traceback(tmp_path, monkeypatch, capsys):
    import inspection_reconcile.runner as runner

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(runner, "evaluate", boom)
    monkeypatch.delenv("INSPECTION_RECONCILE_DEBUG", raising=False)
    out = tmp_path / "out"
    assert assess("S01-clean", out) == EXIT_RUN_ERROR
    err = capsys.readouterr().err
    assert "INTERNAL_ERROR" in err and "Traceback" not in err
    assert not out.exists()


def test_log_json_diagnostics(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("x")
    assert (
        cli.main(
            [
                "--log-json",
                "assess",
                "--snapshot",
                snap("S01-clean"),
                "--policy",
                DEMO_POLICY,
                "--out",
                str(out),
            ]
        )
        == 2
    )
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert json.loads(line)["code"] == "OUT_NOT_EMPTY"


def test_usage_errors_exit_2(capsys):
    assert cli.main(["assess", "--policy", DEMO_POLICY]) == EXIT_RUN_ERROR
    assert cli.main(["no-such-command"]) == EXIT_RUN_ERROR


def test_version(capsys):
    assert cli.main(["--version"]) == 0
    assert "inspection-reconcile 0.1.0" in capsys.readouterr().out


def test_as_of_must_be_rfc3339(tmp_path, capsys):
    assert assess("S01-clean", tmp_path / "o", "--as-of", "2026-10-01") == EXIT_RUN_ERROR
    assert "USAGE" in capsys.readouterr().err


def test_as_of_before_effective_from(tmp_path, capsys):
    assert assess("S01-clean", tmp_path / "o", "--as-of", "2026-08-01T00:00:00Z") == EXIT_RUN_ERROR
    assert "POLICY_NOT_EFFECTIVE" in capsys.readouterr().err


def test_evidence_digest_prints_the_approved_digest(capsys):
    extra = json.loads((SCENARIOS / "S01-clean" / "expected.json").read_text(encoding="utf-8"))
    code = cli.main(
        [
            "evidence-digest",
            "--snapshot",
            snap("S01-clean"),
            "--policy",
            DEMO_POLICY,
            "--inspection",
            "INS-001",
        ]
    )
    assert code == EXIT_OK
    assert capsys.readouterr().out.splitlines()[0] == extra["apr_001_digest"]


def test_evidence_digest_unknown_inspection(capsys):
    code = cli.main(
        [
            "evidence-digest",
            "--snapshot",
            snap("S01-clean"),
            "--policy",
            DEMO_POLICY,
            "--inspection",
            "INS-999",
        ]
    )
    assert code == EXIT_RUN_ERROR
    assert "INSPECTION_NOT_FOUND" in capsys.readouterr().err


def test_validate_lists_record_defects(capsys):
    assert cli.main(["validate", "--snapshot", snap("S23-duplicate-key"), "--policy", DEMO_POLICY]) == EXIT_OK
    assert "R0:inspection:INS-004@1#DUPLICATE_KEY" in capsys.readouterr().out


def test_compare_s02_s06_ac19(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    assess("S02-missing-inspection", a)
    assess("S06-corrected", b)
    capsys.readouterr()
    out = tmp_path / "cmp.json"
    code = cli.main(["compare", "--before", str(a), "--after", str(b / "assessment.json"), "--out", str(out)])
    assert code == EXIT_COMPARE_DIFF
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [c["key"] for c in doc["changed"]] == [f"R{n}:obligation:O-017" for n in range(2, 7)]
    assert (doc["before"]["status"], doc["after"]["status"]) == ("BLOCKED", "READY_FOR_REVIEW")
    assert doc["added"] == [] and doc["removed"] == []
    assert doc["unchanged_count"] == 202


def test_compare_identical_exit_0(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assess("S01-clean", a)
    assess("S06-corrected", b)
    assert cli.main(["compare", "--before", str(a), "--after", str(b)]) == EXIT_OK


def test_demo_single_scenario(tmp_path):
    code = cli.main(
        [
            "demo",
            "--scenario",
            "S02-missing-inspection",
            "--out",
            str(tmp_path / "d"),
            "--fixtures",
            str(SCENARIOS),
        ]
    )
    assert code == EXIT_BLOCKED
    assert (tmp_path / "d" / "S02-missing-inspection" / "report.html").is_file()
