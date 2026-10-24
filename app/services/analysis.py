"""How the centre is doing, and what to make better: the analysis reports.

Two readings, both counted from the records (no language model):

* `centre`: the whole centre over the last weeks: registrations, money, presence,
  learning, opinions, the classes side by side, what the job market asks, and a
  list of what to improve, the most pressing first;
* `learners`: the candidates' progress: are they learning (the results of the
  last weeks), do they come (the last sessions), what they say, and who needs a
  word.

Each week is a calendar week (Monday to Sunday), local time.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.classsession import ClassSession
from app.models.email_thread import EmailThread
from app.models.payment import Payment
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.models.user import Attendance
from app.services.schedule_utils import local_now, utc_to_local

WEEKS = 8


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _weeks(today: date, count: int = WEEKS) -> list[date]:
    last = _monday(today)
    return [last - timedelta(weeks=count - 1 - index) for index in range(count)]


def _label(monday: date) -> str:
    return f"{monday.day} {monday:%b}"


def _local_day(moment) -> date | None:
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return utc_to_local(moment).date()
    return moment


def _rate(present: int, late: int, absent: int) -> int | None:
    total = present + late + absent
    return round((present + late) / total * 100) if total else None


def _mean(values) -> float | None:
    values = list(values)
    return round(sum(values) / len(values), 1) if values else None


def _change(now_value, before_value) -> str:
    """'+18%' / '-6%' / '' between two periods."""
    if now_value is None or before_value in (None, 0):
        return ""
    change = round((now_value - before_value) / before_value * 100)
    return f"{'+' if change >= 0 else ''}{change}%"


def weekly_series(db: Session, today: date | None = None) -> dict:
    """Per week, the last WEEKS weeks: registrations, money received, presence, results of the weekly questions."""
    today = today or local_now().date()
    mondays = _weeks(today)
    first = mondays[0]
    index = {monday: position for position, monday in enumerate(mondays)}

    def slot(day: date | None) -> int | None:
        return index.get(_monday(day)) if day and day >= first else None

    registrations, money, payments = [0] * WEEKS, [0.0] * WEEKS, [0] * WEEKS
    since = datetime.combine(first - timedelta(days=1), datetime.min.time())
    for (moment,) in db.query(Registration.registration_date).filter(Registration.registration_date >= since).all():
        position = slot(_local_day(moment))
        if position is not None:
            registrations[position] += 1
    for moment, amount in db.query(Payment.payment_date, Payment.amount).filter(Payment.payment_date >= since).all():
        position = slot(_local_day(moment))
        if position is not None:
            money[position] += amount or 0
            payments[position] += 1
    marks = [[0, 0, 0] for _ in range(WEEKS)]
    rows = (db.query(Attendance.status, ClassSession.session_date)
            .join(ClassSession, ClassSession.id == Attendance.session_id)
            .filter(ClassSession.session_date >= first).all())
    for status, day in rows:
        position = slot(day)
        if position is not None:
            marks[position][("present", "late", "absent").index(status) if status in ("present", "late", "absent") else 2] += 1
    scores, sent, answered = [[] for _ in range(WEEKS)], [0] * WEEKS, [0] * WEEKS
    for quiz in db.query(Quiz).filter(Quiz.kind.in_(("weekly", "session")), Quiz.sent_at >= since).all():
        position = slot(_local_day(quiz.sent_at))
        if position is None:
            continue
        sent[position] += 1
        if quiz.completed_at is not None:
            answered[position] += 1
            scores[position].append(quiz.score or 0)
    return {
        "labels": [_label(monday) for monday in mondays], "mondays": mondays,
        "registrations": registrations, "money": [round(value) for value in money], "payments": payments,
        "presence": [_rate(*week) for week in marks], "marked": [sum(week) for week in marks],
        "understanding": [_mean(week) for week in scores], "sent": sent, "answered": answered,
    }


def _halves(values: list) -> tuple[list, list]:
    """The last four weeks and the four before, without the weeks that have no value."""
    return [value for value in values[-4:] if value is not None], [value for value in values[-8:-4] if value is not None]


def progress(series: dict) -> list[dict]:
    """How much we progress: the last four weeks against the four before, per measure."""
    def total(key):
        now, before = _halves(series[key])
        return sum(now), sum(before)

    def average(key):
        now, before = _halves(series[key])
        return _mean(now), _mean(before)

    rows = []
    for label, key, kind, unit in (("Registrations", "registrations", total, ""), ("Money received", "money", total, " DT"),
                                    ("Presence in sessions", "presence", average, "%"), ("Results of the weekly questions", "understanding", average, "%")):
        now, before = kind(key)
        better = None if now is None or before is None else now >= before
        rows.append({"label": label, "now": now, "before": before, "unit": unit, "change": _change(now, before), "better": better})
    return rows


def classes(db: Session) -> list[dict]:
    """The classes that run or ran, with their dynamics."""
    from app.services import class_dynamics, program_icons

    found = []
    for class_group in (db.query(ClassGroup).options(joinedload(ClassGroup.program))
                        .filter(ClassGroup.status != ClassStatus.NOT_STARTED).order_by(ClassGroup.name).all()):
        dynamics = class_dynamics.of_class(db, class_group)
        found.append({"class": class_group, "dynamics": dynamics, "icon": program_icons.view(class_group.program) if class_group.program else None})
    return found


def learners(db: Session) -> dict:
    """The candidates' progress: per candidate in a class, and the weeks of the whole centre."""
    from app.services import feedback, program_feedback

    series = weekly_series(db)
    rows = []
    registrations = (db.query(Registration).options(joinedload(Registration.candidate), joinedload(Registration.class_group))
                     .filter(Registration.class_group_id.isnot(None), Registration.status != RegistrationStatus.CANCELLED).all())
    marks_by = {}
    for mark in db.query(Attendance).all():
        marks_by.setdefault(mark.registration_id, []).append(mark)
    sessions = {row.id: row.session_date for row in db.query(ClassSession).all()}
    quizzes_by = {}
    for quiz in db.query(Quiz).filter(Quiz.kind.in_(("weekly", "session")), Quiz.sent_at.isnot(None)).all():
        quizzes_by.setdefault((quiz.candidate_id, quiz.class_group_id), []).append(quiz)
    for registration in registrations:
        candidate, class_group = registration.candidate, registration.class_group
        if candidate is None or class_group is None:
            continue
        marks = sorted(marks_by.get(registration.id, []), key=lambda mark: sessions.get(mark.session_id) or date.min)
        counts = {status: sum(1 for mark in marks if mark.status == status) for status in ("present", "late", "absent")}
        recent = marks[-3:]
        recent_rate = _rate(*(sum(1 for mark in recent if mark.status == status) for status in ("present", "late", "absent")))
        quizzes = sorted(quizzes_by.get((candidate.id, class_group.id), []), key=lambda quiz: quiz.week_number or 0)
        done = [quiz for quiz in quizzes if quiz.completed_at is not None]
        average = _mean(quiz.score or 0 for quiz in done)
        last = done[-1].score if done else None
        streak = 0
        for mark in reversed(marks):
            if mark.status != "absent":
                break
            streak += 1
        if streak >= 2:
            state = ("Stopped coming", "bad")
        elif (average is not None and average < 50) or (recent_rate is not None and recent_rate < 60):
            state = ("Needs help", "warn")
        elif average is not None and average >= 75 and (recent_rate is None or recent_rate >= 80):
            state = ("Doing well", "ok")
        elif not marks and not done:
            state = ("Just started", "muted")
        else:
            state = ("On track", "info")
        rows.append({"candidate": candidate, "name": candidate.full_name or candidate.email, "class": class_group,
                     "presence": _rate(**counts), "recent_presence": recent_rate, "marked": len(marks),
                     "average": average, "last": last, "answered": len(done), "sent": len(quizzes),
                     "state": state, "absent_in_a_row": streak})
    order = {"bad": 0, "warn": 1, "info": 2, "muted": 3, "ok": 4}
    rows.sort(key=lambda row: (order[row["state"][1]], row["name"].lower()))
    opinions = program_feedback.overview(db)
    published = feedback.overview(db)
    hard: dict[str, int] = {}
    for quiz in db.query(Quiz).filter(Quiz.kind.in_(("weekly", "session")), Quiz.completed_at.isnot(None)).all():
        for line in (quiz.improvement_areas or "").splitlines():
            line = line.strip(" -•")
            if 3 <= len(line) <= 90:
                hard[line] = hard.get(line, 0) + 1
    counts = {}
    for row in rows:
        counts[row["state"][0]] = counts.get(row["state"][0], 0) + 1
    return {
        "series": series, "rows": rows, "counts": counts, "opinions": opinions, "published": published,
        "hardest": sorted(hard.items(), key=lambda pair: -pair[1])[:8],
        "presence": _mean(row["presence"] for row in rows if row["presence"] is not None),
        "understanding": _mean(row["average"] for row in rows if row["average"] is not None),
        "advice": _learner_advice(rows, opinions, published, series),
    }


def _learner_advice(rows, opinions, published, series) -> list[dict]:
    found = []
    stopped = [row["name"] for row in rows if row["state"][0] == "Stopped coming"]
    if stopped:
        found.append({"text": f"{len(stopped)} candidate{'s' if len(stopped) != 1 else ''} stopped coming ({', '.join(stopped[:4])}{'…' if len(stopped) > 4 else ''}): call or write to them this week.", "tone": "bad"})
    helped = [row["name"] for row in rows if row["state"][0] == "Needs help"]
    if helped:
        found.append({"text": f"{len(helped)} need help with their results or their presence: ask their professor for a word with each.", "tone": "warn"})
    recent = [value for value in series["understanding"][-3:] if value is not None]
    if recent and _mean(recent) < 60:
        found.append({"text": f"The weekly questions of the last weeks average {_mean(recent)}%: go back over the hardest topics in class.", "tone": "warn"})
    asked, done = sum(series["sent"][-4:]), sum(series["answered"][-4:])
    if asked >= 5 and done / asked < 0.6:
        found.append({"text": f"Only {round(done / asked * 100)}% of the weekly questions of the last four weeks were answered: remind the classes once.", "tone": "warn"})
    if opinions["happy_share"] is not None and opinions["happy_share"] < 70:
        found.append({"text": f"{opinions['happy_share']}% of the end-of-program opinions are happy: read the others and answer them.", "tone": "warn"})
    if published.get("to_answer"):
        found.append({"text": f"{len(published['to_answer'])} published review{'s' if len(published['to_answer']) != 1 else ''} are mixed or negative: answer them on the website.", "tone": "warn"})
    if not found:
        found.append({"text": "The candidates are learning and coming: keep the rhythm.", "tone": "ok"})
    return found


def centre(db: Session) -> dict:
    """Everything, for the analysis report: progress, money, learning, opinions, classes, market, and what to improve."""
    from app.services import feedback, level_check, market, overview, program_feedback

    now_utc = datetime.utcnow()
    figures = overview.figures(db, now_utc)
    series = weekly_series(db)
    class_rows = classes(db)
    learning = learners(db)
    opinions, published = learning["opinions"], learning["published"]
    view = market.overview(db)
    gaps = []
    for row in view["programs"]:
        for item in row.get("add") or []:
            gaps.append({"program": row["program"].name, "skill": item["label"], "pct": item["pct"]})
    gaps.sort(key=lambda item: -item["pct"])
    waiting_class = (db.query(Registration).filter(Registration.payment_status == PaymentStatus.PAID, Registration.class_group_id.is_(None),
                                                   Registration.status != RegistrationStatus.CANCELLED).count())
    levels = level_check.overview(db)

    improve = []
    if figures["due"] > 0:
        improve.append({"text": f"{figures['due']:,.0f} DT are still due from {figures['owing_candidates']} candidate(s); {figures['collection_rate']}% of what is due was collected.".replace(",", " "),
                        "tone": "warn" if figures["collection_rate"] >= 60 else "bad", "where": "/payments"})
    if figures["emails_waiting"]:
        improve.append({"text": f"{figures['emails_waiting']} email conversation(s) wait for your answer.", "tone": "warn", "where": "/inbox"})
    if waiting_class:
        improve.append({"text": f"{waiting_class} paid candidate(s) wait for a class.", "tone": "bad", "where": "/classes/?tab=decisions"})
    for row in class_rows:
        for item in row["dynamics"]["advice"]:
            if item["tone"] in ("bad", "warn"):
                improve.append({"text": f"{row['class'].name}: {item['text']}", "tone": item["tone"], "where": f"/classes/{row['class'].id}"})
    for item in learning["advice"]:
        if item["tone"] in ("bad", "warn"):
            improve.append({**item, "where": "/candidate/all"})
    if levels["counts"].get("sent"):
        improve.append({"text": f"{levels['counts']['sent']} level check(s) were sent and not answered yet.", "tone": "info", "where": "/quiz/all"})
    for gap in gaps[:3]:
        improve.append({"text": f"{gap['program']}: {gap['pct']}% of the job offers of its field ask for {gap['skill']}, which it does not teach.", "tone": "info", "where": "/programs/market"})
    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    improve.sort(key=lambda item: order.get(item["tone"], 3))
    if not improve:
        improve.append({"text": "Nothing stands out: everything is in order.", "tone": "ok", "where": "/"})

    candidates = db.query(Candidate).count()
    return {
        "figures": figures, "series": series, "progress": progress(series), "classes": class_rows, "learning": learning,
        "opinions": opinions, "published": published, "gaps": gaps[:8], "market_total": view["total"], "levels": levels,
        "improve": improve, "candidates": candidates, "waiting_class": waiting_class,
    }
