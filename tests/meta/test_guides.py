"""The guides (docs/guide/*.md) stay true to the tool: every command annotated with an exit code is run as printed,
every image exists, every reason code exists, and the run-error catalog lists every code the package can raise."""

import re
import shlex
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from conftest import REPO

from inspection_reconcile import cli
from inspection_reconcile.report.templates import TEMPLATE_ALIAS, TEMPLATES
from inspection_reconcile.vocab import STATUS_EXIT

GUIDE_DIR = REPO / "docs" / "guide"
GUIDES = sorted(GUIDE_DIR.glob("*.md"))
SRC = REPO / "src" / "inspection_reconcile"
# Codes the package raises that never reach the command line, and why.
NOT_IN_CATALOG = {"QB_FILE_TOO_LARGE": "handled inside the capture: the file is recorded as too_large"}


def commands(guide: Path) -> list[tuple[list[str], int]]:
    """Every `uv run inspection-reconcile ...` line in a fenced shell block that ends in `# exit N`, in order."""
    found = []
    for block in re.findall(r"```(?:bash|powershell)\n(.*?)```", guide.read_text(encoding="utf-8"), re.S):
        for line in block.replace("\\\n", " ").splitlines():
            match = re.match(r"\s*uv run inspection-reconcile (.*?)\s*# exit (\d+)", line)
            if match:
                found.append((shlex.split(match.group(1)), int(match.group(2))))
    return found


def test_the_guides_annotate_commands():
    assert all(commands(guide) for guide in GUIDES), [g.name for g in GUIDES if not commands(g)]


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.stem)
def test_annotated_commands_exit_as_documented(guide, tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    for argv, expected in commands(guide):
        argv = [str(tmp_path / a) if a.startswith("out/") else a for a in argv]  # outputs go to a temp dir
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as err:
            code = cli.main(argv)
        assert code == expected, (argv, err.getvalue())


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.stem)
def test_every_image_exists(guide):
    images = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", guide.read_text(encoding="utf-8"))
    assert images
    missing = [src for src in images if not (guide.parent / src).is_file()]
    assert not missing, missing


def test_every_reason_code_in_the_user_guide_exists():
    text = (GUIDE_DIR / "user-guide.md").read_text(encoding="utf-8")
    codes = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)`", text, re.M))
    codes |= {
        c
        for row in re.findall(r"^\| (`[A-Z_]+`(?:, `[A-Z_]+`)+) \|", text, re.M)
        for c in re.findall(r"`([A-Z_]+)`", row)
    }
    known = (
        set(TEMPLATES) | set(TEMPLATE_ALIAS) | set(STATUS_EXIT) | {"PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED"}
    )
    run_errors = raised_codes()  # the troubleshooting table lists run errors next to the reason codes
    assert codes, "no reason-code tables found"
    assert not codes - known - run_errors, sorted(codes - known - run_errors)


def raised_codes() -> set[str]:
    codes = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        codes |= set(re.findall(r'RunError\(\s*"([A-Z][A-Z0-9_]+)"', text))
        codes |= set(re.findall(r'super\(\).__init__\(\s*"([A-Z][A-Z0-9_]+)"', text))
        codes |= set(re.findall(r'diag\(\s*"error",\s*"([A-Z][A-Z0-9_]+)"', text))
    return codes


def test_the_run_error_catalog_is_complete():
    text = (GUIDE_DIR / "admin-guide.md").read_text(encoding="utf-8")
    catalog = text.split("### Run-error codes", 1)[1].split("\n### ", 1)[0]
    listed = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", catalog, re.M))
    raised = raised_codes()
    assert raised >= {"OUT_NOT_EMPTY", "QB_HTTP_ERROR", "INTERNAL_ERROR"}  # the scan itself works
    assert not raised - listed - set(NOT_IN_CATALOG), sorted(raised - listed - set(NOT_IN_CATALOG))
    assert not listed - raised, sorted(listed - raised)
