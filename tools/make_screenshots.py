"""Reproducible screenshots of the tool's own outputs, for the README, the guides and the site.

    uv run python tools/make_screenshots.py [--out docs/images] [--browser PATH]

It runs `demo --all` into a temporary directory and captures each shot with headless Chrome or Edge at a fixed
width and twice the pixel density. A shot is taken of a temporary COPY of the page, never of a published report.
The copy gets a small injected script that keeps the chosen report sections and measures the height, so every
image fits its content exactly; only the copy's CSP is relaxed to allow that script. Dark variants force the
report's own dark palette. Every browser run is bounded by a Python subprocess timeout and uses a throwaway
profile, so no browser window or user profile is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CSP = (
    """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">"""
)
SHOT_CSP = """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">"""
DARK_QUERY = "@media (prefers-color-scheme: dark) {"
BROWSER_TIMEOUT = 120
CANDIDATES = (
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


@dataclass(frozen=True)
class Shot:
    name: str
    page: str  # relative to the demo output directory
    # The report sections (h2 text) to keep after the header; None keeps the whole page.
    keep: tuple[str, ...] | None
    width: int
    dark: bool  # also write <name>-dark.png with the report's dark palette
    # Data rows (first, last; 1-based) to keep in the tables of the kept sections; None keeps them all.
    rows: tuple[int, int] | None = None
    header: bool = True  # keep the report header (banner, title, status) above the kept sections


SHOTS = (
    Shot("demo-index", "index.html", None, 1100, False),
    Shot(
        "report-s02",
        "S02-missing-inspection/report.html",
        ("What this result means", "Coverage", "Summary", "Root findings"),
        1200,
        True,
    ),
    Shot("report-s08-root", "S08-silent-byte-change/report.html", ("Root findings",), 1200, True),
    Shot("report-s05-unknown", "S05-incomplete-capture/report.html", ("Coverage", "Summary"), 1200, True),
    Shot("report-s02-grid", "S02-missing-inspection/report.html", ("Obligations",), 1200, False),
    # The guides (docs/guide): a narrower width keeps the text legible on a printed page.
    Shot("guide-s02-header", "S02-missing-inspection/report.html", ("What this result means",), 880, False),
    Shot("guide-s05-coverage", "S05-incomplete-capture/report.html", ("Coverage", "Summary"), 880, False),
    Shot("guide-s02-root", "S02-missing-inspection/report.html", ("Root findings",), 880, False),
    Shot(
        "guide-s02-grid", "S02-missing-inspection/report.html", ("Obligations",), 880, False, (14, 20), False
    ),
    Shot(
        "guide-s02-findings",
        "S02-missing-inspection/report.html",
        ("All findings",),
        880,
        False,
        (20, 26),
        False,
    ),
    Shot(
        "guide-s02-provenance", "S02-missing-inspection/report.html", ("Provenance",), 880, False, None, False
    ),
    Shot("guide-s06-ready", "S06-corrected/report.html", ("Summary", "Root findings"), 880, False),
    Shot("guide-s08-root", "S08-silent-byte-change/report.html", ("Root findings",), 880, False),
    Shot(
        "guide-s12-root", "S12-attachment-not-captured/report.html", ("Coverage", "Root findings"), 880, False
    ),
    Shot("guide-s17-root", "S17-quickbase-unmapped-value/report.html", ("Root findings",), 880, False),
    Shot("guide-demo-index", "index.html", None, 880, False),
)


def find_browser(explicit: str | None) -> str:
    for candidate in (explicit, os.environ.get("INSPECTION_RECONCILE_BROWSER")):
        if candidate:
            return candidate
    for path in CANDIDATES:
        if Path(path).is_file():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("no Chrome, Chromium or Edge found; pass --browser PATH")


def shot_script(
    keep: tuple[str, ...] | None, rows: tuple[int, int] | None = None, header: bool = True
) -> str:
    return (
        "<script>(function(){var keep=" + json.dumps(list(keep) if keep else None) + ";"
        "var rows=" + json.dumps(list(rows) if rows else None) + ";"
        "var header=" + json.dumps(header) + ";"
        "var main=document.querySelector('main')||document.body;"
        "if(keep){var section='';Array.prototype.forEach.call(main.children,function(el){"
        "if(el.tagName==='H2'){section=el.textContent.trim();}"
        "if((section===''&&!header)||(section!==''&&keep.indexOf(section)<0)){el.style.display='none';}});}"
        # A row window: in every table still shown, keep only data rows first..last (a header row has a <th>).
        "if(rows){Array.prototype.forEach.call(main.querySelectorAll('table'),function(t){var n=0;"
        "Array.prototype.forEach.call(t.rows,function(r){if(r.querySelector('th')&&!r.querySelector('td')){return;}"
        "n+=1;if(n<rows[0]||n>rows[1]){r.style.display='none';}});});}"
        "document.body.setAttribute('data-shot-height',"
        "String(Math.ceil(document.body.getBoundingClientRect().height)));})();</script>"
    )


def prepare_copy(page: Path, shot: Shot, dark: bool) -> Path:
    html = page.read_text(encoding="utf-8")
    if CSP not in html:
        raise SystemExit(f"{page}: the expected CSP meta is missing; the page layout changed")
    html = html.replace(CSP, SHOT_CSP, 1)
    # The headless browser follows the system theme, so each variant pins its palette explicitly.
    if dark and DARK_QUERY not in html:
        raise SystemExit(f"{page}: no dark palette to force")
    html = html.replace(DARK_QUERY, "@media all {" if dark else "@media not all {", 1)
    html = html.replace("</body>", shot_script(shot.keep, shot.rows, shot.header) + "</body>", 1)
    copy = page.with_name(f".shot-{shot.name}{'-dark' if dark else ''}.html")
    copy.write_text(html, encoding="utf-8", newline="\n")
    return copy


def browser_run(browser: str, profile: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
        *args,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=BROWSER_TIMEOUT,
        check=False,
    )


def capture(browser: str, profile: Path, copy: Path, width: int, target: Path) -> tuple[int, int]:
    url = copy.resolve().as_uri()
    # Measure at a normal window: a very short one lays the page out at zero width. A headless window's viewport
    # is slightly narrower than the window, so the measured height is an upper bound for the screenshot width.
    dom = browser_run(browser, profile, f"--window-size={width},900", "--dump-dom", url)
    match = re.search(r'data-shot-height="(\d+)"', dom.stdout)
    if match is None:
        raise SystemExit(f"{copy.name}: could not measure the page (browser exit {dom.returncode})")
    height = int(match.group(1))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    result = browser_run(
        browser,
        profile,
        f"--window-size={width},{height}",
        "--force-device-scale-factor=2",
        f"--screenshot={target.resolve()}",
        url,
    )
    if not target.is_file():
        raise SystemExit(f"{target.name}: the browser wrote no screenshot (exit {result.returncode})")
    return width, height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=str(REPO / "docs" / "images"))
    parser.add_argument("--browser")
    args = parser.parse_args(argv)
    browser = find_browser(args.browser)
    out = Path(args.out)
    # A browser helper process can hold a throwaway profile for a moment after exit; that must not turn a
    # successful run into a traceback.
    with tempfile.TemporaryDirectory(prefix="ir-shots-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        demo = work / "demo"
        run = subprocess.run(
            [sys.executable, "-m", "inspection_reconcile", "demo", "--all", "--out", str(demo)],
            cwd=REPO,
            timeout=600,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if run.returncode != 0:
            raise SystemExit(f"demo --all exited {run.returncode}:\n{run.stdout}\n{run.stderr}")
        for shot in SHOTS:
            for dark in (False, True) if shot.dark else (False,):
                copy = prepare_copy(demo / shot.page, shot, dark)
                name = f"{shot.name}{'-dark' if dark else ''}.png"
                profile = work / f"profile-{name}"
                width, height = capture(browser, profile, copy, shot.width, out / name)
                print(f"{(out / name).as_posix()}  {width}x{height} css px (2x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
