"""Any page of the application as a PDF report, in the centre's look.

A page describes its report as a list of blocks (plain data, no drawing):

    {"kind": "figures", "items": [(label, value, note, tone), ...]}       tiles, four to a row
    {"kind": "heading", "text": "...", "note": "..."}                      a section title
    {"kind": "text", "text": "..."} / {"kind": "points", "items": [...]}
    {"kind": "box", "tone": "brand|gold|green|grey", "text": "..."}       a coloured square
    {"kind": "bars", "title": "...", "labels": [...], "series": [{"name", "values", "tone"}], "unit": "DT"}
    {"kind": "donut", "title": "...", "parts": [(label, value, tone)], "center": ("62%", "paid")}
    {"kind": "hbars", "title": "...", "rows": [(label, value, note)], "most": 100, "unit": "%"}
    {"kind": "table", "columns": [...], "rows": [[cell, ...]], "widths": [2, 1, ...], "right": [2, 3]}
    {"kind": "row", "blocks": [block, block]}                              two blocks side by side
    {"kind": "empty", "text": "..."} / {"kind": "break"}

A table cell is text, or {"chip": "Paid", "tone": "ok"}, or {"bar": 62, "text": "300 / 480 DT"},
or {"text": "...", "bold": True, "note": "a second, smaller line"}.

Everything written comes from the records; the drawing only lays it out. The fonts of
the PDF write Western European letters (see program_pdf.plain), so Arabic is left out.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.services import program_pdf
from app.services.email_layout import BRAND, GOLD, INK

TONES = {           # (ink, soft fill)
    "brand": (None, None),          # the accent chosen in Settings, filled in by _palette
    "gold": ("#b45309", "#fef3c7"),
    "ok": ("#15803d", "#e9f9ef"),
    "warn": ("#b45309", "#fef3c7"),
    "bad": ("#be123c", "#fff1f2"),
    "info": ("#1d4ed8", "#eef3ff"),
    "violet": ("#6d28d9", "#f3e8ff"),
    "teal": ("#0f766e", "#e6fbf8"),
    "muted": ("#5b5670", "#f1eff5"),
}
MUTED, LINE, TEXT = "#85809c", "#e7e1f0", "#3a3350"


def _palette(accent: str) -> dict:
    tones = dict(TONES)
    tones["brand"] = (accent, None)
    return tones


def _hex(value):
    from reportlab.lib.colors import HexColor

    return value if not isinstance(value, str) else HexColor(value)


def _soft(tone: str, accent: str):
    ink, soft = _palette(accent).get(tone, TONES["muted"])
    return _hex(soft) if soft else program_pdf._mix(ink, "#ffffff", 0.88)


def _ink(tone: str, accent: str):
    return _hex(_palette(accent).get(tone, TONES["muted"])[0])


def _nice_top(value: float) -> tuple[float, float]:
    """A round top for an axis and its step: 7 -> (8, 2), 430 -> (500, 100)."""
    if value <= 0:
        return 4, 1
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (magnitude / 5, magnitude / 4, magnitude / 2, magnitude, magnitude * 2, magnitude * 2.5, magnitude * 5):
        if step > 0 and value / step <= 5:
            top = step * (int(value / step) + (0 if value % step == 0 else 1))
            return top, step
    return value, value / 4


def _number(value: float, unit: str = "") -> str:
    shown = f"{value:,.0f}".replace(",", " ") if abs(value) >= 100 or float(value).is_integer() else f"{value:.1f}"
    return f"{shown} {unit}".strip() if unit and unit != "%" else f"{shown}{unit}"


def _flowables(accent: str):
    """The drawn pieces. Built here so reportlab is only needed when a PDF is made."""
    from reportlab.lib.colors import white
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Flowable

    ink, muted, line = _hex(INK), _hex(MUTED), _hex(LINE)

    def fitted(text, font, size, width):
        while stringWidth(text, font, size) > width and len(text) > 3:
            text = text[:-2].rstrip() + "…"
        return text

    class Figures(Flowable):
        """Tiles: a coloured dot and label, the value, a note under it."""

        def __init__(self, items):
            super().__init__()
            self.items = items
            self.height = 64

        def wrap(self, width, height):
            self.width = width
            return width, self.height

        def draw(self):
            canvas, count, gap = self.canv, max(1, len(self.items)), 9
            width = (self.width - gap * (count - 1)) / count
            for index, item in enumerate(self.items):
                label, value, note, tone = (list(item) + [None, "brand"])[:4]
                tone = tone or "brand"
                x = index * (width + gap)
                canvas.setFillColor(white)
                canvas.setStrokeColor(line)
                canvas.setLineWidth(0.8)
                canvas.roundRect(x, 0, width, self.height, 10, stroke=1, fill=1)
                canvas.setFillColor(_ink(tone, accent))
                canvas.roundRect(x, self.height - 4, width, 4, 2, stroke=0, fill=1)
                canvas.circle(x + 14, self.height - 17, 3, stroke=0, fill=1)
                canvas.setFillColor(muted)
                canvas.setFont("Helvetica-Bold", 6.9)
                canvas.drawString(x + 22, self.height - 19.5, fitted(str(label).upper(), "Helvetica-Bold", 6.9, width - 30))
                value = program_pdf.plain(value)
                size = program_pdf._fit(value, "Helvetica-Bold", 16, width - 22, 8)
                canvas.setFillColor(ink)
                canvas.setFont("Helvetica-Bold", size)
                canvas.drawString(x + 11, self.height - 24 - size, fitted(value, "Helvetica-Bold", size, width - 22))
                if note:
                    note = program_pdf.plain(note)
                    canvas.setFillColor(muted)
                    canvas.setFont("Helvetica", 7.4)
                    canvas.drawString(x + 11, 9, fitted(note, "Helvetica", 7.4, width - 22))

    class Bars(Flowable):
        """Columns per label, one colour per series, with a light grid and the values on top."""

        def __init__(self, labels, series, unit="", height=180):
            super().__init__()
            self.labels, self.series, self.unit, self.height = labels, series, unit, height

        def wrap(self, width, height):
            self.width = width
            return width, self.height

        def draw(self):
            canvas = self.canv
            series = [item for item in self.series if item.get("values")]
            if not series or not self.labels:
                return
            biggest = max([max(item["values"] or [0]) for item in series] + [0])
            top, step = _nice_top(biggest)
            legend = 16 if len(series) > 1 else 0
            left, bottom = 34, 22
            chart_width, chart_height = self.width - left - 4, self.height - bottom - 14 - legend
            canvas.setFont("Helvetica", 7)
            ticks = int(round(top / step)) if step else 4
            for tick in range(ticks + 1):
                y = bottom + chart_height * tick / max(1, ticks)
                canvas.setStrokeColor(line if tick else muted)
                canvas.setLineWidth(0.5 if tick else 0.8)
                canvas.line(left, y, left + chart_width, y)
                canvas.setFillColor(muted)
                canvas.drawRightString(left - 5, y - 2.5, _number(step * tick))
            slot = chart_width / len(self.labels)
            group = min(slot * 0.7, 22 * len(series) + 4 * (len(series) - 1))
            bar = (group - 4 * (len(series) - 1)) / len(series)
            for index, label in enumerate(self.labels):
                x0 = left + slot * index + (slot - group) / 2
                for number, item in enumerate(series):
                    value = item["values"][index] if index < len(item["values"]) else 0
                    tall = chart_height * (value / top) if top else 0
                    x = x0 + number * (bar + 4)
                    canvas.setFillColor(_ink(item.get("tone", "brand"), accent))
                    if tall > 0:
                        canvas.roundRect(x, bottom, bar, max(tall, 2.5), min(3.5, bar / 2), stroke=0, fill=1)
                        canvas.rect(x, bottom, bar, min(max(tall, 2.5), 3.5), stroke=0, fill=1)
                        canvas.setFillColor(ink)
                        canvas.setFont("Helvetica-Bold", 6.8)
                        canvas.drawCentredString(x + bar / 2, bottom + tall + 3, _number(value))
                canvas.setFillColor(muted)
                canvas.setFont("Helvetica", 7.2)
                canvas.drawCentredString(left + slot * index + slot / 2, bottom - 12, fitted(program_pdf.plain(label), "Helvetica", 7.2, slot - 2))
            if legend:
                x = left
                for item in series:
                    canvas.setFillColor(_ink(item.get("tone", "brand"), accent))
                    canvas.roundRect(x, self.height - 10, 9, 9, 2, stroke=0, fill=1)
                    canvas.setFillColor(ink)
                    canvas.setFont("Helvetica", 7.6)
                    name = program_pdf.plain(item.get("name", ""))
                    canvas.drawString(x + 13, self.height - 8.5, name)
                    x += stringWidth(name, "Helvetica", 7.6) + 28

    class Donut(Flowable):
        """A ring cut in parts, its figure in the middle, the legend at its side."""

        def __init__(self, parts, center=None, height=150):
            super().__init__()
            self.parts, self.center, self.height = parts, center, height

        def wrap(self, width, height):
            self.width = width
            return width, self.height

        def draw(self):
            canvas = self.canv
            total = sum(max(0, value) for _, value, _ in self.parts)
            radius = min(self.height / 2 - 6, 64, self.width * 0.2)
            cx, cy = radius + 6, self.height / 2
            if total <= 0:
                canvas.setFillColor(line)
                canvas.circle(cx, cy, radius, stroke=0, fill=1)
            else:
                angle = 90
                for _, value, tone in self.parts:
                    if value <= 0:
                        continue
                    extent = -360 * value / total
                    canvas.setFillColor(_ink(tone, accent))
                    canvas.setStrokeColor(white)
                    canvas.setLineWidth(1.4)
                    if abs(extent) >= 359.9:
                        canvas.circle(cx, cy, radius, stroke=0, fill=1)
                    else:
                        canvas.wedge(cx - radius, cy - radius, cx + radius, cy + radius, angle, extent, stroke=1, fill=1)
                    angle += extent
            canvas.setFillColor(white)
            canvas.circle(cx, cy, radius * 0.62, stroke=0, fill=1)
            if self.center:
                big, small = self.center
                canvas.setFillColor(ink)
                canvas.setFont("Helvetica-Bold", program_pdf._fit(program_pdf.plain(big), "Helvetica-Bold", 17, radius * 1.1, 8))
                canvas.drawCentredString(cx, cy + 1, program_pdf.plain(big))
                canvas.setFillColor(muted)
                canvas.setFont("Helvetica", 7.2)
                canvas.drawCentredString(cx, cy - 11, fitted(program_pdf.plain(small), "Helvetica", 7.2, radius * 1.15))
            x, rows = cx + radius + 20, len(self.parts)
            gap = min(28, (self.height - 6) / max(1, rows))
            y = cy + gap * (rows - 1) / 2 + 2
            for label, value, tone in self.parts:
                canvas.setFillColor(_ink(tone, accent))
                canvas.roundRect(x, y - 2, 9, 9, 3, stroke=0, fill=1)
                canvas.setFillColor(ink)
                canvas.setFont("Helvetica-Bold", 8.6)
                canvas.drawString(x + 15, y, fitted(program_pdf.plain(label), "Helvetica-Bold", 8.6, self.width - x - 17))
                share = f"{value / total * 100:.0f}%" if total else "0%"
                canvas.setFillColor(muted)
                canvas.setFont("Helvetica", 7.8)
                canvas.drawString(x + 15, y - 10.5, f"{_number(value)}  ·  {share}")
                y -= gap

    class HBars(Flowable):
        """One line per item: its name, a bar, its value."""

        def __init__(self, rows, most=None, unit="", tone="brand"):
            super().__init__()
            self.rows, self.most, self.unit, self.tone = rows, most, unit, tone
            self.height = 19 * len(rows) + 4

        def wrap(self, width, height):
            self.width = width
            return width, self.height

        def draw(self):
            canvas = self.canv
            most = self.most or max([value for _, value, *_ in self.rows] + [1])
            name_width = min(190, self.width * 0.36)
            track = self.width - name_width - 70
            y = self.height - 14
            for row in self.rows:
                label, value, note = (list(row) + [None])[:3]
                canvas.setFillColor(ink)
                canvas.setFont("Helvetica", 8.4)
                canvas.drawString(0, y, fitted(program_pdf.plain(label), "Helvetica", 8.4, name_width - 8))
                canvas.setFillColor(_hex("#efebf6"))
                canvas.roundRect(name_width, y - 1, track, 8, 4, stroke=0, fill=1)
                share = max(0.0, min(1.0, value / most)) if most else 0
                if share > 0:
                    canvas.setFillColor(_ink(self.tone, accent))
                    canvas.roundRect(name_width, y - 1, max(8, track * share), 8, 4, stroke=0, fill=1)
                canvas.setFillColor(ink)
                canvas.setFont("Helvetica-Bold", 8.2)
                canvas.drawRightString(self.width - 2, y, program_pdf.plain(note) if note else _number(value, self.unit))
                y -= 19

    class Chip(Flowable):
        def __init__(self, text, tone):
            super().__init__()
            self.text, self.tone = fitted(program_pdf.plain(text), "Helvetica-Bold", 7.4, 150), tone
            self.width, self.height = stringWidth(self.text, "Helvetica-Bold", 7.4) + 14, 14

        def draw(self):
            self.canv.setFillColor(_soft(self.tone, accent))
            self.canv.roundRect(0, 0, self.width, self.height, 7, stroke=0, fill=1)
            self.canv.setFillColor(_ink(self.tone, accent))
            self.canv.setFont("Helvetica-Bold", 7.4)
            self.canv.drawString(7, 4.2, self.text)

    class Progress(Flowable):
        def __init__(self, percent, text, width=110):
            super().__init__()
            self.percent, self.text, self.width, self.height = max(0, min(100, percent or 0)), program_pdf.plain(text), width, 20

        def wrap(self, width, height):
            self.width = min(self.width, width)
            return self.width, self.height

        def draw(self):
            canvas = self.canv
            canvas.setFillColor(_hex("#efebf6"))
            canvas.roundRect(0, 2, self.width, 5, 2.5, stroke=0, fill=1)
            if self.percent:
                canvas.setFillColor(_hex("#15803d") if self.percent >= 100 else _hex(accent))
                canvas.roundRect(0, 2, max(5, self.width * self.percent / 100), 5, 2.5, stroke=0, fill=1)
            canvas.setFillColor(ink)
            canvas.setFont("Helvetica", 7.6)
            canvas.drawString(0, 11, fitted(self.text, "Helvetica", 7.6, self.width))

    return {"Figures": Figures, "Bars": Bars, "Donut": Donut, "HBars": HBars, "Chip": Chip, "Progress": Progress}


def _story(blocks: list[dict], accent: str, inner: float) -> list:
    from reportlab.lib.colors import white
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import CondPageBreak, KeepTogether, PageBreak, Paragraph, Spacer, Table, TableStyle

    pieces = _flowables(accent)
    styles = program_pdf._styles(accent)
    body = ParagraphStyle("rbody", parent=styles["body"], fontSize=9.8, leading=14, spaceAfter=6)
    small = ParagraphStyle("rsmall", parent=styles["small"], fontSize=8.2, leading=11)
    cell = ParagraphStyle("rcell", parent=styles["cell"], fontSize=8.4, leading=11)
    cell_bold = ParagraphStyle("rcellb", parent=styles["cell_bold"], fontSize=8.4, leading=11)
    head = ParagraphStyle("rhead", parent=styles["head"], fontSize=7.2, leading=9)
    title = ParagraphStyle("rtitle", parent=styles["h2"], fontSize=11, leading=14, spaceBefore=4, spaceAfter=4)

    def text(value):
        return escape(program_pdf.plain(value))

    def heading(block):
        parts = [CondPageBreak(block.get("room", 150)), Spacer(1, 6), Paragraph(text(block.get("text", "")), styles["h2"])]
        if block.get("note"):
            parts.append(Paragraph(text(block["note"]), small))
            parts.append(Spacer(1, 4))
        return parts

    def table_cell(value):
        if isinstance(value, dict):
            if "chip" in value:
                return pieces["Chip"](value["chip"], value.get("tone", "muted"))
            if "bar" in value:
                return pieces["Progress"](value["bar"], value.get("text", ""))
            lines = [Paragraph(text(value.get("text", "")), cell_bold if value.get("bold") else cell)]
            if value.get("note"):
                lines.append(Paragraph(text(value["note"]), ParagraphStyle("note", parent=small, fontSize=7.4, leading=9.5)))
            return lines
        return Paragraph(text("" if value is None else value), cell)

    def table(block, width):
        columns, rows = block.get("columns") or [], block.get("rows") or []
        if not rows:
            return [Paragraph(text(block.get("empty") or "Nothing to show yet."), small)]
        weights = block.get("widths") or [1] * len(columns)
        widths = [width * weight / sum(weights) for weight in weights]
        data = [[Paragraph(text(name).upper(), head) for name in columns]] + [[table_cell(value) for value in row] for row in rows]
        shown = Table(data, colWidths=widths, repeatRows=1)
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), _hex(INK)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, _hex(LINE)),
            ("ROUNDEDCORNERS", [7, 7, 0, 0]),
        ]
        for index in range(1, len(data)):
            if index % 2 == 0:
                commands.append(("BACKGROUND", (0, index), (-1, index), _hex("#faf8fd")))
        for column in block.get("right") or []:
            commands.append(("ALIGN", (column, 1), (column, -1), "RIGHT"))
        shown.setStyle(TableStyle(commands))
        return [shown]

    def chart_title(block):
        return [Paragraph(text(block["title"]), title)] if block.get("title") else []

    def keep(parts, inside):
        """Kept on one page, except inside a table cell (side by side), where that cannot work."""
        return parts if inside else [KeepTogether(parts)]

    def render(block, width, inside=False):
        kind = block.get("kind")
        if kind == "figures":
            items = list(block.get("items") or [])
            rows = [items[start:start + 4] for start in range(0, len(items), 4)]
            out = []
            for row in rows:
                out += [pieces["Figures"](row), Spacer(1, 9)]
            return out
        if kind == "heading":
            return heading(block)
        if kind == "text":
            return [Paragraph(text(line), body) for line in str(block.get("text", "")).split("\n") if line.strip()]
        if kind == "points":
            point = ParagraphStyle("rpoint", parent=styles["points"], fontSize=9.4, leading=13.4, leftIndent=12, bulletIndent=1, spaceAfter=2)
            return [Paragraph(text(item), point, bulletText="•") for item in block.get("items") or []] + [Spacer(1, 4)]
        if kind == "box":
            tone = block.get("tone", "brand")
            fill, ink = ({"gold": ("#fff4dc", "#b45309"), "green": ("#e7f8ee", "#15803d"), "grey": ("#f1eff5", INK)}.get(tone)
                         or (program_pdf._mix(accent, "#ffffff", 0.9), accent))
            shown = Table([[Paragraph(text(block.get("text", "")), ParagraphStyle("rbox", parent=body, textColor=_hex(ink), fontName="Helvetica-Bold", spaceAfter=0))]], colWidths=[width])
            shown.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), _hex(fill)), ("ROUNDEDCORNERS", [9, 9, 9, 9]),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                                       ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
            return keep([shown], inside) + [Spacer(1, 8)]
        if kind == "bars":
            return keep(chart_title(block) + [pieces["Bars"](block.get("labels") or [], block.get("series") or [], block.get("unit", ""), block.get("height", 180))], inside) + [Spacer(1, 8)]
        if kind == "donut":
            return keep(chart_title(block) + [pieces["Donut"](block.get("parts") or [], block.get("center"), block.get("height", 150))], inside) + [Spacer(1, 8)]
        if kind == "hbars":
            rows = block.get("rows") or []
            if not rows:
                return chart_title(block) + [Paragraph(text(block.get("empty") or "Nothing to show yet."), small)]
            return keep(chart_title(block) + [pieces["HBars"](rows, block.get("most"), block.get("unit", ""), block.get("tone", "brand"))], inside) + [Spacer(1, 8)]
        if kind == "table":
            return chart_title(block) + table(block, width) + [Spacer(1, 10)]
        if kind == "row":
            parts = block.get("blocks") or []
            gap = 16
            each = (width - gap * (len(parts) - 1)) / max(1, len(parts))
            cells = [[render(part, each, inside=True) for part in parts]]
            shown = Table(cells, colWidths=[each + (gap if index < len(parts) - 1 else 0) for index in range(len(parts))])
            shown.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), gap), ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                                       ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
            return [KeepTogether([shown]), Spacer(1, 6)]
        if kind == "empty":
            return [Paragraph(text(block.get("text") or "Nothing to show yet."), small), Spacer(1, 6)]
        if kind == "break":
            return [PageBreak()]
        if kind == "keep":
            return [CondPageBreak(block.get("room", 160))]
        return []

    story = []
    for block in blocks:
        story += render(block, inner)
    return story


def _when(moment: datetime | None = None) -> str:
    moment = moment or datetime.now()
    months = "January February March April May June July August September October November December".split()
    return f"{moment.day} {months[moment.month - 1]} {moment.year}, {moment:%H:%M}"


def build(db: Session, *, title: str, blocks: list[dict], overline: str = "Report", subtitle: str = "",
          landscape: bool = False, when: datetime | None = None) -> bytes:
    """The report as a PDF: a dark band with the centre and the title, then the blocks."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4, landscape as turned
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import BaseDocTemplate, Frame, NextPageTemplate, PageTemplate, Paragraph, Spacer

    accent = program_pdf.accent_of(db)
    size = turned(A4) if landscape else A4
    width, height = size
    left = 36
    inner = width - 2 * left
    title = program_pdf.plain(title)[:120] or "Report"
    subtitle = program_pdf.plain(subtitle)[:220]
    made = f"Made on {_when(when)}"
    contact = {"brand": BRAND, "website": program_pdf.WEBSITE, "email": program_pdf.plain(os.getenv("SMTP_EMAIL") or "")}
    title_size = 30 if stringWidth(title, "Helvetica-Bold", 30) <= width - 2 * left - 120 else 22
    band = 178 if subtitle else 156

    def footer(canvas):
        canvas.saveState()
        canvas.setStrokeColor(HexColor(LINE))
        canvas.setLineWidth(0.6)
        canvas.line(left, 34, width - left, 34)
        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica", 7.4)
        line = " · ".join(part for part in (contact["brand"], contact["website"], made) if part)
        canvas.drawString(left, 22, line)
        canvas.linkURL(program_pdf.SITE_URL, (left, 17, left + canvas.stringWidth(line, "Helvetica", 7.4), 31), relative=0)
        canvas.drawRightString(width - left, 22, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    def first(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(INK))
        canvas.rect(0, height - band, width, band, stroke=0, fill=1)
        clip = canvas.beginPath()
        clip.rect(0, height - band, width, band)
        canvas.clipPath(clip, stroke=0, fill=0)
        canvas.setFillColor(HexColor(accent))
        canvas.circle(width - 50, height - 20, 150, stroke=0, fill=1)
        canvas.setFillColor(program_pdf._mix(accent, INK, 0.45))
        canvas.circle(width + 10, height - band + 20, 96, stroke=0, fill=1)
        canvas.setFillColor(HexColor(GOLD))
        canvas.circle(width - 200, height - band + 38, 22, stroke=0, fill=1)
        canvas.setFillColor(program_pdf._mix(accent, "#ffffff", 0.3))
        canvas.circle(width - 250, height - 30, 6, stroke=0, fill=1)
        canvas.restoreState()
        program_pdf._logo(canvas, left, height - 66, 32, accent)
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 13.5)
        canvas.drawString(left + 42, height - 49, contact["brand"])
        canvas.setFillColor(HexColor("#cfc6e3"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(left + 42, height - 61, contact["website"])
        canvas.setFillColor(HexColor("#ffd9a8"))
        canvas.setFont("Helvetica-Bold", 7.6)
        canvas.drawString(left, height - 92, " ".join(program_pdf.plain(overline).upper()))
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", title_size)
        canvas.drawString(left, height - 92 - title_size - 4, title if title_size == 30 else title[:70])
        if subtitle:
            under = Paragraph(escape(subtitle), ParagraphStyle("under", fontName="Helvetica", fontSize=9.6, leading=12.6, textColor=HexColor("#e4dcf3")))
            _, used = under.wrap(width - 2 * left - 150, 40)
            under.drawOn(canvas, left, height - 92 - title_size - 14 - used)
        canvas.setFillColor(HexColor("#cfc6e3"))
        canvas.setFont("Helvetica", 7.8)
        canvas.drawRightString(width - left, height - band + 12, made)
        footer(canvas)

    def later(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(accent))
        canvas.rect(0, height - 7, width, 7, stroke=0, fill=1)
        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica-Bold", 7.8)
        canvas.drawString(left, height - 26, title[:100])
        canvas.restoreState()
        footer(canvas)

    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=size, title=title, author=contact["brand"], subject=overline, invariant=1,
                               leftMargin=left, rightMargin=left)
    document.addPageTemplates([
        PageTemplate(id="first", frames=[Frame(left, 46, inner, height - band - 20 - 46, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=first),
        PageTemplate(id="later", frames=[Frame(left, 46, inner, height - 46 - 40, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=later),
    ])
    story = [NextPageTemplate("later")] + _story(blocks, accent, inner)
    if len(story) == 1:
        story.append(Spacer(1, 1))
    document.build(story)
    return buffer.getvalue()


def filename(title: str, when: datetime | None = None) -> str:
    from app.services import email_files

    return email_files.safe_name(f"{program_pdf.plain(title)[:70] or 'Report'} - {(when or datetime.now()):%Y-%m-%d}.pdf", "report.pdf")
