"""The trends agent (orchestrator.py): what is new in every field the centre could teach, which services it could add,
and what a name says. For the admin and the team only.

    “what is trendy in AI?”, “quoi de neuf en cybersécurité ?”      the journal's articles of the last weeks, by field:
                                                                    the words they repeat, and the headlines, numbered
    “what is trendy?” (no field)                                    one line per field, the busiest first
    “what services could we add?”                                   what the job offers ask and nobody teaches here, what
                                                                    the articles speak of, and services that fit a centre
    “how inspiring is the name 9antra?”, “le nom « CodeLab » ?”     a reading by fixed criteria: length, how it is said,
                                                                    what it means, how it stands out, what it fits

Everything comes from the records (news_items, market_offers, programs) or from fixed criteria written here: the local
model is not asked, and an empty journal is said, not filled in.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.news_item import NewsItem
from app.services.ui_words import t as _t, use as _use_language

DAYS = 30
_TRENDS = (r"trend|trendy|trending|tendances?|a la mode|en vogue|quoi de neuf|what'?s new|what is new|nouveautes?|actualites?|"
           r"tech news|latest news|news (in|about|of)|dernieres nouvelles|hot topics?|buzz|qu.?est.ce qui (marche|cartonne|monte)|"
           r"what.?s (hot|big|rising)|ترند|رائج")
_MARKET = r"market|marche du travail|job offers?|offres? d.?emploi|emplois?|recrut|hiring|linkedin skills?"
_ADD = r"ajouter|add|proposer|propose|offrir|offer|lancer|launch|creer|create|nouveaux?|nouvelles?|new|other|autres?"
_SERVICES = (r"(?:services?|prestations?)\W+(?:\w+\W+){0,4}(?:" + _ADD + r")\b|(?:" + _ADD + r")\W+(?:\w+\W+){0,4}(?:services?|prestations?)\b|"
             r"what (else|more) (can|could|should) we (offer|add|propose|do)|que (pourrions|peut|pouvons|devrions)[- ]nous "
             r"(ajouter|proposer|offrir)|idees? (de|pour) (services?|formations?|programmes?)|ideas? (for|of) (services?|programs?|courses?)")
_ORDER = r"\s*(envoi|envoy|send|ecri|write|rappel|remind|sms|whatsapp|mail|retiens|remember|note)"
_NAME = r"\bnoms?\b|\bnames?\b|appellation|\bbrand\b|\bmarque\b|اسم|esm\b|\bisem\b"
_NAME_JUDGE = (r"inspir|bon nom|good name|que (penses|pensez)|what do you think|evaluat|evalue|how (good|strong|inspiring)|"
               r"meaning|signifi|veut dire|choisir|choose|mieux|better|fort|strong|memor|accroch|catchy")

_FIELDS = {
    "ai": r"\bia\b|\bai\b|intelligence artificielle|artificial intelligence|machine learning|deep learning|llm|genai|chatgpt|ذكاء",
    "devops": r"devops|cloud|docker|kubernetes|k8s|ci/?cd|aws|azure",
    "cyber": r"cyber|securite|security|hacking|pentest|سيبراني",
    "web": r"\bweb\b|javascript|react|frontend|front.end|backend|mern|spring|full.?stack|angular",
    "python": r"python",
    "flutter": r"mobile|flutter|android|\bios\b",
    "bi": r"\bbi\b|power bi|data|donnees|analytics|analyse de donnees|dashboard",
    "iot": r"\biot\b|arduino|objets connectes|embarque|embedded|esp32|robotique|robotics",
    "marketing": r"marketing|\bseo\b|social media|reseaux sociaux|community manag|publicite|ads\b",
}
_MEANINGS = {
    "9antra": ("bridge", "“9antra” (قنطرة) means “bridge” in Tunisian: a bridge from learning to a job, a story told in one word."),
    "qantra": ("bridge", "“Qantra” (قنطرة) means “bridge”: a bridge from learning to a job."),
    "kantra": ("bridge", "“Kantra” (قنطرة) means “bridge”: a bridge from learning to a job."),
    "qantara": ("bridge", "“Qantara” (قنطرة) means “bridge”: a bridge from learning to a job."),
    "قنطرة": ("bridge", "قنطرة means “bridge”: a bridge from learning to a job."),
    "bridge": ("bridge", "“Bridge”: from learning to a job, a clear promise."),
    "pont": ("bridge", "“Pont”: from learning to a job, a clear promise."),
    "skill": ("skills", "“Skill”: abilities, close to what employers look for."),
    "skills": ("skills", "“Skills”: abilities, close to what employers look for."),
    "lab": ("lab", "“Lab”: a place to try and build, practical."),
    "labs": ("lab", "“Labs”: places to try and build, practical."),
    "hub": ("hub", "“Hub”: where people meet, a community."),
    "code": ("code", "“Code”: programming; narrow if the centre also teaches marketing or BI."),
    "coding": ("code", "“Coding”: programming; narrow if the centre also teaches marketing or BI."),
    "future": ("future", "“Future”: momentum, a little common."),
    "futur": ("future", "“Futur”: momentum, a little common."),
    "next": ("future", "“Next”: momentum, a little common."),
    "desk": ("desk", "“Desk”: a work place, calm and practical."),
    "campus": ("place", "“Campus”: a place of study, neutral."),
}
_GENERIC = {"academy", "academie", "academia", "tech", "center", "centre", "formation", "formations", "training", "school", "ecole",
            "institut", "institute", "pro", "group", "learning", "education", "digital", "online", "consulting", "solutions"}
_SERVICES_FOR = {
    "bridge": ["Internships with partner companies: the bridge to a first job",
               "CV, LinkedIn and interview coaching at the end of each program",
               "A job board for the centre's former candidates",
               "Mentoring by former candidates who found work",
               "Training for companies' teams: the bridge the other way"],
    "skills": ["Short certifying workshops (one skill, one weekend)", "A skills check before and after each program",
               "Preparation for international certifications"],
    "lab": ["Open project evenings in the lab", "Hackathons with a local company's real problem", "A demo day where candidates show their projects"],
    "hub": ["Monthly meetups with professionals", "An online community for candidates and former candidates", "Talks by alumni"],
}
_ALWAYS = ["Preparation for certifications (AWS, Cisco, Microsoft, Google)", "Courses for teenagers in the holidays (coding, robotics)",
           "Evening and weekend formats for people who work", "Self-paced online courses from the videos already recorded"]


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", (text or "").lower()).encode("ascii", "ignore").decode()
    return f" {folded} {(text or '').lower()} "


def _has(text: str, pattern: str) -> bool:
    return re.search(r"(?<![a-z])(?:" + pattern + r")", text) is not None


def wants(message: str) -> str | None:
    """trends | services | name, or None when it is for another agent (the job market is the figures' own answer)."""
    text = _plain(message)
    if re.match(_ORDER, text):
        return None              # something to send or to remember: another agent's
    if _has(text, _NAME) and (_has(text, _NAME_JUDGE) or _quoted(message)):
        return "name"
    if _has(text, _SERVICES) and not re.search(r"\bservice (client|clientele|apres)", text):
        return "services"
    if _has(text, _TRENDS) and not _has(text, _MARKET):
        return "trends"
    return None


def answer(db: Session, message: str, *, lang: str | None = None) -> dict | None:
    from app.services import howto

    kind = wants(message)
    if kind is None:
        return None
    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(message)
    token = _use_language("fr" if spoken in ("fr", "tn") else spoken)
    try:
        if kind == "name":
            return name_reading(db, _name_in(db, message))
        if kind == "services":
            return services(db)
        return trends(db, _field_in(message))
    finally:
        from app.services.ui_words import current

        current.reset(token)


# ------------------------------------------------------------------
# What is new
# ------------------------------------------------------------------

def _field_in(message: str) -> str | None:
    text = _plain(message)
    for key, pattern in _FIELDS.items():
        if re.search(pattern, text):
            return key
    return None


def _recent(db: Session, field: str | None = None, days: int = DAYS) -> list[NewsItem]:
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(NewsItem).filter((NewsItem.published_at >= since) | (NewsItem.published_at.is_(None) & (NewsItem.fetched_at >= since)))
    if field:
        query = query.filter(NewsItem.domain == field)
    return query.order_by(NewsItem.published_at.desc()).all()


def _words_of(rows: list[NewsItem]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        for term in set(row.term_list()):
            counts[term] += 1
    return counts


def trends(db: Session, field: str | None) -> dict:
    from app.services import journal

    rows = _recent(db, field)
    if not rows:
        reply = (_t("The journal has no article about {0} from the last {1} days yet: it reads the news every 6 hours (Journal page).",
                    _t(journal.DOMAINS[field][0]), DAYS) if field else
                 _t("The journal has no article from the last {0} days yet: it reads the news every 6 hours (Journal page).", DAYS))
        return {"reply": reply, "links": [{"label": "Open the journal", "url": "/journal"}], "source": "system",
                "sources": [{"label": _t("The journal"), "url": "/journal"}], "resources": []}
    sources, lines = [], []
    if field:
        name = _t(journal.DOMAINS[field][0])
        top = [term for term, count in _words_of(rows).most_common(8) if count >= 1][:6]
        lines.append(_t("What is new in {0} ({1} articles in {2} days):", name, len(rows), DAYS))
        if top:
            lines.append(_t("The words the articles repeat: {0}.", ", ".join(top)))
        for row in rows[:4]:
            sources.append({"label": f"[{len(sources) + 1}] {row.source}: {row.title[:90]}", "url": row.url})
            lines.append(f"[{len(sources)}] {row.title}")
        taught = _taught_terms(db, field, top)
        if taught["missing"]:
            lines.append(_t("Not taught here yet: {0}. A session or a week to consider.", ", ".join(taught["missing"][:4])))
        if taught["taught"]:
            lines.append(_t("Already taught here: {0}. Say so in the posts and on the program's page.", ", ".join(taught["taught"][:4])))
    else:
        by_field: dict[str, list[NewsItem]] = {}
        for row in rows:
            by_field.setdefault(row.domain, []).append(row)
        lines.append(_t("What is new in each field ({0} articles in {1} days), the busiest first:", len(rows), DAYS))
        for key, mine in sorted(by_field.items(), key=lambda pair: -len(pair[1])):
            if key not in journal.DOMAINS:
                continue
            top = [term for term, _ in _words_of(mine).most_common(3)]
            sources.append({"label": f"[{len(sources) + 1}] {mine[0].source}: {mine[0].title[:90]}", "url": mine[0].url})
            lines.append(f"• {_t(journal.DOMAINS[key][0])} ({len(mine)}): " + (", ".join(top) if top else mine[0].title[:80]) + f" [{len(sources)}]")
    market = _market_line(db, field)
    if market:
        lines.append(market)
    return {"reply": "\n".join(lines), "links": [{"label": "Open the journal", "url": "/journal" + (f"?domain={field}" if field else "")}],
            "source": "journal", "sources": sources + [{"label": _t("The journal"), "url": "/journal"}], "resources": []}


def _taught_terms(db: Session, field: str, terms: list[str]) -> dict:
    from app.services import journal, market

    text = " ".join(market.program_text(db, program) for program in journal.programs_by_domain(db).get(field, [])).lower()
    text = " " + re.sub(r"[^a-z0-9.+#-]+", " ", text) + " "
    return {"taught": [term for term in terms if f" {term} " in text], "missing": [term for term in terms if f" {term} " not in text]}


def _market_line(db: Session, field: str | None) -> str | None:
    try:
        from app.services import market

        rows = market.trends(db)
    except Exception:
        return None
    if field:
        rows = [row for row in rows if row["key"] == field or (field == "flutter" and row["key"] == "mobile")]
    rows = [row for row in rows if row["offers"]][:3]
    if not rows:
        return None
    return _t("The job offers read so far ask most for: {0}.", ", ".join(f"{row['name']} ({row['offers']})" for row in rows))


# ------------------------------------------------------------------
# Services to add
# ------------------------------------------------------------------

def services(db: Session) -> dict:
    from app.services import journal

    lines = [_t("Services the centre could add, the best supported first:")]
    sources = []
    try:
        from app.services import market

        rows = [row for row in market.trends(db) if not row["taught"] and row["enough"]][:3]
    except Exception:
        rows = []
    for row in rows:
        lines.append("• " + _t("A program in {0}: {1} job offers ask for it and no program teaches it here.", row["name"], row["offers"]))
    if rows:
        sources.append({"label": _t("The job offers"), "url": "/programs/market"})
    ideas = [item for item in journal.suggestions(db, most=6) if not item["taught"]][:2]
    for item in ideas:
        lines.append("• " + item["text"])
    if ideas:
        sources.append({"label": _t("The journal"), "url": "/journal"})
    meaning = _meaning_of(_own_name(db))
    fitting = _SERVICES_FOR.get(meaning[0] if meaning else "", [])[:3]
    for idea in fitting:
        lines.append("• " + _t(idea))
    for idea in _ALWAYS[: max(1, 5 - len(rows) - len(ideas) - len(fitting))]:
        lines.append("• " + _t(idea))
    if fitting:
        lines.append(_t("The ones that follow the centre's name come from what “{0}” means.", _own_name(db)))
    sources.append({"label": _t("Ideas that suit a training centre (fixed list)")})
    return {"reply": "\n".join(lines), "links": [{"label": "What the market asks", "url": "/programs/market"}, {"label": "Open the journal", "url": "/journal"}],
            "source": "system", "sources": sources, "resources": []}


# ------------------------------------------------------------------
# What a name says
# ------------------------------------------------------------------

def _own_name(db: Session) -> str:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    return (settings.platform_name or "9antra").strip() or "9antra"


def _quoted(message: str) -> str | None:
    found = re.search(r"[«“\"']\s*([^«»“”\"']{2,40}?)\s*[»”\"']", message or "")
    return found.group(1).strip() if found else None


def _name_in(db: Session, message: str) -> str:
    quoted = _quoted(message)
    if quoted:
        return quoted
    # The words around the name in any case; a second word of the name only with a capital (“Code Lab”, not “est-il”).
    found = re.search(r"(?i:le nom|the name|nom de|name of|name|nom|appel(?:er|lation))\s+(?:(?i:de|of)\s+)?([A-Za-z0-9][\w-]{1,24}(?:\s+[A-Z][\w-]{1,20})?)",
                      message or "")
    if found and found.group(1).lower() not in {"est", "is", "du", "de", "of", "the", "le", "la", "ce", "this", "notre", "our", "centre", "center"}:
        return found.group(1).strip()
    for word in re.findall(r"[\w-]+", message or ""):
        if word.lower() in _MEANINGS:
            return word
    return _own_name(db)


_STRONGEST = ["bridge", "skills", "lab", "hub", "future", "desk", "place", "code"]


def _meaning_of(name: str) -> tuple[str, str] | None:
    """What the name means: a whole word first, else a known word inside a joined one (“CodeLab”), the strongest first."""
    words = re.findall(r"[\w؀-ۿ]+", (name or "").lower())
    for word in words:
        if word in _MEANINGS:
            return _MEANINGS[word]
    inside = [_MEANINGS[key] for word in words for key in _MEANINGS if len(key) >= 3 and key in word]
    inside.sort(key=lambda meaning: _STRONGEST.index(meaning[0]) if meaning[0] in _STRONGEST else len(_STRONGEST))
    return inside[0] if inside else None


def name_reading(db: Session, name: str) -> dict:
    """A reading of a name by five fixed criteria, each 0 to 2, out of 10, with what it suggests."""
    letters = re.sub(r"[^\w]", "", name or "")
    words = [word.lower() for word in re.findall(r"[\w؀-ۿ]+", name or "")]
    marks, lines = [], []
    short = 2 if len(letters) <= 7 else 1 if len(letters) <= 12 else 0
    marks.append(short)
    lines.append(_t("Length: {0} letters, {1}.", len(letters), _t("short and easy to remember") if short == 2 else
                    _t("fine") if short == 1 else _t("long: people will shorten it")))
    arabizi = re.search(r"[a-z][2379]|[2379][a-z]", (name or "").lower()) is not None
    hard = re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", (name or "").lower()) is not None
    said = 0 if hard else 1 if arabizi else 2
    marks.append(said)
    lines.append(_t("How it is said: {0}.", _t("figures used as letters, natural for Tunisians on WhatsApp, harder to spell for others")
                    if arabizi else _t("hard to say: many consonants together") if hard else _t("easy to say in French, Arabic and English")))
    meaning = _meaning_of(name)
    meant = 2 if meaning and meaning[0] in ("bridge", "skills", "lab", "hub") else 1 if meaning else 0
    marks.append(meant)
    lines.append(_t("What it means: {0}", _t(meaning[1]) if meaning else _t("no meaning I know: it can still work, but its story has to be told.")))
    generic = [word for word in words if word in _GENERIC]
    stands = 0 if words and len(generic) == len(words) else 1 if generic else 2
    marks.append(stands)
    lines.append(_t("How it stands out: {0}.", _t("only common words: easy to confuse with other centres") if stands == 0 else
                    _t("one common word: the other one carries it") if stands == 1 else _t("its own word: easy to find online")))
    fits = 2 if meaning and meaning[0] in ("bridge", "skills", "hub", "lab") else 1
    marks.append(fits)
    lines.append(_t("What it fits: {0}.", _t("every program the centre teaches, and what comes after them (a job)") if fits == 2 else
                    _t("some programs more than others")))
    score = sum(marks)
    verdict = (_t("A strong name: keep it and tell its story.") if score >= 8 else
               _t("A good name, with one point to watch.") if score >= 6 else _t("A name to explain, or to rethink."))
    head = _t("“{0}”: {1}/10. {2}", name, score, verdict)
    ideas = _SERVICES_FOR.get(meaning[0] if meaning else "", [])
    tail = [_t("Services it suggests:")] + ["• " + _t(idea) for idea in ideas[:4]] if ideas else []
    return {"reply": "\n".join([head, *lines, *tail]), "links": [], "source": "system",
            "sources": [{"label": _t("A reading by fixed criteria: length, how it is said, what it means, how it stands out, what it fits")}],
            "resources": [], "score": score}
