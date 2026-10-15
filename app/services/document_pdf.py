"""Any information as a PDF, in the centre's look: to download, or to send to clients.

A title and a text the admin wrote (or an answer of the assistant), laid out
like the program PDFs: the dark band with the centre's name, then the text.
In the text, an empty line starts a new paragraph, a line starting with "-"
or "•" is a point of a list, a short line ending with ":" is a heading,
**words** are bold, and a paragraph starting with [box], [box gold],
[box green] or [box grey] is a coloured square, as in the emails.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.models.email_file import EmailFile
from app.services import email_files, program_pdf
from app.services.email_layout import INK

_BOX = re.compile(r"^\[box(?:\s+(gold|green|grey|gray))?\]\s*", re.IGNORECASE)
_POINT = re.compile(r"^\s*(?:[-•*·]|\d+[.)])\s+")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MONTHS = {"fr": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split(),
           "en": "January February March April May June July August September October November December".split()}
LIMIT = 20000       # characters of text in one document


def _inline(text: str) -> str:
    """One line for the PDF: its letters, its **bold** words."""
    return _BOLD.sub(r"<b>\1</b>", escape(program_pdf.plain(text)))


def blocks(text: str) -> list[tuple[str, object]]:
    """The text cut into what the PDF draws: ("heading", str) ("text", [lines]) ("points", [lines]) ("box", (tone, [lines]))."""
    found = []
    for part in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = [line.rstrip() for line in part.split("\n") if line.strip()]
        if not lines:
            continue
        box = _BOX.match(lines[0])
        if box:
            lines[0] = lines[0][box.end():]
            found.append(("box", ((box.group(1) or "brand").lower().replace("gray", "grey"), [line for line in lines if line.strip()])))
            continue
        if len(lines) == 1 and lines[0].strip().endswith(":") and len(lines[0]) <= 70 and not _POINT.match(lines[0]):
            found.append(("heading", lines[0].strip().rstrip(": ")))
            continue
        text_lines, points = [], []
        for line in lines:
            if _POINT.match(line):
                if len(text_lines) == 1 and text_lines[0].endswith(":") and len(text_lines[0]) <= 70:
                    found.append(("heading", text_lines[0].rstrip(": ")))     # "Tarifs :" just before its list
                elif text_lines:
                    found.append(("text", text_lines))
                text_lines = []
                points.append(_POINT.sub("", line, count=1).strip())
            elif points and line[:1] in (" ", "\t"):
                points[-1] += " " + line.strip()        # a point that goes on, on the next line
            else:
                if points:
                    found.append(("points", points))
                    points = []
                text_lines.append(line.strip())
        if text_lines:
            found.append(("text", text_lines))
        if points:
            found.append(("points", points))
    return found


def date_of(lang: str, moment: datetime | None = None) -> str:
    moment = moment or datetime.now()
    month = _MONTHS["fr" if lang == "fr" else "en"][moment.month - 1]
    return f"{moment.day} {month} {moment.year}" if lang == "fr" else f"{month} {moment.day}, {moment.year}"


def build(db: Session, title: str, text: str, lang: str = "fr", candidate_name: str | None = None, when: datetime | None = None) -> bytes:
    """The document as a PDF."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageTemplate, Paragraph, Spacer, Table, TableStyle

    lang = program_pdf.clean_language(lang)
    words = program_pdf._WORDS[lang]
    accent = program_pdf.accent_of(db)
    styles = program_pdf._styles(accent)
    contact = {"brand": program_pdf.BRAND, "website": program_pdf.WEBSITE, "email": program_pdf.plain(os.getenv("SMTP_EMAIL") or "")}
    title = program_pdf.plain(title)[:140] or "Document"
    candidate = program_pdf.plain(candidate_name)[:60]
    from reportlab.pdfbase.pdfmetrics import stringWidth

    width, height = A4
    left = 40
    band = 236 if stringWidth(title, "Helvetica-Bold", 34) <= 380 else 280      # a title on two lines needs a taller band
    inner = width - 2 * left
    page_content = {"contact": contact}
    overline = "Information" if lang == "en" else "Informations"
    tones = {"brand": (program_pdf._mix(accent, "#ffffff", 0.9), HexColor(accent)), "gold": (HexColor("#fff4dc"), HexColor("#b45309")),
             "green": (HexColor("#e7f8ee"), HexColor("#15803d")), "grey": (HexColor("#f1eff5"), HexColor("#5b5670"))}

    def cover(canvas, doc):
        program_pdf._cover_band(canvas, contact, words, accent, band, overline, title, date_of(lang, when), candidate)

    def later(canvas, doc):
        program_pdf._running_head(canvas, page_content, words, accent, title[:90])

    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=A4, title=title, author=contact["brand"], subject=overline, invariant=1)
    document.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(left, 52, inner, height - band - 24 - 52, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=cover),
        PageTemplate(id="later", frames=[Frame(left, 52, inner, height - 52 - 46, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=later),
    ])
    body = ParagraphStyle("doc", parent=styles["body"], spaceAfter=8)
    point = ParagraphStyle("point", parent=styles["points"], fontSize=10.2, leading=14.6, leftIndent=14, bulletIndent=2, spaceAfter=3)
    story = [NextPageTemplate("later")]
    for kind, value in blocks((text or "")[:LIMIT]):
        if kind == "heading":
            story.append(Paragraph(_inline(value), styles["h2"]))
        elif kind == "text":
            story.append(Paragraph("<br/>".join(_inline(line) for line in value), body))
        elif kind == "points":
            story += [Paragraph(_inline(line), point, bulletText="•") for line in value]
            story.append(Spacer(1, 5))
        else:
            tone, lines = value
            fill, ink = tones.get(tone, tones["brand"])
            box = Table([[Paragraph("<br/>".join(_inline(line) for line in lines),
                                    ParagraphStyle("box", parent=styles["body"], textColor=HexColor(INK) if tone == "grey" else ink, fontName="Helvetica-Bold"))]],
                        colWidths=[inner])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fill), ("ROUNDEDCORNERS", [10, 10, 10, 10]), ("LEFTPADDING", (0, 0), (-1, -1), 16),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 16), ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12)]))
            story += [Spacer(1, 4), KeepTogether([box]), Spacer(1, 10)]
    if len(story) == 1:
        story.append(Paragraph("", body))
    story += [Spacer(1, 16), program_pdf._closing(page_content, words, styles, inner)]
    document.build(story)
    return buffer.getvalue()


def filename_of(title: str) -> str:
    return email_files.safe_name(f"{program_pdf.plain(title)[:80] or 'Document'}.pdf", "document.pdf")


def store(db: Session, title: str, text: str, lang: str = "fr", candidate_name: str | None = None) -> EmailFile:
    """The document kept as a file an email can carry. The same document is kept once."""
    return program_pdf._kept(db, build(db, title, text, lang, candidate_name), "document:", filename_of(title))


def message_for(title: str) -> dict:
    """The email that carries the document, ready for the admin to read and change before sending."""
    name = program_pdf.plain(title).replace("{", "(").replace("}", ")") or "le document"
    return {
        "subject": name,
        "title": name,
        "body": (
            "Bonjour {first_name},\n\n"
            f"Vous trouverez en pièce jointe « {name} ».\n\n"
            "Si vous avez la moindre question, répondez simplement à cet email."
        ),
    }
