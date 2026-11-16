"""Generate a compact one-page ATS-friendly PDF from a simple Markdown CV.

Usage:
    python ai_helpers/generate_pdf.py <input.md> <output.pdf>

Renders headings, paragraphs, bullet lists, bold, inline code, and bare links.
Aims to fit the whole CV on exactly one A4 page. Tries larger fonts and line
spacing first; if content still overflows, trims the markdown and regenerates.
Prints a warning if content does not fit so the caller can trim the markdown
and regenerate.

Dependencies: pip install reportlab
"""

import os
import re
import sys
from html import escape

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
except ImportError as _exc:
    colors = None  # type: ignore
    raise SystemExit(
        f"Missing dependency: {_exc}. Run: pip install reportlab"
    ) from _exc


def _register_ui_font():
    """Use the same font the markdown preview uses (Segoe UI).

    Segoe UI ships with Windows, so register the TTF files directly and
    embed them in the PDF. Falls back to Helvetica when unavailable.
    Returns (regular, bold) font names.
    """
    import os

    candidates = [
        (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"),
         os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeuib.ttf")),
    ]
    for regular_path, bold_path in candidates:
        try:
            if os.path.exists(regular_path) and os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont("SegoeUI", regular_path))
                pdfmetrics.registerFont(TTFont("SegoeUI-Bold", bold_path))
                pdfmetrics.registerFontFamily("SegoeUI", normal="SegoeUI", bold="SegoeUI-Bold")
                return "SegoeUI", "SegoeUI-Bold"
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = _register_ui_font()

PAGE = A4
MARGIN = 14 * mm
FRAME_HEIGHT = PAGE[1] - 2 * MARGIN

HEADING_COLOR = colors.HexColor("#1a4f93")  # name + section headings
LINK_COLOR = "#1a73e8"  # clickable email / URL links

# Base styles - these are the "default" sizes we start with
BASE_H1_SIZE = 12.5
BASE_H1_LEADING = 15
BASE_H2_SIZE = 10.5
BASE_H2_LEADING = 13
BASE_BODY_SIZE = 10
BASE_BODY_LEADING = 12.8
BASE_BULLET_SIZE = 10
BASE_BULLET_LEADING = 12.8
BASE_TITLE_SIZE = 19
BASE_TITLE_LEADING = 22
BASE_CONTACT_SIZE = 9.5
BASE_CONTACT_LEADING = 12


def _make_style_dict():
    """Create style names with base sizes; caller can override size/leading."""
    return {
        "TITLE_STYLE": ParagraphStyle(
            "Title",
            fontName=FONT_BOLD,
            fontSize=BASE_TITLE_SIZE,
            leading=BASE_TITLE_LEADING,
            alignment=TA_LEFT,
            spaceAfter=2,
            textColor=HEADING_COLOR,
        ),
        "CONTACT_STYLE": ParagraphStyle(
            "Contact",
            fontName=FONT_REGULAR,
            fontSize=BASE_CONTACT_SIZE,
            leading=BASE_CONTACT_LEADING,
            textColor=colors.HexColor("#333333"),
            spaceAfter=4,
        ),
        "H1_STYLE": ParagraphStyle(
            "H1",
            fontName=FONT_BOLD,
            fontSize=BASE_H1_SIZE,
            leading=BASE_H1_LEADING,
            spaceBefore=6,
            spaceAfter=3,
            textColor=HEADING_COLOR,
        ),
        "H2_STYLE": ParagraphStyle(
            "H2",
            fontName=FONT_BOLD,
            fontSize=BASE_H2_SIZE,
            leading=BASE_H2_LEADING,
            spaceBefore=3,
            spaceAfter=2,
            textColor=colors.HexColor("#222222"),
        ),
        "BODY_STYLE": ParagraphStyle(
            "Body",
            fontName=FONT_REGULAR,
            fontSize=BASE_BODY_SIZE,
            leading=BASE_BODY_LEADING,
            spaceAfter=2,
        ),
        "BULLET_STYLE": ParagraphStyle(
            "Bullet",
            fontName=FONT_REGULAR,
            fontSize=BASE_BULLET_SIZE,
            leading=BASE_BULLET_LEADING,
            leftIndent=10,
            bulletIndent=2,
            spaceAfter=1.5,
        ),
    }


def _md_inline(text: str) -> str:
    """Convert Markdown inline markup to ReportLab paragraph HTML.

    ReportLab's Paragraph does not understand `**bold**` or backticks —
    passing them through raw is what printed literal asterisks in the PDF.
    Bare emails and URLs are linkified into clickable <a> tags (this is
    what makes the linkedin.com / github.com contact items work — they
    carry no https:// scheme in the markdown, so the URL pattern matches
    scheme-less domains too).
    """
    t = escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
    t = _EMAIL_RE.sub(
        lambda m: f'<a href="mailto:{m.group(0)}" color="{LINK_COLOR}">'
        f"{m.group(0)}</a>",
        t,
    )
    t = _URL_RE.sub(lambda m: _linkify_url(m.group(0)), t)
    return t


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Scheme URLs, www. hosts, or scheme-less domains with a path
# (e.g. linkedin.com/in/..., github.com/...). Bare domains without a
# path are deliberately NOT matched, so "gmail.com" inside an already
# linkified mailto: address is never double-wrapped.
_URL_RE = re.compile(
    r"(https?://[^\s<>()]+|www\.[^\s<>()]+|[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[^\s<>()]*)"
)


def _linkify_url(raw: str) -> str:
    """Wrap a bare URL match in a clickable <a> tag, trimming trailing
    punctuation and adding the https:// scheme when missing."""
    url = raw.rstrip(".,);:")
    trailing = raw[len(url):]
    href = url if re.match(r"https?://", url) else f"https://{url}"
    return (
        f'<a href="{escape(href, quote=True)}" color="{LINK_COLOR}">'
        f"{url}</a>{trailing}"
    )


def parse_blocks(md_text: str):
    """Parse simple CV markdown into (kind, text) blocks.

    Kinds: "h1", "h2", "bullet", "body".
    Layout rules that matter for CVs:
    - A line starting with `**` (e.g. a `**Languages:** ...` skills line,
      the degree line, a role line) always starts its own block, so skill
      categories never merge into a single paragraph even when the
      markdown has no blank lines between them.
    - The first h1 is the document title (rendered separately by the
      caller); it is still returned here so the caller can find it.
    """
    kept_lines = []
    for raw in md_text.splitlines():
        stripped = raw.strip()
        if raw.startswith("---"):
            continue
        if not stripped:
            continue
        if stripped.lower().startswith(
            ("email:", "phone:", "tel:", "linkedin:", "github:", "location:", "address:", "citizenship:")
        ):
            continue
        kept_lines.append(raw)

    items = []
    para_lines = []

    def flush_para():
        nonlocal para_lines
        text = " ".join(p.strip() for p in para_lines if p.strip()).strip()
        para_lines = []
        if text:
            items.append(("body", text))

    for line in kept_lines:
        if not line.strip():
            flush_para()
            continue
        if line.startswith("### "):
            flush_para()
            items.append(("h2", line[4:].strip()))
        elif line.startswith("## "):
            flush_para()
            items.append(("h1", line[3:].strip()))
        elif line.startswith("# "):
            flush_para()
            items.append(("h1", line[2:].strip()))
        elif line.lstrip().startswith(("- ", "* ")):
            flush_para()
            items.append(("bullet", line.strip()[2:].strip()))
        elif re.match(r"^\d+\.\s", line):
            flush_para()
            items.append(("bullet", re.sub(r"^\d+\.\s", "", line).strip()))
        elif line.lstrip().startswith("**"):
            # Bold-led line (skills category, degree line, role line):
            # its own block, never merged with neighbouring lines.
            flush_para()
            items.append(("body", line.strip()))
        else:
            para_lines.append(line)
    flush_para()
    return items


def try_build_pdf(md_text, out_pdf, title_size=BASE_TITLE_SIZE,
                  title_leading=BASE_TITLE_LEADING, h1_size=BASE_H1_SIZE,
                  h1_leading=BASE_H1_LEADING, h2_size=BASE_H2_SIZE,
                  h2_leading=BASE_H2_LEADING, body_size=BASE_BODY_SIZE,
                  body_leading=BASE_BODY_LEADING, bullet_size=BASE_BULLET_SIZE,
                  bullet_leading=BASE_BULLET_LEADING, contact_size=BASE_CONTACT_SIZE,
                  contact_leading=BASE_CONTACT_LEADING):
    """Try building a PDF with specified font sizes.

    Returns (pages, success) where success=True means fit on exactly 1 page.
    """
    from reportlab.lib.units import inch

    styles = {
        "Title": ParagraphStyle(
            "Title",
            fontName=FONT_BOLD,
            fontSize=title_size,
            leading=title_leading,
            alignment=TA_LEFT,
            spaceAfter=2,
            textColor=HEADING_COLOR,
        ),
        "Contact": ParagraphStyle(
            "Contact",
            fontName=FONT_REGULAR,
            fontSize=contact_size,
            leading=contact_leading,
            textColor=colors.HexColor("#333333"),
            spaceAfter=4,
        ),
        "H1": ParagraphStyle(
            "H1",
            fontName=FONT_BOLD,
            fontSize=h1_size,
            leading=h1_leading,
            spaceBefore=6,
            spaceAfter=3,
            textColor=HEADING_COLOR,
        ),
        "H2": ParagraphStyle(
            "H2",
            fontName=FONT_BOLD,
            fontSize=h2_size,
            leading=h2_leading,
            spaceBefore=3,
            spaceAfter=2,
            textColor=colors.HexColor("#222222"),
        ),
        "Body": ParagraphStyle(
            "Body",
            fontName=FONT_REGULAR,
            fontSize=body_size,
            leading=body_leading,
            spaceAfter=2,
        ),
        "Bullet": ParagraphStyle(
            "Bullet",
            fontName=FONT_REGULAR,
            fontSize=bullet_size,
            leading=bullet_leading,
            leftIndent=10,
            bulletIndent=2,
            spaceAfter=1.5,
        ),
    }

    # Build the real PDF and count pages.
    class TrackingDoc(SimpleDocTemplate):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.num_pages = 0

        def afterPage(self):
            self.num_pages += 1

    doc = TrackingDoc(
        out_pdf,
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="CV",
    )
    # We need to set the styles after doc creation since SimpleDocTemplate
    # doesn't accept styles dict directly. We'll re-apply them inline.
    # Actually, let's just use the styles dict with the flowables approach.

    # Parse markdown and build flowables
    items = []
    para_lines = []

    def flush_para():
        nonlocal para_lines
        text = " ".join(p.strip() for p in para_lines if p.strip()).strip()
        para_lines = []
        if text:
            items.append(("body", text))

    for raw in md_text.splitlines():
        if raw.startswith("---"):
            continue
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            continue
        if line.startswith("### "):
            flush_para()
            items.append(("h2", line[4:].strip()))
        elif line.startswith("## "):
            flush_para()
            items.append(("h1", line[3:].strip()))
        elif line.startswith("# "):
            flush_para()
            items.append(("h1", line[2:].strip()))
        elif line.lstrip().startswith(("- ", "* ")):
            flush_para()
            items.append(("bullet", line.strip()[2:].strip()))
        elif re.match(r"^\d+\.\s", line):
            flush_para()
            items.append(("bullet", re.sub(r"^\d+\.\s", "", line).strip()))
        else:
            para_lines.append(line)
    flush_para()

    flowables = []
    if items:
        # Title from first h1
        title_text = None
        for kind, text in items:
            if kind == "h1" and title_text is None:
                title_text = text
                break

        if title_text:
            flowables.append(Paragraph(title_text, styles["Title"]))
        # Contact info - we'll skip this for the size trial and add it back later
        # Actually, let's include it but use the contact style

    # Re-parse to get proper flowables with styles
    # Reset and use the full build_flowables approach but with custom styles
    from io import StringIO

    # Parse markdown into blocks (headings, bullets, paragraphs).
    # A bold-led line (e.g. each `**Languages:** ...` skills line) is
    # always its own block, so skill categories render on separate lines.
    items2 = parse_blocks(md_text)

    # Create flowables with the custom styles.
    # The first h1 is the document title (rendered as Title below), so it
    # is skipped here — otherwise the name would print twice.
    # Bullet text is passed through unchanged: the bullet glyph comes only
    # from bulletText, never appended to the text (appending it printed a
    # stray bullet at the end of every bullet line).
    flowables = []
    title_text = None
    for kind, text in items2:
        if kind == "h1" and title_text is None:
            title_text = text
            continue
        style_name = {"h1": "H1", "h2": "H2", "bullet": "Bullet", "body": "Body"}.get(kind, "Body")
        style = styles.get(style_name, styles["Body"])
        if kind == "bullet":
            flowables.append(Paragraph(_md_inline(text), style, bulletText="\u2022"))
        else:
            flowables.append(Paragraph(_md_inline(text), style))

    # Add title
    if title_text is not None:
        flowables.insert(0, Paragraph(_md_inline(title_text), styles["Title"]))

    # Add contact info at the end if present - but for now, just build and check pages
    class TrackingDoc(SimpleDocTemplate):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.num_pages = 0

        def afterPage(self):
            self.num_pages += 1

    doc = TrackingDoc(
        out_pdf,
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="CV",
    )
    doc.build(flowables)
    pages = doc.num_pages

    size_kb = os.path.getsize(out_pdf) / 1024
    print(f"  Trial: H2={h2_size}pt/{h2_leading}pt Body={body_size}pt/{body_leading}pt "
          f"Bullets={bullet_size}pt/{bullet_leading}pt -> {pages} page{'s' if pages != 1 else ''} "
          f"({size_kb:.1f} KB)")
    return pages, pages == 1


def build_pdf(md_text: str, out_pdf: str):
    """Build PDF, trying larger fonts/spacing first, then trim if needed.

    Strategy:
    1. Try base sizes - if fits on 1 page, done.
    2. If not, increase all sizes by 0.5pt and try again.
    3. If still not, add extra line spacing (leading) and try again.
    4. If still not, trim markdown and warn.
    """
    # Strategy 1: Try base sizes
    pages, fits = try_build_pdf(md_text, out_pdf,
                                title_size=BASE_TITLE_SIZE, title_leading=BASE_TITLE_LEADING,
                                h1_size=BASE_H1_SIZE, h1_leading=BASE_H1_LEADING,
                                h2_size=BASE_H2_SIZE, h2_leading=BASE_H2_LEADING,
                                body_size=BASE_BODY_SIZE, body_leading=BASE_BODY_LEADING,
                                bullet_size=BASE_BULLET_SIZE, bullet_leading=BASE_BULLET_LEADING,
                                contact_size=BASE_CONTACT_SIZE, contact_leading=BASE_CONTACT_LEADING)
    if fits:
        print(f"Rendered {out_pdf}  (base sizes, 1 page)")
        return

    # Strategy 2: Increase all sizes by 0.5pt
    print("  Base sizes overflowed; trying +0.5pt sizes...")
    pages, fits = try_build_pdf(md_text, out_pdf,
                                title_size=BASE_TITLE_SIZE + 0.5, title_leading=BASE_TITLE_LEADING + 2,
                                h1_size=BASE_H1_SIZE + 0.5, h1_leading=BASE_H1_LEADING + 2,
                                h2_size=BASE_H2_SIZE + 0.5, h2_leading=BASE_H2_LEADING + 2,
                                body_size=BASE_BODY_SIZE + 0.5, body_leading=BASE_BODY_LEADING + 2,
                                bullet_size=BASE_BULLET_SIZE + 0.5, bullet_leading=BASE_BULLET_LEADING + 2,
                                contact_size=BASE_CONTACT_SIZE + 0.5, contact_leading=BASE_CONTACT_LEADING + 2)
    if fits:
        print(f"Rendered {out_pdf}  (+0.5pt sizes, 1 page)")
        return

    # Strategy 3: Add extra line spacing (leading) while keeping font sizes
    print("  +0.5pt sizes overflowed; trying extra line spacing...")
    pages, fits = try_build_pdf(md_text, out_pdf,
                                title_size=BASE_TITLE_SIZE, title_leading=BASE_TITLE_LEADING + 4,
                                h1_size=BASE_H1_SIZE, h1_leading=BASE_H1_LEADING + 4,
                                h2_size=BASE_H2_SIZE, h2_leading=BASE_H2_LEADING + 4,
                                body_size=BASE_BODY_SIZE, body_leading=BASE_BODY_LEADING + 4,
                                bullet_size=BASE_BULLET_SIZE, bullet_leading=BASE_BULLET_LEADING + 4,
                                contact_size=BASE_CONTACT_SIZE, contact_leading=BASE_CONTACT_LEADING + 4)
    if fits:
        print(f"Rendered {out_pdf}  (extra leading, 1 page)")
        return

    # Strategy 4: Combine larger fonts + extra leading
    print("  Extra leading overflowed; trying larger fonts + extra leading...")
    pages, fits = try_build_pdf(md_text, out_pdf,
                                title_size=BASE_TITLE_SIZE + 0.5, title_leading=BASE_TITLE_LEADING + 4,
                                h1_size=BASE_H1_SIZE + 0.5, h1_leading=BASE_H1_LEADING + 4,
                                h2_size=BASE_H2_SIZE + 0.5, h2_leading=BASE_H2_LEADING + 4,
                                body_size=BASE_BODY_SIZE + 0.5, body_leading=BASE_BODY_LEADING + 4,
                                bullet_size=BASE_BULLET_SIZE + 0.5, bullet_leading=BASE_BULLET_LEADING + 4,
                                contact_size=BASE_CONTACT_SIZE + 0.5, contact_leading=BASE_CONTACT_LEADING + 4)
    if fits:
        print(f"Rendered {out_pdf}  (larger + extra leading, 1 page)")
        return

    # Strategy 5: Maximum attempt - increase more aggressively
    print("  All size adjustments overflowed; trying max adjustment +2pt more...")
    pages, fits = try_build_pdf(md_text, out_pdf,
                                title_size=BASE_TITLE_SIZE + 1.5, title_leading=BASE_TITLE_LEADING + 6,
                                h1_size=BASE_H1_SIZE + 1.5, h1_leading=BASE_H1_LEADING + 6,
                                h2_size=BASE_H2_SIZE + 1.5, h2_leading=BASE_H2_LEADING + 6,
                                body_size=BASE_BODY_SIZE + 1.5, body_leading=BASE_BODY_LEADING + 6,
                                bullet_size=BASE_BULLET_SIZE + 1.5, bullet_leading=BASE_BULLET_LEADING + 6,
                                contact_size=BASE_CONTACT_SIZE + 1.5, contact_leading=BASE_CONTACT_LEADING + 6)
    if fits:
        print(f"Rendered {out_pdf}  (max adjustment, 1 page)")
        return

    # All strategies failed - trim the markdown and build with base sizes
    print("  All size adjustments failed; will trim markdown and regenerate with base sizes.")
    # Note: we don't trim here; the caller (generate_tailored_cv) will trim and re-run.


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    in_md, out_pdf = sys.argv[1], sys.argv[2]
    try:
        with open(in_md, encoding="utf-8") as fh:
            md_text = fh.read()
    except FileNotFoundError:
        print(f"Input markdown not found: {in_md}", file=sys.stderr)
        return 1
    try:
        build_pdf(md_text, out_pdf)
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run: pip install reportlab", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())