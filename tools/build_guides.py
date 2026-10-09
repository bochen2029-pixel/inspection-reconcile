"""Build the User Guide and the Administrator Guide (docs/guide/*.md) into print-designed PDFs.

    uv run --no-project --with markdown-it-py --with pypdf python tools/build_guides.py
        [--only user|admin] [--paper letter|a4] [--docx] [--date YYYY-MM-DD] [--browser PATH] [--keep-html DIR]

The Markdown files are the sources and read well on GitHub. For each guide the build:

1. renders the Markdown (markdown-it-py: CommonMark plus tables) with docs/guide/guide.css into one HTML file:
   a cover page, a contents page, numbered sections, figures with numbered captions, and GitHub alerts
   (``> [!NOTE]``) as callouts. Links to other repository files become GitHub links, and links between the two
   guides point at the other PDF;
2. prints it to PDF with headless Chrome or Edge (--print-to-pdf, with a document outline), through
   subprocess.run with a timeout and a throwaway browser profile;
3. reads the outline back (pypdf) to learn the page on which each section starts, writes those page numbers into
   the contents page, and prints again. The second print must keep the same page count.

``--docx`` also writes a Word version with pandoc, when pandoc is installed. Nothing in src/ imports this script;
markdown-it-py and pypdf come from the ephemeral uv environment shown above.

Reproducibility: the document date (by default, that of the last commit that changed the guides) stands in for the
build time in the PDF metadata and in the DOCX package. Rebuilding from the same commit therefore gives byte-identical
Word files. The PDFs are identical in text, layout and appearance, and byte-identical in repeated runs. The
browser does not guarantee byte identity in every run: a cold first run was once seen to omit invisible drawing
detail.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from make_screenshots import find_browser  # tools/ is on sys.path when this file runs as a script
from markdown_it import MarkdownIt
from markdown_it.token import Token
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject

REPO = Path(__file__).resolve().parents[1]
GUIDE_DIR = REPO / "docs" / "guide"
REPO_URL = "https://github.com/bochen2029-pixel/inspection-reconcile"
BROWSER_TIMEOUT = 180
GUIDES = {"user": "user-guide.md", "admin": "admin-guide.md"}
ALERTS = {"NOTE": "Note", "TIP": "Tip", "IMPORTANT": "Important", "WARNING": "Warning", "CAUTION": "Caution"}
PAPER = {"letter": ("letter", "279.4mm"), "a4": ("A4", "297mm")}
META = re.compile(r"\A<!--\s*\n(.*?)\n-->\s*\n", re.S)


@dataclass
class Entry:
    level: int
    number: str
    text: str  # plain text, as the PDF outline shows it
    html: str  # inline HTML, for the contents page
    ident: str
    page: int | None = None
    outline_index: int | None = None  # position in the PDF outline, in document order


def pdf_name(source: str) -> str:
    return f"inspection-reconcile-{Path(source).stem}.pdf"


def version() -> str:
    init = (REPO / "src" / "inspection_reconcile" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', init)
    if match is None:
        raise SystemExit("no __version__ in src/inspection_reconcile/__init__.py")
    return match.group(1)


def read_guide(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = META.match(text)
    if match is None:
        raise SystemExit(f"{path.name}: the file must start with a <!-- title: ... --> metadata comment")
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    for key in ("title", "subtitle", "audience", "running"):
        if key not in meta:
            raise SystemExit(f"{path.name}: the metadata comment needs '{key}:'")
    return meta, text[match.end() :]


def slug(text: str, seen: set[str]) -> str:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces to hyphens, duplicates numbered."""
    base = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    ident, n = base, 0
    while ident in seen:
        n += 1
        ident = f"{base}-{n}"
    seen.add(ident)
    return ident


def plain(markdown_inline: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", markdown_inline)).strip()


def number_headings(md: MarkdownIt, tokens: list[Token]) -> tuple[list[Token], list[Entry]]:
    """Drop the document title (the cover replaces it); number and anchor every h2 and h3."""
    out: list[Token] = []
    entries: list[Entry] = []
    seen: set[str] = set()
    chapter = section = 0
    dropped_title = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1])
            inline = tokens[i + 1]
            if level == 1 and not dropped_title:
                dropped_title = True
                i += 3
                continue
            token.attrSet("id", slug(plain(inline.content), seen))
            number = None
            if level == 2:
                chapter, section = chapter + 1, 0
                number = str(chapter)
            elif level == 3:
                section += 1
                number = f"{chapter}.{section}"
            if number is not None and inline.children is not None:
                span = Token("html_inline", "", 0)
                span.content = f'<span class="num">{number}</span> '
                inline.children.insert(0, span)
                text, rendered = plain(inline.content), md.renderInline(inline.content).strip()
                entries.append(Entry(level, number, text, rendered, str(token.attrGet("id"))))
        out.append(token)
        i += 1
    return out, entries


def retarget(href: str, source: Path, extension: str) -> str:
    """A link in a guide, for the built document. Web links and in-page anchors stay. The other guide becomes its
    built file: a relative link in a DOCX, a GitHub link in a PDF (the browser that prints the PDF would resolve a
    relative link against its temporary folder). Any other repository file becomes a GitHub link."""
    if re.match(r"^[a-z][a-z0-9+.-]*:|^#", href):
        return href
    path, _, fragment = href.partition("#")
    target = (source.parent / path).resolve()
    if target.parent == GUIDE_DIR.resolve() and target.name in GUIDES.values():
        new = pdf_name(target.name).replace(".pdf", extension)
        if extension == ".pdf":
            new = f"{REPO_URL}/blob/main/docs/guide/{new}"
    else:
        rel = target.relative_to(REPO.resolve()).as_posix()
        new = f"{REPO_URL}/{'tree' if target.is_dir() else 'blob'}/main/{rel}"
    return new + ("#" + fragment if fragment else "")


def rewrite_links(body: str, source: Path) -> str:
    def link(match: re.Match[str]) -> str:
        return f'href="{html.escape(retarget(html.unescape(match.group(1)), source, ".pdf"))}"'

    return re.sub(r'href="([^"]+)"', link, body)


def figures(body: str, source: Path) -> str:
    count = 0

    def figure(match: re.Match[str]) -> str:
        nonlocal count
        src, alt = html.unescape(match.group(1)), match.group(2)
        uri = (source.parent / src).resolve().as_uri()
        css = ' class="diagram"' if src.endswith(".svg") else ""
        if not alt:
            return f'<figure><img src="{uri}"{css} alt=""></figure>'
        count += 1
        return (
            f'<figure><img src="{uri}"{css} alt="{alt}">'
            f'<figcaption><span class="fig">Figure {count}.</span> {alt}</figcaption></figure>'
        )

    return re.sub(r'<p><img src="([^"]+)" alt="([^"]*)"(?: title="[^"]*")? ?/?></p>', figure, body)


def callouts(body: str) -> str:
    pattern = re.compile(
        r"<blockquote>\s*<p>\[!(" + "|".join(ALERTS) + r")\][ \t]*(?:\n|<br ?/?>\n?)?(.*?)</blockquote>", re.S
    )

    def callout(match: re.Match[str]) -> str:
        kind = match.group(1)
        inner = ("<p>" + match.group(2)).replace("<p></p>", "").strip()
        return f'<div class="callout callout-{kind.lower()}"><p class="callout-title">{ALERTS[kind]}</p>{inner}</div>'

    return pattern.sub(callout, body)


def tables(body: str) -> str:
    """Keep small tables on one page, and let long codes in table cells break only after an underscore."""

    def table(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.count("<tr>") <= 8:
            text = text.replace("<table>", '<table class="keep">', 1)
        return re.sub(
            r"<code>([^<]*)</code>", lambda m: "<code>" + m.group(1).replace("_", "_<wbr>") + "</code>", text
        )

    return re.sub(r"<table>.*?</table>", table, body, flags=re.S)


def contents(entries: list[Entry]) -> str:
    items = []
    for e in entries:
        page = str(e.page) if e.page is not None else "00"  # same width in both passes: no reflow
        items.append(
            f'<li class="toc-l{e.level}"><a href="#{e.ident}"><span class="toc-num">{e.number}</span>'
            f'<span class="toc-text">{e.html}</span><span class="toc-fill"></span>'
            f'<span class="toc-page">{page}</span></a></li>'
        )
    return '<nav class="toc"><p class="toc-heading">Contents</p><ol>' + "".join(items) + "</ol></nav>"


def cover(meta: dict[str, str], when: dt.date) -> str:
    rows = [
        ("Version", version()),
        ("Date", f"{when.day} {when.strftime('%B %Y')}"),
        ("Audience", meta["audience"]),
        ("Repository", REPO_URL.removeprefix("https://")),
        ("Live demo", "bochen2029-pixel.github.io/inspection-reconcile"),
        ("Project page", "opnaorta.ai/inspection-reconcile"),
    ]
    meta_rows = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in rows)
    return (
        '<section class="cover"><div class="cover-band">'
        '<p class="cover-kicker">inspection-reconcile</p>'
        f'<h1 class="cover-title">{html.escape(meta["title"])}</h1>'
        f'<p class="cover-subtitle">{html.escape(meta["subtitle"])}</p></div>'
        '<div class="cover-rule"></div>'
        f'<div class="cover-body"><dl class="cover-meta">{meta_rows}</dl></div>'
        '<p class="cover-note"><strong>Synthetic demonstration data.</strong> Every example in this guide uses the '
        "fictional North Creek project (NC-001). No real organization's data appears anywhere in this guide or in the "
        "repository. Quickbase is a trademark of Quickbase, Inc.; this project is not affiliated with or endorsed by "
        "Quickbase.</p></section>"
    )


def document(meta: dict[str, str], body: str, entries: list[Entry], paper: str, when: dt.date) -> str:
    size, height = PAPER[paper]
    css = (GUIDE_DIR / "guide.css").read_text(encoding="utf-8")
    running = meta["running"].replace("\\", "").replace('"', "")
    page_css = (
        f"@page {{ size: {size}; @top-left {{ content: \"{running}\"; font: 7.8pt 'Segoe UI', Arial, sans-serif; "
        "color: #5b6475; vertical-align: bottom; padding-bottom: 4mm; } }\n"
        f".cover {{ height: {height}; }}"
    )
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>inspection-reconcile {html.escape(meta['title'])}</title>"
        f"<style>{css}</style><style>{page_css}</style></head><body>"
        f"{cover(meta, when)}{contents(entries)}<main>{body}</main></body></html>\n"
    )


def print_pdf(browser: str, page: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="ir-guide-profile-") as profile:
        target.unlink(missing_ok=True)
        result = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--no-pdf-header-footer",
                "--generate-pdf-document-outline",
                f"--print-to-pdf={target.resolve()}",
                page.resolve().as_uri(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BROWSER_TIMEOUT,
            check=False,
        )
    if not target.is_file():
        raise SystemExit(f"{target.name}: the browser wrote no PDF (exit {result.returncode})")


def outline_pages(pdf: Path) -> list[tuple[str, int]]:
    reader = PdfReader(pdf)
    found: list[tuple[str, int]] = []

    def walk(items: list[Any]) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            page = reader.get_destination_page_number(item)
            if page is not None:
                found.append((re.sub(r"\s+", " ", item.title).strip(), page + 1))

    walk(reader.outline)
    return found


def assign_pages(entries: list[Entry], outline: list[tuple[str, int]], name: str) -> None:
    """Find each section in the outline, in document order. Chrome sometimes repeats a heading's text in its
    outline title (seen with Segoe UI and the h2 rule), so the match is on containment, not equality; the titles
    are rewritten afterwards (finish_pdf)."""
    cursor = 0
    for e in entries:
        for j in range(cursor, len(outline)):
            if e.text in outline[j][0]:
                e.page, e.outline_index, cursor = outline[j][1], j, j + 1
                break
        else:
            sample = "; ".join(title for title, _ in outline[:6])
            raise SystemExit(
                f"{name}: section '{e.number} {e.text}' is not in the PDF outline (it starts: {sample})"
            )


def outline_items(writer: PdfWriter) -> list[Any]:
    """The outline's items in document order (depth first), as the writer's dictionaries."""
    items: list[Any] = []

    def walk(node: Any) -> None:
        item = node.get("/First")
        while item is not None:
            item = item.get_object()
            items.append(item)
            if "/First" in item:
                walk(item)
            item = item.get("/Next")

    if "/Outlines" in writer.root_object:
        walk(writer.root_object["/Outlines"].get_object())
    return items


def finish_pdf(pdf: Path, meta: dict[str, str], entries: list[Entry], when: dt.date) -> None:
    """Clean outline titles ("2.3 Reading the report") and document metadata. The two dates are the document's
    date, not the time of the build: everything else Chrome writes is already reproducible, so the same sources
    and date give the same bytes."""
    writer = PdfWriter(clone_from=pdf)
    items = outline_items(writer)
    for e in entries:
        if e.outline_index is not None and e.outline_index < len(items):
            items[e.outline_index][NameObject("/Title")] = TextStringObject(f"{e.number} {e.text}")
    stamp = f"D:{when.strftime('%Y%m%d')}000000+00'00'"
    writer.add_metadata(
        {
            "/Title": f"inspection-reconcile {meta['title']}",
            "/Author": "Bo Chen",
            "/Subject": meta["subtitle"],
            "/Keywords": "inspection documentation, readiness, Quickbase, reconciliation",
            "/Creator": "tools/build_guides.py (headless Chromium)",
            "/CreationDate": stamp,
            "/ModDate": stamp,
        }
    )
    with open(pdf, "wb") as handle:
        writer.write(handle)


def build_pdf(source: Path, browser: str, paper: str, when: dt.date, keep_html: Path | None) -> Path:
    meta, markdown = read_guide(source)
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    tokens, entries = number_headings(md, md.parse(markdown))
    body = md.renderer.render(tokens, md.options, {})
    body = tables(figures(callouts(rewrite_links(body, source)), source))
    target = GUIDE_DIR / pdf_name(source.name)
    if paper != "letter":
        target = target.with_name(target.stem + f"-{paper}.pdf")
    with tempfile.TemporaryDirectory(prefix="ir-guide-") as tmp:
        page = Path(tmp) / f"{source.stem}.html"
        first = Path(tmp) / "pass1.pdf"
        page.write_text(document(meta, body, entries, paper, when), encoding="utf-8", newline="\n")
        print_pdf(browser, page, first)
        pages_before = len(PdfReader(first).pages)
        assign_pages(entries, outline_pages(first), source.name)
        page.write_text(document(meta, body, entries, paper, when), encoding="utf-8", newline="\n")
        if keep_html is not None:
            keep_html.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(page, keep_html / page.name)
        print_pdf(browser, page, target)
    pages_after = len(PdfReader(target).pages)
    if pages_after != pages_before:
        raise SystemExit(
            f"{target.name}: the page count changed between passes ({pages_before} -> {pages_after})"
        )
    assign_pages(entries, outline_pages(target), source.name)  # the final positions, for the title rewrite
    finish_pdf(target, meta, entries, when)
    leaks = [m.group(0) for m in re.finditer(rb"file\\?(?:072|:)", target.read_bytes())]
    if leaks:  # a local file URL would expose the build machine's paths
        raise SystemExit(f"{target.name}: contains {len(leaks)} local file link(s)")
    print(f"{target.relative_to(REPO).as_posix()}: {pages_after} pages, {len(entries)} sections")
    return target


def rasterize_svgs(markdown: str, browser: str, tmp: Path) -> str:
    """pandoc cannot embed SVG in DOCX without rsvg-convert, so each SVG figure is rendered to PNG (2x) first."""

    def png(match: re.Match[str]) -> str:
        svg = (GUIDE_DIR / match.group(2)).resolve()
        size = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg.read_text(encoding="utf-8"))
        width, height = (size.group(1), size.group(2)) if size else ("1000", "700")
        target = tmp / "rasterized" / (svg.stem + ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ir-guide-profile-") as profile:
            subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--no-first-run",
                    f"--user-data-dir={profile}",
                    f"--window-size={width},{height}",
                    "--force-device-scale-factor=2",
                    f"--screenshot={target}",
                    svg.as_uri(),
                ],
                capture_output=True,
                timeout=BROWSER_TIMEOUT,
                check=False,
            )
        if not target.is_file():
            raise SystemExit(f"{svg.name}: the browser wrote no PNG")
        # A relative name, found through pandoc's resource path: pandoc stores the path in the DOCX.
        return f"![{match.group(1)}](rasterized/{target.name})"

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+\.svg)\)", png, markdown)


def build_docx(source: Path, when: dt.date, browser: str) -> Path | None:
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        print("pandoc not found: no DOCX written")
        return None
    meta, markdown = read_guide(source)
    markdown = re.sub(r"\A\s*# .*\n", "", markdown)  # the title comes from the metadata
    markdown = re.sub(
        r"^> \[!(" + "|".join(ALERTS) + r")\]\s*\n> ?",
        lambda m: f"> **{ALERTS[m.group(1)]}.** ",
        markdown,
        flags=re.M,
    )
    # Links: other repository files become GitHub links, the other guide becomes its DOCX.
    markdown = re.sub(
        r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: f"[{m.group(1)}]({retarget(m.group(2), source, '.docx')})",
        markdown,
    )
    target = GUIDE_DIR / pdf_name(source.name).replace(".pdf", ".docx")
    with tempfile.TemporaryDirectory(prefix="ir-guide-docx-") as tmp:
        markdown = rasterize_svgs(markdown, browser, Path(tmp))
        subprocess.run(
            [
                pandoc,
                "--from=gfm",
                "--to=docx",
                f"--output={target}",
                "--toc",
                "--toc-depth=2",
                "--number-sections",
                "--shift-heading-level-by=-1",
                f"--resource-path={GUIDE_DIR}{os.pathsep}{tmp}",
                f"--metadata=title:inspection-reconcile {meta['title']}",
                f"--metadata=subtitle:{meta['subtitle']}",
                f"--metadata=date:{when.isoformat()}",
                "--metadata=author:Bo Chen",
            ],
            input=markdown,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=180,
            # pandoc dates the package (core.xml and every zip entry) from SOURCE_DATE_EPOCH when it is set.
            env={
                **os.environ,
                "SOURCE_DATE_EPOCH": str(
                    int(dt.datetime(when.year, when.month, when.day, tzinfo=dt.UTC).timestamp())
                ),
            },
        )
    with zipfile.ZipFile(target) as package:
        xml = "".join(
            package.read(n).decode("utf-8", "replace")
            for n in package.namelist()
            if n.endswith((".xml", ".rels"))
        )
    for local in (tempfile.gettempdir(), str(REPO)):
        for form in {local, Path(local).as_posix()}:
            if form in xml:  # a local path would expose the build machine
                raise SystemExit(f"{target.name}: contains the local path {form!r}")
    print(f"{target.relative_to(REPO).as_posix()}: written by pandoc")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", choices=sorted(GUIDES))
    parser.add_argument("--paper", choices=sorted(PAPER), default="letter")
    parser.add_argument("--docx", action="store_true")
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        help="the document date; default: the date of the last commit that changed docs/guide",
    )
    parser.add_argument("--browser")
    parser.add_argument("--keep-html", type=Path)
    args = parser.parse_args(argv)
    browser = find_browser(args.browser)
    when = args.date or source_date()
    for key, name in GUIDES.items():
        if args.only and key != args.only:
            continue
        source = GUIDE_DIR / name
        build_pdf(source, browser, args.paper, when, args.keep_html)
        if args.docx:
            build_docx(source, when, browser)
    return 0


def source_date() -> dt.date:
    """The date of the last commit that changed the guides' sources, so that a rebuild from the same commit gives
    the same bytes; today's date outside a Git checkout."""
    result = subprocess.run(
        [
            "git",
            "log",
            "-1",
            "--format=%cs",
            "--",
            "docs/guide/user-guide.md",
            "docs/guide/admin-guide.md",
            "docs/guide/guide.css",
            "docs/guide/img",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    text = result.stdout.strip()
    return dt.date.fromisoformat(text) if result.returncode == 0 and text else dt.datetime.now(dt.UTC).date()


if __name__ == "__main__":
    sys.exit(main())
