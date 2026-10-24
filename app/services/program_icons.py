"""The picture shown next to each program.

It is looked up on the web from the program's name (Iconify, a public
catalogue of open-source icons), saved in the program icons folder
(PROGRAM_ICONS_DIR, default: data/program_icons) and served from here
afterwards, so pages keep their pictures when the network is down. The
admin can choose another one from the same catalogue or upload their own.

Every program gets one, whatever its name: the website's technologies for it
help the search; when nothing fits, a general picture of learning is used;
when the web cannot be reached, one is drawn here (its initials on its
colours) and a better one is looked for later. It starts the moment a
program is created, by anyone or anything (install_hooks).
"""
from __future__ import annotations

import io
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.models.program import Program

logger = logging.getLogger(__name__)

API = os.getenv("ICON_API_URL", "https://api.iconify.design").rstrip("/")
TIMEOUT = 8.0
MAX_ICON_BYTES = 300 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
RETRY_AFTER = timedelta(hours=12)       # when the web could not be reached or had nothing
BRAND_COLOR = "#a42c67"

# Sets tried in this order. Logos of technologies first, then flat colour
# pictures; one-colour sets come last and are tinted with the brand colour.
TECH_SETS = ["logos", "devicon", "skill-icons", "vscode-icons"]
COLOUR_SETS = ["fluent-emoji-flat", "noto", "twemoji", "flat-color-icons", "fluent-color", "emojione", "openmoji"]
PLAIN_SETS = ["tabler", "ph", "mdi", "lucide", "solar"]

UPLOAD_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
}
_MEDIA_TYPES = {extension.lstrip("."): media for extension, media in UPLOAD_TYPES.items()}

# What to look for, from words found in a program's name. Technologies are
# searched among logos, subjects among pictures.
_TECHNOLOGIES = [
    (r"flutter", "flutter"), (r"react", "react"), (r"angular", "angular"), (r"vue", "vuejs"),
    (r"spring", "spring"), (r"node", "nodejs"), (r"\bjs\b|javascript", "javascript"),
    (r"typescript|\bts\b", "typescript"), (r"python", "python"), (r"django", "django"),
    (r"\bjava\b", "java"), (r"kotlin", "kotlin"), (r"swift", "swift"), (r"android", "android"),
    (r"\bphp\b", "php"), (r"laravel", "laravel"), (r"symfony", "symfony"), (r"\.net|dotnet|c#", "dotnet"),
    (r"docker|devops", "docker"), (r"kubernetes", "kubernetes"), (r"\baws\b", "aws"), (r"azure", "azure"),
    (r"linux", "linux"), (r"\bgit\b|github", "git"), (r"wordpress", "wordpress"), (r"figma", "figma"),
    (r"power ?bi|\bbi\b|business intelligence", "power bi"), (r"excel", "excel"),
    (r"\bsql\b|mysql", "mysql"), (r"mongo", "mongodb"), (r"unity", "unity"), (r"arduino|\biot\b|internet of things|esp32", "arduino"),
    (r"tensorflow", "tensorflow"), (r"blockchain|solidity", "ethereum"), (r"html", "html5"), (r"\bcss\b", "css3"),
]
_SUBJECTS = [
    (r"\bai\b|artificial intelligence|intelligence artificielle|chatbot|machine learning", "robot"),
    (r"cyber|s[ée]curit", "shield"), (r"marketing|communication", "megaphone"),
    (r"bootcamp|intensive", "rocket"), (r"mobile", "mobile phone"), (r"\bdata\b|donn[ée]es|analytics", "bar chart"),
    (r"design|ux|ui\b|graphi", "artist palette"), (r"cloud", "cloud"), (r"r[ée]seau|network", "globe with meridians"),
    (r"web|full ?stack", "globe with meridians"), (r"english|anglais|fran[çc]ais|langue|language", "speech balloon"),
    (r"gestion|management|business|entrepren", "briefcase"), (r"finance|compta|account", "money bag"),
    (r"photo", "camera"), (r"vid[ée]o|montage", "clapper board"), (r"jeu|game", "video game"),
    (r"robot|embarqu|embedded|[ée]lectron", "robot"),
]

_SVG_OPEN = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE)
_VIEWBOX = re.compile(rb'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"', re.IGNORECASE)
_REF = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*:[a-z0-9]+(?:-[a-z0-9]+)*$")
_ACTIVE_CONTENT = re.compile(rb"<script|\bon[a-z]+\s*=|javascript:|<foreignObject", re.IGNORECASE)

# When nothing on the web fits the name: a general picture of learning, the same one for the same name.
GENERIC = ["fluent-emoji-flat:graduation-cap", "fluent-emoji-flat:laptop", "fluent-emoji-flat:books", "fluent-emoji-flat:light-bulb",
           "fluent-emoji-flat:rocket", "fluent-emoji-flat:direct-hit", "fluent-emoji-flat:gear", "fluent-emoji-flat:trophy"]
DRAWN = "generated"          # the ref of a picture drawn here, looked for again on the web later

# Shown when a program has no picture: its initials on one of these colours.
_TILES = [
    ("#a42c67", "#fdf2f7"), ("#b45309", "#fef3c7"), ("#15803d", "#e9f9ef"),
    ("#1d4ed8", "#eef3ff"), ("#6d28d9", "#f3e8ff"), ("#0f766e", "#e6fbf8"),
]
# The colour of a program's cover, by its place in the catalogue: neighbours never share one.
_COVERS = [
    ("#a42c67", "#fdf2f7"), ("#1d4ed8", "#eef3ff"), ("#b45309", "#fef3c7"), ("#0f766e", "#e6fbf8"),
    ("#6d28d9", "#f3e8ff"), ("#15803d", "#e9f9ef"), ("#be123c", "#fff1f2"), ("#0369a1", "#e8f5fd"),
]


def cover(position: int) -> dict:
    colour, soft = _COVERS[position % len(_COVERS)]
    return {"colour": colour, "soft": soft}


# ------------------------------------------------------------------
# Storage
# ------------------------------------------------------------------

def storage_dir() -> Path:
    folder = Path(os.getenv("PROGRAM_ICONS_DIR") or "data/program_icons")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def path_of(program: Program) -> Path | None:
    if not program.icon_file:
        return None
    path = storage_dir() / program.icon_file
    return path if path.is_file() else None


def media_type(program: Program) -> str:
    extension = (program.icon_file or "").rsplit(".", 1)[-1].lower()
    return _MEDIA_TYPES.get(extension, "application/octet-stream")


def has_icon(program: Program) -> bool:
    return path_of(program) is not None


def url_of(program: Program) -> str | None:
    """Address of the picture; changes when the picture does, so browsers refresh it."""
    if not has_icon(program):
        return None
    return f"/programs/{program.id}/icon?v={program.icon_file.split('.')[0][-8:]}"


def tile(program: Program) -> dict:
    """What to draw when there is no picture."""
    name = (program.name or "?").strip()
    words = [word for word in re.split(r"[^0-9A-Za-zÀ-ÿ]+", name) if word]
    initials = (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper() if words else "?"
    colour, soft = _TILES[sum(ord(char) for char in name) % len(_TILES)]
    return {"initials": initials, "colour": colour, "soft": soft}


def view(program: Program) -> dict:
    """Everything a page needs to draw a program's picture."""
    return {"url": url_of(program), "ref": program.icon_ref, **tile(program)}


def _store(program: Program, data: bytes, extension: str, ref: str) -> None:
    previous = path_of(program)
    stored = f"{program.id}-{uuid.uuid4().hex[:8]}.{extension}"
    (storage_dir() / stored).write_bytes(data)
    program.icon_file = stored
    program.icon_ref = ref
    program.icon_checked_at = datetime.now(timezone.utc)
    if previous is not None:
        try:
            previous.unlink()
        except OSError:
            logger.warning("The previous icon %s could not be removed.", previous)


# ------------------------------------------------------------------
# The web catalogue
# ------------------------------------------------------------------

def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "9antra-training/1.0"}, follow_redirects=True)


def search(query: str, sets: list[str] | None = None, limit: int = 48) -> list[str] | None:
    """Icons matching `query`, as "set:name". None when the web could not be reached."""
    query = " ".join((query or "").split())[:60]
    if not query:
        return []
    params = {"query": query, "limit": max(32, min(limit, 96))}
    if sets:
        params["prefixes"] = ",".join(sets)
    try:
        with _client() as client:
            response = client.get(f"{API}/search", params=params)
            response.raise_for_status()
            found = response.json().get("icons") or []
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Icon search for %r failed: %s", query, exc)
        return None
    return [ref for ref in found if isinstance(ref, str) and _REF.match(ref)][:limit]


def preview_url(ref: str) -> str:
    """Where a browser can show an icon of the catalogue before it is chosen."""
    prefix, name = ref.split(":", 1)
    tint = f"?color=%23{BRAND_COLOR.lstrip('#')}" if prefix in PLAIN_SETS else ""
    return f"{API}/{prefix}/{name}.svg{tint}"


def clean_svg(data: bytes) -> bytes | None:
    """The picture if it is a plain SVG drawing, else None."""
    if not data or len(data) > MAX_ICON_BYTES or not _SVG_OPEN.search(data[:2000]):
        return None
    if _ACTIVE_CONTENT.search(data):
        return None
    return data


def _shape(svg: bytes) -> float:
    """Width divided by height; 1.0 when the drawing does not say."""
    match = _VIEWBOX.search(svg[:2000])
    try:
        width, height = float(match.group(1)), float(match.group(2))
        return width / height if height else 1.0
    except (AttributeError, ValueError):
        return 1.0


def download(ref: str) -> bytes | None:
    """One icon of the catalogue as SVG. None when unavailable."""
    if not _REF.match(ref or ""):
        return None
    prefix, name = ref.split(":", 1)
    params = {"height": "96"}
    if prefix not in TECH_SETS and prefix not in COLOUR_SETS:
        params["color"] = BRAND_COLOR    # one-colour drawings take the brand colour
    try:
        with _client() as client:
            response = client.get(f"{API}/{prefix}/{name}.svg", params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("Icon %s could not be downloaded: %s", ref, exc)
        return None
    return clean_svg(response.content)


def search_plan(name: str) -> list[tuple[str, list[str]]]:
    """What to look for and where, most telling first: (words, sets)."""
    lowered = (name or "").lower()
    plan: list[tuple[int, str, list[str]]] = []
    for pattern, words in _TECHNOLOGIES:
        match = re.search(pattern, lowered)
        if match:
            plan.append((match.start(), words, TECH_SETS))
    plan.sort(key=lambda item: item[0])
    subjects = []
    for pattern, words in _SUBJECTS:
        match = re.search(pattern, lowered)
        if match:
            subjects.append((match.start(), words, COLOUR_SETS))
    subjects.sort(key=lambda item: item[0])

    steps = [(words, sets) for _, words, sets in plan + subjects]
    # Nothing recognised: the name's own words, longest first, anywhere.
    own_words = sorted(
        {word for word in re.findall(r"[a-zà-ÿ]{4,}", lowered) if word not in {"development", "formation", "cours", "training", "months", "mois", "stack", "full"}},
        key=len, reverse=True,
    )
    for word in own_words[:3]:
        steps.append((word, TECH_SETS))
        steps.append((word, COLOUR_SETS + PLAIN_SETS))
    seen, unique = set(), []
    for words, sets in steps:
        if (words, tuple(sets)) not in seen:
            seen.add((words, tuple(sets)))
            unique.append((words, sets))
    return unique


def _ranked(found: list[str], words: str, sets: list[str]) -> list[str]:
    """Best first: the icon named exactly like the words, from the preferred set."""
    slug = "-".join(words.lower().split())

    def rank(ref: str):
        prefix, name = ref.split(":", 1)
        base = re.sub(r"-(icon|light|dark|original|plain|flat|color|colour)$", "", name)
        exact = 0 if base == slug else 1 if slug in base else 2
        prefers_mark = 0 if name.endswith("-icon") or prefix != "logos" else 1
        wordmark = 1 if "wordmark" in name else 0
        return (exact, wordmark, sets.index(prefix) if prefix in sets else len(sets), prefers_mark, len(name))

    return sorted(found, key=rank)


def find_on_web(name: str) -> tuple[str, bytes] | None | bool:
    """The picture for a program called `name`: (ref, svg).

    None when nothing fits, False when the web could not be reached at all.
    """
    reached = False
    for words, sets in search_plan(name):
        found = search(words, sets, limit=32)
        if found is None:
            continue
        reached = True
        for ref in _ranked(found, words, sets)[:4]:
            svg = download(ref)
            if svg is not None and 0.75 <= _shape(svg) <= 1.34:
                return ref, svg
    return None if reached else False


# ------------------------------------------------------------------
# Giving a program its picture
# ------------------------------------------------------------------

def assign_from_web(db: Session, program: Program, ref: str | None = None) -> str:
    """Fetch and store a picture: the given icon, or the best one for the name.

    Returns "ok", "nothing" (no icon fits) or "offline".
    """
    if ref:
        svg = download(ref)
        if svg is None:
            return "offline"
        _store(program, svg, "svg", ref)
        db.commit()
        return "ok"

    program_id, name, extra = program.id, program.name or "", extra_words(db, program)
    db.rollback()                       # nothing held while the web answers (SQLite: other saves must not wait)
    ref, data, outcome = picture_for(name, extra)
    program = db.get(Program, program_id)
    if program is None:
        return outcome
    if program.icon_ref in ("upload", "none") or (has_icon(program) and program.icon_ref not in (DRAWN, None) and outcome != "ok"):
        db.rollback()                   # the admin chose meanwhile, or a real picture is already there
        return outcome
    _store(program, data, "svg", ref)
    db.commit()
    return outcome


def save_upload(db: Session, program: Program, filename: str, data: bytes) -> None:
    """The admin's own picture. Raises ValueError with a message for the admin."""
    extension = Path(filename or "").suffix.lower()
    if extension not in UPLOAD_TYPES:
        raise ValueError("Choose a picture: PNG, JPG, WEBP, GIF or SVG.")
    if not data:
        raise ValueError("This file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("This picture is larger than 2 MB. Choose a smaller one.")
    if extension == ".svg":
        if clean_svg(data) is None:
            raise ValueError("This SVG file cannot be used: it is not a plain drawing.")
        _store(program, data, "svg", "upload")
    else:
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:
            raise ValueError("This file is not a picture that can be read.") from exc
        # A small square-ish picture is all a card needs.
        image.thumbnail((256, 256))
        output = io.BytesIO()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        image.save(output, format="PNG", optimize=True)
        _store(program, output.getvalue(), "png", "upload")
    db.commit()


def remove(db: Session, program: Program) -> None:
    """No picture; the program's initials are shown and the web is not asked again."""
    previous = path_of(program)
    program.icon_file = None
    program.icon_ref = "none"
    program.icon_checked_at = datetime.now(timezone.utc)
    db.commit()
    if previous is not None:
        try:
            previous.unlink()
        except OSError:
            logger.warning("The icon %s could not be removed.", previous)


def needs_icon(program: Program, now: datetime | None = None) -> bool:
    """Whether the web should be asked for this program's picture (also when it only has one drawn here)."""
    if program.icon_ref == "none" or (has_icon(program) and program.icon_ref != DRAWN):
        return False
    if program.icon_checked_at is None:
        return True
    checked = program.icon_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - checked >= RETRY_AFTER


def assign_missing(db: Session, limit: int = 4) -> int:
    """Give a picture to programs that have none yet (or only one drawn here). Returns how many got one from the web."""
    done = 0
    waiting = [program.id for program in db.query(Program).order_by(Program.name.asc()).all() if needs_icon(program)]
    db.rollback()
    for program_id in waiting[:limit]:
        program = db.get(Program, program_id)
        if program is None:
            continue
        outcome = assign_from_web(db, program)
        done += outcome == "ok"
        if outcome == "offline":
            break       # the others get a drawn picture next time; no point asking the web now
    return done


def extra_words(db: Session, program: Program) -> str:
    """What else names the program's subject: the technologies 9antra.tn lists for it."""
    if not getattr(program, "website_slug", None):
        return ""
    try:
        from app.models.website import WebsiteItem

        item = (db.query(WebsiteItem).filter(WebsiteItem.kind == "formation", WebsiteItem.slug == program.website_slug)
                .order_by(WebsiteItem.gone_at.isnot(None)).first())
        import json

        data = json.loads(item.data or "{}") if item is not None else {}
        technologies = data.get("technologies") if isinstance(data, dict) else None
    except Exception:
        return ""
    if isinstance(technologies, str):
        return technologies
    return " ".join(str(word) for word in (technologies or [])[:8])


def drawn_svg(name: str) -> bytes:
    """A picture drawn here: the initials on the program's own colours, with a mortarboard."""
    from html import escape

    look = tile(Program(name=name))
    initials = escape(look["initials"][:2])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{look["colour"]}"/>'
        f'<stop offset="1" stop-color="{look["colour"]}" stop-opacity=".72"/></linearGradient></defs>'
        '<rect x="2" y="2" width="60" height="60" rx="16" fill="url(#g)"/>'
        '<path d="M32 13 50 21 32 29 14 21Z" fill="#fff" fill-opacity=".92"/>'
        '<path d="M21 25v6c0 3 5 5 11 5s11-2 11-5v-6l-11 5Z" fill="#fff" fill-opacity=".7"/>'
        f'<text x="32" y="54" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-size="15" font-weight="700" fill="#fff">{initials}</text>'
        '</svg>'
    ).encode()


def picture_for(name: str, extra: str = "") -> tuple[str, bytes, str]:
    """(ref, svg, outcome) for a program called `name`: always a picture. outcome: "ok" (it fits the name), "nothing"
    (a general picture: nothing on the web fits), "offline" (drawn here: the web could not be reached)."""
    found = find_on_web(f"{name} {extra}".strip())
    if found:
        return found[0], found[1], "ok"
    if found is None:
        ref = GENERIC[sum(ord(char) for char in name) % len(GENERIC)]
        svg = download(ref)
        if svg is not None:
            return ref, svg, "nothing"
    return DRAWN, drawn_svg(name), "offline"


# ------------------------------------------------------------------
# A new program: its picture is looked for at once
# ------------------------------------------------------------------

def give_new(program_id: str) -> None:
    """For a program that was just created (in the background: it opens its own session)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        program = db.get(Program, program_id)
        if program is not None and needs_icon(program):
            assign_from_web(db, program)
    except Exception:
        db.rollback()
        logger.exception("The picture of the new program %s could not be given.", program_id)
    finally:
        db.close()


def install_hooks(session_factory) -> None:
    """Whatever creates a program (the admin, the website, a catalogue, a registration), its picture follows."""
    from sqlalchemy import event

    @event.listens_for(session_factory, "after_flush")
    def _collect(session, flush_context):
        new = [obj.id for obj in session.new if isinstance(obj, Program) and obj.id]
        if new:
            session.info.setdefault("new_programs", []).extend(new)

    @event.listens_for(session_factory, "after_commit")
    def _give(session):
        new = session.info.pop("new_programs", [])
        if not new or os.getenv("PROGRAM_ICONS_ON_CREATE", "1") == "0":
            return
        from app.services.background import run_in_background

        for program_id in dict.fromkeys(new):
            run_in_background(give_new, program_id)

    @event.listens_for(session_factory, "after_rollback")
    def _forget(session):
        session.info.pop("new_programs", None)
