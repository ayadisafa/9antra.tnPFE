"""What a post of the Facebook or LinkedIn page says, read by rules (no language model): its language, the programs it
names, the dates and the prices it gives, what it announces (a session, a workshop or event, an offer, a testimonial,
news), its hashtags and links, and its tone. Shown on the Website page and given with the post to the assistant."""
from __future__ import annotations

import re
import unicodedata

_ANNOUNCES = {
    "session": r"nouvelle session|nouvelles? sessions?|inscriptions? (ouvertes?|ouverte)|places? limitees?|demarrage|rentree|new (session|intake|cohort)|registrations? (are )?open|enrol+ now",
    "event": r"workshop|atelier|evenement|event|webinaire?|conference|meetup|hackathon|journee portes ouvertes|open day|seminaire",
    "offer": r"stage|stages|internship|offre|pfe|recrut\w*|hiring|job|emploi|poste",
    "testimonial": r"temoignage|felicitations|bravo|merci a|thank you|congratulations|fiers de|proud of|certifies?|diplomes?",
    "promotion": r"promo\w*|reduction|remise|discount|offre speciale|special offer|gratuit|free",
}
_NAMES = {"session": "a new session", "event": "a workshop or an event", "offer": "an offer (internship, job)",
          "testimonial": "a testimonial or a success", "promotion": "a promotion", "news": "news"}
_DATE = re.compile(r"\b(\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{1,2}\s+(?:janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre|"
                   r"january|february|march|april|may|june|july|august|september|october|november|december))\b", re.I)
_PRICE = re.compile(r"\b(\d{2,5})\s?(?:dt|tnd|dinars?)\b", re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9#/ .-]+", " ", text)


def analyze(db, text: str) -> dict:
    """{"language", "programs", "dates", "prices", "announces", "announces_said", "hashtags", "links", "tone"}."""
    from app.services import emotions, howto, program_documents

    plain = _plain(text)
    announces = [key for key, pattern in _ANNOUNCES.items() if re.search(r"\b(" + pattern + r")\b", plain)] or ["news"]
    try:
        programs = [item.name for item in program_documents.named_programs(db, text or "")]
    except Exception:
        programs = []
    feeling = (emotions.read(text or "") or {}).get("main")
    return {
        "language": howto.language_of(text or ""), "programs": programs[:4],
        "dates": list(dict.fromkeys(match.group(1) for match in _DATE.finditer(text or "")))[:4],
        "prices": [f"{amount} DT" for amount in dict.fromkeys(_PRICE.findall(text or ""))][:4],
        "announces": announces, "announces_said": [_NAMES[key] for key in announces],
        "hashtags": list(dict.fromkeys(re.findall(r"#\w+", text or "")))[:8],
        "links": list(dict.fromkeys(re.findall(r"https?://\S+", text or "")))[:4],
        "tone": feeling or "neutral",
    }


def knowledge_line(source: str, when, text: str, found: dict) -> str:
    """The post as the assistant knows it: what it says, then what it was read to announce."""
    parts = [f"Publication {source}" + (f" du {when:%d/%m/%Y}" if when else "") + f" : {text[:1200]}"]
    if found["programs"]:
        parts.append("Formations citées : " + ", ".join(found["programs"]) + ".")
    if found["dates"]:
        parts.append("Dates citées : " + ", ".join(found["dates"]) + ".")
    if found["prices"]:
        parts.append("Prix cités : " + ", ".join(found["prices"]) + ".")
    parts.append("Annonce : " + ", ".join(found["announces_said"]) + ".")
    return "\n".join(parts)
