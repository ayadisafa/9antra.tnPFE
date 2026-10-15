"""Published feedback: kept here, read, and its tone worked out.

The website publishes its feedback as pictures (a card with a name, a few
lines and stars); the Facebook page gives its reviews as text. Each picture
is downloaded once and kept, then read with the text reader that comes with
Windows. What is read goes through the same word rules as the candidates'
weekly check-in, so a tone is never a model's opinion.

A reading is stored on the website item (`WebsiteItem.reading`): the picture's
file, the name, the words, the stars, the tone and what the person talks about.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from app.models.website import WebsiteItem
from app.services import week_insights

logger = logging.getLogger(__name__)

FOLDER = Path(os.getenv("FEEDBACK_DIR") or "data/website_feedback")
MAX_PICTURE_BYTES = 6 * 1024 * 1024
RETRY_AFTER = timedelta(hours=12)       # a picture that could not be fetched or read is tried again
READING = 2                             # raised when the way of reading changes: everything is read again
_SCRIPT = Path(__file__).with_name("read_picture.ps1")
_HEADERS = {"User-Agent": "9antra training app (feedback)"}
_reader = {"checked": False, "ok": False}

SENTIMENTS = {
    "positive": {"label": "Happy", "tone": "ok", "icon": "bi-emoji-smile-fill"},
    "mixed": {"label": "Mixed", "tone": "warn", "icon": "bi-emoji-neutral-fill"},
    "negative": {"label": "Unhappy", "tone": "bad", "icon": "bi-emoji-frown-fill"},
    "neutral": {"label": "No clear tone", "tone": "muted", "icon": "bi-emoji-expressionless"},
    "unknown": {"label": "Not read yet", "tone": "muted", "icon": "bi-image"},
}
# What people talk about when they speak of a training.
THEMES = {
    "trainer": {"label": "The trainer", "icon": "bi-person-video3",
                "words": r"formateur|formatrice|\bprof\b|professeur|enseignant|trainer|teacher|instructor|coach|pedagog"},
    "support": {"label": "Being followed and helped", "icon": "bi-life-preserver",
                "words": r"encadrement|encadr|suivi|accompagn|disponible|ecoute|reactiv|support|aide|help|mentor|tes2l|tnajjm|tnajem"},
    "content": {"label": "The content", "icon": "bi-journal-richtext",
                "words": r"contenu|programme|cours|competence|notion|apprentissage|appris|learn|skill|content|connaissance|methode"},
    "practice": {"label": "Practice and projects", "icon": "bi-tools",
                 "words": r"pratique|projet|exercice|atelier|hands[- ]on|project|practical|application|\btp\b|realis"},
    "atmosphere": {"label": "The atmosphere", "icon": "bi-people-fill",
                   "words": r"ambiance|groupe|equipe|convivial|atmosphere|team|bonne humeur|famil|jaw"},
    "organisation": {"label": "Organisation", "icon": "bi-calendar2-check",
                     "words": r"organis|horaire|rythme|planning|ponctu|schedule|timing|duree|structure"},
    "career": {"label": "Work and career", "icon": "bi-briefcase-fill",
               "words": r"stage|emploi|carriere|career|job|recrut|embauch|professionnel|pfe|marche du travail|freelance"},
    "value": {"label": "Worth the price", "icon": "bi-cash-coin",
              "words": r"\bprix\b|rapport qualite|tarif|price|worth|argent|cher|abordable|soum"},
}
_THEME_PATTERNS = {key: re.compile(theme["words"]) for key, theme in THEMES.items()}
_ARABIC = re.compile("[؀-ۿ]")
_TUNISIAN = re.compile(
    r"\b(barcha|brcha|behi|behy|behya|bahi|yaser|yasser|tayara|tayyara|maa|m3a|mta3|aalkher|3alekher|3al|kanet|"
    r"haja|famma|fama|chay|kol|ena|ama|w|el|mouch|mch|ya3tik|y3aychek|sa7a|saha|nchallah|rabi)\b|\b[a-z]+[3795][a-z]+\b|\b[3795][a-z]{2,}\b"
)
_ENGLISH = {"the", "and", "was", "is", "very", "with", "this", "great", "good", "course", "training", "recommend", "thanks", "really", "my"}
_FRENCH = {"le", "la", "les", "est", "et", "une", "des", "tres", "avec", "cette", "formation", "merci", "je", "bien", "etait", "pour"}


def view(sentiment: str | None) -> dict:
    return SENTIMENTS.get(sentiment or "unknown", SENTIMENTS["unknown"])


# ------------------------------------------------------------------
# Reading a picture
# ------------------------------------------------------------------

def reader_available() -> bool:
    """Whether this computer can read the text of a picture (Windows, with its own reader)."""
    if not _reader["checked"]:
        _reader.update(checked=True, ok=os.name == "nt" and _SCRIPT.is_file() and shutil.which("powershell") is not None)
    return _reader["ok"]


def read_lines(path: Path) -> dict | None:
    """What is written in a picture: {"width", "height", "language", "lines": [{"y", "h", "text"}]}.

    None when it could not be read.
    """
    if not reader_available():
        return None
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT), "-Path", str(path)],
            capture_output=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("The picture %s could not be read: %s", path.name, exc)
        return None
    if done.returncode != 0:
        logger.warning("The picture %s could not be read: %s", path.name, done.stderr.decode("utf-8", "replace")[-300:])
        return None
    try:
        data = json.loads(done.stdout.decode("utf-8-sig", "replace").strip() or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        return None
    data["lines"] = [line for line in data["lines"] if isinstance(line, dict) and str(line.get("text") or "").strip()]
    return data


# Tests replace this to avoid running Windows' reader.
read = read_lines


def parse_card(page: dict) -> dict:
    """The name and the words of a feedback card.

    The card has the site's name and "FEEDBACK" at the top, a portrait, then
    the person's name in larger letters and their words. Anything else is
    read as words only.
    """
    lines = sorted(page.get("lines") or [], key=lambda line: line.get("y") or 0)
    height = page.get("height") or 0
    body = [line for line in lines if height and (line.get("y") or 0) >= 0.45 * height]
    if not body:
        body = [line for line in lines if not re.fullmatch(r"(the\s+\S*\s*ridge|feedback)", str(line["text"]).strip().lower())]
    if not body:
        return {"name": "", "text": ""}
    first, rest = body[0], body[1:]
    words = str(first["text"]).split()
    # The name stands apart (more room under it than between two lines of the
    # words) or is written larger; long words leave less room, short ones look as tall.
    apart = not rest or (rest[0].get("y") or 0) - (first.get("y") or 0) >= 1.4 * (first.get("h") or 1)
    taller = bool(rest) and (first.get("h") or 0) >= 1.1 * sorted(line.get("h") or 0 for line in rest)[len(rest) // 2]
    is_name = len(words) <= 4 and not re.search(r"[.!?,;:]", first["text"]) and (apart or taller)
    text_lines = rest if is_name else body
    return {
        "name": " ".join(words)[:120] if is_name else "",
        "text": _repair(" ".join(" ".join(str(line["text"]).split()) for line in text_lines))[:2000],
    }


def _repair(text: str) -> str:
    """Put right what the reader gets wrong in this lettering: a "u" comes out
    as "tJ", "lJ" or a capital in the middle of a word ("tOUjours", "atJX")."""
    def fix(match: re.Match) -> str:
        word = match.group(0)
        return re.sub(r"[tl]J", "u", word).lower()

    text = re.sub(r"\b[a-zà-ÿ][a-zà-ÿ']*[A-Z][A-Za-zà-ÿ]*\b", fix, text)
    return re.sub(r"\btouiours\b", "toujours", text)


def stars_of(path: Path) -> int | None:
    """How many yellow stars the card shows (1 to 5), or None."""
    try:
        from PIL import Image

        with Image.open(path) as picture:
            picture = picture.convert("RGB")
            width, height = picture.size
            # Small is enough to count five stars, and quick to scan.
            scale = 360 / width if width > 360 else 1.0
            small = picture.resize((max(1, round(width * scale)), max(1, round(height * scale))))
    except Exception:
        return None
    width, height = small.size
    pixels = small.load()
    columns = []
    for x in range(width):
        yellow = 0
        for y in range(int(height * 0.6), int(height * 0.97)):
            red, green, blue = pixels[x, y]
            if red > 225 and 170 < green < 235 and blue < 90:
                yellow += 1
        columns.append(yellow >= 3)
    runs, length = 0, 0
    for filled in columns + [False]:
        if filled:
            length += 1
            continue
        if length >= max(4, width * 0.02):
            runs += 1
        length = 0
    return runs if 1 <= runs <= 5 else None


# ------------------------------------------------------------------
# The tone of the words
# ------------------------------------------------------------------

def language_of(text: str) -> str:
    plain = week_insights._plain(text)
    if _ARABIC.search(text or ""):
        return "Arabic"
    words = set(re.findall(r"[a-z0-9']+", plain))
    if len(_TUNISIAN.findall(plain)) >= 2:
        return "Tunisian" if len(words & _FRENCH) < 4 else "Tunisian and French"
    return "English" if len(words & _ENGLISH) > len(words & _FRENCH) else "French"


def analyse(text: str, *, stars: int | None = None, recommends: bool | None = None) -> dict:
    """The tone of a feedback and what it talks about.

    The stars (or Facebook's "recommends") and the words are read together:
    five stars with harsh words is "mixed", not "happy".
    """
    tone = week_insights.tone_of(text)
    words = tone["tone"]
    if stars is not None:
        if stars >= 4:
            sentiment = "mixed" if words == "negative" else "positive"
        elif stars == 3:
            sentiment = "positive" if words == "positive" else "negative" if words == "negative" else "mixed"
        else:
            sentiment = "mixed" if words == "positive" else "negative"
    elif recommends is not None:
        sentiment = ("mixed" if words == "negative" else "positive") if recommends else ("mixed" if words == "positive" else "negative")
    else:
        sentiment = words if words in ("positive", "negative", "mixed") else "neutral" if (text or "").strip() else "unknown"
    plain = week_insights._plain(text)
    return {
        "sentiment": sentiment,
        "words": words,
        "positive": tone["positive"],
        "negative": tone["negative"],
        "themes": [key for key, pattern in _THEME_PATTERNS.items() if pattern.search(plain)],
        "language": language_of(text) if (text or "").strip() else None,
    }


# ------------------------------------------------------------------
# Keeping and reading what is published
# ------------------------------------------------------------------

def _file_name(item: WebsiteItem, url: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]", "", item.external_id or "")[:60] or item.id
    extension = (re.search(r"\.(jpe?g|png|webp)(?:$|\?)", url.lower()) or [None, "jpg"])[1]
    return f"{stem}.{extension}"


def download(item: WebsiteItem) -> Path | None:
    """Keep the picture of a feedback on this computer. None when it could not be fetched."""
    url = (item.details.get("image") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    target = FOLDER / _file_name(item, url)
    if target.is_file() and target.stat().st_size > 0:
        return target
    try:
        response = requests.get(url, headers=_HEADERS, timeout=40)
    except requests.RequestException as exc:
        logger.warning("Feedback picture %s did not come: %s", url, type(exc).__name__)
        return None
    kind = (response.headers.get("content-type") or "").lower()
    if response.status_code != 200 or not kind.startswith("image/") or not 0 < len(response.content) <= MAX_PICTURE_BYTES:
        logger.warning("Feedback picture %s was refused (%s, %s).", url, response.status_code, kind or "no type")
        return None
    FOLDER.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return target


def picture_path(item: WebsiteItem) -> Path | None:
    """The kept picture of a feedback, when there is one."""
    name = item.read.get("file")
    if not name or "/" in name or "\\" in name:
        return None
    path = FOLDER / name
    return path if path.is_file() else None


def read_item(item: WebsiteItem) -> dict:
    """Read one feedback (a review's text, or a picture) and return its reading."""
    now = datetime.utcnow().isoformat()
    details = item.details
    if details.get("text"):
        # A Facebook review: the words are given.
        text = str(details["text"])
        return {
            "status": "read", "source": "facebook", "name": item.title, "text": text, "stars": None, "read_at": now,
            **analyse(text, recommends=details.get("recommends")),
        }
    path = download(item)
    if path is None:
        return {"status": "failed", "source": "website", "why": "The picture could not be downloaded.", "read_at": now}
    reading = {"status": "picture", "source": "website", "file": path.name, "stars": stars_of(path), "read_at": now}
    page = read(path)
    if page is None:
        reading["why"] = "This computer cannot read the text of a picture." if not reader_available() else "The text could not be read."
        reading.update(analyse("", stars=reading["stars"]))
        return reading
    card = parse_card(page)
    reading.update(status="read", name=card["name"], text=card["text"], reader=page.get("language"))
    reading.update(analyse(card["text"], stars=reading["stars"]))
    return reading


def _due(item: WebsiteItem, now: datetime) -> bool:
    reading = item.read
    if not reading or reading.get("v") != READING:
        return True
    try:
        read_at = datetime.fromisoformat(reading.get("read_at") or "")
    except ValueError:
        return True
    if reading.get("status") == "read":
        # A picture replaced on the website is read again.
        return bool(item.changed_at and item.changed_at > read_at)
    return read_at < now - RETRY_AFTER


def tick(db: Session, limit: int = 4, now: datetime | None = None) -> int:
    """Read a few of the feedback not read yet. Returns how many were done."""
    now = now or datetime.utcnow()
    waiting = [
        item for item in db.query(WebsiteItem).filter(WebsiteItem.kind == "feedback", WebsiteItem.gone_at.is_(None)).all()
        if _due(item, now)
    ]
    waiting.sort(key=lambda item: (item.details.get("order") or 0, item.title or ""))
    done = 0
    for item in waiting[:limit]:
        try:
            item.reading = json.dumps({**read_item(item), "v": READING}, ensure_ascii=False)
        except Exception:
            logger.exception("Feedback %s could not be read.", item.id)
            item.reading = json.dumps({"status": "failed", "why": "Reading failed.", "read_at": now.isoformat(), "v": READING})
        db.commit()
        done += 1
    return done


# ------------------------------------------------------------------
# What the pages and the assistant use
# ------------------------------------------------------------------

def _rows(db: Session) -> list[dict]:
    items = db.query(WebsiteItem).filter(WebsiteItem.kind == "feedback", WebsiteItem.gone_at.is_(None)).all()
    rows = []
    for item in sorted(items, key=lambda entry: (entry.details.get("order") or 0, entry.title or "")):
        reading = item.read
        rows.append({
            "item": item, "reading": reading, "view": view(reading.get("sentiment")),
            "name": reading.get("name") or (item.title if reading.get("source") == "facebook" else ""),
            "text": reading.get("text") or "", "stars": reading.get("stars"),
            "picture": f"/website/feedback/{item.id}/picture" if reading.get("file") else (item.details.get("image") or ""),
            "themes": [THEMES[key] for key in reading.get("themes") or [] if key in THEMES],
            "waiting": not reading,
        })
    return rows


def overview(db: Session) -> dict:
    """Everything the Website page shows about feedback."""
    rows = _rows(db)
    read = [row for row in rows if row["text"]]
    rated = [row["stars"] for row in rows if row["stars"]]
    counts = {key: sum(1 for row in rows if row["reading"].get("sentiment") == key) for key in ("positive", "mixed", "negative", "neutral")}
    themes = []
    for key, theme in THEMES.items():
        about = [row for row in read if key in (row["reading"].get("themes") or [])]
        if about:
            happy = sum(1 for row in about if row["reading"].get("sentiment") == "positive")
            themes.append({
                **theme, "key": key, "count": len(about), "happy": happy, "share": round(100 * len(about) / len(read)),
                "quote": next(({"name": row["name"], "words": row["text"][:220]} for row in about if 40 <= len(row["text"])), None),
            })
    themes.sort(key=lambda theme: theme["count"], reverse=True)
    languages: dict[str, int] = {}
    for row in read:
        language = row["reading"].get("language") or "Other"
        languages[language] = languages.get(language, 0) + 1
    praise: dict[str, int] = {}
    for row in read:
        for word in row["reading"].get("positive") or []:
            praise[word] = praise.get(word, 0) + 1
    judged = counts["positive"] + counts["mixed"] + counts["negative"]
    return {
        "rows": rows, "total": len(rows), "read": len(read), "waiting": sum(1 for row in rows if row["waiting"]),
        "unreadable": sum(1 for row in rows if row["reading"] and not row["text"]),
        "counts": counts, "judged": judged,
        "happy_share": round(100 * counts["positive"] / judged) if judged else None,
        "stars": round(sum(rated) / len(rated), 1) if rated else None, "rated": len(rated),
        "themes": themes, "languages": sorted(languages.items(), key=lambda pair: pair[1], reverse=True),
        "praise": sorted(praise.items(), key=lambda pair: pair[1], reverse=True)[:10],
        "to_answer": [row for row in rows if row["reading"].get("sentiment") in ("negative", "mixed")],
        "reader": reader_available(),
    }


def knowledge_lines(db: Session, limit: int = 8) -> list[str]:
    """What former candidates say, for the email assistant's knowledge."""
    rows = [row for row in _rows(db) if row["text"] and row["reading"].get("sentiment") == "positive" and len(row["text"]) >= 40]
    if not rows:
        return []
    rated = [row["stars"] for row in rows if row["stars"]]
    lines = [
        f"Avis et témoignages (feedback, reviews) d'anciens participants publiés par 9antra : {len(rows)} avis positifs lus"
        + (f", note moyenne {sum(rated) / len(rated):.1f} sur 5 (stars)." if rated else ".")
    ]
    for row in rows[:limit]:
        lines.append(f"« {row['text'][:320]} »" + (f" — {row['name']}" if row["name"] else "") + (f" ({row['stars']}/5)" if row["stars"] else ""))
    return lines
