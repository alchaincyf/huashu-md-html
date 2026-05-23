#!/usr/bin/env python3
"""
md_to_html.py — Convert Markdown to a polished, self-contained HTML using Pandoc + huashu-md-html themes.

Four themes available:
  - article       : Tufte-inspired editorial (essays, blogs, deep reading)
  - report        : Wide-body publishing-grade (technical reports, whitepapers)
  - reading       : Medium-style minimal (read-only, social repost)
  - interactive   : Long-form with collapsible TOC + sidebar (books, deep guides)

Part of huashu-md-html skill — md is source, html is product.
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SKILL_ROOT / "templates"
VALID_THEMES = ("article", "report", "reading", "interactive", "wechat")


HELP_PANDOC = """\
pandoc is not installed. Install it with:

    brew install pandoc       # macOS
    apt install pandoc        # Debian/Ubuntu
    choco install pandoc      # Windows

Or download from https://pandoc.org/installing.html
"""


def ensure_pandoc() -> str:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        sys.stderr.write(HELP_PANDOC)
        sys.exit(2)
    return pandoc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert Markdown to polished HTML using Pandoc + huashu-md-html themes.",
    )
    p.add_argument("input", help="Input .md file path.")
    p.add_argument(
        "-o", "--output",
        help="Output .html path. Defaults to <input-stem>.html.",
    )
    p.add_argument(
        "--theme",
        choices=VALID_THEMES,
        default="article",
        help="Theme to use (default: article).",
    )
    p.add_argument(
        "--inline-images",
        action="store_true",
        help="Embed referenced local images as base64 (single self-contained file).",
    )
    p.add_argument(
        "--copy-images",
        action="store_true",
        help="Copy referenced local images next to the output html (relative paths).",
    )
    p.add_argument(
        "--toc",
        action="store_true",
        help="Render a table of contents (auto-on for theme=interactive).",
    )
    p.add_argument(
        "--no-toc",
        action="store_true",
        help="Disable TOC even if the theme would default to it.",
    )
    p.add_argument(
        "--standalone",
        action="store_true",
        default=True,
        help="Produce a self-contained single HTML file (default).",
    )
    p.add_argument(
        "--embed-css",
        action="store_true",
        default=True,
        help="Inline theme CSS into the HTML (default). Portable single file.",
    )
    p.add_argument(
        "--no-embed-css",
        dest="embed_css",
        action="store_false",
        help="Link theme CSS by absolute path instead of inlining.",
    )
    p.add_argument(
        "--katex",
        action="store_true",
        help="Render math via KaTeX (loaded from CDN; requires network for first view).",
    )
    p.add_argument(
        "--highlight-style",
        default="pygments",
        help="Pandoc highlight style (default: pygments).",
    )
    p.add_argument(
        "--title",
        help="Override the document title (otherwise inferred from first H1 or filename).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error stderr output.",
    )
    return p.parse_args()


def load_theme(theme: str) -> tuple[Path, Path | None]:
    theme_dir = TEMPLATE_DIR / theme
    css_path = theme_dir / "theme.css"
    if not css_path.exists():
        sys.stderr.write(f"[error] theme '{theme}' is missing CSS at: {css_path}\n")
        sys.exit(2)
    template_path = theme_dir / "template.html5"
    return css_path, template_path if template_path.exists() else None


def infer_title_and_strip(md_path: Path, override: str | None) -> tuple[str, str]:
    """Return (title, md_text_with_leading_h1_stripped).

    Pandoc's --standalone renders a title block from metadata, so we strip
    the leading H1 from the body to avoid duplicate titles.
    """
    raw = md_path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    # Find first non-blank line
    first_non_blank = next(
        (i for i, line in enumerate(lines) if line.strip()),
        None,
    )

    title = override
    body_lines = lines

    if first_non_blank is not None:
        first_line = lines[first_non_blank].strip()
        if first_line.startswith("# ") and not first_line.startswith("## "):
            extracted = first_line[2:].strip()
            if not title:
                title = extracted
            # Strip the H1 line and any blank lines that immediately follow it
            cut = first_non_blank + 1
            while cut < len(lines) and not lines[cut].strip():
                cut += 1
            body_lines = lines[:first_non_blank] + lines[cut:]

    if not title:
        title = md_path.stem

    return title, "\n".join(body_lines)


def should_emit_toc(args: argparse.Namespace) -> bool:
    if args.no_toc:
        return False
    if args.toc:
        return True
    return args.theme == "interactive"


def collect_local_images(md_text: str, base_dir: Path) -> list[Path]:
    """Find referenced local images in markdown that exist on disk."""
    pattern = re.compile(r'!\[[^\]]*\]\(([^)\s"]+)')
    found: list[Path] = []
    for m in pattern.finditer(md_text):
        ref = m.group(1)
        if ref.startswith(("http://", "https://", "data:")):
            continue
        candidate = (base_dir / ref).resolve()
        if candidate.exists() and candidate.is_file():
            found.append(candidate)
    return found


def inline_images_in_html(html: str, base_dir: Path) -> str:
    """Replace local img src references with base64 data URIs."""
    pattern = re.compile(r'(<img[^>]+src=)"([^"]+)"')

    def replace(match: re.Match[str]) -> str:
        prefix, src = match.group(1), match.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        candidate = (base_dir / src).resolve()
        if not candidate.exists():
            return match.group(0)
        mime, _ = mimetypes.guess_type(candidate)
        if not mime:
            mime = "application/octet-stream"
        b64 = base64.b64encode(candidate.read_bytes()).decode("ascii")
        return f'{prefix}"data:{mime};base64,{b64}"'

    return pattern.sub(replace, html)


def copy_images_alongside(images: list[Path], src_base: Path, out_dir: Path, quiet: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for img in images:
        try:
            relative = img.relative_to(src_base)
        except ValueError:
            relative = Path(img.name)
        dest = out_dir / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if img.resolve() != dest.resolve():
            shutil.copy2(img, dest)
            if not quiet:
                sys.stderr.write(f"[copied] {img} → {dest}\n")


def build_pandoc_command(
    pandoc_bin: str,
    md_input: Path | str,
    output_html: Path,
    css_path: Path,
    template_path: Path | None,
    title: str,
    args: argparse.Namespace,
) -> list[str]:
    cmd = [
        pandoc_bin,
        str(md_input),
        "--from", "markdown+smart+pipe_tables+task_lists+fenced_divs+bracketed_spans",
        "--to", "html5",
        "--metadata", f"title={title}",
        "--metadata", f"theme={args.theme}",
        "--highlight-style", args.highlight_style,
        "-o", str(output_html),
    ]
    if args.standalone:
        cmd.append("--standalone")
    if args.embed_css:
        css_text = css_path.read_text(encoding="utf-8")
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        )
        tmp.write(f"<style>\n{css_text}\n</style>\n")
        tmp.close()
        cmd.extend(["--include-in-header", tmp.name])
    else:
        cmd.extend(["--css", str(css_path)])
    if template_path:
        cmd.extend(["--template", str(template_path)])
    if should_emit_toc(args):
        cmd.extend(["--toc", "--toc-depth=3"])
    if args.katex:
        cmd.append("--katex")
    return cmd


def main() -> int:
    pandoc_bin = ensure_pandoc()
    args = parse_args()

    input_md = Path(args.input).resolve()
    if not input_md.exists():
        sys.stderr.write(f"[error] input file not found: {input_md}\n")
        return 1

    output_html = Path(args.output).resolve() if args.output else input_md.with_suffix(".html")
    output_html.parent.mkdir(parents=True, exist_ok=True)

    css_path, template_path = load_theme(args.theme)
    title, md_text = infer_title_and_strip(input_md, args.title)
    src_base = input_md.parent

    if args.copy_images:
        images = collect_local_images(md_text, src_base)
        if images:
            copy_images_alongside(images, src_base, output_html.parent, args.quiet)

    # Pipe the stripped markdown to pandoc via stdin to avoid temp files
    cmd = build_pandoc_command(
        pandoc_bin, "-", output_html, css_path, template_path, title, args,
    )

    try:
        proc = subprocess.run(cmd, input=md_text, capture_output=True, text=True)
    except FileNotFoundError:
        sys.stderr.write(HELP_PANDOC)
        return 2

    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or "[error] pandoc failed without stderr output.\n")
        return proc.returncode

    if args.inline_images:
        html_text = output_html.read_text(encoding="utf-8")
        html_text = inline_images_in_html(html_text, src_base)
        output_html.write_text(html_text, encoding="utf-8")

    if not args.quiet:
        sys.stderr.write(f"[ok] {input_md.name} → {output_html} (theme: {args.theme})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
