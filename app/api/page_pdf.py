"""Every page as a PDF: what the page shows, laid out as a report in the centre's look.

Each address sits under its page's own (/candidate/…, /classes/…), so whoever may
open the page may open its PDF, and nobody else. "?download=1" saves the file
instead of showing it. Nothing is sent from here.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.email_thread import EmailThread
from app.models.payment import Payment
from app.models.program import Program
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.services import program_pdf, report_pdf
from app.services.schedule_utils import local_now, utc_to_local

router = APIRouter(tags=["Page PDFs"])

TONE_OF_ADVICE = {"urgent": ("Urgent", "bad"), "important": ("Important", "warn"), "info": ("To know", "info"), "good": ("Fine", "ok")}


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def _send(request: Request, data: bytes, title: str) -> Response:
    from app.api.program_pdf import _pdf

    return _pdf(data, report_pdf.filename(title), request.query_params.get("download") == "1")


def _report(request: Request, db: Session, title: str, blocks: list[dict], overline: str, subtitle: str = "", landscape: bool = False) -> Response:
    if not program_pdf.available():
        raise HTTPException(status_code=503, detail="The PDF library is not installed: run “pip install reportlab” and restart.")
    return _send(request, report_pdf.build(db, title=title, blocks=blocks, overline=overline, subtitle=subtitle, landscape=landscape), title)


def _money(value) -> str:
    return f"{value or 0:,.0f} DT".replace(",", " ")


def _day(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = utc_to_local(value) if value.tzinfo is None else value
        return f"{value.day} {value:%b %Y}"
    return f"{value.day} {value:%b %Y}"


def _hours(start, end) -> str:
    if not start:
        return ""
    return f"{start:%H:%M}" + (f"-{end:%H:%M}" if end else "")


def _percent(value) -> str:
    return "-" if value is None else f"{round(value)}%"


def pay_state(total: float, paid: float) -> tuple[str, str]:
    """Where one registration's payment stands, in the words of the candidate's file."""
    if total and paid >= total - 0.001:
        return "Paid in full", "ok"
    if paid > 0:
        return "Paid in part", "warn"
    if total:
        return "Just registered", "bad"
    return "No price yet", "muted"


def _week_range(now) -> str:
    start = (now - timedelta(days=6)).date()
    return f"{start.day} {start:%b} to {now.day} {now:%b %Y}"


def _program_name(registration: Registration) -> str:
    return registration.program.name if registration.program else (registration.program_name or "Program")


def _event_label(overview, event_type: str | None) -> str:
    kind = overview._RECORD_ID.sub("", event_type or "")
    if kind.endswith("_EMAIL_SENT"):
        return "Email sent"
    return overview._EVENTS.get(kind, (kind.replace("_", " ").capitalize() or "Event",))[0]


def _improve_rows(items: list[dict]) -> dict:
    labels = {"bad": "First", "warn": "Soon", "info": "To plan", "ok": "Fine"}
    return {"kind": "table", "columns": ["", "What to improve"], "widths": [0.9, 7],
            "rows": [[{"chip": labels.get(item["tone"], "To plan"), "tone": item["tone"] if item["tone"] != "info" else "info"}, item["text"]] for item in items]}


# ------------------------------------------------------------------
# Dashboard: this week, and the analysis of everything
# ------------------------------------------------------------------

@router.get("/dashboard/pdf")
def dashboard_pdf(request: Request, db: Session = Depends(get_db)):
    """The dashboard: this week day by day, money, classes, what needs the admin."""
    from app.services import advisor, overview, website_sync

    now_utc, now_local = datetime.utcnow(), local_now()
    figures = overview.figures(db, now_utc)
    days = [(now_local - timedelta(days=6 - index)).date() for index in range(7)]
    since = now_utc - timedelta(days=8)
    per_day = {day: {"registrations": 0, "payments": 0, "money": 0.0, "emails": 0, "answered": 0} for day in days}

    def bucket(moment):
        return per_day.get(utc_to_local(moment).date()) if moment else None

    for (moment,) in db.query(Registration.registration_date).filter(Registration.registration_date >= since).all():
        if (slot := bucket(moment)) is not None:
            slot["registrations"] += 1
    for moment, amount in db.query(Payment.payment_date, Payment.amount).filter(Payment.payment_date >= since).all():
        if (slot := bucket(moment)) is not None:
            slot["payments"] += 1
            slot["money"] += amount or 0
    for moment, direction, author in db.query(CandidateEmail.received_at, CandidateEmail.direction, CandidateEmail.author).filter(CandidateEmail.received_at >= since).all():
        if (slot := bucket(moment)) is not None:
            if direction == "inbound":
                slot["emails"] += 1
            elif author == "system":
                slot["answered"] += 1
    labels = [f"{day:%a} {day.day}" for day in days]

    active = [item for item in db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all()]
    states = {"Paid in full": 0, "Paid in part": 0, "Just registered": 0}
    for item in active:
        state, _ = pay_state(item.total_amount or item.total_price or 0, item.paid_amount or 0)
        if state in states:
            states[state] += 1
    priced = sum(states.values())

    blocks = [
        {"kind": "figures", "items": [
            ("Candidates", str(figures["candidates"]), f"{figures['new_candidates']} new this week", "brand"),
            ("Collected", _money(figures["collected"]), f"{figures['collection_rate']}% of what is due", "ok"),
            ("Still due", _money(figures["due"]), f"from {figures['owing_candidates']} people", "warn"),
            ("Classes running", str(figures["classes_running"]), f"{figures['classes_upcoming']} about to start", "info"),
            ("Emails waiting", str(figures["emails_waiting"]), "for your answer", "bad" if figures["emails_waiting"] else "ok"),
            ("Understanding", _percent(figures["understanding"]), f"over {figures['answers']} weekly answers", "violet"),
            ("Registrations", str(figures["registrations"]), "not cancelled", "teal"),
            ("Paid in full", str(figures["paid_registrations"]), "registrations", "ok"),
        ]},
        {"kind": "row", "blocks": [
            {"kind": "bars", "title": "This week, day by day", "labels": labels, "series": [
                {"name": "Registrations", "values": [per_day[day]["registrations"] for day in days], "tone": "brand"},
                {"name": "Payments", "values": [per_day[day]["payments"] for day in days], "tone": "ok"},
                {"name": "Emails received", "values": [per_day[day]["emails"] for day in days], "tone": "info"}]},
            {"kind": "donut", "title": "Where payments stand", "parts": [(label, value, tone) for (label, value), tone in zip(states.items(), ("ok", "warn", "bad"))],
             "center": (f"{round(states['Paid in full'] / priced * 100) if priced else 0}%", "paid in full")},
        ]},
        {"kind": "bars", "title": "Money received this week (DT)", "labels": labels, "height": 150,
         "series": [{"name": "DT", "values": [round(per_day[day]["money"]) for day in days], "tone": "ok"}], "unit": "DT"},
        {"kind": "heading", "text": "What the week brought", "note": "Counted from what was recorded; the same event recorded twice counts once."},
        {"kind": "hbars", "rows": [(part["label"], part["count"]) for part in overview.activity_parts(db, now_utc)]},
    ]
    advice = advisor.for_dashboard(db)
    blocks += [{"kind": "heading", "text": "What needs you", "note": "The most pressing first, each with the facts it rests on."},
               {"kind": "table", "columns": ["", "What to do", "Why"], "widths": [1, 3, 5], "empty": "Nothing needs you right now.",
                "rows": [[{"chip": TONE_OF_ADVICE.get(item.tone, ("To know", "info"))[0], "tone": TONE_OF_ADVICE.get(item.tone, ("", "info"))[1]},
                          {"text": item.title, "bold": True}, item.why] for item in advice[:10]]}]
    sessions = overview.coming_sessions(db, now_local)
    blocks += [{"kind": "heading", "text": "Next sessions"},
               {"kind": "table", "columns": ["Day", "Time", "Class", "Topic", "Candidates"], "widths": [1.4, 1.1, 2.6, 3, 1], "right": [4],
                "empty": "No session in the next 7 days.",
                "rows": [[f"{item['date']:%a} {item['date'].day} {item['date']:%b}", _hours(item["start_time"], item["end_time"]),
                          item["class"].name, item["topic"] or (f"Week {item['week']}" if item["week"] else ""), str(item["members"])] for item in sessions]}]
    running = overview.classes_in_progress(db, now_local)
    blocks += [{"kind": "heading", "text": "Classes in progress"},
               {"kind": "table", "columns": ["Class", "Professor", "Where it stands", "Candidates"], "widths": [3, 2, 2.2, 1.2], "right": [3],
                "empty": "No class is running or about to start.",
                "rows": [[{"text": row["class"].name, "bold": True, "note": row["class"].program.name if row["class"].program else ""},
                          row["class"].professor_name or "Not assigned",
                          {"bar": row["progress"], "text": f"Week {row['summary']['current_week']} of {row['summary']['total_weeks']}"}
                          if row["class"].status == ClassStatus.ONGOING else row["summary"]["starts_label"],
                          f"{row['summary']['enrolled']} / {row['summary']['capacity']}"] for row in running]}]
    blocks += [{"kind": "heading", "text": "Most chosen programs", "note": "Registrations that are not cancelled"},
               {"kind": "hbars", "rows": [(row["program"].name, row["registrations"]) for row in overview.popular_programs(db, limit=8)]}]
    newest = overview.newest_registrations(db, limit=8)
    blocks += [{"kind": "heading", "text": "Newest registrations"},
               {"kind": "table", "columns": ["Candidate", "Programs", "When", "Still owes"], "widths": [2.4, 3.4, 1.6, 1.2], "right": [3],
                "rows": [[{"text": row["name"], "bold": True}, ", ".join(row["programs"]), _day(row["when"]), _money(row["owes"])] for row in newest]}]
    news = website_sync.week_news(db, now_utc)
    if news["lines"]:
        blocks += [{"kind": "heading", "text": "New on 9antra.tn this week"},
                   {"kind": "points", "items": [f"{line['count']} {line['label'].lower() if line['count'] != 1 else line['one'].lower()}: {', '.join(line['names'])}" for line in news["lines"]]}]
    return _report(request, db, "Dashboard", blocks, "This week at the centre", _week_range(now_local))


def analysis_report(db: Session) -> dict:
    """Everything in one report: how the centre progresses, what candidates learn and say, and what to improve."""
    from app.services import analysis

    data = analysis.centre(db)
    figures, series = data["figures"], data["series"]
    blocks = [
        {"kind": "box", "tone": "grey", "text": "This report puts together the figures of the whole application: registrations, money, presence, "
                                                 "results of the weekly questions, opinions, the classes and the job market. Each figure is counted from what was recorded."},
        {"kind": "heading", "text": "What to improve, the most pressing first"},
        _improve_rows(data["improve"]),
        {"kind": "heading", "text": "How much we progress", "note": "The last four weeks against the four weeks before."},
        {"kind": "table", "columns": ["Measure", "Last 4 weeks", "4 weeks before", "Change"], "widths": [3, 1.6, 1.6, 1.2], "right": [1, 2, 3],
         "rows": [[{"text": row["label"], "bold": True},
                   "-" if row["now"] is None else f"{row['now']:,.0f}{row['unit']}".replace(",", " "),
                   "-" if row["before"] is None else f"{row['before']:,.0f}{row['unit']}".replace(",", " "),
                   {"chip": row["change"] or "-", "tone": "muted" if row["better"] is None or not row["change"] else ("ok" if row["better"] else "bad")}]
                  for row in data["progress"]]},
        {"kind": "figures", "items": [
            ("Candidates", str(data["candidates"]), f"{figures['new_candidates']} new this week", "brand"),
            ("Collected", _money(figures["collected"]), f"{figures['collection_rate']}% of what is due", "ok"),
            ("Presence", _percent(data["learning"]["presence"]), "average of the candidates in a class", "info"),
            ("Understanding", _percent(data["learning"]["understanding"]), "average of the weekly questions", "violet")]},
        {"kind": "row", "blocks": [
            {"kind": "bars", "title": "Registrations per week", "labels": series["labels"], "height": 150,
             "series": [{"name": "Registrations", "values": series["registrations"], "tone": "brand"}]},
            {"kind": "bars", "title": "Money received per week (DT)", "labels": series["labels"], "height": 150,
             "series": [{"name": "DT", "values": series["money"], "tone": "ok"}]},
        ]},
        {"kind": "row", "blocks": [
            {"kind": "bars", "title": "Presence in sessions per week (%)", "labels": series["labels"], "height": 150,
             "series": [{"name": "%", "values": [value or 0 for value in series["presence"]], "tone": "info"}]},
            {"kind": "bars", "title": "Results of the weekly questions (%)", "labels": series["labels"], "height": 150,
             "series": [{"name": "%", "values": [value or 0 for value in series["understanding"]], "tone": "violet"}]},
        ]},
    ]
    opinions, published = data["opinions"], data["published"]
    blocks += [{"kind": "heading", "text": "What candidates say"},
               {"kind": "figures", "items": [
                   ("End-of-program opinions", str(opinions["answered"]), f"{opinions['waiting']} still to ask" if opinions.get("waiting") else "given", "brand"),
                   ("Stars", f"{opinions['stars']} / 5" if opinions["stars"] else "-", "end of program", "gold"),
                   ("Happy", _percent(opinions["happy_share"]), "of the opinions", "ok"),
                   ("Published reviews", str(published.get("total", 0)), f"{_percent(published.get('happy_share'))} happy" if published.get("happy_share") is not None else "on 9antra.tn", "info")]}]
    if opinions["themes"]:
        blocks.append({"kind": "hbars", "title": "What the opinions talk about", "rows": [(label, count) for label, count in opinions["themes"]]})
    if data["classes"]:
        blocks += [{"kind": "heading", "text": "The classes side by side", "note": "Interactivity: what the professor noted in the sessions (70%) and the questions asked (30%)."},
                   {"kind": "table", "columns": ["Class", "Presence", "Last sessions", "Interactivity", "Answers", "Understanding"],
                    "widths": [3, 1.1, 1.3, 1.3, 1.1, 1.4], "right": [1, 2, 3, 4, 5],
                    "rows": [[{"text": row["class"].name, "bold": True, "note": row["class"].program.name if row["class"].program else ""},
                              _percent(row["dynamics"]["figures"]["attendance"]), _percent(row["dynamics"]["figures"]["recent_attendance"]),
                              "-" if row["dynamics"]["figures"]["interactivity"] is None else f"{row['dynamics']['figures']['interactivity']}/100",
                              _percent(row["dynamics"]["figures"]["answer_rate"]), _percent(row["dynamics"]["figures"]["understanding"])]
                             for row in data["classes"]]}]
    if data["gaps"]:
        blocks += [{"kind": "heading", "text": "What the job market asks that we do not teach yet", "note": f"From {data['market_total']} job offers read."},
                   {"kind": "hbars", "rows": [(f"{gap['skill']} · {gap['program']}", gap["pct"]) for gap in data["gaps"]], "most": 100, "unit": "%", "tone": "gold"}]
    levels = data["levels"]
    blocks += [{"kind": "heading", "text": "Level checks"},
               {"kind": "figures", "items": [("Ready to send", str(levels["counts"].get("ready", 0)), "", "muted"),
                                             ("Sent, waiting", str(levels["counts"].get("sent", 0)), "", "warn"),
                                             ("Answered", str(levels["counts"].get("answered", 0)), "", "ok"),
                                             ("Paid, waiting for a class", str(data["waiting_class"]), "", "bad" if data["waiting_class"] else "ok")]}]
    return {"title": "Analysis", "blocks": blocks, "overline": "How the centre is doing", "subtitle": f"Made from the records, {local_now():%d %b %Y}"}


@router.get("/dashboard/analysis/pdf")
def analysis_pdf(request: Request, db: Session = Depends(get_db)):
    report = analysis_report(db)
    return _report(request, db, report["title"], report["blocks"], report["overline"], report["subtitle"])


# ------------------------------------------------------------------
# Candidates
# ------------------------------------------------------------------

@router.get("/candidate/all/pdf")
def candidates_pdf(request: Request, db: Session = Depends(get_db)):
    """Every candidate: contact, programs, what they paid, what to do next."""
    from app.api.candidate import candidate_rows

    rows, totals = candidate_rows(db)
    search = (request.query_params.get("search") or "").strip().lower()
    if search:
        rows = [row for row in rows if search in f"{row['name']} {row['candidate'].email} {row['candidate'].phone or ''}".lower()]
    standing = {"Paid in full": 0, "Paid in part": 0, "Nothing paid yet": 0, "No program": 0}
    table = []
    for row in rows:
        if not row["total"]:
            state = ("No program", "muted")
        elif row["due"] <= 0:
            state = ("Paid in full", "ok")
        elif row["paid"] > 0:
            state = ("Paid in part", "warn")
        else:
            state = ("Nothing paid yet", "bad")
        standing[state[0]] += 1
        table.append([{"text": row["name"], "bold": True, "note": row["candidate"].email},
                      row["candidate"].phone or "",
                      ", ".join(item["name"] for item in row["programs"]) or "-",
                      {"bar": row["paid_pct"], "text": f"{row['paid']:,.0f} / {row['total']:,.0f} DT".replace(",", " ")} if row["total"] else "-",
                      {"chip": state[0], "tone": state[1]},
                      row["advice"].title if row["advice"] else ""])
    blocks = [
        {"kind": "figures", "items": [("Candidates", str(totals["candidates"]), f"{totals['new']} new this week", "brand"),
                                      ("Need you", str(totals["needs_you"]), "something to do now", "bad" if totals["needs_you"] else "ok"),
                                      ("Owe money", str(totals["owing"]), _money(totals["due"]) + " in all", "warn"),
                                      ("In a class", str(totals["in_class"]), "following a program now", "info")]},
        {"kind": "donut", "title": "Where each candidate stands with payment", "height": 130,
         "parts": [(label, value, tone) for (label, value), tone in zip(standing.items(), ("ok", "warn", "bad", "muted"))],
         "center": (str(len(rows)), "candidates")},
        {"kind": "table", "title": "Every candidate, the ones who need you first",
         "columns": ["Candidate", "Phone", "Programs", "Paid", "Payment", "Next step"], "widths": [2.6, 1.3, 2.8, 1.7, 1.3, 2.8], "rows": table},
    ]
    return _report(request, db, "Candidates", blocks, "Everyone who registered", f"{len(rows)} candidate(s)" + (f" matching “{search}”" if search else ""), landscape=True)


def learners_report(db: Session) -> dict:
    """The candidates' progress: are they learning, do they come, what they say, and who needs a word."""
    from app.services import analysis

    data = analysis.learners(db)
    series, opinions, published = data["series"], data["opinions"], data["published"]
    states = data["counts"]
    blocks = [
        {"kind": "figures", "items": [("In a class", str(len(data["rows"])), "candidates followed here", "brand"),
                                      ("Presence", _percent(data["presence"]), "on average", "info"),
                                      ("Understanding", _percent(data["understanding"]), "weekly questions, on average", "violet"),
                                      ("Happy opinions", _percent(opinions["happy_share"]), f"{opinions['answered']} end-of-program opinions", "ok")]},
        {"kind": "heading", "text": "What to improve in the service"},
        _improve_rows(data["advice"]),
        {"kind": "row", "blocks": [
            {"kind": "bars", "title": "Are they learning? Results per week (%)", "labels": series["labels"], "height": 150,
             "series": [{"name": "%", "values": [value or 0 for value in series["understanding"]], "tone": "violet"}]},
            {"kind": "bars", "title": "Do they come? Presence per week (%)", "labels": series["labels"], "height": 150,
             "series": [{"name": "%", "values": [value or 0 for value in series["presence"]], "tone": "info"}]},
        ]},
        {"kind": "row", "blocks": [
            {"kind": "donut", "title": "Where the candidates stand", "height": 140,
             "parts": [(label, states.get(label, 0), tone) for label, tone in (("Doing well", "ok"), ("On track", "info"), ("Needs help", "warn"),
                                                                                ("Stopped coming", "bad"), ("Just started", "muted"))],
             "center": (str(len(data["rows"])), "in a class")},
            {"kind": "bars", "title": "Weekly questions sent and answered", "labels": series["labels"], "height": 140,
             "series": [{"name": "Sent", "values": series["sent"], "tone": "muted"}, {"name": "Answered", "values": series["answered"], "tone": "ok"}]},
        ]},
    ]
    if data["hardest"]:
        blocks.append({"kind": "hbars", "title": "What was hardest (missed most in the weekly questions)", "rows": data["hardest"], "tone": "gold"})
    blocks += [{"kind": "heading", "text": "Each candidate", "note": "Presence: sessions attended out of those marked. Last 3: the three latest sessions."},
               {"kind": "table", "columns": ["Candidate", "Class", "Presence", "Last 3", "Average", "Last week", "Answered", "State"],
                "widths": [2.4, 2.4, 1, 1, 1, 1, 1.1, 1.4], "right": [2, 3, 4, 5, 6],
                "rows": [[{"text": row["name"], "bold": True}, row["class"].name, _percent(row["presence"]), _percent(row["recent_presence"]),
                          _percent(row["average"]), _percent(row["last"]), f"{row['answered']} / {row['sent']}",
                          {"chip": row["state"][0], "tone": row["state"][1]}] for row in data["rows"]],
                "empty": "No candidate is in a class yet."}]
    blocks += [{"kind": "heading", "text": "What they say"},
               {"kind": "figures", "items": [("Opinions", str(opinions["answered"]), "at the end of a program", "brand"),
                                             ("Stars", f"{opinions['stars']} / 5" if opinions["stars"] else "-", "", "gold"),
                                             ("Published reviews", str(published.get("total", 0)), "on 9antra.tn", "info"),
                                             ("Their stars", f"{published['stars']} / 5" if published.get("stars") else "-", "published", "gold")]}]
    worried = [item for item in opinions["to_answer"]][:8]
    if worried:
        blocks.append({"kind": "table", "title": "Opinions to answer", "columns": ["Who", "Program", "What they wrote"], "widths": [1.6, 2, 5],
                       "rows": [[item["name"], item["program"] or "", item["words"][:300]] for item in worried]})
    return {"title": "Candidates' progress", "blocks": blocks, "overline": "Are they learning?",
            "subtitle": f"The last {len(series['labels'])} weeks, {local_now():%d %b %Y}"}


@router.get("/candidate/all/analysis/pdf")
def learners_pdf(request: Request, db: Session = Depends(get_db)):
    report = learners_report(db)
    return _report(request, db, report["title"], report["blocks"], report["overline"], report["subtitle"])


@router.get("/candidate/{candidate_id}/pdf")
def candidate_pdf(candidate_id: str, request: Request, db: Session = Depends(get_db)):
    """One candidate's file: contact, programs by where their payment stands, sessions, learning, behaviour, history."""
    from app.api.candidate import profile_data
    from app.services import overview

    data = profile_data(db, candidate_id)
    candidate, cards = data["candidate"], data["program_cards"]
    total, paid = sum(card["total"] for card in cards), sum(card["paid"] for card in cards)
    learning, presence, behaviour = data["learning"], data["presence"], data["behaviour"]
    blocks = [
        {"kind": "figures", "items": [
            ("Paid", f"{paid:,.0f} / {total:,.0f} DT".replace(",", " "), f"{round(paid / total * 100) if total else 0}% of the price", "ok" if total and paid >= total else "warn"),
            ("Programs", str(len(cards)), f"{sum(1 for card in cards if card['class'])} with a class", "brand"),
            ("Understanding", _percent(learning["average"]), learning["level"]["label"] if learning["average"] is not None else "no weekly answer yet", "violet"),
            ("Presence", _percent(presence["rate"]), f"{presence['absent']} absence(s) in {presence['marked']} session(s)" if presence["marked"] else "not marked yet", "info")]},
    ]
    if data["advice"]:
        blocks += [{"kind": "heading", "text": "Next steps"},
                   {"kind": "table", "columns": ["", "What to do", "Why"], "widths": [1, 3, 5],
                    "rows": [[{"chip": TONE_OF_ADVICE.get(item.tone, ("To know", "info"))[0], "tone": TONE_OF_ADVICE.get(item.tone, ("", "info"))[1]},
                              {"text": item.title, "bold": True}, item.why] for item in data["advice"]]}]
    for group in data["payment_groups"]:
        if not group["cards"]:
            continue
        note = group["why"] + (f" {_money(group['paid'])} paid, {_money(group['due'])} still due." if group["key"] != "paid" else "")
        blocks.append({"kind": "heading", "text": f"{group['label']} ({len(group['cards'])})", "note": note})
        if group["key"] == "paid":
            blocks.append({"kind": "table", "columns": ["Program", "Mode", "Class", "Professor", "Where it stands"], "widths": [2.6, 1, 2.2, 1.8, 2.2],
                           "rows": [[{"text": card["name"], "bold": True, "note": f"registered {_day(card['registered'])}" if card["registered"] else ""},
                                     "On site" if card["registration"].delivery_mode == "onsite" else "Online",
                                     card["class"].name if card["class"] else "Waiting for a class",
                                     (card["class"].professor_name or "Not assigned yet") if card["class"] else "",
                                     ((f"Week {card['class_info']['current_week']} of {card['class_info']['total_weeks']}"
                                       if card["class"].status.value == "ongoing" else card["class_info"]["starts_label"]) if card["class"] and card["class_info"] else "")]
                                    for card in group["cards"]]})
        elif group["key"] == "partial":
            blocks.append({"kind": "table", "columns": ["Program", "Registered", "Paid", "Still due"], "widths": [3, 1.6, 2.4, 1.4], "right": [3],
                           "rows": [[{"text": card["name"], "bold": True}, _day(card["registered"]),
                                     {"bar": card["paid_pct"], "text": f"{card['paid']:,.0f} / {card['total']:,.0f} DT".replace(",", " ")}, _money(card["due"])]
                                    for card in group["cards"]]})
        else:
            blocks.append({"kind": "table", "columns": ["Program", "Registered", "Price", "Waiting since"], "widths": [3, 1.6, 1.4, 1.6], "right": [2, 3],
                           "rows": [[{"text": card["name"], "bold": True}, _day(card["registered"]), _money(card["total"]) if card["total"] else "No price yet",
                                     f"{card['days_waiting']} day(s)" if card.get("days_waiting") is not None else ""] for card in group["cards"]]})
    if data["session_agenda"]:
        blocks += [{"kind": "heading", "text": "Next sessions"},
                   {"kind": "table", "columns": ["Day", "Time", "Class", "Topic"], "widths": [1.6, 1.2, 2.6, 3.6],
                    "rows": [[f"{item['date']:%a} {item['date'].day} {item['date']:%b}", _hours(item["start_time"], item["end_time"]), item["class_name"],
                              item["topic"] or (f"Week {item['week']}" if item["week"] else "")] for item in data["session_agenda"]]}]
    # ---- Their analysis ----
    blocks.append({"kind": "heading", "text": "Their analysis", "note": "Counted from what was recorded: the presence sheets, the weekly questions, the level check."})
    done = [week for week in learning["weeks"] if week["done"]]
    if done:
        blocks.append({"kind": "bars", "title": "Results of the weekly questions (%)", "labels": [f"W{week['week']}" for week in done], "height": 140,
                       "series": [{"name": "%", "values": [round(week["score"] or 0) for week in done], "tone": "violet"}]})
    if data["level_checks"]:
        blocks.append({"kind": "table", "title": "Level check", "columns": ["Program", "State", "Level found"], "widths": [3, 1.6, 2.6],
                       "rows": [[row["program"], row["state"].capitalize(), row["result"]["info"]["label"] if row["result"] else "-"] for row in data["level_checks"]]})
    if behaviour.get("score") is not None:
        blocks.append({"kind": "box", "tone": "brand", "text": f"Behaviour score: {behaviour['score']}/100. {behaviour.get('headline') or ''}"})
    if behaviour.get("params"):
        blocks.append({"kind": "table", "title": "How the behaviour score is made", "columns": ["Parameter", "Measured", "Mark", "Weight", "Rule"],
                       "widths": [1.6, 2.6, 0.8, 0.8, 3.6], "right": [2, 3],
                       "rows": [[{"text": item["label"], "bold": True}, item["value"], "-" if item["score"] is None else str(item["score"]), f"{item['weight']}%", item["rule"]]
                                for item in behaviour["params"]]})
    if behaviour.get("traits"):
        blocks.append({"kind": "points", "items": [f"{trait['label']}: {trait['why']}" for trait in behaviour["traits"]]})
    if data["activities"]:
        blocks += [{"kind": "heading", "text": "History", "note": "The latest 15 events."},
                   {"kind": "table", "columns": ["When", "What", "Details"], "widths": [1.6, 2, 5],
                    "rows": [[_day(item.created_at), _event_label(overview, item.event_type), (item.details or "")[:200]]
                             for item in data["activities"][:15]]}]
    if data["cancelled"]:
        blocks.append({"kind": "text", "text": "Cancelled: " + ", ".join(_program_name(item) for item in data["cancelled"]) + "."})
    subtitle = " · ".join(part for part in (candidate.email, candidate.phone, candidate.university, candidate.city) if part)
    return _report(request, db, data["name"], blocks, "Candidate file", subtitle)


# ------------------------------------------------------------------
# Programs
# ------------------------------------------------------------------

@router.get("/programs/pdf")
def programs_pdf(request: Request, db: Session = Depends(get_db)):
    """The programs for clients: a welcome page about 9antra, then every program in short, with links to 9antra.tn."""
    if not program_pdf.available():
        raise HTTPException(status_code=503, detail="The PDF library is not installed.")
    lang = program_pdf.clean_language(request.query_params.get("lang") or "fr")
    data = program_pdf.build_summary(db, None, lang)
    from app.api.program_pdf import _pdf

    count = db.query(Program).count()
    return _pdf(data, program_pdf.catalogue_filename(lang, count, True, summary=True), request.query_params.get("download") == "1")


@router.get("/programs/overview/pdf")
def programs_overview_pdf(request: Request, db: Session = Depends(get_db)):
    """The programs for the team: prices, registrations, money, classes."""
    programs = db.query(Program).order_by(Program.name).all()
    registrations = db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all()
    classes = db.query(ClassGroup).all()
    rows, counts = [], []
    for program in programs:
        mine = [item for item in registrations if item.program_id == program.id]
        running = sum(1 for item in classes if item.program_id == program.id and item.status == ClassStatus.ONGOING)
        collected = sum(item.paid_amount or 0 for item in mine)
        paid = sum(1 for item in mine if item.payment_status == PaymentStatus.PAID)
        counts.append((program.name, len(mine)))
        rows.append([{"text": program.name, "bold": True}, _money(program.price_online) if program.price_online else "-",
                     _money(program.price_onsite) if program.price_onsite else "-", program.class_session_time or "-",
                     str(len(mine)), str(paid), _money(collected), str(running)])
    blocks = [
        {"kind": "figures", "items": [("Programs", str(len(programs)), "", "brand"),
                                      ("Registrations", str(len(registrations)), "not cancelled", "info"),
                                      ("Collected", _money(sum(item.paid_amount or 0 for item in registrations)), "", "ok"),
                                      ("Classes running", str(sum(1 for item in classes if item.status == ClassStatus.ONGOING)), "", "violet")]},
        {"kind": "hbars", "title": "Registrations per program", "rows": sorted(counts, key=lambda pair: -pair[1])[:15]},
        {"kind": "table", "title": "Every program", "columns": ["Program", "Online", "On site", "Hours", "Registrations", "Paid", "Collected", "Running"],
         "widths": [3, 1.1, 1.1, 1.6, 1.1, 0.8, 1.3, 0.9], "right": [1, 2, 4, 5, 6, 7], "rows": rows},
        {"kind": "box", "tone": "gold", "text": "For clients, use the programs PDF with the welcome page (Programs page, or PDF documents)."},
    ]
    return _report(request, db, "Programs", blocks, "For the team", f"{len(programs)} programs", landscape=True)


@router.get("/programs/market/pdf")
def market_pdf(request: Request, db: Session = Depends(get_db)):
    """What employers ask, field by field and program by program."""
    from app.services import market

    view = market.overview(db)
    last = utc_to_local(view["last_read"]) if view["last_read"] else None
    blocks = [
        {"kind": "figures", "items": [("Offers read", str(view["total"]), "public job boards and pasted ones", "brand"),
                                      ("Sources", str(len(view["sources"])), ", ".join(view["sources"])[:40], "info"),
                                      ("Fields", str(len(view["trends"])), "with offers", "violet"),
                                      ("Last read", _day(last) if last else "never", "", "muted")]},
        {"kind": "hbars", "title": "The fields employers ask for (share of the offers)",
         "rows": [(f"{row['name']}{'' if row['taught'] else ' (not taught here)'}", row["pct"], f"{row['pct']}% · {row['offers']}") for row in view["trends"][:14]],
         "most": 100, "empty": "No offer was read yet."},
    ]
    for row in view["programs"]:
        if not row.get("skills"):
            continue
        blocks += [{"kind": "heading", "text": row["program"].name,
                    "note": f"{row['offers']} offer(s) of its field ({row['field']})" + (f" · the program covers {row['fit']}% of what they ask" if row["fit"] is not None else "")},
                   {"kind": "hbars", "rows": [(f"{item['label']}{' (taught)' if item['covered'] else ''}", item["pct"]) for item in row["skills"][:10]],
                    "most": 100, "unit": "%", "tone": "info"}]
        if row.get("advice"):
            blocks.append({"kind": "points", "items": row["advice"][:4]})
    return _report(request, db, "Job market", blocks, "What employers ask", "Counts from fixed skill lists; LinkedIn and Facebook are never read automatically.")


# ------------------------------------------------------------------
# Classes
# ------------------------------------------------------------------

@router.get("/classes/pdf")
def classes_pdf(request: Request, db: Session = Depends(get_db)):
    """Every class: its program, professor, dates, candidates and where it stands."""
    from app.services.class_views import class_summary
    from app.services.schedule_utils import class_start_dt

    now = local_now()
    classes = db.query(ClassGroup).options(joinedload(ClassGroup.program), joinedload(ClassGroup.registrations)).all()
    order = {ClassStatus.ONGOING: 0, ClassStatus.NOT_STARTED: 1, ClassStatus.COMPLETED: 2}
    classes.sort(key=lambda item: (order.get(item.status, 3), class_start_dt(item) or datetime.max, item.name))
    labels = {ClassStatus.ONGOING: ("Running", "ok"), ClassStatus.NOT_STARTED: ("Not started", "info"), ClassStatus.COMPLETED: ("Completed", "muted")}
    rows, counts = [], {"Running": 0, "Not started": 0, "Completed": 0}
    for item in classes:
        summary = class_summary(item, now)
        label = labels.get(item.status, ("", "muted"))
        if summary["on_hold"]:
            label = ("On hold", "warn")
        counts[label[0]] = counts.get(label[0], 0) + 1
        rows.append([{"text": item.name, "bold": True, "note": item.program.name if item.program else ""}, item.professor_name or "Not assigned",
                     {"chip": label[0], "tone": label[1]}, summary["starts_label"], summary["schedule_label"],
                     {"bar": summary["fill_pct"], "text": f"{summary['enrolled']} / {summary['capacity']}"},
                     f"{summary['current_week']} / {summary['total_weeks']}" if item.status == ClassStatus.ONGOING else "-"])
    blocks = [
        {"kind": "figures", "items": [("Running", str(counts.get("Running", 0)), "", "ok"), ("Not started", str(counts.get("Not started", 0)), "", "info"),
                                      ("On hold", str(counts.get("On hold", 0)), "waiting for candidates or a professor", "warn"),
                                      ("Without a professor", str(sum(1 for item in classes if not item.professor_name and item.status != ClassStatus.COMPLETED)), "", "bad")]},
        {"kind": "table", "title": "Every class", "columns": ["Class", "Professor", "State", "Starts", "Schedule", "Candidates", "Week"],
         "widths": [3, 1.8, 1.1, 1.9, 2, 1.4, 0.8], "rows": rows, "empty": "No class yet."},
    ]
    return _report(request, db, "Classes", blocks, "Every class of the centre", f"{len(classes)} class(es)", landscape=True)


def _class(db: Session, class_id: str) -> ClassGroup:
    class_group = db.query(ClassGroup).options(joinedload(ClassGroup.program)).filter(ClassGroup.id == class_id).first()
    if class_group is None:
        raise HTTPException(status_code=404, detail="Class not found")
    return class_group


def dynamics_blocks(dynamics: dict) -> list[dict]:
    """The class's dynamics as report blocks: shared by the class PDF and the analysis."""
    figures = dynamics["figures"]
    blocks = [{"kind": "figures", "items": [
        ("Presence", _percent(figures["attendance"]), f"last {len(dynamics['sessions'][-3:])} sessions: {_percent(figures['recent_attendance'])}" if dynamics["sessions"] else "no session marked", "info"),
        ("Interactivity", "-" if figures["interactivity"] is None else f"{figures['interactivity']}/100",
         f"{figures['questions_each']} question(s) per person per session" if figures["questions_each"] is not None else "not noted yet", "brand"),
        ("Weekly answers", _percent(figures["answer_rate"]), f"{figures['answered']} of {figures['sent']} answered", "violet"),
        ("Understanding", _percent(figures["understanding"]), f"last week {_percent(figures['last_week_score'])}" if figures["last_week_score"] is not None else "", "ok")]}]
    if dynamics["sessions"]:
        sessions = dynamics["sessions"][-10:]
        blocks.append({"kind": "bars", "title": "Session by session: presence and participation (%)", "height": 150,
                       "labels": [f"{row['date'].day} {row['date']:%b}" if row["date"] else f"W{row['week']}" for row in sessions],
                       "series": [{"name": "Presence", "values": [row["rate"] or 0 for row in sessions], "tone": "info"},
                                  {"name": "Participation", "values": [round(row["participation"] or 0) for row in sessions], "tone": "brand"}]})
    if dynamics["weeks"]:
        blocks.append({"kind": "bars", "title": "Week by week: answered and understood (%)", "height": 140,
                       "labels": [f"W{row['week']}" for row in dynamics["weeks"]],
                       "series": [{"name": "Answered", "values": [row["answer_rate"] or 0 for row in dynamics["weeks"]], "tone": "muted"},
                                  {"name": "Understood", "values": [round(row["score"] or 0) for row in dynamics["weeks"]], "tone": "violet"}]})
    blocks.append(_improve_rows(dynamics["advice"]) if dynamics["advice"] else {"kind": "empty", "text": "Nothing recorded yet: the analysis starts with the first presence sheet."})
    if dynamics["leaders"] or dynamics["to_watch"]:
        rows = [[{"chip": "Leads", "tone": "ok"}, person["name"], f"very active ({round(person['participation'])}/100), {person['questions']} question(s) asked"] for person in dynamics["leaders"]]
        rows += [[{"chip": "A word", "tone": item["tone"] if item["tone"] != "muted" else "muted"}, item["name"], item["why"]] for item in dynamics["to_watch"]]
        blocks.append({"kind": "table", "title": "Who leads, who needs a word", "columns": ["", "Candidate", "Why"], "widths": [1, 2.4, 5], "rows": rows})
    return blocks


@router.get("/classes/{class_id}/pdf")
def class_pdf(class_id: str, request: Request, db: Session = Depends(get_db)):
    """One class: its dynamics, its candidates, its sessions."""
    from app.services import attendance, class_dynamics, level_check
    from app.services.class_views import class_summary
    from app.services.enrollment_service import class_members

    class_group = _class(db, class_id)
    summary = class_summary(class_group, local_now())
    dynamics = class_dynamics.of_class(db, class_group)
    presence = attendance.class_summary(db, class_group)
    levels = level_check.class_overview(db, class_group)
    level_of = {row["registration"].id: row["result"]["info"]["label"] for row in levels.get("people", []) if row.get("result")}
    people = {row["registration"].id: row for row in dynamics["people"]}
    blocks = [{"kind": "points", "items": [part for part in (
        f"Program: {class_group.program.name}" if class_group.program else None,
        f"Professor: {class_group.professor_name or 'not assigned yet'}",
        f"Schedule: {summary['schedule_label']}", f"Starts: {summary['starts_label']}",
        f"Candidates: {summary['enrolled']} of {summary['capacity']} places",
        f"Week {summary['current_week']} of {summary['total_weeks']}" if class_group.status == ClassStatus.ONGOING else None) if part]},
        {"kind": "heading", "text": "How this class is doing", "note": "Interactivity: what the professor noted (quiet 30, takes part 65, very active 100) for 70%, questions asked for 30%."}]
    blocks += dynamics_blocks(dynamics)
    rows = []
    for registration in class_members(db, class_group.id):
        total, paid = registration.total_amount or registration.total_price or 0, registration.paid_amount or 0
        state = pay_state(total, paid)
        person = people.get(registration.id, {})
        candidate = registration.candidate
        rows.append([{"text": (candidate.full_name or candidate.email) if candidate else "Candidate", "bold": True, "note": candidate.email if candidate else ""},
                     candidate.phone if candidate and candidate.phone else "", {"chip": state[0], "tone": state[1]},
                     _percent(person.get("rate")), "-" if person.get("participation") is None else f"{round(person['participation'])}/100",
                     _percent(person.get("score")), level_of.get(registration.id, "-")])
    blocks += [{"kind": "heading", "text": "Candidates"},
               {"kind": "table", "columns": ["Candidate", "Phone", "Payment", "Presence", "Participation", "Understanding", "Level at start"],
                "widths": [2.6, 1.3, 1.3, 1, 1.2, 1.2, 1.4], "right": [3, 4, 5], "rows": rows, "empty": "Nobody is in this class yet."}]
    sessions = sorted(class_group.sessions, key=lambda item: (item.session_date, item.week_number or 0))
    by_session = presence["by_session"]
    blocks += [{"kind": "heading", "text": "Sessions"},
               {"kind": "table", "columns": ["Day", "Week", "Time", "Topic", "Presence"], "widths": [1.6, 0.7, 1.2, 4, 1.2],
                "rows": [[f"{item.session_date:%a} {item.session_date.day} {item.session_date:%b}", str(item.week_number or ""),
                          _hours(item.start_time or class_group.start_time, item.end_time or class_group.end_time), item.topic or "",
                          (lambda counts: f"{counts['present'] + counts['late']} / {sum(counts.values())}" if counts and sum(counts.values()) else "-")(by_session.get(item.id))]
                         for item in sessions], "empty": "No weekly plan: the class meets on its schedule."}]
    if dynamics["opinions"]["answered"]:
        opinions = dynamics["opinions"]
        blocks += [{"kind": "heading", "text": "Their opinions at the end"},
                   {"kind": "table", "columns": ["Who", "Stars", "What they wrote"], "widths": [1.6, 0.8, 6],
                    "rows": [[item["name"], str(item["stars"] or "-"), item["words"][:300]] for item in opinions["rows"] if item["answered"]][:12]}]
    return _report(request, db, class_group.name, blocks, "Class", summary["schedule_label"])


@router.get("/classes/{class_id}/attendance/pdf")
def attendance_pdf(class_id: str, request: Request, db: Session = Depends(get_db)):
    """A presence sheet to print for the next sessions, and the presence so far."""
    from app.services import attendance
    from app.services.schedule_utils import class_start_dt, describe_schedule_en

    class_group = _class(db, class_id)
    today = local_now().date()
    coming = [item.session_date for item in sorted(class_group.sessions, key=lambda row: row.session_date) if item.session_date >= today][:6]
    if not coming and class_start_dt(class_group):
        first = class_start_dt(class_group).date()
        coming = [day for day in (first + timedelta(weeks=index) for index in range(class_group.total_weeks or 12)) if day >= today][:6]
    if not coming:
        coming = [None] * 5
    members = attendance.members(db, class_group.id)
    sheet = []
    for number, registration in enumerate(members, start=1):
        candidate = registration.candidate
        sheet.append([str(number), {"text": (candidate.full_name or candidate.email) if candidate else "Candidate", "bold": True},
                      candidate.phone if candidate and candidate.phone else ""] + ["" for _ in coming] + [""])
    for _ in range(3):        # room for someone who joins
        sheet.append([""] * (4 + len(coming)))
    summary = attendance.class_summary(db, class_group)
    blocks = [{"kind": "table", "title": "Presence sheet: tick P (present), L (late) or A (absent)",
               "columns": ["#", "Candidate", "Phone"] + [f"{day:%a} {day.day}/{day.month}" if day else "Date: ____" for day in coming] + ["Notes"],
               "widths": [0.4, 3, 1.5] + [1.1] * len(coming) + [2.2], "rows": sheet}]
    blocks += [{"kind": "heading", "text": "Presence so far"},
               {"kind": "table", "columns": ["Candidate", "Present", "Late", "Absent", "Rate", "Absent in a row"], "widths": [3, 1, 1, 1, 1, 1.4],
                "right": [1, 2, 3, 4, 5],
                "rows": [[{"text": person["name"], "bold": True}, str(person["present"]), str(person["late"]), str(person["absent"]), _percent(person["rate"]),
                          {"chip": str(person["absent_in_a_row"]), "tone": "bad" if person["absent_in_a_row"] >= 2 else "muted"}] for person in summary["people"]],
                "empty": "No session marked yet."}]
    subtitle = f"{class_group.professor_name or 'Professor not assigned'} · {describe_schedule_en(class_group)}"
    return _report(request, db, f"Presence · {class_group.name}", blocks, "Presence sheet", subtitle, landscape=True)


# ------------------------------------------------------------------
# Payments, level checks, inbox, reminders
# ------------------------------------------------------------------

@router.get("/payments/pdf")
def payments_pdf(request: Request, db: Session = Depends(get_db)):
    """Who still has to pay, the money of the last weeks, and the latest payments."""
    from app.api.dashboard import payment_rows
    from app.services import analysis

    rows, totals = payment_rows(db)
    series = analysis.weekly_series(db)
    latest = (db.query(Payment).options(joinedload(Payment.registration).joinedload(Registration.candidate), joinedload(Payment.registration).joinedload(Registration.program))
              .order_by(Payment.payment_date.desc()).limit(25).all())
    blocks = [
        {"kind": "figures", "items": [("Collected", _money(totals["paid"]), "in all", "ok"), ("Still due", _money(totals["due"]), f"from {totals['people']} people", "warn"),
                                      ("Reminders to decide", str(totals["to_decide"]), "the dashboard suggests them", "bad" if totals["to_decide"] else "ok"),
                                      ("This week", _money(series["money"][-1]), f"{series['payments'][-1]} payment(s)", "brand")]},
        {"kind": "bars", "title": "Money received per week (DT)", "labels": series["labels"], "height": 160,
         "series": [{"name": "DT", "values": series["money"], "tone": "ok"}]},
        {"kind": "table", "title": "Who still has to pay", "columns": ["Candidate", "Programs", "Still due", "Reminders sent", "Last reminder", "What to do"],
         "widths": [2.4, 2.8, 1.2, 1.1, 1.3, 2.8], "right": [2, 3],
         "rows": [[{"text": row["name"], "bold": True, "note": row["candidate"].phone or row["candidate"].email}, ", ".join(row["programs"]), _money(row["due"]),
                   str(row["sent"]), f"{row['days']} day(s) ago" if row["days"] is not None else "never",
                   row["advice"].title if row["advice"] else ""] for row in rows], "empty": "Nobody owes anything."},
        {"kind": "table", "title": "The latest payments", "columns": ["Day", "Candidate", "Program", "Amount", "How"], "widths": [1.4, 2.6, 3, 1.2, 1.2], "right": [3],
         "rows": [[_day(item.payment_date), (item.registration.candidate.full_name or item.registration.candidate.email) if item.registration and item.registration.candidate else "",
                   _program_name(item.registration) if item.registration else "", _money(item.amount), (item.payment_method or "").capitalize()] for item in latest],
         "empty": "No payment recorded yet."},
    ]
    return _report(request, db, "Payments", blocks, "Money", f"{totals['people']} candidate(s) still owe {_money(totals['due'])}", landscape=True)


@router.get("/quiz/pdf")
def levels_pdf(request: Request, db: Session = Depends(get_db)):
    """The level checks: who answered, and the level found."""
    from app.services import level_check

    view = level_check.overview(db)
    names = {key: label for key, label, *_ in level_check.LEVELS}
    states = {"ready": ("Ready to send", "muted"), "sent": ("Sent, waiting", "warn"), "answered": ("Answered", "ok"), "none": ("Not prepared", "muted")}
    blocks = [
        {"kind": "figures", "items": [("Ready to send", str(view["counts"].get("ready", 0)), "", "muted"), ("Sent, waiting", str(view["counts"].get("sent", 0)), "", "warn"),
                                      ("Answered", str(view["counts"].get("answered", 0)), "", "ok"),
                                      ("Reminder", "on" if view["reminders_on"] else "off", f"once, after {view['remind_after_days']} days", "info")]},
        {"kind": "hbars", "title": "Levels found", "rows": [(names.get(key, key), count) for key, count in view["levels"].items()], "empty": "No level check answered yet."},
        {"kind": "table", "title": "Every paid candidate", "columns": ["Candidate", "Program", "Class", "State", "Level found"], "widths": [2.6, 2.8, 2.2, 1.4, 1.8],
         "rows": [[{"text": row["name"], "bold": True}, row["program"], row["class_group"].name if row["class_group"] else "No class yet",
                   {"chip": states.get(row["state"], (row["state"], "muted"))[0], "tone": states.get(row["state"], ("", "muted"))[1]},
                   row["result"]["info"]["label"] if row["result"] else "-"] for row in view["rows"]], "empty": "No paid candidate waits for a level check."},
    ]
    return _report(request, db, "Level checks", blocks, "Where each one starts from", f"{len(view['rows'])} paid candidate(s)")


@router.get("/inbox/pdf")
def inbox_pdf(request: Request, db: Session = Depends(get_db)):
    """The emails of the week, how they were answered, and what waits for the admin."""
    now_utc, now_local = datetime.utcnow(), local_now()
    days = [(now_local - timedelta(days=6 - index)).date() for index in range(7)]
    counts = {day: [0, 0, 0] for day in days}         # received, answered by the assistant, answered by you
    for moment, direction, author in db.query(CandidateEmail.received_at, CandidateEmail.direction, CandidateEmail.author).filter(
            CandidateEmail.received_at >= now_utc - timedelta(days=8)).all():
        slot = counts.get(utc_to_local(moment).date()) if moment else None
        if slot is None:
            continue
        if direction == "inbound":
            slot[0] += 1
        elif author == "system":
            slot[1] += 1
        elif author == "admin":
            slot[2] += 1
    waiting = (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.requires_admin.is_(True))
               .order_by(CandidateEmail.received_at.desc()).limit(30).all())
    people = {row.id: row for row in db.query(Candidate).filter(Candidate.id.in_({item.candidate_id for item in waiting} or {""})).all()}
    week = [sum(counts[day][index] for day in days) for index in range(3)]
    blocks = [
        {"kind": "figures", "items": [("Received", str(week[0]), "these 7 days", "brand"), ("Answered by the assistant", str(week[1]), "alone", "ok"),
                                      ("Answered by you", str(week[2]), "", "info"),
                                      ("Waiting for you", str(db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()), "conversations", "warn")]},
        {"kind": "bars", "title": "This week, day by day", "labels": [f"{day:%a} {day.day}" for day in days],
         "series": [{"name": "Received", "values": [counts[day][0] for day in days], "tone": "brand"},
                    {"name": "Answered by the assistant", "values": [counts[day][1] for day in days], "tone": "ok"},
                    {"name": "Answered by you", "values": [counts[day][2] for day in days], "tone": "info"}]},
        {"kind": "table", "title": "Emails waiting for your answer", "columns": ["Received", "Candidate", "Subject", "Why it waits"], "widths": [1.3, 2, 2.8, 3.6],
         "rows": [[_day(item.received_at), (people[item.candidate_id].full_name or people[item.candidate_id].email) if item.candidate_id in people else "",
                   item.subject or "(no subject)", (item.escalation_reason or "")[:220]] for item in waiting], "empty": "Nothing waits for you."},
    ]
    return _report(request, db, "Inbox", blocks, "Emails with candidates", _week_range(now_local))


@router.get("/reminders/pdf")
def reminders_pdf(request: Request, db: Session = Depends(get_db)):
    """The reminders planned and the ones already sent."""
    from app.services import reminders

    def rows(items, sent):
        return [[{"text": item.subject, "bold": True}, item.audience_label or "", _day(item.sent_at if sent and item.sent_at else item.send_at),
                 str(item.sent_count or 0) if sent else str(len(reminders.recipients(db, item.audience, item.audience_id))),
                 {"chip": (item.status or "").capitalize(), "tone": "ok" if item.status == "sent" else "muted" if item.status == "cancelled" else "info"}]
                for item in items]

    upcoming, history = reminders.upcoming(db), reminders.history(db, limit=40)
    blocks = [
        {"kind": "figures", "items": [("Planned", str(len(upcoming)), "to go", "info"), ("Sent", str(sum(1 for item in history if item.status == "sent")), "the latest", "ok"),
                                      ("Ideas", str(len(reminders.suggestions(db))), "from how the classes stand", "gold")]},
        {"kind": "table", "title": "Planned", "columns": ["Reminder", "To", "When", "People", "State"], "widths": [3, 2.4, 1.6, 0.8, 1.1], "right": [3],
         "rows": rows(upcoming, False), "empty": "No reminder planned."},
        {"kind": "table", "title": "Already sent", "columns": ["Reminder", "To", "Sent", "People", "State"], "widths": [3, 2.4, 1.6, 0.8, 1.1], "right": [3],
         "rows": rows(history, True), "empty": "No reminder sent yet."},
    ]
    return _report(request, db, "Reminders", blocks, "Your reminders")


# ------------------------------------------------------------------
# Website, system, settings, every table
# ------------------------------------------------------------------

@router.get("/website/pdf")
def website_pdf(request: Request, db: Session = Depends(get_db)):
    """What 9antra.tn publishes, how the programs follow it, and the published feedback."""
    from app.services import feedback, website_sync

    view, said = website_sync.overview(db), feedback.overview(db)
    tones = {"positive": ("Happy", "ok"), "mixed": ("Mixed", "warn"), "negative": ("Unhappy", "bad"), "neutral": ("Neutral", "muted")}
    blocks = [
        {"kind": "figures", "items": [("Formations", str(view["counts"].get("formation", 0)), "on the website", "brand"),
                                      ("Coming events", str(len(view["events_coming"])), "", "info"),
                                      ("Offers", str(view["counts"].get("offer", 0)), "internships and jobs", "violet"),
                                      ("Feedback", str(said["total"]), f"{_percent(said['happy_share'])} happy" if said["happy_share"] is not None else "", "ok")]},
        {"kind": "table", "title": "Formations and the programs that follow them", "columns": ["On the website", "Program here", "To decide"], "widths": [3.4, 3, 1.4], "right": [2],
         "rows": [[{"text": entry["item"].title, "bold": True, "note": entry["item"].url or ""}, entry["program"].name if entry["program"] else ("Not followed" if entry["ignored"] else "No program yet"),
                   str(len(entry["differences"])) if entry["differences"] else "-"] for entry in view["formations"]], "empty": "No formation read yet."},
    ]
    if view["events_coming"]:
        blocks.append({"kind": "table", "title": "Coming events", "columns": ["When", "Event"], "widths": [1.6, 6],
                       "rows": [[_day(item.starts_at), item.title] for item in view["events_coming"][:15]]})
    if said["total"]:
        blocks.append({"kind": "donut", "title": "Published feedback, by tone", "height": 130,
                       "parts": [(tones[key][0], said["counts"].get(key, 0), tones[key][1]) for key in ("positive", "mixed", "negative", "neutral")],
                       "center": (f"{said['stars']}/5" if said["stars"] else str(said["total"]), "stars on average" if said["stars"] else "reviews")})
        if said["themes"]:
            blocks.append({"kind": "hbars", "title": "What they talk about", "rows": [(theme["label"], theme["count"]) for theme in said["themes"]]})
        if said["to_answer"]:
            blocks.append({"kind": "table", "title": "Feedback to answer", "columns": ["Who", "Tone", "What they wrote"], "widths": [1.6, 1, 6],
                           "rows": [[row["name"], {"chip": tones.get(row["reading"].get("sentiment"), ("", "muted"))[0], "tone": tones.get(row["reading"].get("sentiment"), ("", "muted"))[1]},
                                     row["text"][:300]] for row in said["to_answer"][:10]]})
    return _report(request, db, "Website", blocks, "9antra.tn", "What the website publishes, kept up to date every 6 hours")


@router.get("/system/pdf")
def system_pdf(request: Request, db: Session = Depends(get_db)):
    """How the application is working: the automatic work and the services it relies on."""
    from app.api.system import _next_runs
    from app.services import system_status, weekly_questions
    from app.services.settings_service import public_base_url

    base_url = public_base_url(db, request)
    work = system_status.work(db, _next_runs())
    connections = system_status.connections(db, base_url, weekly_questions.is_local_address(base_url))
    summary = system_status.summary(work, connections)
    states = {"ok": ("Working", "ok"), "working": ("Working now", "info"), "problem": ("Problem", "bad"), "waiting": ("Starting", "muted")}
    links = {"on": ("On", "ok"), "off": ("Off", "bad"), "partial": ("Partly", "warn")}
    blocks = [
        {"kind": "box", "tone": "green" if summary["tone"] == "ok" else "gold", "text": f"{summary['title']}. {summary['text']}"},
        {"kind": "table", "title": "The work it does by itself", "columns": ["Work", "How often", "State", "Last done", "Lately"], "widths": [2.4, 1.6, 1.1, 1.4, 3.4],
         "rows": [[{"text": row["name"], "bold": True, "note": row["what"][:140]}, row["every"], {"chip": states.get(row["state"], ("", "muted"))[0], "tone": states.get(row["state"], ("", "muted"))[1]},
                   row["last"] or "-", row["problem"] or row["fact"]] for row in work]},
        {"kind": "table", "title": "What it is connected to", "columns": ["Service", "State", "What it means"], "widths": [2.2, 0.9, 5],
         "rows": [[{"text": row["name"], "bold": True}, {"chip": links.get(row["state"], ("", "muted"))[0], "tone": links.get(row["state"], ("", "muted"))[1]}, row["text"]]
                  for row in connections]},
    ]
    return _report(request, db, "How it is working", blocks, "The application", f"Since {system_status.STARTED_AT:%d %b %Y %H:%M}" if getattr(system_status, "STARTED_AT", None) else "")


@router.get("/settings/pdf")
def settings_pdf(request: Request, db: Session = Depends(get_db)):
    """Every setting in plain words: what is chosen now."""
    from app.services import settings_guide

    labels = {"ok": ("Set", "ok"), "warn": ("To check", "warn"), "off": ("Not working", "bad")}
    blocks = [{"kind": "box", "tone": "grey", "text": "What the application does, as it is set now. Each part can be changed on the Settings page."}]
    for group in settings_guide.plain_words(db, request):
        blocks += [{"kind": "heading", "text": group["title"], "room": 110},
                   {"kind": "table", "columns": ["What is set", ""], "widths": [7, 1.1],
                    "rows": [[line["text"], {"chip": labels[line["tone"]][0], "tone": labels[line["tone"]][1]}] for line in group["lines"]]}]
    return _report(request, db, "Settings", blocks, "In plain words", "What is chosen now")


@router.get("/data/{key}.pdf")
def data_pdf(key: str, request: Request, db: Session = Depends(get_db)):
    """Every record of one kind, as its list shows it."""
    from app.services import data_editor

    if data_editor.table(key) is None:
        raise HTTPException(status_code=404, detail="Unknown kind of record")
    search = (request.query_params.get("search") or "").strip()
    data = data_editor.everything(db, key, search)
    note = f"{data['total']} record(s)" + (f" matching “{search}”" if search else "") + (f", the first {data['shown']} shown" if data["shown"] < data["total"] else "")
    blocks = [{"kind": "table", "columns": data["columns"], "rows": data["rows"], "empty": "No record."}]
    return _report(request, db, data["name"], blocks, "All the data", note, landscape=len(data["columns"]) > 4)

