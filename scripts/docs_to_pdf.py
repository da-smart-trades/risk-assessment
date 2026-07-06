#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "markdown>=3.6",
#     "weasyprint>=62",
# ]
# ///
"""Render the reference Markdown docs to PDF.

Converts the Markdown reference docs under ``docs/reference/`` (metrics + tokens)
to print-ready PDFs using a Markdown -> styled-HTML -> WeasyPrint pipeline. No
system packages beyond the Pango/Cairo libraries WeasyPrint already links
against are required; the Python deps are declared inline (PEP 723) so ``uv``
installs them in an ephemeral environment.

Usage::

    uv run scripts/docs_to_pdf.py                 # all of docs/reference/**.md
    uv run scripts/docs_to_pdf.py docs/reference/metrics
    uv run scripts/docs_to_pdf.py docs/reference/metrics/canton.md
    uv run scripts/docs_to_pdf.py --out-dir build/pdf docs/reference/metrics

By default each ``foo.md`` is written next to its source as ``foo.pdf``; pass
``--out-dir DIR`` to collect the PDFs under a single directory instead (the
source tree's relative layout is preserved).
"""

from __future__ import annotations

# ruff: noqa: T201 — this is a CLI tool; progress/status output is intentional.
import argparse
import sys
from pathlib import Path

import markdown
from weasyprint import HTML

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "docs" / "reference"

# GFM-ish feature set: tables, fenced code, definition lists, and tight lists
# matching how the docs are authored.
_MD_EXTENSIONS = (
    "tables",
    "fenced_code",
    "sane_lists",
    "def_list",
    "attr_list",
    "toc",
)

# Print stylesheet. The font stack leads with DejaVu (broad Unicode coverage for
# the ⅔ / → / ≈ / — glyphs used in the docs) and falls back to whatever serif /
# sans the system provides.
_CSS = """
@page {
    size: Letter;
    margin: 2cm 1.8cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: "DejaVu Sans", sans-serif;
        font-size: 8pt;
        color: #888;
    }
}
html { font-size: 10.5pt; }
body {
    font-family: "DejaVu Serif", Georgia, "Times New Roman", serif;
    line-height: 1.45;
    color: #1a1a1a;
}
h1, h2, h3, h4 {
    font-family: "DejaVu Sans", Helvetica, Arial, sans-serif;
    color: #0b2545;
    line-height: 1.2;
}
h1 { font-size: 20pt; border-bottom: 2px solid #0b2545; padding-bottom: 6px; }
h2 { font-size: 14pt; margin-top: 1.4em; border-bottom: 1px solid #d8dee9; padding-bottom: 3px; }
h3 { font-size: 11.5pt; margin-top: 1.1em; }
h2, h3, h4 { page-break-after: avoid; }
p, ul, ol { orphans: 3; widows: 3; }
a { color: #1d4e89; text-decoration: none; }
hr { border: none; border-top: 1px solid #d8dee9; margin: 1.4em 0; }
blockquote {
    margin: 1em 0;
    padding: 0.4em 1em;
    background: #f3f6fb;
    border-left: 4px solid #3fa7d6;
    color: #33415c;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.8em 0;
    font-size: 9pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #cdd5e0;
    padding: 4px 7px;
    text-align: left;
    vertical-align: top;
}
th { background: #0b2545; color: #fff; font-family: "DejaVu Sans", sans-serif; }
tr:nth-child(even) td { background: #f5f7fa; }
code {
    font-family: "DejaVu Sans Mono", "Courier New", monospace;
    font-size: 8.8pt;
    background: #eef1f5;
    padding: 0.5px 3px;
    border-radius: 3px;
}
pre {
    background: #f5f7fa;
    border: 1px solid #d8dee9;
    border-radius: 4px;
    padding: 8px 10px;
    overflow-x: auto;
    page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 8.5pt; }
em { color: #33415c; }
"""

_HTML_TEMPLATE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>{title}</title><style>{css}</style></head>"
    "<body>{body}</body></html>"
)


def _iter_markdown(paths: list[Path]) -> list[Path]:
    """Expand the given files/dirs into a sorted, de-duplicated list of .md files."""
    found: set[Path] = set()
    for raw in paths:
        path = raw if raw.is_absolute() else (Path.cwd() / raw)
        if path.is_dir():
            found.update(p for p in path.rglob("*.md"))
        elif path.suffix.lower() == ".md" and path.is_file():
            found.add(path)
        else:
            print(f"  ! skipping {raw} (not a .md file or directory)", file=sys.stderr)
    return sorted(found)


def _title_of(md_text: str, fallback: str) -> str:
    """Use the first level-1 heading as the document title, else the filename."""
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _output_path(md_path: Path, out_dir: Path | None) -> Path:
    """Resolve the PDF path: next to the source, or mirrored under ``out_dir``."""
    if out_dir is None:
        return md_path.with_suffix(".pdf")
    try:
        rel = md_path.resolve().relative_to(DEFAULT_SOURCE.resolve())
    except ValueError:
        rel = Path(md_path.name)
    return (out_dir / rel).with_suffix(".pdf")


def _convert(md_path: Path, out_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        md_text, extensions=list(_MD_EXTENSIONS), output_format="html"
    )
    html = _HTML_TEMPLATE.format(
        title=_title_of(md_text, md_path.stem), css=_CSS, body=body
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # base_url lets relative image/asset links resolve against the source dir.
    HTML(string=html, base_url=str(md_path.parent)).write_pdf(str(out_path))


def main() -> int:
    """Parse args, convert the selected Markdown docs, and report results."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Markdown files or directories to convert (default: docs/reference/).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Write PDFs under this directory (mirroring the docs/reference layout) "
        "instead of next to each source file.",
    )
    args = parser.parse_args()

    sources = args.paths or [DEFAULT_SOURCE]
    md_files = _iter_markdown(sources)
    if not md_files:
        print("No Markdown files found to convert.", file=sys.stderr)
        return 1

    print(f"Converting {len(md_files)} document(s) to PDF…")
    failures = 0
    for md_path in md_files:
        out_path = _output_path(md_path, args.out_dir)
        try:
            _convert(md_path, out_path)
        except Exception as exc:  # noqa: BLE001 - report and continue with the rest
            failures += 1
            print(f"  ✗ {md_path}: {exc}", file=sys.stderr)
            continue
        rel_out = (
            out_path.relative_to(REPO_ROOT)
            if out_path.is_relative_to(REPO_ROOT)
            else out_path
        )
        print(f"  ✓ {md_path.name} → {rel_out}")

    if failures:
        print(f"Done with {failures} failure(s).", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
