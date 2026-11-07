"""The journal: what is new in each field the centre teaches, and what it suggests.

Public news feeds (RSS or Atom) are read every six hours, a few per field (DevOps, AI, cyber security, the web, Python,
mobile, BI and data, IoT, digital marketing): each article's title, link and first sentences, as the feed gives them.
The centre's own LinkedIn page is read too when its access is set (LinkedIn lets no application read anything else:
a post or an article can be pasted on the journal page). Nothing is sent to these sites, and no model is asked.

What it suggests comes from the words of each field the articles name (program_match.SUBJECT_WORDS and the new words of
each field below): a word many articles of the last weeks name that a program of that field does not teach yet is a
subject to consider adding; one it teaches already is something to say about it.
"""
from __future__ import annotations

import html
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from app.models.news_item import NewsItem

logger = logging.getLogger(__name__)

EVERY_HOURS = 6
KEEP_DAYS = 60
MOST_BYTES = 2_000_000
DOMAINS = {
    "ai": ("Artificial intelligence", "bi-robot"),
    "devops": ("DevOps and cloud", "bi-cloud-arrow-up"),
    "cyber": ("Cyber security", "bi-shield-lock"),
    "web": ("Web development", "bi-code-slash"),
    "python": ("Python", "bi-filetype-py"),
    "flutter": ("Mobile", "bi-phone"),
    "bi": ("BI and data", "bi-bar-chart"),
    "iot": ("Internet of things", "bi-cpu"),
    "marketing": ("Digital marketing", "bi-megaphone"),
    "general": ("Tech in general", "bi-newspaper"),
}
FEEDS = [
    ("ai", "Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("ai", "MarkTechPost", "https://www.marktechpost.com/feed/"),
    ("devops", "Docker blog", "https://www.docker.com/blog/feed/"),
    ("devops", "DEV · DevOps", "https://dev.to/feed/tag/devops"),
    ("devops", "Kubernetes", "https://kubernetes.io/feed.xml"),
    ("cyber", "The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
    ("cyber", "BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
    ("web", "DEV · JavaScript", "https://dev.to/feed/tag/javascript"),
    ("web", "DEV · React", "https://dev.to/feed/tag/react"),
    ("python", "Real Python", "https://realpython.com/atom.xml"),
    ("python", "Python Insider", "https://blog.python.org/feeds/posts/default"),
    ("flutter", "Flutter on Medium", "https://medium.com/feed/flutter"),
    ("flutter", "DEV · Flutter", "https://dev.to/feed/tag/flutter"),
    ("bi", "Power BI blog", "https://powerbi.microsoft.com/en-us/blog/feed/"),
    ("bi", "DEV · Data science", "https://dev.to/feed/tag/datascience"),
    ("iot", "Hackaday", "https://hackaday.com/feed/"),
    ("iot", "DEV · Arduino", "https://dev.to/feed/tag/arduino"),
    ("iot", "DEV · IoT", "https://dev.to/feed/tag/iot"),
    ("marketing", "HubSpot Marketing", "https://blog.hubspot.com/marketing/rss.xml"),
    ("marketing", "Social Media Today", "https://www.socialmediatoday.com/feeds/news/"),
    ("general", "TechCrunch", "https://techcrunch.com/feed/"),
]
# What is new in each field, besides the words of its courses.
NEW_WORDS = {
    "ai": "agent agents agentic llm llms rag multimodal fine-tuning mcp copilot gemini claude llama mistral openai embeddings vector reasoning",
    "devops": "gitops argocd opentelemetry platform engineering serverless finops observability ebpf podman helm terraform opentofu",
    "cyber": "ransomware zero-day phishing zero trust supply chain mfa passkeys cve exploit exploited patch patched breach vulnerability "
             "vulnerabilities flaw flaws malware attack attacks attackers hackers backdoor botnet credentials authentication",
    "web": "next.js nextjs svelte htmx bun deno vite astro typescript tailwind server components webassembly",
    "python": "uv polars fastapi pydantic asyncio typing pandas numpy django flask",
    "flutter": "flutter dart impeller riverpod bloc kotlin swiftui compose multiplatform",
    "bi": "fabric copilot dax power query lakehouse dbt snowflake databricks semantic model analytics dashboard dashboards data "
          "warehouse etl sql visualization",
    "iot": "matter thread lorawan edge esp32 esp32-s3 raspberry arduino sensor sensors digital twin mqtt 5g microcontroller firmware",
    "marketing": "seo geo ai overviews tiktok threads instagram reels linkedin ads google ads meta ads ugc influencer",
    "general": "",
}
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _strip(text: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def _brief(text: str, most: int = 260) -> str:
    """The first sentences of an article's summary, briefly."""
    text = _strip(text)
    if len(text) <= most:
        return text
    cut = text[:most]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…")


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment.astimezone(tz=None).replace(tzinfo=None) if moment.tzinfo else moment


def parse(data: bytes) -> list[dict]:
    """The articles of an RSS or Atom feed: [{"title", "url", "summary", "published"}]. Nothing from a document that
    declares entities (they could make it explode)."""
    from xml.etree import ElementTree

    if not data or len(data) > MOST_BYTES or b"<!ENTITY" in data[:4000].upper():
        return []
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return []

    def bare(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    found = []
    for node in root.iter():
        if bare(node.tag) not in ("item", "entry"):
            continue
        fields = {bare(child.tag): child for child in node}
        title = _strip(fields["title"].text if "title" in fields else "")
        link = ""
        for child in node:
            if bare(child.tag) == "link":
                link = (child.get("href") or child.text or "").strip()
                if child.get("rel") in (None, "alternate"):
                    break
        text = ""
        for key in ("description", "summary", "content", "encoded"):
            if key in fields and (fields[key].text or "").strip():
                text = fields[key].text
                break
        moment = None
        for key in ("pubdate", "published", "updated", "date"):
            if key in fields:
                moment = _when(fields[key].text)
                if moment:
                    break
        if title and link.startswith(("http://", "https://")):
            found.append({"title": title[:300], "url": link[:600], "summary": _brief(text), "published": moment,
                          "image": _image_of(node, text or "")})
    return found


_IMG = re.compile(r"""<img[^>]+src=["'](https://[^"'\s>]+)["']""", re.IGNORECASE)


def _image_of(node, text: str) -> str | None:
    """The article's photo: its media:content or media:thumbnail, an image enclosure, or the first picture of its text.
    Only https addresses (the pages load it as it is)."""
    for child in node.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        url = (child.get("url") or child.get("href") or "").strip()
        if tag in ("content", "thumbnail") and url.startswith("https://") and (child.get("medium") in (None, "image")
                                                                                 and not (child.get("type") or "image").startswith(("video", "audio"))):
            return url[:600]
        if tag in ("enclosure", "link") and url.startswith("https://") and (child.get("type") or "").startswith("image/"):
            return url[:600]
    found = _IMG.search(html.unescape(text or ""))
    return found.group(1)[:600] if found else None


def vocabulary(domain: str) -> set[str]:
    from app.services.program_match import SUBJECT_WORDS

    subject = {"web": "mern"}.get(domain, domain)
    words = set((SUBJECT_WORDS.get(subject, "") + " " + SUBJECT_WORDS.get("web" if domain == "web" else "", "")).split())
    words |= set(NEW_WORDS.get(domain, "").split())
    return {word for word in words if len(word) >= 3}


def terms_in(text: str, domain: str) -> list[str]:
    plain = " " + re.sub(r"[^a-z0-9.+#-]+", " ", (text or "").lower()) + " "
    return sorted(word for word in vocabulary(domain) if f" {word} " in plain)


def fetch(url: str) -> bytes | None:
    """One feed (replaced in tests). None when it cannot be read."""
    import requests

    try:
        answer = requests.get(url, timeout=20, headers={"User-Agent": "training-centre-journal/1.0"}, stream=True)
        answer.raise_for_status()
        data = answer.raw.read(MOST_BYTES + 1, decode_content=True)
        return data if len(data) <= MOST_BYTES else None
    except Exception as exc:
        logger.warning("The feed %s could not be read: %s", url, exc)
        return None


def keep(db: Session, domain: str, source: str, item: dict) -> bool:
    """Keep an article once (by its link). True when it is new."""
    if item.get("url"):
        known = db.query(NewsItem).filter(NewsItem.url == item["url"]).first()
        if known is not None:
            if item.get("image") and not known.image_url:
                known.image_url = item["image"]          # read before photos were kept: it gets its photo now
            return False
    db.add(NewsItem(domain=domain, source=source, title=item["title"], url=item.get("url"), summary=item.get("summary") or None,
                    image_url=item.get("image") or None,
                    terms=json.dumps(terms_in(f"{item['title']} {item.get('summary') or ''}", domain)),
                    published_at=item.get("published") or datetime.utcnow(), fetched_at=datetime.utcnow()))
    return True


def read_all(db: Session) -> dict:
    """Every feed, once: {"new", "feeds", "failed"}."""
    new = failed = 0
    for domain, source, url in FEEDS:
        data = fetch(url)
        articles = parse(data) if data else []
        if not articles:
            failed += 1
            continue
        for item in articles[:25]:
            new += keep(db, domain, source, item)
        db.commit()
    new += _linkedin(db)
    db.query(NewsItem).filter(NewsItem.fetched_at < datetime.utcnow() - timedelta(days=KEEP_DAYS),
                              NewsItem.source != "Pasted").delete(synchronize_session=False)
    db.commit()
    return {"new": new, "feeds": len(FEEDS), "failed": failed}


def _linkedin(db: Session) -> int:
    """The centre's own LinkedIn posts, when LinkedIn's access is set (Settings): nothing else can be read there."""
    from app.services import linkedin

    if not linkedin.configured():
        return 0
    new = 0
    try:
        for post in linkedin.fetch_posts(limit=10):
            text = post.get("text") or post.get("summary") or ""
            item = {"title": _brief(text, 120) or "LinkedIn", "url": post.get("url"), "summary": _brief(text), "published": post.get("published")}
            new += keep(db, "general", "LinkedIn · 9antra", item)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("The LinkedIn page could not be read for the journal.")
    return new


def last_read(db: Session) -> datetime | None:
    row = db.query(NewsItem.fetched_at).filter(NewsItem.source != "Pasted").order_by(NewsItem.fetched_at.desc()).first()
    return row[0] if row else None


def read_if_due(db: Session, now: datetime | None = None) -> dict | None:
    now = now or datetime.utcnow()
    last = last_read(db)
    if last is not None and now - last < timedelta(hours=EVERY_HOURS):
        return None
    return read_all(db)


def add_pasted(db: Session, text: str, domain: str = "", url: str = "") -> NewsItem | None:
    """A LinkedIn post or an article the admin pasted: kept in the journal, its field found from its words."""
    text = (text or "").strip()
    if len(text) < 20:
        return None
    if domain not in DOMAINS:
        counts = {key: len(terms_in(text, key)) for key in DOMAINS if key != "general"}
        domain = max(counts, key=counts.get) if counts and max(counts.values()) > 0 else "general"
    link = url.strip() if url.strip().startswith(("http://", "https://")) else None
    if link and db.query(NewsItem.id).filter(NewsItem.url == link).first() is not None:
        return None
    first = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)[0]
    item = NewsItem(domain=domain, source="Pasted", title=first[:160], url=link, summary=_brief(text, 400),
                    terms=json.dumps(terms_in(text, domain)), published_at=datetime.utcnow(), fetched_at=datetime.utcnow())
    db.add(item)
    db.commit()
    return item


# ------------------------------------------------------------------
# What it suggests
# ------------------------------------------------------------------

def programs_by_domain(db: Session) -> dict[str, list]:
    from app.models import Program
    from app.services import level_bank

    found: dict[str, list] = {}
    for program in db.query(Program).order_by(Program.name).all():
        subject = level_bank.subject_of(program.name, program.website_slug)
        domain = {"mern": "web", "spring": "web"}.get(subject, subject)
        if domain in DOMAINS:
            found.setdefault(domain, []).append(program)
    return found


def suggestions(db: Session, days: int = 30, most: int = 6) -> list[dict]:
    """What the articles of the last weeks suggest for each program: {"domain", "program", "term", "count", "taught", "text"}."""
    from app.services import market
    from app.services.ui_words import t as _t

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(NewsItem).filter(NewsItem.published_at >= since).all()
    by_domain: dict[str, Counter] = {}
    for row in rows:
        try:
            for term in json.loads(row.terms or "[]"):
                by_domain.setdefault(row.domain, Counter())[term] += 1
        except ValueError:
            continue
    found = []
    for domain, programs in programs_by_domain(db).items():
        counts = by_domain.get(domain)
        if not counts:
            continue
        for program in programs[:2]:
            text = " " + re.sub(r"[^a-z0-9.+#-]+", " ", market.program_text(db, program).lower()) + " "
            for term, count in counts.most_common(8):
                if count < 2:
                    break
                taught = f" {term} " in text
                found.append({"domain": domain, "program": program, "term": term, "count": count, "taught": taught,
                              "text": (_t("{0} articles speak of “{1}”, which {2} already teaches: say so in its posts and its page.", count, term, program.name)
                                       if taught else
                                       _t("{0} articles speak of “{1}”, which {2} does not teach yet: a session or a week to consider.", count, term, program.name))})
                break
    found.sort(key=lambda item: (item["taught"], -item["count"]))
    return found[:most]


def latest(db: Session, domain: str | None = None, limit: int = 30) -> list[NewsItem]:
    query = db.query(NewsItem)
    if domain in DOMAINS:
        query = query.filter(NewsItem.domain == domain)
    return query.order_by(NewsItem.published_at.desc()).limit(limit).all()


def for_dashboard(db: Session) -> dict:
    """A few headlines, one per field first, and what they suggest."""
    items, seen = [], set()
    for row in latest(db, limit=80):
        if row.domain in seen:
            continue
        seen.add(row.domain)
        items.append(row)
        if len(items) >= 5:
            break
    return {"items": items, "suggestions": suggestions(db, most=3), "last": last_read(db), "domains": DOMAINS}


# ------------------------------------------------------------------
# The journal beside every page
# ------------------------------------------------------------------

PANEL_SIZE = 12


def _mixed(rows: list[NewsItem], most: int) -> list[NewsItem]:
    """The newest of each field in turn, so one busy feed does not fill it (the ones with a photo first in each round)."""
    by_domain: dict[str, list[NewsItem]] = {}
    for row in rows:
        by_domain.setdefault(row.domain, []).append(row)
    for items in by_domain.values():
        items.sort(key=lambda row: (row.image_url is None, -(row.published_at or row.fetched_at or datetime.min).timestamp()))
    found = []
    while len(found) < most and any(by_domain.values()):
        for domain in sorted(by_domain, key=lambda key: -max(((row.published_at or row.fetched_at or datetime.min).timestamp()
                                                               for row in by_domain[key]), default=0)):
            if by_domain[domain] and len(found) < most:
                found.append(by_domain[domain].pop(0))
    return found


def professor_programs(db: Session, professor_id: str | None) -> list:
    """The programs a professor teaches (their classes')."""
    from app.models import ClassGroup

    if not professor_id:
        return []
    seen, programs = set(), []
    for class_group in db.query(ClassGroup).filter(ClassGroup.professor_id == professor_id).all():
        if class_group.program is not None and class_group.program.id not in seen:
            seen.add(class_group.program.id)
            programs.append(class_group.program)
    return programs


def panel(db: Session, user=None) -> dict:
    """What the journal beside every page shows: the latest articles with their photo, field after field; for a
    professor, the fields they teach and what could make their courses better (the articles, and the job offers of the
    field: recommender.recommend)."""
    programs = professor_programs(db, getattr(user, "professor_id", None)) if getattr(user, "is_professor", False) else []
    ids = {program.id for program in programs}
    domains = {domain for domain, found in programs_by_domain(db).items() if any(program.id in ids for program in found)} if programs else set()
    rows = latest(db, limit=120)
    if domains:
        rows = [row for row in rows if row.domain in domains] or rows
    advice = []
    if programs:
        from app.services import recommender

        for program in programs[:3]:
            try:
                items = recommender.recommend(db, program)["items"]
            except Exception:
                logger.exception("No recommendation for %s.", program.name)
                items = []
            for item in [item for item in items if item["kind"] in ("add", "change", "strengthen")][:2]:
                advice.append({"program": program.name, "title": item["title"], "why": item["why"], "kind": item["kind"]})
    said = suggestions(db, most=12)
    if programs:
        said = [item for item in said if item["program"].id in ids]
    newest = max((row.fetched_at for row in rows if row.fetched_at), default=None)
    return {"articles": _mixed(rows, PANEL_SIZE), "suggestions": said[:3], "advice": advice[:4], "programs": programs,
            "last": last_read(db), "newest": newest, "domains": DOMAINS, "mine": bool(programs)}


def new_since(db: Session, moment: datetime | None) -> int:
    """How many articles arrived since this browser last opened the journal."""
    if moment is None:
        return min(PANEL_SIZE, db.query(NewsItem.id).count())
    return db.query(NewsItem.id).filter(NewsItem.fetched_at > moment).count()
