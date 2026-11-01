"""Ideas to keep candidates engaged, from everything they said (services/feedback_hub.py: the website, the reviews,
the weekly check-ins), how the classes go (presence, the weekly questions answered) and what is going on in every
industry (the journal's articles of the last 30 days).

Each idea says what it rests on (counted, never guessed by a model), a plan in two steps, and a SMART target: what to
measure, how much, by when. Nothing happens from here: each idea leads to the page where it is done.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

WINDOW_DAYS = 30
# Words the articles name often that are no subject for a workshop on their own.
EVERYDAY = set("""browser function functions model models data content security flaw flaws attack attacks attackers hackers patch patched
exploit exploited breach vulnerability vulnerabilities credentials authentication api apis cloud mobile sensor sensors dashboard dashboards
analytics sql app apps code web server python iot ai edge platform engineering typing reasoning warehouse visualization protocol
protocols package packages library libraries framework frameworks tool tools network networks device devices system systems release version
update digital twin""".split())
# What to do when people ask to improve a subject: a title and two steps, written by hand.
PLANS = {
    "trainer": ("bi-person-video3", "More time with the trainer",
                ("A 30-minute question time with the trainer after each session.", "The trainer names one person's progress each week.")),
    "support": ("bi-life-preserver", "Closer follow-up",
                ("A 15-minute call each week for those who struggle.", "A group where the professor answers within 24 hours.")),
    "content": ("bi-journal-richtext", "Refresh the weeks that came back",
                ("Read the weeks they named and update the weakest one first.", "One real-world example in every week.")),
    "practice": ("bi-tools", "More hands-on",
                 ("A mini-project each week, shown in the last 20 minutes.", "A final project presented to the group.")),
    "atmosphere": ("bi-people-fill", "A closer group",
                   ("A short game to meet each other in the first session.", "A group chat and one meet-up a month.")),
    "organisation": ("bi-calendar2-check", "A steadier rhythm",
                     ("A 10-minute break every 90 minutes.", "The plan of the session sent the day before.")),
    "career": ("bi-briefcase-fill", "Towards a job",
               ("A CV and LinkedIn workshop.", "A former candidate who found a job comes to talk.")),
    "value": ("bi-cash-coin", "Show what the price includes",
              ("List what is included: follow-up, files, certificate.", "Offer to pay in parts.")),
}


def _naive(moment):
    return moment.replace(tzinfo=None) if moment is not None and moment.tzinfo else moment


def _by(days: int) -> str:
    moment = datetime.now() + timedelta(days=days)
    return moment.strftime("%d/%m")


def from_what_they_said(db: Session, voices: dict | None = None) -> list[dict]:
    """Plans from what people asked to improve, and from what they liked."""
    from app.models.social import FeedbackAnswer
    from app.services import feedback_hub

    voices = voices or feedback_hub.overview(db)
    happy = voices.get("happy_share")
    ideas = []
    for item in voices.get("improve", [])[:3]:
        plan = PLANS.get(item["key"])
        if plan is None:
            continue
        icon, title, steps = plan
        target = (_t("By {0}: fewer people than today ({1}) ask to improve “{2}”, and the happy share goes from {3}% to {4}%.",
                     _by(WINDOW_DAYS), item["count"], _t(item["label"]), happy, min(100, happy + 10)) if happy is not None
                  else _t("By {0}: fewer people than today ({1}) ask to improve “{2}”.", _by(WINDOW_DAYS), item["count"], _t(item["label"])))
        ideas.append({"key": f"improve:{item['key']}", "icon": icon, "title": _t(title), "plan": [_t(step) for step in steps], "target": target,
                      "why": [_t("{0} of them asked to improve “{1}”.", item["count"], _t(item["label"]))],
                      "href": "/social#listen", "action": _t("Say what you changed")})
    answered = db.query(FeedbackAnswer).filter(FeedbackAnswer.active.is_(True)).count()
    if voices.get("improve") and not answered:
        ideas.append({"key": "we-listen", "icon": "bi-ear", "title": _t("Say what you changed"),
                      "plan": [_t("Write “you said, we did” on the Social media page."), _t("Post it, and make its video.")],
                      "why": [_t("{0} subject(s) to improve, and no answer published yet.", len(voices["improve"]))],
                      "target": _t("By {0}: one answer published, and its post shared.", _by(7)), "href": "/social#listen", "action": _t("Write it")})
    if happy is not None and happy >= 70 and voices.get("judged", 0) >= 3:
        ideas.append({"key": "share-success", "icon": "bi-trophy", "title": _t("Share their success"),
                      "plan": [_t("A “their success” post with the figures."), _t("A video with the testimonials.")],
                      "why": [_t("{0}% of those who gave their opinion are happy.", happy)],
                      "target": _t("By {0}: two posts, each at least at your average engagement.", _by(WINDOW_DAYS)), "href": "/social#create",
                      "action": _t("Prepare the posts")})
    return ideas


def from_the_classes(db: Session, now: datetime | None = None) -> list[dict]:
    """Plans from how the classes go: a pace too fast, presence falling, weekly questions left unanswered."""
    from app.models import ClassGroup, ClassStatus, Quiz
    from app.models.classsession import ClassSession
    from app.models.user import Attendance

    now = now or datetime.utcnow()
    since = now - timedelta(days=WINDOW_DAYS)
    ideas = []
    groups = {group.id: group for group in db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.ONGOING).all()}
    fast: Counter = Counter()
    for quiz in db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.feedback.isnot(None), Quiz.completed_at >= since).all():
        try:
            said = json.loads(quiz.feedback or "{}")
        except ValueError:
            continue
        if said.get("pace") == "fast" and quiz.class_group_id in groups:
            fast[quiz.class_group_id] += 1
    for group_id, count in fast.most_common(2):
        if count < 2:
            break
        group = groups[group_id]
        ideas.append({"key": f"pace:{group_id}", "icon": "bi-speedometer2", "title": _t("A recap session for {0}", group.name),
                      "plan": [_t("One recap session on the weeks marked hard."), _t("The week's summary sent after each session.")],
                      "why": [_t("{0} weekly check-ins of {1} say the pace is too fast.", count, group.name)],
                      "target": _t("By {0}: in the next two weekly check-ins of {1}, fewer than {2} say the pace is too fast.", _by(14), group.name, count),
                      "href": f"/classes/{group_id}", "action": _t("Open the class")})
    for group in groups.values():
        sessions = (db.query(ClassSession.id).filter(ClassSession.class_group_id == group.id, ClassSession.session_date <= now.date())
                    .order_by(ClassSession.session_date.desc()).limit(4).all())
        marks = db.query(Attendance.status).filter(Attendance.session_id.in_([row[0] for row in sessions])).all() if sessions else []
        if len(marks) < 8:
            continue
        rate = round(100 * sum(1 for (status,) in marks if status in ("present", "late")) / len(marks))
        if rate < 75:
            ideas.append({"key": f"presence:{group.id}", "icon": "bi-person-check", "title": _t("Bring {0} back", group.name),
                          "plan": [_t("A personal “we missed you” to each one absent."), _t("The reminder the day before each session (on by default).")],
                          "why": [_t("{0}% present at the last {1} sessions of {2}.", rate, len(sessions), group.name)],
                          "target": _t("By {0}: presence from {1}% to 85% over the next three sessions.", _by(21), rate),
                          "href": f"/reminders?audience=class&to={group.id}&idea=missed#new", "action": _t("Prepare the reminder")})
    sent = db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.sent_at.isnot(None), Quiz.sent_at >= since, Quiz.sent_at <= now - timedelta(days=2)).all()
    if len(sent) >= 5:
        rate = round(100 * sum(1 for quiz in sent if quiz.completed_at is not None) / len(sent))
        if rate < 60:
            ideas.append({"key": "weekly-game", "icon": "bi-controller", "title": _t("Make the weekly questions a game"),
                          "plan": [_t("Name the best three in the next session."), _t("A small badge after four weeks answered in a row.")],
                          "why": [_t("{0}% of the weekly questions of the last 30 days were answered.", rate)],
                          "target": _t("By {0}: from {1}% answered to 75%.", _by(14), rate), "href": "/quiz/weekly", "action": _t("Open the weekly questions")})
    return ideas


def industries(db: Session, now: datetime | None = None) -> list[dict]:
    """What is going on in every industry this month: for each field, its articles, the words they name most, and
    what it means for the centre (a program shows it, or a workshop would bring people in)."""
    from app.models.news_item import NewsItem
    from app.services import journal, market

    now = now or datetime.utcnow()
    rows = db.query(NewsItem).filter(NewsItem.published_at >= now - timedelta(days=WINDOW_DAYS)).all()
    by_domain: dict[str, Counter] = {}
    articles: Counter = Counter()
    for row in rows:
        articles[row.domain] += 1
        try:
            for term in json.loads(row.terms or "[]"):
                by_domain.setdefault(row.domain, Counter())[term] += 1
        except ValueError:
            continue
    programs = journal.programs_by_domain(db)
    found = []
    for domain, count in articles.most_common():
        if domain not in journal.DOMAINS:
            continue
        label, icon = journal.DOMAINS[domain]
        terms = [(term, times) for term, times in (by_domain.get(domain) or Counter()).most_common(3) if times >= 2]
        item = {"domain": domain, "label": _t(label), "icon": icon, "articles": count, "terms": terms, "idea": None, "href": f"/journal?domain={domain}"}
        # Only what names a technology or a practice can make a workshop ("next.js", "agents"), not everyday words ("data").
        named = [(term, times) for term, times in (by_domain.get(domain) or Counter()).most_common(12) if times >= 3 and term not in EVERYDAY]
        if named:
            texts = [(program, " " + re.sub(r"[^a-z0-9.+#-]+", " ", market.program_text(db, program).lower()) + " ") for program in programs.get(domain, [])]
            teaches = {term: next((program for program, text in texts if f" {term} " in text), None) for term, _ in named}
            # A word no program teaches yet is where something can be done; otherwise the most named one, to show it.
            term, times = next(((term, times) for term, times in named if teaches[term] is None), named[0])
            taught_by = teaches[term]
            item["idea"] = (_t("“{0}” is named in {1} articles and {2} teaches it: say so in its posts and in the sessions.", term, times, taught_by.name)
                            if taught_by else
                            _t("“{0}” is named in {1} articles and no program teaches it yet: a free one-hour workshop would bring people in.", term, times))
            item["taught"] = taught_by is not None
        found.append(item)
    return found


def overview(db: Session) -> dict:
    """Everything for the dashboard: the plans (what they said first, then the classes) and every industry."""
    from app.services import feedback_hub

    plans = from_what_they_said(db, feedback_hub.overview(db)) + from_the_classes(db)
    fields = industries(db)
    return {"plans": plans, "industries": fields, "count": len(plans) + sum(1 for item in fields if item["idea"])}
