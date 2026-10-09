"""capture-quickbase from the CLI (SPEC §11, §12.5): no network is touched in these tests."""

from conftest import REPO

from inspection_reconcile import cli
from inspection_reconcile.adapters.qb_capture import load_capture_config
from inspection_reconcile.vocab import EXIT_RUN_ERROR

EXAMPLE = REPO / "docs" / "qb-capture.example.yml"
MAPPING = str(REPO / "mappings" / "quickbase-demo.yml")


def test_example_config_is_valid():
    config = load_capture_config(EXAMPLE)
    assert config.token_env == "QB_USER_TOKEN"
    assert config.requests_per_10s <= 100


def test_missing_token_is_a_run_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("QB_USER_TOKEN", raising=False)
    out = tmp_path / "capture"
    code = cli.main(["capture-quickbase", "--config", str(EXAMPLE), "--mapping", MAPPING, "--out", str(out)])
    assert code == EXIT_RUN_ERROR
    assert "QB_TOKEN_MISSING" in capsys.readouterr().err
    assert not out.exists() or not any(out.iterdir())


def test_capture_refuses_non_empty_out(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QB_USER_TOKEN", "not-a-real-token")
    out = tmp_path / "capture"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    code = cli.main(["capture-quickbase", "--config", str(EXAMPLE), "--mapping", MAPPING, "--out", str(out)])
    assert code == EXIT_RUN_ERROR
    assert "OUT_NOT_EMPTY" in capsys.readouterr().err
