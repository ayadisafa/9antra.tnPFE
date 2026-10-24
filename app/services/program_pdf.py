"""A program as a PDF: to download, to print, or to send to a candidate.

The same content in three looks (see STYLES), and several programs (or all
of them) in one catalogue: every program at a glance, then each one in detail. Everything written in it is the
program's own: its name, description, prices, rhythm, levels and weeks, plus
what the website says of it (level, format, schedule, certificate). A piece
that is not known is left out; nothing is invented.

The file is the same each time the program is the same, so one already sent
is recognised and kept once (see `store`).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import unicodedata
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.models.email_file import EmailFile
from app.models.program import Program
from app.models.website import WebsiteItem
from app.services import brand, email_files
from app.services.email_layout import BRAND, DEFAULT_ACCENT, GOLD, INK, clean_accent

WEBSITE = "9antra.tn"
DEFAULT_STYLE = "brochure"
STYLES = {
    "brochure": {"name": "Brochure", "icon": "bi-stars",
                 "what": "A colourful cover, the key figures as tiles, then the journey week by week. The one to send to a candidate."},
    "classic": {"name": "Classic", "icon": "bi-file-earmark-text",
                "what": "A sober document with tables. For a company, a partner or an official file."},
    "one_page": {"name": "One page", "icon": "bi-file-earmark-richtext",
                 "what": "Everything essential on a single page. For WhatsApp, a notice board, or a quick answer."},
}
LANGUAGES = {"fr": "Français", "en": "English"}

_WORDS = {
    "fr": {
        "overline": "Programme de formation", "for": "Préparé pour", "about": "À propos de la formation", "brief": "En bref",
        "duration": "Durée", "pace": "Rythme", "schedule": "Horaires", "level": "Niveau", "mode": "Format",
        "certificate": "Certificat", "certificate_yes": "Délivré en fin de formation", "tools": "Technologies et outils",
        "prices": "Tarifs", "online": "En ligne", "onsite": "En présentiel", "month": "par mois", "journey": "Le parcours, semaine par semaine",
        "content": "Contenu de la formation", "level_n": "Niveau", "week": "Semaine", "week_short": "S", "weeks": "semaines", "days": "jours",
        "per_week": "h par semaine", "teaching": "h de cours", "supervision": "h d'encadrement", "theme": "Thème", "objectives": "Objectifs",
        "join": "Inscription et renseignements", "join_text": "Pour vous inscrire ou poser une question, écrivez-nous ou rendez-vous sur",
        "page": "Page", "price_online": "Tarif en ligne", "price_onsite": "Tarif en présentiel",
        "catalogue": "Catalogue des formations", "catalogue_title": "Nos formations",
        "catalogue_sub": "{count} formations · le programme détaillé de chacune, semaine par semaine",
        "catalogue_one": "1 formation · son programme détaillé, semaine par semaine",
        "glance": "Toutes les formations en un coup d'œil", "program": "Formation", "number": "Formation {number} sur {count}",
        "summary": "Résumé des formations", "summary_title": "Nos formations en bref",
        "summary_sub": "{count} formations · l'essentiel de chacune", "path": "Parcours", "price_line": "{online} en ligne · {onsite} en présentiel",
        "welcome": "Bienvenue chez 9antra", "welcome_lead": "9antra est un centre de formation aux métiers du numérique. Nous proposons {count} formations{modes}, dans ces domaines :",
        "modes_both": " en ligne et en présentiel", "modes_online": " en ligne", "modes_onsite": " en présentiel",
        "fields": "Domaines", "formats": "Format", "format_both": "En ligne · Présentiel", "format_online": "En ligne", "format_onsite": "Présentiel",
        "reviews": "Avis", "reviews_note": "{rated} avis publiés", "programs_tile": "Formations",
        "how": "Comment se passe une formation",
        "steps": [("Vous vous inscrivez", "à la formation de votre choix, en ligne ou en présentiel."),
                  ("Un test de niveau", "avant le début, pour que le professeur sache d'où part chacun."),
                  ("Des séances avec un professeur de la spécialité", "chaque semaine, en suivant le parcours de la formation."),
                  ("Des questions chaque semaine", "pour vérifier ce qui est compris et revenir sur ce qui ne l'est pas.")],
        "said": "Nos anciens candidats donnent en moyenne {stars}/5, sur {rated} avis publiés sur 9antra.tn.",
        "more_box": "Les dates des prochaines sessions, les témoignages et l'inscription : sur notre site", "more": "En savoir plus sur 9antra.tn",
        "list_next": "Nos formations, l'une après l'autre, aux pages suivantes.",
    },
    "en": {
        "overline": "Training program", "for": "Prepared for", "about": "About the program", "brief": "At a glance",
        "duration": "Duration", "pace": "Pace", "schedule": "Schedule", "level": "Level", "mode": "Format",
        "certificate": "Certificate", "certificate_yes": "Given at the end of the program", "tools": "Technologies and tools",
        "prices": "Prices", "online": "Online", "onsite": "On site", "month": "a month", "journey": "The journey, week by week",
        "content": "What the program covers", "level_n": "Level", "week": "Week", "week_short": "W", "weeks": "weeks", "days": "days",
        "per_week": "h a week", "teaching": "h of class", "supervision": "h of supervision", "theme": "Topic", "objectives": "Objectives",
        "join": "Registration and questions", "join_text": "To register or ask a question, write to us or visit",
        "page": "Page", "price_online": "Price online", "price_onsite": "Price on site",
        "catalogue": "Programs catalogue", "catalogue_title": "Our programs",
        "catalogue_sub": "{count} programs · the detailed content of each one, week by week",
        "catalogue_one": "1 program · its detailed content, week by week",
        "glance": "All the programs at a glance", "program": "Program", "number": "Program {number} of {count}",
        "summary": "Programs in short", "summary_title": "Our programs in short",
        "summary_sub": "{count} programs · the essentials of each", "path": "Path", "price_line": "{online} online · {onsite} on site",
        "welcome": "Welcome to 9antra", "welcome_lead": "9antra is a training centre for the digital professions. We offer {count} programs{modes}, in these fields:",
        "modes_both": " online and on site", "modes_online": " online", "modes_onsite": " on site",
        "fields": "Fields", "formats": "Format", "format_both": "Online · On site", "format_online": "Online", "format_onsite": "On site",
        "reviews": "Reviews", "reviews_note": "{rated} published reviews", "programs_tile": "Programs",
        "how": "How a program goes",
        "steps": [("You register", "for the program of your choice, online or on site."),
                  ("A level check", "before it starts, so the professor knows where each one starts from."),
                  ("Sessions with a professor of the field", "every week, following the program's path."),
                  ("Questions every week", "to check what was understood and go back over what was not.")],
        "said": "Our former candidates give {stars}/5 on average, over {rated} reviews published on 9antra.tn.",
        "more_box": "The dates of the next sessions, the testimonials and registration: on our website", "more": "More on 9antra.tn",
        "list_next": "Our programs, one after the other, on the next pages.",
    },
}
_LEVEL_PREFIX = re.compile(r"^\s*(level|niveau)\s*\d+\s*[:\-–.]\s*", re.IGNORECASE)
_TOOL_NAMES = {"nodejs": "Node.js", "springboot": "Spring Boot", "reactjs": "React", "nextjs": "Next.js", "mongodb": "MongoDB",
               "javascript": "JavaScript", "typescript": "TypeScript", "powerbi": "Power BI", "mysql": "MySQL", "postgresql": "PostgreSQL",
               "html": "HTML", "css": "CSS", "sql": "SQL", "ai": "AI", "iot": "IoT", "seo": "SEO", "aws": "AWS", "cicd": "CI/CD",
               "githubactions": "GitHub Actions", "argocd": "ArgoCD", "googleads": "Google Ads", "kalilinux": "Kali Linux",
               "mqtt": "MQTT", "openai": "OpenAI", "tensorflow": "TensorFlow", "wordpress": "WordPress", "espressif": "ESP32 (Espressif)",
               "express": "Express", "sonarqube": "SonarQube", "pytorch": "PyTorch", "fastapi": "FastAPI", "langchain": "LangChain"}
_SWAPS = (("→", "->"), ("✓", ""), ("✔", ""), (" ", " "), (" ", " "), (" ", " "), ("‑", "-"))


def clean_style(value) -> str:
    return value if value in STYLES else DEFAULT_STYLE


def clean_language(value) -> str:
    return value if value in LANGUAGES else "fr"


def plain(text) -> str:
    """Text the PDF's own fonts can write (Western European letters and signs)."""
    text = unicodedata.normalize("NFC", str(text or ""))
    for old, new in _SWAPS:
        text = text.replace(old, new)
    return " ".join(text.encode("cp1252", "ignore").decode("cp1252").split())


def filename_of(program: Program, lang: str = "fr") -> str:
    word = "Programme" if lang == "fr" else "Program"
    return email_files.safe_name(f"{word} - {plain(program.name) or 'formation'}.pdf", "programme.pdf")


def catalogue_filename(lang: str, count: int, everything: bool, summary: bool = False) -> str:
    """"Catalogue des formations.pdf" for all of them, "Programmes - 3 formations.pdf" for a few; "Résumé…" for the short one."""
    if summary:
        name = ("Résumé des formations.pdf" if lang == "fr" else "Programs in short.pdf") if everything else \
               (f"Résumé - {count} formations.pdf" if lang == "fr" else f"In short - {count} programs.pdf")
    elif everything:
        name = "Catalogue des formations.pdf" if lang == "fr" else "Programs catalogue.pdf"
    else:
        name = f"Programmes - {count} formations.pdf" if lang == "fr" else f"Programs - {count} programs.pdf"
    return email_files.safe_name(name, "catalogue.pdf")


def _number(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def content_of(db: Session, program: Program, lang: str = "fr") -> dict:
    """What the PDF says, already in its language: {"name", "about", "facts", "prices", "tools", "levels", "weeks", "contact"}."""
    words = _WORDS[clean_language(lang)]
    formation = db.query(WebsiteItem).filter(
        WebsiteItem.kind == "formation", WebsiteItem.program_id == program.id, WebsiteItem.gone_at.is_(None)).first()
    site = formation.details if formation is not None else {}
    site_prices = site.get("price") if isinstance(site.get("price"), dict) else {}

    levels, number = [], 0
    for level in program.levels:
        weeks = []
        for week in level.weeks:
            number += 1
            points = [plain(part).rstrip(".") for part in re.split(r"[;\n]+", week.learning_objectives or "") if plain(part)]
            weeks.append({"number": number, "title": plain(week.topic_title) or f"{words['week']} {number}", "points": points})
        levels.append({
            "order": len(levels) + 1,
            "title": plain(_LEVEL_PREFIX.sub("", level.title or "")) or f"{words['level_n']} {len(levels) + 1}",
            "about": plain(level.description),
            "weeks": weeks,
        })

    facts = []          # (key, label, value, detail)
    site_duration, days = plain(site.get("duration")), program.total_duration_days
    if lang == "fr":
        duration = site_duration or (f"{number} {words['weeks']}" if number else (f"{days} {words['days']}" if days else ""))
    else:
        duration = (f"{number} {words['weeks']}" if number else "") or (f"{days} {words['days']}" if days else "") or site_duration
    if duration:
        facts.append(("duration", words["duration"], duration,
                      f"{number} {words['weeks']}" if number and str(number) not in duration else ""))
    if program.hours_per_week:
        parts = []
        if program.training_hours:
            parts.append(f"{_number(program.training_hours)} {words['teaching']}")
        if program.supervision_hours:
            parts.append(f"{_number(program.supervision_hours)} {words['supervision']}")
        facts.append(("pace", words["pace"], f"{_number(program.hours_per_week)} {words['per_week']}", " + ".join(parts)))
    if plain(site.get("level")):
        facts.append(("level", words["level"], plain(site.get("level")), ""))
    if plain(site.get("mode")):
        facts.append(("mode", words["mode"], plain(site.get("mode")), ""))
    own, public = plain(program.class_session_time), plain(site.get("schedule"))
    schedule = (public or own) if lang == "fr" else (own or public)
    if schedule:
        facts.append(("schedule", words["schedule"], schedule, ""))
    if site.get("certificate"):
        facts.append(("certificate", words["certificate"], words["certificate_yes"], ""))

    prices = []
    for key, amount in (("online", program.price_online), ("onsite", program.price_onsite)):
        if amount:
            monthly = "mois" in str(site_prices.get(key) or "").lower()
            prices.append({"label": words[key], "value": f"{amount:.0f} DT", "note": words["month"] if monthly else ""})

    tools = []
    for item in site.get("technologies") or []:
        name = plain(item) if isinstance(item, str) else ""
        if name:
            tools.append(_TOOL_NAMES.get(name.lower(), name.capitalize() if name.islower() else name))

    return {
        "name": plain(program.name), "about": plain(program.description), "facts": facts, "prices": prices, "tools": tools[:10],
        "levels": levels, "weeks": number,
        "url": (formation.url if formation is not None and formation.url else None),
        "contact": {"brand": BRAND, "website": WEBSITE, "email": plain(os.getenv("SMTP_EMAIL") or "")},
    }


SITE_URL = "https://" + WEBSITE


def intro_of(db: Session, programs: list[Program]) -> dict:
    """What the welcome page says: how many programs, in which fields, in which formats, what reviews say."""
    from app.services import feedback, level_bank

    fields = []
    for program in programs:
        subject = level_bank.subject_of(program.name, program.website_slug)
        name = level_bank.SUBJECTS[subject][0] if subject else None
        if name and name not in fields:
            fields.append(name)
    try:
        said = feedback.overview(db)
    except Exception:
        said = {}
    return {"count": len(programs), "fields": fields, "online": any(program.price_online for program in programs),
            "onsite": any(program.price_onsite for program in programs), "stars": said.get("stars"), "rated": said.get("rated") or 0}


def _welcome(intro: dict, words: dict, accent: str, styles: dict, inner: float) -> list:
    """The first page of the programs PDFs: who we are, how a program goes, and the website."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    Tiles, Chips, Badge = _flowables(accent)
    both, online = intro["online"] and intro["onsite"], intro["online"]
    modes = words["modes_both"] if both else words["modes_online"] if online else words["modes_onsite"] if intro["onsite"] else ""
    lead = ParagraphStyle("welcome_lead", parent=styles["lead"], fontSize=11.2, leading=16.4, spaceAfter=8)
    story = [Paragraph(escape(words["welcome"]), ParagraphStyle("welcome", parent=styles["h2"], fontSize=19, leading=23, spaceBefore=0, spaceAfter=8)),
             Paragraph(escape(words["welcome_lead"].format(count=intro["count"], modes=modes)), lead)]
    if intro["fields"]:
        story += [Chips([plain(name) for name in intro["fields"]], _mix(accent, "#ffffff", 0.88), HexColor(accent), size=8.6), Spacer(1, 12)]
    tiles = [(words["programs_tile"], str(intro["count"]), ""), (words["fields"], str(len(intro["fields"])) if intro["fields"] else "-", "")]
    tiles.append((words["formats"], words["format_both"] if both else words["format_online"] if online else words["format_onsite"], ""))
    if intro["stars"]:
        tiles.append((words["reviews"], f"{intro['stars']:.1f} / 5", words["reviews_note"].format(rated=intro["rated"])))
    story += [Tiles(tiles, height=56, border=HexColor("#ebe5f3")),
              Paragraph(escape(words["how"]), ParagraphStyle("how", parent=styles["h2"], spaceBefore=10))]
    step_style = ParagraphStyle("step", parent=styles["body"], fontSize=9.8, leading=13.6)
    rows = [[Badge(str(number)), Paragraph(f"<b>{escape(title)}</b> {escape(text)}", step_style)]
            for number, (title, text) in enumerate(words["steps"], start=1)]
    steps = Table(rows, colWidths=[34, inner - 34])
    steps.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(steps)
    if intro["stars"] and intro["rated"]:
        story += [Spacer(1, 6), Paragraph(escape(words["said"].format(stars=f"{intro['stars']:.1f}", rated=intro["rated"])), styles["lead"])]
    box = Table([[[Paragraph(escape(words["more_box"]), ParagraphStyle("box_title", fontName="Helvetica-Bold", fontSize=11.5, leading=15, textColor=white)),
                   Paragraph(f'<link href="{SITE_URL}" color="#ffd9a8"><u>{escape(SITE_URL)}</u></link>',
                             ParagraphStyle("box_link", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=HexColor("#ffd9a8"), spaceBefore=3))]]],
                colWidths=[inner])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(INK)), ("ROUNDEDCORNERS", [12, 12, 12, 12]),
                             ("LEFTPADDING", (0, 0), (-1, -1), 18), ("RIGHTPADDING", (0, 0), (-1, -1), 18),
                             ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 15)]))
    story += [Spacer(1, 10), KeepTogether([box])]
    return story


def _more_link(content: dict, words: dict, accent: str):
    """A link to the program's own page on 9antra.tn (or the website when it has none)."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    url = content.get("url") or SITE_URL
    return Paragraph(f'<link href="{escape(url)}" color="{accent}"><b>{escape(words["more"])} ›</b></link>',
                     ParagraphStyle("more", fontName="Helvetica", fontSize=8.6, leading=11.5, spaceBefore=5))


def accent_of(db: Session) -> str:
    from app.services.settings_service import get_settings

    try:
        return clean_accent(getattr(get_settings(db), "email_accent_color", None))
    except Exception:
        return DEFAULT_ACCENT


# ------------------------------------------------------------------
# Drawing
# ------------------------------------------------------------------

def _mix(first: str, second: str, share: float):
    """A colour between two: share 0 is the first, 1 the second."""
    from reportlab.lib.colors import Color, HexColor

    a, b = HexColor(first), HexColor(second)
    return Color(a.red + (b.red - a.red) * share, a.green + (b.green - a.green) * share, a.blue + (b.blue - a.blue) * share)


def _fit(text: str, font: str, size: float, width: float, smallest: float = 7) -> float:
    """The largest size, from `size` down, at which the text fits the width."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    while size > smallest and stringWidth(text, font, size) > width:
        size -= 0.5
    return size


def _logo(canvas, x: float, y: float, side: float, accent: str) -> None:
    """The centre's picture in a rounded square; its initial when there is none."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.utils import ImageReader

    canvas.saveState()
    path = canvas.beginPath()
    path.roundRect(x, y, side, side, side * 0.24)
    canvas.clipPath(path, stroke=0, fill=0)
    file = brand.FOLDER / "logo.png"
    drawn = False
    if file.is_file():
        try:
            canvas.drawImage(ImageReader(str(file)), x, y, side, side, mask="auto")
            drawn = True
        except Exception:
            drawn = False
    if not drawn:
        canvas.setFillColor(HexColor(accent))
        canvas.rect(x, y, side, side, stroke=0, fill=1)
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", side * 0.56)
        canvas.drawCentredString(x + side / 2, y + side * 0.3, BRAND[:1])
    canvas.restoreState()


def _flowables(accent: str):
    """The small drawn pieces the looks share. Built here so the PDF library is only needed when a PDF is made."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Flowable

    ink, muted = HexColor(INK), HexColor("#85809c")

    class Tiles(Flowable):
        """Figures side by side: a small label, the value, a note under it."""

        def __init__(self, items, height=58, fill=None, value_color=None, value_size=13, border=None):
            super().__init__()
            self.items, self.height = items, height
            self.fill, self.value_color, self.value_size, self.border = fill or white, value_color or ink, value_size, border

        def wrap(self, available_width, available_height):
            self.width = available_width
            return available_width, self.height

        def draw(self):
            canvas, count, gap = self.canv, len(self.items), 8
            width = (self.width - gap * (count - 1)) / count
            for index, (label, value, note) in enumerate(self.items):
                x = index * (width + gap)
                canvas.setFillColor(self.fill)
                if self.border is not None:
                    canvas.setStrokeColor(self.border)
                    canvas.setLineWidth(0.8)
                canvas.roundRect(x, 0, width, self.height, 9, stroke=1 if self.border is not None else 0, fill=1)
                canvas.setFillColor(muted)
                canvas.setFont("Helvetica-Bold", 6.8)
                canvas.drawString(x + 11, self.height - 16, label.upper())
                size = _fit(value, "Helvetica-Bold", self.value_size, width - 22, 7.5)
                shown = value
                while stringWidth(shown, "Helvetica-Bold", size) > width - 22 and len(shown) > 4:
                    shown = shown[:-2].rstrip() + "…"
                canvas.setFillColor(self.value_color)
                canvas.setFont("Helvetica-Bold", size)
                canvas.drawString(x + 11, self.height - 21 - size, shown)
                if note:
                    canvas.setFillColor(muted)
                    canvas.setFont("Helvetica", _fit(note, "Helvetica", 7.5, width - 22, 6))
                    canvas.drawString(x + 11, 9, note)

    class Chips(Flowable):
        """Words in rounded labels, wrapping to the width."""

        def __init__(self, words, fill, color, size=8.5):
            super().__init__()
            self.words, self.fill, self.color, self.size = words, fill, color, size
            self.rows = []

        def wrap(self, available_width, available_height):
            self.width, self.rows, row, used = available_width, [], [], 0
            for word in self.words:
                width = stringWidth(word, "Helvetica-Bold", self.size) + 18
                if row and used + width > available_width:
                    self.rows.append(row)
                    row, used = [], 0
                row.append((word, width))
                used += width + 6
            if row:
                self.rows.append(row)
            self.height = len(self.rows) * (self.size + 16)
            return available_width, self.height

        def draw(self):
            canvas, step = self.canv, self.size + 16
            for index, row in enumerate(self.rows):
                x, y = 0, self.height - (index + 1) * step + 5
                for word, width in row:
                    canvas.setFillColor(self.fill)
                    canvas.roundRect(x, y, width, self.size + 10, (self.size + 10) / 2, stroke=0, fill=1)
                    canvas.setFillColor(self.color)
                    canvas.setFont("Helvetica-Bold", self.size)
                    canvas.drawString(x + 9, y + 5.5, word)
                    x += width + 6

    class Badge(Flowable):
        """A week's number in a circle."""

        def __init__(self, text, side=24, fill=None):
            super().__init__()
            self.text, self.width, self.height, self.fill = text, side, side, fill or HexColor(accent)

        def draw(self):
            canvas, half = self.canv, self.width / 2
            canvas.setFillColor(self.fill)
            canvas.circle(half, half, half, stroke=0, fill=1)
            canvas.setFillColor(white)
            canvas.setFont("Helvetica-Bold", 9.5 if len(self.text) < 3 else 8)
            canvas.drawCentredString(half, half - 3.3, self.text)

    return Tiles, Chips, Badge


def _styles(accent: str, serif: bool = False) -> dict:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import ParagraphStyle

    bold, regular, italic = ("Times-Bold", "Times-Roman", "Times-Italic") if serif else ("Helvetica-Bold", "Helvetica", "Helvetica-Oblique")
    ink, text, muted = HexColor(INK), HexColor("#3a3350"), HexColor("#85809c")
    return {
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13.5, leading=17, textColor=ink, spaceBefore=16, spaceAfter=7),
        "body": ParagraphStyle("body", fontName=regular, fontSize=10.4, leading=15.2, textColor=text),
        "lead": ParagraphStyle("lead", fontName=italic if serif else regular, fontSize=11.4, leading=16.5, textColor=text),
        "small": ParagraphStyle("small", fontName=regular, fontSize=8.8, leading=12.4, textColor=muted),
        "over": ParagraphStyle("over", fontName="Helvetica-Bold", fontSize=7.4, leading=10, textColor=HexColor(accent)),
        "week": ParagraphStyle("week", fontName=bold, fontSize=10.6, leading=13.6, textColor=ink),
        "points": ParagraphStyle("points", fontName=regular, fontSize=9.3, leading=13, textColor=text, leftIndent=9, bulletIndent=0),
        "level": ParagraphStyle("level", fontName=bold, fontSize=12, leading=15, textColor=ink),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=9.4, leading=12.8, textColor=text),
        "cell_bold": ParagraphStyle("cell_bold", fontName=bold, fontSize=9.4, leading=12.8, textColor=ink),
        "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=white),
        "white": ParagraphStyle("white", fontName=regular, fontSize=9.6, leading=13.4, textColor=white),
    }


def _footer(canvas, content: dict, words: dict, accent: str) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4

    width = A4[0]
    canvas.saveState()
    canvas.setStrokeColor(HexColor("#e7e1f0"))
    canvas.setLineWidth(0.6)
    canvas.line(40, 40, width - 40, 40)
    canvas.setFillColor(HexColor("#85809c"))
    canvas.setFont("Helvetica", 7.8)
    contact = content["contact"]
    line = " · ".join(part for part in (contact["brand"], contact["website"], contact["email"]) if part)
    canvas.drawString(40, 27, line)
    if contact.get("website"):
        canvas.linkURL("https://" + contact["website"].removeprefix("https://"), (40, 22, 40 + canvas.stringWidth(line, "Helvetica", 7.8), 36), relative=0)
    canvas.drawRightString(width - 40, 27, f"{words['page']} {canvas.getPageNumber()}")
    canvas.restoreState()


def _cover_band(canvas, contact: dict, words: dict, accent: str, band: float, overline: str, title: str, subtitle: str,
                candidate: str = "", note: str = "") -> None:
    """The dark band at the top of a first page: the centre, a small overline, the title, a line under it, for whom."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph

    width, height = A4
    left = 40
    canvas.saveState()
    canvas.setFillColor(HexColor(INK))
    canvas.rect(0, height - band, width, band, stroke=0, fill=1)
    clip = canvas.beginPath()
    clip.rect(0, height - band, width, band)
    canvas.clipPath(clip, stroke=0, fill=0)
    canvas.setFillColor(HexColor(accent))
    canvas.circle(width - 44, height - 34, 168, stroke=0, fill=1)
    canvas.setFillColor(_mix(accent, INK, 0.45))
    canvas.circle(width + 16, height - band + 26, 104, stroke=0, fill=1)
    canvas.setFillColor(HexColor(GOLD))
    canvas.circle(width - 214, height - band + 50, 27, stroke=0, fill=1)
    canvas.restoreState()

    _logo(canvas, left, height - 76, 36, accent)
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(left + 46, height - 57, contact["brand"])
    canvas.setFillColor(HexColor("#cfc6e3"))
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(left + 46, height - 70, contact["website"])
    canvas.setFillColor(HexColor("#ffd9a8"))
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(left, height - 112, " ".join(overline.upper()))
    if stringWidth(title, "Helvetica-Bold", 34) <= 380:
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 34)
        canvas.drawString(left, height - 152, title)
        top = height - 166
    else:       # a longer title (a document's) goes on two lines, smaller
        for size in (26, 22, 19, 17):
            heading = Paragraph(escape(title), ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=size, leading=size * 1.12, textColor=white))
            _, used = heading.wrap(400, 120)
            if used <= size * 1.12 * 2 + 1:
                break
        heading.drawOn(canvas, left, height - 124 - used)
        top = height - 124 - used - 12
    under = Paragraph(escape(subtitle), ParagraphStyle("under", fontName="Helvetica", fontSize=10.5, leading=14, textColor=HexColor("#e4dcf3")))
    _, used = under.wrap(330, 60)
    under.drawOn(canvas, left, top - used)
    if candidate:
        label = f"{words['for']} {candidate}"
        canvas.setFont("Helvetica-Bold", 8.6)
        pill = canvas.stringWidth(label, "Helvetica-Bold", 8.6) + 20
        y = top - used - 32
        canvas.setFillColor(HexColor(GOLD))
        canvas.roundRect(left, y, pill, 19, 9.5, stroke=0, fill=1)
        canvas.setFillColor(white)
        canvas.drawString(left + 10, y + 6, label)
    if note:
        canvas.setFillColor(HexColor("#cfc6e3"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(width - left, height - band + 16, note)
    _footer(canvas, {"contact": contact}, words, accent)


def _running_head(canvas, content: dict, words: dict, accent: str, label: str) -> None:
    """The top of the pages after the first: a thin coloured line and the document's name."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4

    width, height = A4
    canvas.saveState()
    canvas.setFillColor(HexColor(accent))
    canvas.rect(0, height - 8, width, 8, stroke=0, fill=1)
    canvas.setFillColor(HexColor("#85809c"))
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(40, height - 32, label)
    canvas.restoreState()
    _footer(canvas, content, words, accent)


def _kept(db: Session, data: bytes, origin: str, filename: str) -> EmailFile:
    """A PDF kept as a file an email can carry. The same PDF is kept once: `origin` is its kind ("program:", "document:"…)."""
    origin = origin + hashlib.sha1(data).hexdigest()[:20]
    kept = db.query(EmailFile).filter(EmailFile.origin == origin).first()
    if kept is not None and email_files.path_of(kept).is_file():
        return kept
    file = email_files._store(db, filename=filename, content_type="application/pdf", data=data, is_image=False, origin=origin)
    db.commit()
    return file


def _points(week: dict, style) -> list:
    from reportlab.platypus import Paragraph

    return [Paragraph(escape(point), style, bulletText="•") for point in week["points"]]


def _brochure(content: dict, words: dict, accent: str, candidate: str) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import BaseDocTemplate, Frame, NextPageTemplate, PageTemplate, Paragraph, Spacer

    Tiles, _, _ = _flowables(accent)
    styles = _styles(accent)
    width, height = A4
    band, left, tile = 236, 40, 58
    tiles = [(label, value, detail) for key, label, value, detail in content["facts"] if key in ("duration", "pace", "level", "mode")][:4]

    def first_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(INK))
        canvas.rect(0, height - band, width, band, stroke=0, fill=1)
        clip = canvas.beginPath()
        clip.rect(0, height - band, width, band)
        canvas.clipPath(clip, stroke=0, fill=0)
        canvas.setFillColor(HexColor(accent))
        canvas.circle(width - 50, height - 30, 150, stroke=0, fill=1)
        canvas.setFillColor(_mix(accent, INK, 0.45))
        canvas.circle(width + 10, height - band + 20, 95, stroke=0, fill=1)
        canvas.setFillColor(HexColor(GOLD))
        canvas.circle(width - 196, height - band + 46, 26, stroke=0, fill=1)
        canvas.restoreState()

        _logo(canvas, left, height - 76, 36, accent)
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawString(left + 46, height - 57, content["contact"]["brand"])
        canvas.setFillColor(HexColor("#cfc6e3"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(left + 46, height - 70, content["contact"]["website"])

        canvas.setFillColor(HexColor("#ffd9a8"))
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(left, height - 108, " ".join(words["overline"].upper()))
        size = 28 if len(content["name"]) <= 24 else 24 if len(content["name"]) <= 44 else 20
        title = Paragraph(escape(content["name"]), ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=size, leading=size * 1.14, textColor=white))
        _, used = title.wrap(360, 120)
        title.drawOn(canvas, left, height - 120 - used)
        if candidate:
            label = f"{words['for']} {candidate}"
            canvas.setFont("Helvetica-Bold", 8.6)
            pill = canvas.stringWidth(label, "Helvetica-Bold", 8.6) + 20
            y = height - 120 - used - 30
            canvas.setFillColor(HexColor(GOLD))
            canvas.roundRect(left, y, pill, 19, 9.5, stroke=0, fill=1)
            canvas.setFillColor(white)
            canvas.drawString(left + 10, y + 6, label)
        if tiles:
            canvas.saveState()
            canvas.translate(left, height - band - tile / 2)
            canvas.setFillColor(HexColor("#1b1030"))
            canvas.setFillAlpha(0.10)
            canvas.roundRect(1, -3, width - 2 * left - 2, tile, 10, stroke=0, fill=1)
            canvas.setFillAlpha(1)
            row = Tiles(tiles, height=tile, border=HexColor("#ebe5f3"))
            row.wrap(width - 2 * left, tile)
            row.drawOn(canvas, 0, 0)
            canvas.restoreState()
        _footer(canvas, content, words, accent)

    def later_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(accent))
        canvas.rect(0, height - 8, width, 8, stroke=0, fill=1)
        canvas.setFillColor(HexColor("#85809c"))
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(left, height - 32, content["name"])
        canvas.restoreState()
        _footer(canvas, content, words, accent)

    top = height - band - (tile / 2 + 14 if tiles else 12)
    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=A4, title=content["name"], author=content["contact"]["brand"],
                               subject=words["overline"], invariant=1)
    document.addPageTemplates([
        PageTemplate(id="first", frames=[Frame(left, 52, width - 2 * left, top - 52, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=first_page),
        PageTemplate(id="later", frames=[Frame(left, 52, width - 2 * left, height - 52 - 46, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=later_page),
    ])

    story = [NextPageTemplate("later")] + _program_story(content, words, accent, styles, width - 2 * left)
    story += [Spacer(1, 18), _closing(content, words, styles, width - 2 * left)]
    document.build(story)
    return buffer.getvalue()


def _program_story(content: dict, words: dict, accent: str, styles: dict, inner: float) -> list:
    """What the brochure says of one program under its cover: about, schedule, tools, prices, then the journey week by week."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    Tiles, Chips, Badge = _flowables(accent)
    soft, softer = _mix(accent, "#ffffff", 0.9), _mix(accent, "#ffffff", 0.95)
    story = []
    if content["about"]:
        story += [Paragraph(escape(words["about"]), styles["h2"]), Paragraph(escape(content["about"]), styles["lead"])]
    rest = [(label, value, detail) for key, label, value, detail in content["facts"] if key in ("schedule", "certificate")]
    if rest:
        rows = [[Paragraph(escape(label.upper()), styles["over"]), Paragraph(escape(value), styles["body"])] for label, value, _ in rest]
        table = Table(rows, colWidths=[86, inner - 86])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                   ("LEFTPADDING", (0, 0), (0, -1), 0), ("LINEBELOW", (0, 0), (-1, -2), 0.5, HexColor("#eee9f5"))]))
        story += [Spacer(1, 10), table]
    if content["tools"]:
        story += [Paragraph(escape(words["tools"]), styles["h2"]), Chips(content["tools"], soft, HexColor(accent))]
    if content["prices"]:
        story += [Paragraph(escape(words["prices"]), styles["h2"]),
                  Tiles([(item["label"], item["value"], item["note"]) for item in content["prices"]], height=62, fill=softer,
                        value_color=HexColor(accent), value_size=19)]
    if content["levels"]:
        story.append(Paragraph(escape(words["journey"]), styles["h2"]))
    for level in content["levels"]:
        head = [Paragraph(escape(f"{words['level_n']} {level['order']}".upper()), styles["over"]), Paragraph(escape(level["title"]), styles["level"])]
        if level["about"]:
            head.append(Paragraph(escape(level["about"]), styles["small"]))
        box = Table([[head]], colWidths=[inner])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), soft), ("ROUNDEDCORNERS", [9, 9, 9, 9]), ("LEFTPADDING", (0, 0), (-1, -1), 14),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 14), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        rows = []
        for week in level["weeks"]:
            row = Table([[Badge(str(week["number"])), [Paragraph(escape(week["title"]), styles["week"])] + _points(week, styles["points"])]],
                        colWidths=[36, inner - 36])
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 2), ("TOPPADDING", (0, 0), (-1, -1), 7),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LINEBELOW", (1, 0), (1, 0), 0.5, HexColor("#eee9f5"))]))
            rows.append(row)
        story += [Spacer(1, 8), KeepTogether([box] + rows[:1])] + rows[1:]
    return story


def _closing(content: dict, words: dict, styles: dict, inner: float):
    """The dark box at the end: how to register or ask a question."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, Paragraph, Table, TableStyle

    contact = content["contact"]
    reach = " · ".join(part for part in (contact["website"], contact["email"]) if part)
    closing = Table([[[Paragraph(escape(words["join"]), ParagraphStyle("join", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=white)),
                       Paragraph(escape(f"{words['join_text']} {reach}."), styles["white"])]]], colWidths=[inner])
    closing.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(INK)), ("ROUNDEDCORNERS", [11, 11, 11, 11]), ("LEFTPADDING", (0, 0), (-1, -1), 18),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 18), ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 15)]))
    return KeepTogether([closing])


def _classic(content: dict, words: dict, accent: str, candidate: str) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

    styles = _styles(accent, serif=True)
    width, height = A4
    left = 54
    line = HexColor("#d9d2e6")

    def page(canvas, doc):
        canvas.saveState()
        _logo(canvas, left, height - 62, 24, accent)
        canvas.setFillColor(HexColor(INK))
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(left + 32, height - 54, content["contact"]["brand"])
        canvas.setFillColor(HexColor("#85809c"))
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(width - left, height - 54, words["overline"])
        canvas.setStrokeColor(HexColor(accent))
        canvas.setLineWidth(1.4)
        canvas.line(left, height - 70, width - left, height - 70)
        canvas.restoreState()
        _footer(canvas, content, words, accent)

    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=A4, title=content["name"], author=content["contact"]["brand"],
                               subject=words["overline"], invariant=1)
    document.addPageTemplates([PageTemplate(id="page", frames=[Frame(left, 54, width - 2 * left, height - 54 - 84, leftPadding=0, rightPadding=0,
                                                                     topPadding=0, bottomPadding=0)], onPage=page)])
    inner = width - 2 * left
    story = [Paragraph(escape(content["name"]), ParagraphStyle("title", fontName="Times-Bold", fontSize=23, leading=27, textColor=HexColor(INK), spaceAfter=6))]
    if candidate:
        story.append(Paragraph(escape(f"{words['for']} {candidate}"), styles["small"]))
    if content["about"]:
        story += [Spacer(1, 6), Paragraph(escape(content["about"]), styles["lead"])]

    rows = [[Paragraph(escape(label), styles["cell_bold"]), Paragraph(escape(value + (f" ({detail})" if detail else "")), styles["cell"])]
            for _, label, value, detail in content["facts"]]
    for key, item in zip(("price_online", "price_onsite"), content["prices"]):
        label = words["price_online"] if item["label"] == words["online"] else words["price_onsite"]
        rows.append([Paragraph(escape(label), styles["cell_bold"]), Paragraph(escape(f"{item['value']} {item['note']}".strip()), styles["cell"])])
    if content["tools"]:
        rows.append([Paragraph(escape(words["tools"]), styles["cell_bold"]), Paragraph(escape(", ".join(content["tools"])), styles["cell"])])
    if rows:
        table = Table(rows, colWidths=[132, inner - 132])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, line), ("BACKGROUND", (0, 0), (0, -1), HexColor("#f6f3fb")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 9)]))
        story += [Paragraph(escape(words["brief"]), styles["h2"]), table]

    if content["levels"]:
        story.append(Paragraph(escape(words["content"]), styles["h2"]))
    for level in content["levels"]:
        story.append(Paragraph(escape(f"{words['level_n']} {level['order']} : {level['title']}" if words["level_n"] == "Niveau" else f"{words['level_n']} {level['order']}: {level['title']}"),
                               ParagraphStyle("level", fontName="Times-Bold", fontSize=12, leading=15, textColor=HexColor(accent), spaceBefore=8, spaceAfter=3)))
        if level["about"]:
            story += [Paragraph(escape(level["about"]), styles["small"]), Spacer(1, 5)]
        rows = [[Paragraph(escape(words["week"]), styles["head"]), Paragraph(escape(words["theme"]), styles["head"]), Paragraph(escape(words["objectives"]), styles["head"])]]
        for week in level["weeks"]:
            rows.append([Paragraph(str(week["number"]), styles["cell_bold"]), Paragraph(escape(week["title"]), styles["cell_bold"]),
                         [Paragraph(escape(point), styles["cell"], bulletText="–") for point in week["points"]] or Paragraph("", styles["cell"])])
        table = Table(rows, colWidths=[48, 150, inner - 198], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HexColor(INK)), ("GRID", (0, 0), (-1, -1), 0.5, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), HexColor("#faf8fd")]),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        story.append(table)
    contact = content["contact"]
    reach = " · ".join(part for part in (contact["website"], contact["email"]) if part)
    story += [Spacer(1, 16), Paragraph(f"<b>{escape(words['join'])}.</b> {escape(words['join_text'])} {escape(reach)}.", styles["body"])]
    document.build(story)
    return buffer.getvalue()


def _one_page(content: dict, words: dict, accent: str, candidate: str) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Frame, KeepInFrame, Paragraph, Spacer, Table, TableStyle

    _, Chips, _ = _flowables(accent)
    styles = _styles(accent)
    width, height = A4
    side = 196
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    canvas.setTitle(content["name"])
    canvas.setAuthor(content["contact"]["brand"])
    canvas.setSubject(words["overline"])

    canvas.saveState()
    canvas.setFillColor(HexColor(INK))
    canvas.rect(0, 0, side, height, stroke=0, fill=1)
    clip = canvas.beginPath()
    clip.rect(0, 0, side, height)
    canvas.clipPath(clip, stroke=0, fill=0)
    canvas.setFillColor(HexColor(accent))
    canvas.circle(side + 14, height + 6, 78, stroke=0, fill=1)
    canvas.setFillColor(HexColor(GOLD))
    canvas.circle(18, 40, 30, stroke=0, fill=1)
    canvas.restoreState()
    _logo(canvas, 24, height - 66, 32, accent)
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(64, height - 49, content["contact"]["brand"])

    label = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=6.8, leading=9, textColor=HexColor("#ffd9a8"), spaceBefore=11)
    value = ParagraphStyle("value", fontName="Helvetica-Bold", fontSize=10.2, leading=13, textColor=white)
    note = ParagraphStyle("note", fontName="Helvetica", fontSize=8, leading=10.5, textColor=HexColor("#cfc6e3"))
    size = 20 if len(content["name"]) <= 26 else 17 if len(content["name"]) <= 44 else 15
    column = [Paragraph(escape(" ".join(words["overline"].upper()).replace("   ", "\u00a0\u00a0\u00a0")), ParagraphStyle("over", fontName="Helvetica-Bold", fontSize=6.6, leading=9, textColor=HexColor("#ffd9a8"))),
              Spacer(1, 5),
              Paragraph(escape(content["name"]), ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=size, leading=size * 1.16, textColor=white))]
    if candidate:
        column += [Spacer(1, 6), Paragraph(escape(f"{words['for']} {candidate}"), note)]
    column.append(Spacer(1, 6))
    for _, name, text, detail in content["facts"]:
        column += [Paragraph(escape(name.upper()), label), Paragraph(escape(text), value)]
        if detail:
            column.append(Paragraph(escape(detail), note))
    for item in content["prices"]:
        column += [Paragraph(escape(f"{words['prices']} · {item['label']}".upper()), label),
                   Paragraph(escape(f"{item['value']} {item['note']}".strip()), ParagraphStyle("price", parent=value, fontSize=13, leading=16))]
    contact = content["contact"]
    column += [Spacer(1, 16), Paragraph(escape(words["join"].upper()), label), Paragraph(escape(contact["website"]), value)]
    if contact["email"]:
        column.append(Paragraph(escape(contact["email"]), note))
    Frame(22, 30, side - 40, height - 30 - 84, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0).addFromList(
        [KeepInFrame(side - 40, height - 30 - 84, column, mode="shrink")], canvas)

    inner = width - side - 60
    main = []
    if content["about"]:
        main += [Paragraph(escape(words["about"]), styles["h2"]), Paragraph(escape(content["about"]), styles["body"])]
    if content["tools"]:
        main += [Spacer(1, 9), Chips(content["tools"], _mix(accent, "#ffffff", 0.9), HexColor(accent), size=8)]
    if content["levels"]:
        main.append(Paragraph(escape(words["journey"]), styles["h2"]))
    week_style = ParagraphStyle("line", fontName="Helvetica", fontSize=9.6, leading=13.4, textColor=HexColor("#3a3350"))
    roomy = content["weeks"] <= 10
    for level in content["levels"]:
        main.append(Paragraph(escape(f"{words['level_n']} {level['order']} · {level['title']}"),
                              ParagraphStyle("lvl", fontName="Helvetica-Bold", fontSize=10.6, leading=14, textColor=HexColor(accent), spaceBefore=9, spaceAfter=3)))
        rows = []
        for week in level["weeks"]:
            cell = [Paragraph(escape(week["title"]), week_style)]
            if roomy and week["points"]:       # a short program has room for what each week covers
                cell.append(Paragraph(escape(" · ".join(week["points"])), styles["small"]))
            rows.append([Paragraph(f"<b>{words['week_short']}{week['number']}</b>", week_style), cell])
        if rows:
            table = Table(rows, colWidths=[30, inner - 30])
            table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 0), ("LINEBELOW", (0, 0), (-1, -2), 0.4, HexColor("#eee9f5"))]))
            main.append(table)
    Frame(side + 30, 34, inner, height - 34 - 34, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0).addFromList(
        [KeepInFrame(inner, height - 68, main, mode="shrink")], canvas)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _catalogue(contents: list[dict], words: dict, accent: str, candidate: str, contact: dict, intro: dict | None = None) -> bytes:
    """Several programs in one document: a cover with all of them at a glance, then each one as the brochure tells it."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import BaseDocTemplate, Flowable, Frame, NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

    Tiles, _, _ = _flowables(accent)
    styles = _styles(accent)
    width, height = A4
    band, left = 250, 40
    inner = width - 2 * left
    count = len(contents)
    page_content = {"contact": contact}

    class Head(Flowable):
        """The first lines of one program: its number and its name on a dark band."""

        def __init__(self, number: int, name: str):
            super().__init__()
            self.number, self.name, self.height = number, name, 86

        def wrap(self, available_width, available_height):
            self.width = available_width
            return available_width, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            path = canvas.beginPath()
            path.roundRect(0, 0, self.width, self.height, 14)
            canvas.clipPath(path, stroke=0, fill=0)
            canvas.setFillColor(HexColor(INK))
            canvas.rect(0, 0, self.width, self.height, stroke=0, fill=1)
            canvas.setFillColor(HexColor(accent))
            canvas.circle(self.width - 26, self.height + 14, 74, stroke=0, fill=1)
            canvas.setFillColor(HexColor(GOLD))
            canvas.circle(self.width - 128, -4, 15, stroke=0, fill=1)
            canvas.restoreState()
            canvas.setFillColor(HexColor(GOLD))
            canvas.setFont("Helvetica-Bold", 30)
            canvas.drawString(20, self.height / 2 - 11, f"{self.number:02d}")
            canvas.setFillColor(HexColor("#ffd9a8"))
            canvas.setFont("Helvetica-Bold", 7.4)
            canvas.drawString(84, self.height - 26, words["number"].format(number=self.number, count=count).upper())
            room = self.width - 84 - 30
            for size in (19, 17, 15, 13, 12):
                title = Paragraph(escape(self.name), ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=size, leading=size * 1.15, textColor=white))
                _, used = title.wrap(room, 60)
                if used <= size * 1.15 * 2 + 1:
                    break
            title.drawOn(canvas, 84, self.height - 34 - used)

    def cover(canvas, doc):
        line = words["catalogue_sub"].format(count=count) if count != 1 else words["catalogue_one"]
        _cover_band(canvas, contact, words, accent, band, words["catalogue"], words["catalogue_title"], line, candidate)

    def later(canvas, doc):
        _running_head(canvas, page_content, words, accent, words["catalogue"])

    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=A4, title=words["catalogue"], author=contact["brand"], subject=words["catalogue"], invariant=1)
    document.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(left, 52, inner, height - band - 22 - 52, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=cover),
        PageTemplate(id="later", frames=[Frame(left, 52, inner, height - 52 - 46, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=later),
    ])

    # All of them at a glance: how long, how much a week, and the two prices.
    head = ParagraphStyle("head", parent=styles["head"])
    price = ParagraphStyle("price", parent=styles["cell_bold"], textColor=HexColor(accent))
    rows = [[Paragraph(escape(text.upper()), head) for text in (words["program"], words["duration"], words["pace"], words["online"], words["onsite"])]]
    for content in contents:
        facts = {key: value for key, _, value, _ in content["facts"]}
        amounts = {item["label"]: item["value"] for item in content["prices"]}
        rows.append([Paragraph(escape(content["name"]), styles["cell_bold"]), Paragraph(escape(facts.get("duration") or "-"), styles["cell"]),
                     Paragraph(escape(facts.get("pace") or "-"), styles["cell"]), Paragraph(escape(amounts.get(words["online"]) or "-"), price),
                     Paragraph(escape(amounts.get(words["onsite"]) or "-"), price)])
    glance = Table(rows, colWidths=[inner - 4 * 82, 82, 82, 82, 82], repeatRows=1)
    glance.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HexColor(INK)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), _mix(accent, "#ffffff", 0.95)]),
                                ("LINEBELOW", (0, 1), (-1, -1), 0.5, HexColor("#eee9f5")), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("ROUNDEDCORNERS", [8, 8, 0, 0])]))
    story = [NextPageTemplate("later")]
    if intro:
        story += _welcome(intro, words, accent, styles, inner) + [PageBreak()]
    story += [Paragraph(escape(words["glance"]), styles["h2"]), glance]

    for number, content in enumerate(contents, start=1):
        story += [PageBreak(), Head(number, content["name"]), Spacer(1, 12)]
        tiles = [(label, value, detail) for key, label, value, detail in content["facts"] if key in ("duration", "pace", "level", "mode")][:4]
        if tiles:
            story.append(Tiles(tiles, height=58, border=HexColor("#ebe5f3")))
        story += _program_story(content, words, accent, styles, inner)
        story.append(_more_link(content, words, accent))
    story += [Spacer(1, 18), _closing(page_content, words, styles, inner)]
    document.build(story)
    return buffer.getvalue()


def _short(text: str, most: int = 260) -> str:
    """A description cut at a word, with "…" when something was left out."""
    text = " ".join((text or "").split())
    if len(text) <= most:
        return text
    cut = text[:most].rsplit(" ", 1)[0].rstrip(",;:.")
    return cut + "…"


def _summary(contents: list[dict], words: dict, accent: str, candidate: str, contact: dict, intro: dict | None = None) -> bytes:
    """Every program in a few lines: what it teaches, how long, the path, the tools and the two prices."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageTemplate, Paragraph, Spacer, Table, TableStyle

    _, _, Badge = _flowables(accent)
    styles = _styles(accent)
    width, height = A4
    band, left = 236, 40
    inner = width - 2 * left
    count = len(contents)
    page_content = {"contact": contact}

    def cover(canvas, doc):
        line = words["summary_sub"].format(count=count) if count != 1 else words["catalogue_one"]
        _cover_band(canvas, contact, words, accent, band, words["summary"], words["summary_title"], line, candidate)

    def later(canvas, doc):
        _running_head(canvas, page_content, words, accent, words["summary"])

    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=A4, title=words["summary"], author=contact["brand"], subject=words["summary"], invariant=1)
    document.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(left, 52, inner, height - band - 20 - 52, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=cover),
        PageTemplate(id="later", frames=[Frame(left, 52, inner, height - 52 - 46, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=later),
    ])
    name_style = ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=12.5, leading=15.5, textColor=HexColor(INK))
    facts_style = ParagraphStyle("facts", parent=styles["small"], fontName="Helvetica-Bold", textColor=HexColor("#6b6584"), spaceBefore=1)
    about_style = ParagraphStyle("about", parent=styles["body"], fontSize=9.6, leading=13.6, spaceBefore=5)
    path_style = ParagraphStyle("path", parent=styles["small"], spaceBefore=5)
    tools_style = ParagraphStyle("tools", parent=styles["small"], fontName="Helvetica-Bold", textColor=HexColor(accent), spaceBefore=4)
    price_big = ParagraphStyle("price", fontName="Helvetica-Bold", fontSize=14, leading=16, textColor=HexColor(accent), alignment=2)
    price_note = ParagraphStyle("note", parent=styles["small"], fontSize=7.6, leading=9.5, alignment=2)

    story = [NextPageTemplate("later")]
    if intro:
        from reportlab.platypus import PageBreak

        story += _welcome(intro, words, accent, styles, inner) + [PageBreak()]
    for number, content in enumerate(contents, start=1):
        facts = {key: value for key, _, value, _ in content["facts"]}
        said = " · ".join(part for part in (facts.get("duration"), facts.get("pace"), facts.get("level"), facts.get("mode")) if part)
        middle = [Paragraph(escape(content["name"]), name_style)]
        if said:
            middle.append(Paragraph(escape(said), facts_style))
        if content["about"]:
            middle.append(Paragraph(escape(_short(content["about"])), about_style))
        if content["levels"]:
            path = " › ".join(level["title"] for level in content["levels"])     # the PDF's fonts have "›", not an arrow
            middle.append(Paragraph(f"<b>{escape(words['path'])}</b> : {escape(_short(path, 180))}" if words["level_n"] == "Niveau"
                                    else f"<b>{escape(words['path'])}</b>: {escape(_short(path, 180))}", path_style))
        if content["tools"]:
            middle.append(Paragraph(escape(" · ".join(content["tools"][:7])), tools_style))
        middle.append(_more_link(content, words, accent))
        prices = []
        for item in content["prices"]:
            prices += [Paragraph(escape(item["value"]), price_big), Paragraph(escape(f"{item['label']} {item['note']}".strip()), price_note), Spacer(1, 6)]
        row = Table([[Badge(str(number), side=26), middle, prices]], colWidths=[40, inner - 40 - 96, 96])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0), ("LEFTPADDING", (1, 0), (1, 0), 4),
                                 ("RIGHTPADDING", (2, 0), (2, 0), 0), ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                                 ("LINEBELOW", (0, 0), (-1, -1), 0.6, HexColor("#eee9f5"))]))
        story.append(KeepTogether([row]))
    story += [Spacer(1, 18), _closing(page_content, words, styles, inner)]
    document.build(story)
    return buffer.getvalue()


_MAKERS = {"brochure": _brochure, "classic": _classic, "one_page": _one_page}


def available() -> bool:
    """Whether the PDF library is installed on this machine."""
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def build(db: Session, program: Program, style: str = DEFAULT_STYLE, lang: str = "fr", candidate_name: str | None = None) -> bytes:
    """The program's PDF in one of the looks."""
    lang = clean_language(lang)
    content = content_of(db, program, lang)
    return _MAKERS[clean_style(style)](content, _WORDS[lang], accent_of(db), plain(candidate_name)[:60])


def store(db: Session, program: Program, style: str = DEFAULT_STYLE, lang: str = "fr", candidate_name: str | None = None) -> EmailFile:
    """The PDF kept as a file an email can carry. The same PDF is kept once."""
    return _kept(db, build(db, program, style, lang, candidate_name), "program:", filename_of(program, clean_language(lang)))


def _chosen(db: Session, programs) -> list[Program]:
    """The programs asked for, in the order of their names; every program when none is given."""
    chosen = list(programs) if programs else db.query(Program).all()
    return sorted(chosen, key=lambda program: (program.name or "").lower())


def build_catalogue(db: Session, programs=None, lang: str = "fr", candidate_name: str | None = None, welcome: bool = True) -> bytes:
    """Several programs, or all of them, in one PDF, after a welcome page about 9antra."""
    lang = clean_language(lang)
    chosen = _chosen(db, programs)
    if not chosen:
        raise ValueError("There is no program to put in the PDF.")
    contents = [content_of(db, program, lang) for program in chosen]
    intro = intro_of(db, chosen) if welcome else None
    return _catalogue(contents, _WORDS[lang], accent_of(db), plain(candidate_name)[:60], contents[0]["contact"], intro)


def store_catalogue(db: Session, programs=None, lang: str = "fr", candidate_name: str | None = None, summary: bool = False) -> EmailFile:
    """The catalogue (or the short summary) kept as a file an email can carry. The same PDF is kept once."""
    lang = clean_language(lang)
    chosen = _chosen(db, programs)
    data = (build_summary if summary else build_catalogue)(db, chosen, lang, candidate_name)
    everything = len(chosen) >= db.query(Program).count()
    return _kept(db, data, "program:summary-" if summary else "program:catalogue-", catalogue_filename(lang, len(chosen), everything, summary))


def build_summary(db: Session, programs=None, lang: str = "fr", candidate_name: str | None = None, welcome: bool = True) -> bytes:
    """Several programs, or all of them, in short: a welcome page about 9antra, then the essentials of each."""
    lang = clean_language(lang)
    chosen = _chosen(db, programs)
    if not chosen:
        raise ValueError("There is no program to put in the PDF.")
    contents = [content_of(db, program, lang) for program in chosen]
    intro = intro_of(db, chosen) if welcome else None
    return _summary(contents, _WORDS[lang], accent_of(db), plain(candidate_name)[:60], contents[0]["contact"], intro)


def message_for_summary(count: int) -> dict:
    """The email that carries the short summary of the programs."""
    return {
        "subject": "Nos formations en bref",
        "title": "Nos formations en bref",
        "body": (
            "Bonjour {first_name},\n\n"
            f"Vous trouverez en pièce jointe un résumé de nos {count} formations : pour chacune, ce qu'elle enseigne, "
            "sa durée, son rythme et ses tarifs.\n\n"
            "Si l'une d'elles vous intéresse, répondez simplement à cet email : nous vous enverrons son programme détaillé."
        ),
    }


def message_for_catalogue(count: int) -> dict:
    """The email that carries several programs in one PDF, ready for the admin to read and change before sending."""
    return {
        "subject": "Le programme détaillé de nos formations",
        "title": "Nos formations",
        "body": (
            "Bonjour {first_name},\n\n"
            f"Vous trouverez en pièce jointe le programme détaillé de nos {count} formations, réunies dans un seul document : "
            "pour chacune, le contenu semaine par semaine, le rythme et les tarifs.\n\n"
            "Si vous avez la moindre question, répondez simplement à cet email."
        ),
    }


def message_for(program: Program) -> dict:
    """The email that carries the PDF, ready for the admin to read and change before sending."""
    name = plain(program.name).replace("{", "(").replace("}", ")")
    return {
        "subject": f"Le programme de la formation {name}",
        "title": "Votre programme de formation",
        "body": (
            "Bonjour {first_name},\n\n"
            f"Vous trouverez en pièce jointe le programme détaillé de la formation **{name}** : "
            "le contenu semaine par semaine, le rythme et les tarifs.\n\n"
            "Si vous avez la moindre question, répondez simplement à cet email."
        ),
    }
