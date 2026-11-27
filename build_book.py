#!/usr/bin/env python3
"""Package the book into EPUB and PDF.

    python3 book/build_book.py REVISION [--date YYYY-MM-DD] [--out DIR]
                               [--author NAME] [--no-pdf] [--no-epub]

Reads book/Summary.md (for the part titles) and every
book/Chapter X.Y - Title/contents.md, in chapter order, and writes

    <out>/a-test-driven-course-in-fpga-graphics.epub
    <out>/a-test-driven-course-in-fpga-graphics.pdf

(default <out> is book/output). The title page carries REVISION and the
generation date. Both formats have a clickable table of contents, and every
"Chapter X.Y" mentioned in the text links to that chapter.

Requirements: Python 3.9+, `markdown-it-py`, `Pygments` and (PDF metadata) `pypdf`
(pip install markdown-it-py Pygments pypdf), and for the PDF a Chromium or Google
Chrome binary (override with the CHROME environment variable).
"""
import argparse
import base64
import datetime as dt
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.util import ClassNotFound

TITLE = "A Test-Driven Course in FPGA Graphics"
SUBTITLE = "From a blinking LED to a 3D GPU, one testbench at a time"
BASENAME = "a-test-driven-course-in-fpga-graphics"
AUTHOR = "Matthew Shapiro"                      # override with --author
AUTHOR_SORT = "Shapiro, Matthew"                # EPUB file-as form; derived from --author
DESCRIPTION = ("A hands-on, test-driven course that takes you from blinking an LED to a working 3D GPU "
               "on an FPGA, one Verilog testbench at a time: simulation workflow, VGA and HDMI video, "
               "2D games, a framebuffer and drawing engine, tile and sprite layers, and a fixed-point 3D pipeline.")
KEYWORDS = ["FPGA", "Verilog", "GPU", "computer graphics", "digital design", "HDL", "VGA", "HDMI",
            "test-driven development", "open-source toolchain"]
LANGUAGE = "en"
BOOK_DIR = Path(__file__).resolve().parent

CHAPTER_DIR_RE = re.compile(r"^Chapter (\d+)\.(\d+) - (.+)$")
CHAPTER_REF_RE = re.compile(r"\bChapters? (\d+\.\d+)(?!\d)")


# ----------------------------------------------------------------- content --

class Chapter:
    def __init__(self, path):
        m = CHAPTER_DIR_RE.match(path.name)
        self.path = path
        self.phase = int(m.group(1))
        self.number = f"{m.group(1)}.{m.group(2)}"
        self.title = m.group(3)
        self.key = (int(m.group(1)), int(m.group(2)))
        self.anchor = "ch-" + self.number.replace(".", "-")
        self.file = f"{self.anchor}.xhtml"          # EPUB file name
        self.body_html = ""
        self.images = {}                              # name -> source file


def load_chapters():
    chapters = []
    for d in BOOK_DIR.iterdir():
        if d.is_dir() and CHAPTER_DIR_RE.match(d.name) and (d / "contents.md").exists():
            chapters.append(Chapter(d))
    chapters.sort(key=lambda c: c.key)
    if not chapters:
        sys.exit("no chapters found")
    return chapters


def load_parts():
    """{phase number: title} from the '## Phase N: Title (about X days)' lines."""
    parts = {}
    for line in (BOOK_DIR / "Summary.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## Phase (\d+): (.+?)(?: \(about [^)]*\))?\s*$", line)
        if m:
            parts[int(m.group(1))] = m.group(2)
    return parts


def make_markdown():
    md = MarkdownIt("commonmark", {"html": False, "xhtmlOut": True}).enable(["table", "strikethrough"])

    def fence(self, tokens, idx, options, env):
        tok = tokens[idx]
        lang = (tok.info or "").strip().split()[0] if tok.info else ""
        try:
            lexer = get_lexer_by_name(lang) if lang else TextLexer()
        except ClassNotFound:
            lexer = TextLexer()
        code = highlight(tok.content, lexer, HtmlFormatter(nowrap=True))
        return f'<pre class="code"><code>{code}</code></pre>\n'

    def image(self, tokens, idx, options, env):
        tok = tokens[idx]
        src = tok.attrGet("src")
        name = f"{env['anchor']}-{Path(src).name}"
        env["images"][name] = env["dir"] / src
        alt = html.escape(tok.content, quote=True)
        return f'<img class="figure" src="img/{name}" alt="{alt}"/>'

    md.add_render_rule("fence", fence)
    md.add_render_rule("image", image)
    return md


def link_chapter_refs(tokens, numbers, own_number, link_for):
    """Turn 'Chapter 4.6' in running text into links (not in code or headings)."""
    from markdown_it.token import Token
    in_heading = False
    for tok in tokens:
        if tok.type == "heading_open":
            in_heading = True
        elif tok.type == "heading_close":
            in_heading = False
        if tok.type != "inline" or in_heading or not tok.children:
            continue
        out, in_link = [], False
        for ch in tok.children:
            if ch.type == "link_open":
                in_link = True
            elif ch.type == "link_close":
                in_link = False
            if ch.type != "text" or in_link:
                out.append(ch)
                continue
            pos = 0
            for m in CHAPTER_REF_RE.finditer(ch.content):
                num = m.group(1)
                if num not in numbers or num == own_number:
                    continue
                if m.start() > pos:
                    t = Token("text", "", 0); t.content = ch.content[pos:m.start()]; out.append(t)
                o = Token("link_open", "a", 1); o.attrs = {"href": link_for(num)}
                t = Token("text", "", 0); t.content = m.group(0)
                c = Token("link_close", "a", -1)
                out += [o, t, c]
                pos = m.end()
            if pos == 0:
                out.append(ch)
            elif pos < len(ch.content):
                t = Token("text", "", 0); t.content = ch.content[pos:]; out.append(t)
        tok.children = out


def strip_local_links(tokens):
    """Links to sibling files (protocol.md, ...) go nowhere in a book: keep the text."""
    for tok in tokens:
        stack = []
        for ch in tok.children or []:
            if ch.type == "link_open":
                href = ch.attrGet("href") or ""
                local = not re.match(r"^(https?:|mailto:|#)", href)
                stack.append(local)
                if local:
                    ch.type, ch.tag, ch.attrs = "span_open", "span", {"class": "file"}
            elif ch.type == "link_close":
                if stack.pop():
                    ch.type, ch.tag = "span_close", "span"


def render_chapters(chapters, link_for):
    md = make_markdown()
    numbers = {c.number for c in chapters}
    for c in chapters:
        text = (c.path / "contents.md").read_text(encoding="utf-8")
        tokens = md.parse(text)
        # Replace the first "# Chapter X.Y - Title" heading with our own.
        if tokens and tokens[0].type == "heading_open" and tokens[0].tag == "h1":
            del tokens[0:3]
        strip_local_links(tokens)
        link_chapter_refs(tokens, numbers, c.number, link_for)
        c.images = {}
        env = {"anchor": c.anchor, "dir": c.path, "images": c.images}
        body = md.renderer.render(tokens, md.options, env)
        c.body_html = (
            f'<h1 id="{c.anchor}"><span class="chnum">Chapter {c.number}</span>'
            f'<span class="chtitle">{html.escape(c.title)}</span></h1>\n{body}'
        )


# -------------------------------------------------------------------- html --

CSS = """
:root { color-scheme: light; }
html { font-size: 11pt; }
body { font-family: "Georgia", "Times New Roman", serif; line-height: 1.45; color: #1a1a1a; }
h1, h2, h3, h4 { font-family: "Helvetica Neue", Arial, sans-serif; line-height: 1.2; }
h1 { font-size: 1.9em; margin: 0 0 1em; }
h1 .chnum { display: block; font-size: 0.5em; font-weight: 600; letter-spacing: 0.08em;
            text-transform: uppercase; color: #5a6b7b; margin-bottom: 0.3em; }
h2 { font-size: 1.35em; margin: 1.6em 0 0.5em; border-bottom: 1px solid #ccd3da; padding-bottom: 0.2em; }
h3 { font-size: 1.1em; margin: 1.3em 0 0.4em; }
p { margin: 0.6em 0; text-align: left; }
a { color: #1d5fa3; text-decoration: none; }
code { font-family: "DejaVu Sans Mono", "Courier New", monospace; font-size: 0.85em;
       background: #f1f3f5; padding: 0.05em 0.25em; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #dde2e7; border-radius: 4px; padding: 0.6em 0.8em;
      font-size: 0.8em; line-height: 1.35; white-space: pre-wrap; word-wrap: break-word; }
pre code { background: none; padding: 0; font-size: 1em; }
table { border-collapse: collapse; margin: 0.9em 0; font-size: 0.9em; }
th, td { border: 1px solid #c8d0d8; padding: 0.25em 0.6em; text-align: left; vertical-align: top; }
th { background: #eef1f4; }
blockquote { margin: 0.8em 0; padding: 0.1em 1em; border-left: 3px solid #b8c4d0; color: #444; }
ul, ol { margin: 0.5em 0; padding-left: 1.6em; }
li { margin: 0.2em 0; }
img.figure { display: block; max-width: 100%; height: auto; margin: 1em auto; }
.file { font-family: "DejaVu Sans Mono", monospace; font-size: 0.9em; }
/* title page */
.titlepage { text-align: center; padding-top: 22%; }
.titlepage h1 { font-size: 2.6em; margin: 0 0 0.3em; }
.titlepage .subtitle { font-size: 1.2em; color: #455566; margin-bottom: 4em; font-style: italic; }
.titlepage .meta { text-align: center; font-family: "Helvetica Neue", Arial, sans-serif; color: #333; line-height: 1.8; }
.titlepage .meta b { letter-spacing: 0.05em; }
/* table of contents */
.toc h1 { margin-bottom: 0.6em; }
.toc ol { list-style: none; padding-left: 0; }
.toc li.part { margin-top: 1.1em; font-family: "Helvetica Neue", Arial, sans-serif; font-weight: 700;
               color: #33414e; }
.toc li.part ol { margin-top: 0.3em; font-weight: 400; }
.toc li.ch { margin: 0.18em 0 0.18em 1em; font-family: Georgia, serif; }
.toc li.ch .n { display: inline-block; min-width: 2.6em; color: #5a6b7b; }
.partpage h1 { margin-top: 30%; text-align: center; }
.partpage h1 .chnum { color: #5a6b7b; }
"""

PYG_CSS = HtmlFormatter(style="default").get_style_defs("pre.code")

PDF_CSS = """
@page { size: A4; margin: 22mm 20mm 24mm 20mm;
        @bottom-center { content: counter(page); font: 9pt "Helvetica Neue", Arial, sans-serif; color: #667; } }
@page :first { @bottom-center { content: none; } }
.chapter, .partpage, .toc, .titlepage { break-before: page; }
.titlepage { break-before: auto; }
h1, h2, h3 { break-after: avoid; }
pre, table, blockquote, img.figure { break-inside: avoid-page; }
pre { break-inside: auto; }
"""


def meta_block(revision, date):
    return (
        f'<p class="meta"><b>Revision</b> {html.escape(revision)}<br/>'
        f'<b>Generated</b> {date}</p>'
    )


def toc_items(chapters, parts, href):
    """Nested list: part -> chapters. href(chapter_or_part) gives the link."""
    out = ["<ol>"]
    by_phase = {}
    for c in chapters:
        by_phase.setdefault(c.phase, []).append(c)
    for phase in sorted(by_phase):
        ptitle = parts.get(phase, "")
        label = f"Part {phase}" + (f": {html.escape(ptitle)}" if ptitle else "")
        out.append(f'<li class="part"><a href="{href("part", phase)}">{label}</a><ol>')
        for c in by_phase[phase]:
            out.append(
                f'<li class="ch"><a href="{href("ch", c)}"><span class="n">{c.number}</span>'
                f'{html.escape(c.title)}</a></li>'
            )
        out.append("</ol></li>")
    out.append("</ol>")
    return "\n".join(out)


def part_page_html(phase, parts, anchor=True):
    ptitle = html.escape(parts.get(phase, ""))
    ident = f' id="part-{phase}"' if anchor else ""
    return (f'<h1{ident}><span class="chnum">Part {phase}</span>'
            f'<span class="chtitle">{ptitle}</span></h1>')


# --------------------------------------------------------------------- pdf --

def build_pdf_html(chapters, parts, revision, date):
    def href(kind, x):
        return f"#part-{x}" if kind == "part" else f"#{x.anchor}"

    out = ['<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"/>',
           f"<title>{html.escape(TITLE)}</title>",
           f'<meta name="author" content="{html.escape(AUTHOR, quote=True)}"/>',
           f'<meta name="description" content="{html.escape(DESCRIPTION, quote=True)}"/>',
           f'<meta name="keywords" content="{html.escape(", ".join(KEYWORDS), quote=True)}"/>',
           f'<meta name="generator" content="build_book.py"/>',
           f"<style>{CSS}\n{PYG_CSS}\n{PDF_CSS}</style></head><body>"]
    out.append(
        f'<section class="titlepage"><h1>{html.escape(TITLE)}</h1>'
        f'<div class="subtitle">{html.escape(SUBTITLE)}</div>{meta_block(revision, date)}</section>'
    )
    out.append(f'<section class="toc"><h1>Contents</h1>{toc_items(chapters, parts, href)}</section>')
    last_phase = None
    for c in chapters:
        if c.phase != last_phase:
            out.append(f'<section class="partpage">{part_page_html(c.phase, parts)}</section>')
            last_phase = c.phase
        body = c.body_html
        for name, path in c.images.items():
            data = base64.b64encode(path.read_bytes()).decode()
            body = body.replace(f'src="img/{name}"', f'src="data:image/svg+xml;base64,{data}"')
        out.append(f'<section class="chapter">{body}</section>')
    out.append("</body></html>")
    return "\n".join(out)


def find_chrome():
    cands = [os.environ.get("CHROME"), "chromium", "chromium-browser", "google-chrome",
             "google-chrome-stable", "chrome"]
    for c in cands:
        if c and shutil.which(c):
            return shutil.which(c)
    sys.exit("PDF needs Chromium or Chrome on PATH (or set CHROME=/path/to/binary)")


def stamp_pdf_metadata(pdf_path, revision, date):
    """Chromium only writes the title; add the rest of the document-info fields."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("warning: pypdf not installed; PDF has title only (pip install pypdf)", file=sys.stderr)
        return
    w = PdfWriter(clone_from=PdfReader(str(pdf_path)))
    stamp = "D:" + date.replace("-", "") + "000000Z"
    w.add_metadata({
        "/Title": f"{TITLE}: {SUBTITLE}",
        "/Author": AUTHOR,
        "/Subject": f"{DESCRIPTION} Revision {revision}.",
        "/Keywords": ", ".join(KEYWORDS),
        "/Creator": "build_book.py",
        "/Producer": "build_book.py (Chromium print-to-PDF, pypdf)",
        "/CreationDate": stamp,
        "/ModDate": stamp,
    })
    w.create_viewer_preferences().display_doc_title = True
    w.page_mode = "/UseOutlines"
    with open(pdf_path, "wb") as f:
        w.write(f)


def write_pdf(html_text, dest, revision="", date=None):
    chrome = find_chrome()
    # A snap-packaged browser can only read/write non-hidden paths under $HOME, so work there.
    with tempfile.TemporaryDirectory(prefix="book-build-", dir=Path.home()) as tmp:
        page = Path(tmp) / "book.html"
        pdf = Path(tmp) / "book.pdf"
        page.write_text(html_text, encoding="utf-8")
        cmd = [chrome, "--headless", "--no-sandbox", "--disable-gpu",
               "--no-pdf-header-footer", "--generate-pdf-document-outline",
               f"--user-data-dir={Path(tmp) / 'profile'}",
               f"--print-to-pdf={pdf}", page.as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if not pdf.exists():
            sys.exit(f"Chromium produced no PDF:\n{r.stderr[-2000:]}")
        stamp_pdf_metadata(pdf, revision, date or dt.date.today().isoformat())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, dest)


# -------------------------------------------------------------------- epub --

XHTML_HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
              '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
              'lang="en" xml:lang="en"><head><meta charset="utf-8"/><title>{title}</title>'
              '<link rel="stylesheet" type="text/css" href="style.css"/></head><body>')


def xhtml(title, body):
    return XHTML_HEAD.format(title=html.escape(title)) + body + "</body></html>\n"


def write_epub(chapters, parts, revision, date, dest):
    book_id = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BASENAME}/{revision}"))
    modified = f"{date}T00:00:00Z"

    def href(kind, x):
        return f"part-{x}.xhtml" if kind == "part" else x.file

    files = {}   # name -> text, all under OEBPS/
    files["style.css"] = CSS + "\n" + PYG_CSS + "\n.titlepage { padding-top: 20%; }\n"
    files["title.xhtml"] = xhtml(TITLE,
        f'<section class="titlepage" epub:type="titlepage"><h1>{html.escape(TITLE)}</h1>'
        f'<div class="subtitle">{html.escape(SUBTITLE)}</div>{meta_block(revision, date)}</section>')
    files["toc.xhtml"] = xhtml("Contents",
        f'<nav epub:type="toc" id="toc" class="toc"><h1>Contents</h1>{toc_items(chapters, parts, href)}</nav>')

    manifest = [("css", "style.css", "text/css", ""),
                ("title", "title.xhtml", "application/xhtml+xml", ""),
                ("toc", "toc.xhtml", "application/xhtml+xml", ' properties="nav"')]
    spine = ["title", "toc"]
    last_phase = None
    for c in chapters:
        if c.phase != last_phase:
            name = f"part-{c.phase}.xhtml"
            files[name] = xhtml(f"Part {c.phase}",
                                f'<section class="partpage">{part_page_html(c.phase, parts)}</section>')
            manifest.append((f"part{c.phase}", name, "application/xhtml+xml", ""))
            spine.append(f"part{c.phase}")
            last_phase = c.phase
        files[c.file] = xhtml(f"Chapter {c.number} {c.title}", f'<section class="chapter">{c.body_html}</section>')
        manifest.append((c.anchor, c.file, "application/xhtml+xml", ""))
        spine.append(c.anchor)
        for name, path in c.images.items():
            files[f"img/{name}"] = path.read_bytes()
            manifest.append((name.replace(".", "-"), f"img/{name}", "image/svg+xml", ""))

    # NCX for older readers (nested by part).
    play, n = [], 0
    by_phase = {}
    for c in chapters:
        by_phase.setdefault(c.phase, []).append(c)
    for phase in sorted(by_phase):
        n += 1
        play.append(f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>Part {phase}: '
                    f'{html.escape(parts.get(phase, ""))}</text></navLabel><content src="part-{phase}.xhtml"/>')
        for c in by_phase[phase]:
            n += 1
            play.append(f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{c.number} '
                        f'{html.escape(c.title)}</text></navLabel><content src="{c.file}"/></navPoint>')
        play.append("</navPoint>")
    files["toc.ncx"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head>'
        f'<docTitle><text>{html.escape(TITLE)}</text></docTitle><navMap>{"".join(play)}</navMap></ncx>\n')
    manifest.append(("ncx", "toc.ncx", "application/x-dtbncx+xml", ""))

    opf = ['<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" '
           'prefix="schema: http://schema.org/" xml:lang="en">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="bookid">{book_id}</dc:identifier>'
           f'<dc:title id="t-main">{html.escape(TITLE)}</dc:title>'
           '<meta refines="#t-main" property="title-type">main</meta>'
           f'<dc:title id="t-sub">{html.escape(SUBTITLE)}</dc:title>'
           '<meta refines="#t-sub" property="title-type">subtitle</meta>'
           '<meta refines="#t-sub" property="display-seq">2</meta>'
           f'<dc:creator id="creator">{html.escape(AUTHOR)}</dc:creator>'
           '<meta refines="#creator" property="role" scheme="marc:relators">aut</meta>'
           f'<meta refines="#creator" property="file-as">{html.escape(AUTHOR_SORT)}</meta>'
           f'<dc:language>{LANGUAGE}</dc:language>'
           f'<dc:date>{date}</dc:date>'
           f'<dc:description>{html.escape(DESCRIPTION)}</dc:description>'
           + "".join(f'<dc:subject>{html.escape(k)}</dc:subject>' for k in KEYWORDS) +
           f'<meta property="schema:version">{html.escape(revision)}</meta>'
           f'<meta property="dcterms:modified">{modified}</meta></metadata><manifest>']
    for mid, name, mtype, props in manifest:
        opf.append(f'<item id="{mid}" href="{name}" media-type="{mtype}"{props}/>')
    opf.append('</manifest><spine toc="ncx">')
    opf += [f'<itemref idref="{s}"/>' for s in spine]
    opf.append('</spine><guide><reference type="toc" title="Contents" href="toc.xhtml"/></guide></package>\n')

    dest.parent.mkdir(parents=True, exist_ok=True)
    fixed = dt.datetime.strptime(date, "%Y-%m-%d").timetuple()[:6]
    fixed = (max(fixed[0], 1980),) + fixed[1:]

    def add(z, name, data, compress=True):
        zi = zipfile.ZipInfo(name, date_time=fixed)
        zi.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        z.writestr(zi, data)

    with zipfile.ZipFile(dest, "w") as z:
        add(z, "mimetype", "application/epub+zip", compress=False)   # must be first, stored
        add(z, "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>\n')
        add(z, "OEBPS/content.opf", "".join(opf))
        for name, text in files.items():
            add(z, f"OEBPS/{name}", text)


# -------------------------------------------------------------------- main --

def main():
    global AUTHOR, AUTHOR_SORT
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("revision", help="revision label shown on the title page (e.g. 1.0 or a git hash)")
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="generation date, YYYY-MM-DD (default: today)")
    ap.add_argument("--out", type=Path, default=BOOK_DIR / "output", help="output folder (default book/output)")
    ap.add_argument("--author", default=AUTHOR, help=f"author name for the metadata (default {AUTHOR!r})")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-epub", action="store_true")
    args = ap.parse_args()
    dt.datetime.strptime(args.date, "%Y-%m-%d")  # validate
    if args.author != AUTHOR:
        AUTHOR = args.author
        first, _, last = AUTHOR.rpartition(" ")
        AUTHOR_SORT = f"{last}, {first}" if first else AUTHOR

    chapters = load_chapters()
    parts = load_parts()
    args.out.mkdir(parents=True, exist_ok=True)

    if not args.no_epub:
        render_chapters(chapters, lambda num: next(c.file for c in chapters if c.number == num))
        dest = args.out / f"{BASENAME}.epub"
        write_epub(chapters, parts, args.revision, args.date, dest)
        print(f"wrote {dest} ({dest.stat().st_size // 1024} KiB)")
    if not args.no_pdf:
        render_chapters(chapters, lambda num: "#ch-" + num.replace(".", "-"))
        dest = args.out / f"{BASENAME}.pdf"
        write_pdf(build_pdf_html(chapters, parts, args.revision, args.date), dest, args.revision, args.date)
        print(f"wrote {dest} ({dest.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
