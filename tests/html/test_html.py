"""report.html (SPEC §9.4): escaping, no scripts, no external resources, CSP, determinism."""

import re

from builder import build, mf, write

from inspection_reconcile.runner import evaluate


def test_hostile_strings_are_escaped(tmp_path):
    def hostile(snap):
        snap.project["name"] = "<script>alert('name')</script>"
        snap.find("inspections", inspection_id="INS-005")["activity_kind"] = '<img src=x onerror="alert(1)">'

    root = write(_mutated(hostile), tmp_path / "snap")
    from conftest import POLICIES

    html = evaluate(root, POLICIES / "north-creek-demo.yml").outputs()["report.html"].decode("utf-8")
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def _mutated(fn):
    snap = mf.baseline("T00-html")
    fn(snap)
    return snap


def test_no_external_resources_and_csp(tmp_path):
    a = build(tmp_path)
    from conftest import POLICIES, SCENARIOS

    html = (
        evaluate(SCENARIOS / "S02-missing-inspection" / "snapshot", POLICIES / "north-creek-demo.yml")
        .outputs()["report.html"]
        .decode("utf-8")
    )
    assert a.status == "READY_FOR_REVIEW"
    assert re.search(r"""(src|href)\s*=\s*["']?https?:""", html, re.I) is None
    assert "<script" not in html.lower()
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    assert '<meta charset="utf-8">' in html
    assert "SYNTHETIC DATA" in html


def test_report_is_byte_deterministic():
    from conftest import POLICIES, SCENARIOS

    snapshot = SCENARIOS / "S05-incomplete-capture" / "snapshot"
    first = evaluate(snapshot, POLICIES / "north-creek-demo.yml").outputs()
    second = evaluate(snapshot, POLICIES / "north-creek-demo.yml").outputs()
    assert first["report.html"] == second["report.html"]
    assert first["assessment.json"] == second["assessment.json"]


SECTIONS = (  # SPEC §9.4, in order
    '<div class="banner">SYNTHETIC DATA: fictional project</div>',
    "<h1>",
    "<h2>What this result means</h2>",
    "<h2>Coverage</h2>",
    "<h2>Summary</h2>",
    "<h2>Root findings</h2>",
    "<h2>Obligations</h2>",
    "<h2>All findings</h2>",
    "<h2>Provenance</h2>",
)
CSP = (
    """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">"""
)


def _report(scenario):
    from conftest import POLICIES, SCENARIOS

    snapshot = SCENARIOS / scenario / "snapshot"
    return evaluate(snapshot, POLICIES / "north-creek-demo.yml").outputs()["report.html"].decode("utf-8")


def test_sections_wording_and_csp_follow_the_spec():
    html = _report("S02-missing-inspection")
    positions = [html.index(marker) for marker in SECTIONS]
    assert positions == sorted(positions)
    assert "READY_FOR_REVIEW is not approval" in html
    assert CSP in html
    assert "· blocks 4</summary>" in html  # R2:obligation:O-017 blocks R3, R4, R5 and R6 (Appendix A, S02)
    assert re.search(r"[A-Za-z]:[\\/]|/(Users|home|tmp|var)/", html) is None  # no absolute paths


def test_every_root_states_how_many_findings_it_blocks():
    summaries = re.findall(r"<summary>(.*?)</summary>", _report("S05-incomplete-capture"), re.S)
    assert summaries
    assert all(re.search(r"· blocks \d+$", s) for s in summaries), summaries
