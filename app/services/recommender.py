"""Recommendations for each program, renewed every 24 hours from several public sources.

Every day, for each skill of a program's field (market.SKILLS), the application keeps:
  - how many job offers of the field ask it (Remotive, Arbeitnow, Jobicy, Himalayas, 9antra.tn, offers pasted by the
    admin; see market.py), and how many Hacker News “Who is hiring” posts of the month name it;
  - how busy the developers' community is with it: new public GitHub projects about it in the last 30 days, and
    dev.to articles about it in the last 30 days.
Kept day after day (table market_signals), these show what rises and what falls. Then, for the program:
  - ADD        what the offers ask a lot (or more and more) and the program does not teach;
  - STRENGTHEN what it teaches in passing (one week at most) while many offers ask it;
  - CHANGE     what it teaches when the offers ask much more for what replaced it (hand-written pairs, checked with
               the numbers: jQuery and React, Jenkins and GitHub Actions, Talend and dbt / Airflow…);
  - REDUCE     what it teaches that almost no offer asks any more, and that is falling;
  - KEEP       what it teaches and many offers ask: to put forward.
Each recommendation shows its numbers and where they come from; no model writes them. Nothing is read from LinkedIn or
Facebook. A source that does not answer is left out for the day, and said so.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

import requests
from sqlalchemy.orm import Session

from app.models.market_signal import MarketSignal

logger = logging.getLogger(__name__)

GITHUB = "https://api.github.com/search/repositories"
DEVTO = "https://dev.to/api/articles"
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{}"
GITHUB_PAUSE = 6.5          # seconds between two GitHub searches: ten a minute without an account
DEVTO_PAUSE = 1.0
ADD_FROM = 15               # percent of the field's offers from which a missing skill is worth adding
RISING = 5                  # points gained in 30 days that make a skill "rising"
STRENGTHEN_FROM = 30
REDUCE_UNDER = 3
KEEP_FROM = 25

# A skill as a GitHub topic and a dev.to tag; None: not a word a community tags (Content, Statistics…).
TAGS = {
    "CI/CD": ("ci-cd", "cicd"), "GitHub Actions": ("github-actions", "githubactions"), "GitLab CI": ("gitlab-ci", "gitlab"),
    "Google Cloud": ("google-cloud", "googlecloud"), "GitOps / ArgoCD": ("argocd", "argocd"), "Node.js": ("nodejs", "node"),
    "Next.js": ("nextjs", "nextjs"), "Spring Boot": ("spring-boot", "springboot"), "Hibernate / JPA": ("hibernate", "hibernate"),
    "Maven / Gradle": ("gradle", "gradle"), "Kafka / RabbitMQ": ("kafka", "kafka"), "Spring Security": ("spring-security", "springsecurity"),
    "Power BI": ("powerbi", "powerbi"), "Looker / Qlik": ("looker", "looker"), "Machine learning": ("machine-learning", "machinelearning"),
    "Deep learning": ("deep-learning", "deeplearning"), "LLMs / generative AI": ("llm", "llm"), "RAG and vector search": ("rag", "rag"),
    "Hugging Face": ("huggingface", "huggingface"), "FastAPI / Flask": ("fastapi", "fastapi"), "Computer vision": ("computer-vision", "computervision"),
    "Prompt engineering": ("prompt-engineering", "promptengineering"), "Recommender systems": ("recommender-system", "recommendersystems"),
    "Scikit-learn": ("scikit-learn", "scikitlearn"), "ESP32 / Arduino": ("esp32", "esp32"), "Raspberry Pi": ("raspberry-pi", "raspberrypi"),
    "C / C++": ("cpp", "cpp"), "Embedded Linux": ("embedded-linux", "embedded"), "Bluetooth / BLE": ("bluetooth-low-energy", "bluetooth"),
    "IoT cloud platforms": ("iot-platform", "iot"), "Node-RED": ("node-red", "nodered"), "Edge AI": ("tinyml", "tinyml"),
    "Penetration testing": ("penetration-testing", "pentesting"), "Kali Linux": ("kali-linux", "kali"), "Burp Suite": ("burp-suite", "burpsuite"),
    "Active Directory": ("active-directory", "activedirectory"), "Threat intelligence": ("threat-intelligence", "threatintelligence"),
    "Red teaming": ("red-team", "redteam"), "Google Ads": ("google-ads", "googleads"), "Google Analytics": ("google-analytics", "googleanalytics"),
    "Email marketing": ("email-marketing", "emailmarketing"), "Marketing automation": ("marketing-automation", "marketing"),
    "React Native": ("react-native", "reactnative"), "UI/UX and Figma": ("figma", "figma"), "PHP / Laravel": ("laravel", "laravel"),
    "Tailwind / Bootstrap": ("tailwindcss", "tailwindcss"), "Tailwind": ("tailwindcss", "tailwindcss"), "Redux": ("redux", "redux"),
    "Authentication (JWT, OAuth)": ("oauth2", "oauth"), "WebSockets": ("websocket", "websocket"), "Vue": ("vuejs", "vue"),
    "Web performance": ("web-performance", "webperf"), "Cloud (AWS, Azure)": ("aws", "aws"), "Cloud (AWS, Azure, GCP)": ("aws", "aws"),
    "Azure data tools": ("databricks", "databricks"), "State management": ("riverpod", "riverpod"), "Push notifications": ("push-notifications", "pushnotifications"),
    "Local storage": ("sqlite", "sqlite"), "Data warehouse": ("data-warehouse", "datawarehouse"), "Data modelling": ("data-modeling", "datamodeling"),
}
_SKIP = {"Content", "Branding", "Statistics", "Sensors", "Testing", "Dashboards and reporting", "Data analysis", "Automation", "Security",
         "Standards (ISO 27001, NIST, GDPR)", "Certifications (CEH, OSCP, CISSP)", "KPIs and reporting", "Influence", "Conversion (landing pages)",
         "Design (Canva, Adobe)", "Video", "Social media", "Publishing to the stores", "Electronics and PCB", "Industrial protocols", "Wi-Fi",
         "Responsive design", "Accessibility", "Object-oriented programming", "Async programming", "Incident response", "Network security",
         "Vulnerability management", "Scripting", "SOC", "SIEM", "Cloud security", "Monitoring", "Microservices", "DevSecOps", "Chatbots",
         "AI tools", "E-commerce", "CRM", "Excel", "ETL", "REST API", "HTML/CSS", "Sensors", "Firmware", "RTOS", "LoRa", "MQTT", "Forensics"}

# What was taught, and what the offers ask instead today: (old name, how the program writes it, new name, how offers write it, why).
REPLACED_BY = [
    ("jQuery", r"jquery|js ?query", "React", r"react(\.js)?", "front-end offers now ask a component framework more than jQuery"),
    ("Bootstrap", r"bootstrap", "Tailwind", r"tailwind\w*", "Tailwind CSS is asked more and more in front-end offers"),
    ("Jenkins", r"jenkins", "GitHub Actions / GitLab CI", r"github actions|gitlab", "pipelines now live with the code (GitHub Actions, GitLab CI)"),
    ("Talend", r"talend", "dbt / Airflow", r"\bdbt\b|airflow", "data teams now build their pipelines with dbt and Airflow"),
    ("AngularJS", r"angularjs|angular\.js", "Angular", r"angular(?!\.?js)", "AngularJS is no longer maintained; offers ask the current Angular"),
    ("Create React App", r"create[- ]react[- ]app", "Vite / Next.js", r"vite|next\.?js", "Create React App is no longer maintained"),
    ("TensorFlow", r"tensorflow", "PyTorch", r"pytorch", "research and many teams moved to PyTorch"),
    ("Docker Swarm", r"docker swarm|swarm", "Kubernetes", r"kubernetes|k8s", "Kubernetes is what teams orchestrate containers with"),
]


def _get(url: str, **params):
    return requests.get(url, params=params, timeout=20, headers={"Accept": "application/json", "User-Agent": "9antra-training-centre"})


def github_count(topic: str, since: date) -> int | None:
    try:
        response = _get(GITHUB, q=f"topic:{topic} created:>{since:%Y-%m-%d}", per_page=1)
        if response.status_code != 200:
            return None
        return int(response.json().get("total_count") or 0)
    except (requests.RequestException, ValueError):
        return None


def devto_count(tag: str) -> int | None:
    try:
        response = _get(DEVTO, tag=tag, top=30, per_page=1000)
        if response.status_code != 200:
            return None
        return len(response.json())
    except (requests.RequestException, ValueError):
        return None


def hn_posts(limit: int = 600) -> list[str]:
    """The posts of the latest Hacker News “Who is hiring?” thread (job offers written by companies)."""
    try:
        found = _get(HN_SEARCH, tags="story,author_whoishiring", query="who is hiring", hitsPerPage=3)
        hits = [hit for hit in found.json().get("hits") or [] if "hiring" in (hit.get("title") or "").lower()]
        if not hits:
            return []
        thread = _get(HN_ITEM.format(hits[0]["objectID"])).json()
    except (requests.RequestException, ValueError, KeyError):
        return []
    texts = []
    for child in (thread.get("children") or [])[:limit]:
        text = re.sub(r"<[^>]+>", " ", child.get("text") or "")
        if len(text) > 80:
            texts.append(text)
    return texts


def tags_of(skill: str) -> tuple[str, str] | None:
    if skill in _SKIP:
        return None
    if skill in TAGS:
        return TAGS[skill]
    slug = re.sub(r"[^a-z0-9]+", "-", skill.lower()).strip("-")
    return (slug, slug.replace("-", "")) if slug and len(slug) <= 20 else None


def subjects_in_use(db: Session) -> list[str]:
    from app.models import Program
    from app.services import level_bank

    found = []
    for program in db.query(Program).all():
        subject = level_bank.subject_of(program.name, program.website_slug)
        if subject and subject not in found:
            found.append(subject)
    return found


def last_day(db: Session) -> date | None:
    row = db.query(MarketSignal.day).order_by(MarketSignal.day.desc()).first()
    return row[0] if row else None


def refresh(db: Session, today: date | None = None, *, community: bool = True, pause: bool = True) -> dict:
    """Read today's signals for every field the programs belong to, and keep them. Returns what was read."""
    from app.services import market

    today = today or date.today()
    since = today - timedelta(days=30)
    offers = market.all_offers(db)
    subjects = subjects_in_use(db)
    db.rollback()      # nothing is held open while the sources are read (SQLite: an open read would block every save)
    posts = hn_posts() if community else []
    done = {"day": today.isoformat(), "fields": 0, "skills": 0, "github": 0, "devto": 0, "hn_posts": len(posts), "failed": []}
    probed: dict[str, tuple] = {}
    rows = []      # read everything first (minutes, with the pauses), then write it all at once: the database is never held
    for subject in subjects:
        mine = [offer for offer in offers if subject in offer["fields"]]
        total = len(mine)
        done["fields"] += 1
        for name, _ in market.SKILLS.get(subject, []):
            asked = sum(1 for offer in mine if name in market.skills_in(offer["text"], subject))
            hn = sum(1 for text in posts if name in market.skills_in(text, subject)) if posts else None
            github = devto = None
            tags = tags_of(name) if community else None
            if tags:
                if name not in probed:
                    github = github_count(tags[0], since)
                    if pause:
                        time.sleep(GITHUB_PAUSE)
                    devto = devto_count(tags[1])
                    if pause:
                        time.sleep(DEVTO_PAUSE)
                    probed[name] = (github, devto)
                    done["github"] += github is not None
                    done["devto"] += devto is not None
                    if github is None and devto is None:
                        done["failed"].append(name)
                github, devto = probed[name]
            rows.append(dict(day=today, subject=subject, skill=name, offers=asked, total=total, github=github, devto=devto, hn=hn))
            done["skills"] += 1
    db.query(MarketSignal).filter(MarketSignal.day == today).delete()
    db.add_all(MarketSignal(**row) for row in rows)
    db.commit()
    return done


def refresh_if_due(db: Session, now: datetime | None = None) -> dict | None:
    """Once a day (the market job calls it every few hours)."""
    today = (now or datetime.now()).date()
    if last_day(db) == today:
        return None
    return refresh(db, today)


def _signals(db: Session, subject: str) -> tuple[dict, dict, date | None, date | None]:
    """(today's signals by skill, the ones about 30 days before (or the oldest at least a week old), their days)."""
    days = [row[0] for row in db.query(MarketSignal.day).filter(MarketSignal.subject == subject).distinct().order_by(MarketSignal.day.desc()).all()]
    if not days:
        return {}, {}, None, None
    latest = days[0]
    target = latest - timedelta(days=30)
    older = [day for day in days if day <= latest - timedelta(days=7)]
    before = min(older, key=lambda day: abs((day - target).days)) if older else None
    now_rows = {row.skill: row for row in db.query(MarketSignal).filter(MarketSignal.subject == subject, MarketSignal.day == latest).all()}
    then_rows = ({row.skill: row for row in db.query(MarketSignal).filter(MarketSignal.subject == subject, MarketSignal.day == before).all()}
                 if before else {})
    return now_rows, then_rows, latest, before


def _pct(row) -> float:
    return round(row.offers / row.total * 100, 1) if row and row.total else 0.0


def recommend(db: Session, program) -> dict:
    """{"items": [...], "day", "compared_with", "sources", "how"} for one program."""
    from app.services import level_bank, market, market_content

    subject = level_bank.subject_of(program.name, program.website_slug)
    result = {"items": [], "day": None, "compared_with": None, "subject": subject, "how": HOW,
              "field": level_bank.SUBJECTS[subject][0] if subject else None}
    if subject is None:
        return result
    now_rows, then_rows, latest, before = _signals(db, subject)
    result["day"], result["compared_with"] = latest, before
    if not now_rows:
        return result
    taught = market.program_text(db, program)
    covered = market.skills_in(taught, subject)
    weeks_text = [" ".join(filter(None, (week.topic_title, week.learning_objectives))) for level in program.levels for week in level.weeks]
    patterns = dict(market._SKILL.get(subject, []))
    total = next(iter(now_rows.values())).total
    items = []
    for name, row in now_rows.items():
        pct = _pct(row)
        old = then_rows.get(name)
        delta = round(pct - _pct(old), 1) if old else None
        gh_growth = (row.github - old.github) if old and row.github is not None and old.github is not None else None
        pattern = patterns.get(name)
        depth = sum(1 for text in weeks_text if pattern and pattern.search(market.plain(text)))
        evidence = _evidence(row, old, delta, gh_growth, total, subject)
        community = (row.github or 0) / 50 + (row.devto or 0) / 10
        score = pct * 1.0 + (delta or 0) * 2 + min(20, community)
        common = {"skill": name, "pct": pct, "delta": delta, "covered": name in covered, "weeks": depth, "evidence": evidence,
                  "score": round(score, 1), "module": market_content.module_for(name)}
        if name not in covered and (pct >= ADD_FROM or (delta is not None and delta >= RISING and pct >= 8)):
            items.append({**common, "kind": "add", "title": f"Add {name}",
                          "why": f"{pct:g}% of the field's offers ask it" + (f" ({delta:+g} points in 30 days)" if delta is not None else "")
                                 + "; the program does not teach it."})
        elif name in covered and depth <= 1 and pct >= STRENGTHEN_FROM:
            items.append({**common, "kind": "strengthen", "title": f"Give more room to {name}",
                          "why": f"{pct:g}% of the offers ask it, and it fills {depth} week{'s' if depth != 1 else ''} of the program."})
        elif name in covered and pct < REDUCE_UNDER and (delta is None or delta <= 0) and total >= 20:
            items.append({**common, "kind": "reduce", "title": f"Teach less {name}", "score": round(10 - pct, 1),
                          "why": f"only {pct:g}% of the field's offers ask it" + (" and it is not rising" if delta is not None else "")
                                 + ": its weeks could go to what is asked more."})
        elif name in covered and pct >= KEEP_FROM:
            items.append({**common, "kind": "keep", "title": f"Put {name} forward",
                          "why": f"{pct:g}% of the offers ask it, and the program already teaches it: say so on 9antra.tn and in the brochure."})
    # What it teaches, and what replaced it in the offers.
    offers = [offer for offer in market.all_offers(db) if subject in offer["fields"]]
    plain_taught = market.plain(taught)
    for old_name, old_words, new_name, new_words, reason in REPLACED_BY:
        if not re.search(old_words, plain_taught, re.I):
            continue
        old_count = sum(1 for offer in offers if re.search(old_words, market.plain(offer["text"]), re.I))
        new_count = sum(1 for offer in offers if re.search(new_words, market.plain(offer["text"]), re.I))
        if len(offers) >= 10 and new_count >= max(3, old_count * 1.5) and not re.search(new_words, plain_taught, re.I):
            # The change says it better than "add" for the same skill: the "add" goes.
            items = [item for item in items if not (item["kind"] == "add" and re.search(new_words, item["skill"], re.I))]
            items.append({"kind": "change", "skill": new_name, "title": f"Change {old_name} for {new_name}", "covered": False,
                          "pct": round(new_count / len(offers) * 100, 1), "delta": None, "weeks": 0, "module": market_content.module_for(new_name),
                          "score": round(new_count / len(offers) * 100 + 10, 1),
                          "why": f"{reason}: {new_count} of {len(offers)} offers name {new_name}, {old_count} name {old_name}.",
                          "evidence": [{"label": f"Job offers of the field: {new_count} name {new_name}, {old_count} name {old_name}", "url": "/programs/market"}]})
    order = {"add": 0, "change": 1, "strengthen": 2, "reduce": 3, "keep": 4}
    items.sort(key=lambda item: (order[item["kind"]], -item["score"]))
    result["items"] = items[:14]
    return result


def _evidence(row, old, delta, gh_growth, total, subject) -> list[dict]:
    from app.services import market

    found = [{"label": f"Job offers: {row.offers} of {total} in the field" + (f" ({delta:+g} points since {old.day:%d %b})" if delta is not None else ""),
              "url": "/programs/market"}]
    if row.hn:
        found.append({"label": f"Hacker News “Who is hiring”: {row.hn} post{'s' if row.hn != 1 else ''} this month",
                      "url": "https://news.ycombinator.com/submitted?id=whoishiring"})
    tags = tags_of(row.skill)
    if row.github is not None and tags:
        found.append({"label": f"GitHub: {row.github:,} new public projects in 30 days".replace(",", " ")
                               + (f" ({gh_growth:+,})".replace(",", " ") if gh_growth else ""),
                      "url": f"https://github.com/topics/{tags[0]}"})
    if row.devto is not None and tags:
        found.append({"label": f"dev.to: {row.devto} article{'s' if row.devto != 1 else ''} in 30 days", "url": f"https://dev.to/t/{tags[1]}"})
    return found


HOW = ("Every 24 hours the application reads public job offers (Remotive, Arbeitnow, Jobicy, Himalayas, 9antra.tn, the offers you "
       "paste, and Hacker News “Who is hiring”), counts for each skill of the program's field how many offers ask it, and compares "
       "with about 30 days before. It also reads how busy developers are with it: new public GitHub projects and dev.to articles of "
       "the last 30 days. Then: add what is asked a lot and not taught; give more room to what is asked a lot and taught in one week "
       "at most; change what offers replaced (hand-written pairs, checked with the numbers); teach less what almost nobody asks any "
       "more; put forward what is taught and asked. Each line shows its numbers and their sources. No model writes them; nothing is "
       "read from LinkedIn or Facebook.")
