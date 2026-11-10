"""What the cloud model may look up to answer a question: read-only functions over the application's own records.

Two sets: for the admin and the team (Yasmine: the whole centre) and for one candidate (the email assistant: only
their own trainings, the programs, the centre and its rules). Each returns plain facts (English keys, dates as
"Sat 14/11/2026", times as "09:00"); the model writes the answer from them and nothing else, and every figure it
writes is checked against them (cloud_answer). Nothing here changes or sends anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict | None
    run: Callable

    def declaration(self) -> dict:
        found = {"name": self.name, "description": self.description}
        if self.parameters:
            found["parameters"] = self.parameters
        return found


def _params(**fields) -> dict:
    """{"field": ("description", "STRING" | "INTEGER", [allowed values] | None)} as a Gemini parameter schema."""
    properties = {}
    for name, (description, kind, allowed) in fields.items():
        properties[name] = {"type": kind, "description": description}
        if allowed:
            properties[name]["enum"] = allowed
    return {"type": "OBJECT", "properties": properties}


_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_WORDS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
              "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}


def _day(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    return f"{_DAYS[value.weekday()]} {value:%d/%m/%Y}"


def _hour(value) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _money(value) -> str:
    return f"{float(value or 0):.0f} DT"


def _today() -> date:
    from app.services.schedule_utils import local_now

    return local_now().date()


# ----------------------------------------------------------------------
# Classes and sessions (shared)
# ----------------------------------------------------------------------

def _class_facts(db: Session, group, *, members: bool = False) -> dict:
    from app.models import ClassStatus
    from app.services import meetings, schedule_utils
    from app.services.enrollment_service import class_members

    people = class_members(db, group.id)
    capacity = group.max_capacity or group.capacity
    start, end = schedule_utils.class_start_dt(group), schedule_utils.class_end_dt(group)
    found = {
        "class": group.name, "program": group.program.name if group.program else None,
        "state": {ClassStatus.NOT_STARTED: "not started", ClassStatus.ONGOING: "running", ClassStatus.COMPLETED: "completed"}.get(group.status, str(group.status)),
        "first_session": _day(start), "planned_end": _day(end),
        "week": f"{group.current_week or 0} of {group.total_weeks}" if group.status == ClassStatus.ONGOING and group.total_weeks else None,
        "days_and_hours": schedule_utils.describe_schedule_en(group) or "not set",
        "professor": group.professor_name or (group.professor.name if group.professor else None) or "none yet",
        "enrolled": len(people), "capacity": capacity, "seats_left": max(0, (capacity or 0) - len(people)) if capacity else None,
        "followed": meetings.mode_of(db, group), "room": group.room or None,
        "meeting_link": "given" if group.meeting_url else "not given yet",
        "presence": _presence(db, group)["present_share"],
    }
    if members:
        found["candidates"] = [item.candidate.full_name or item.candidate.email for item in people if item.candidate is not None]
    return found


def _find_class(db: Session, name: str):
    from app.models import ClassGroup

    wanted = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if not wanted:
        return None
    groups = db.query(ClassGroup).all()
    exact = [group for group in groups if re.sub(r"[^a-z0-9]", "", (group.name or "").lower()) == wanted]
    if exact:
        return exact[0]
    near = [group for group in groups if wanted in re.sub(r"[^a-z0-9]", "", (group.name or "").lower())]
    return near[0] if len(near) == 1 else None


def _presence(db: Session, group) -> dict:
    from app.models.classsession import ClassSession
    from app.models.user import Attendance

    marks = (db.query(Attendance.status).join(ClassSession, ClassSession.id == Attendance.session_id)
             .filter(ClassSession.class_group_id == group.id).all())
    came = sum(1 for (status,) in marks if status in ("present", "late"))
    return {"marks": len(marks), "present_share": f"{round(100 * came / len(marks))}%" if marks else "no presence marked yet"}


def _session_facts(db: Session, session, *, past: bool) -> dict:
    from app.models import ClassGroup, Professor
    from app.models.user import Attendance

    group = db.get(ClassGroup, session.class_group_id)
    found = {"date": _day(session.session_date), "from": _hour(session.start_time or (group.start_time if group else None)),
             "to": _hour(session.end_time or (group.end_time if group else None)), "class": group.name if group else None,
             "week": session.week_number, "topic": session.topic or None,
             "professor": (group.professor_name if group else None) or None}
    if session.substitute_professor_id:
        substitute = db.get(Professor, session.substitute_professor_id)
        found["replaced_by"] = substitute.name if substitute else None
    if past:
        marks = db.query(Attendance).filter(Attendance.session_id == session.id).all()
        found["presence_marked"] = bool(marks)
        found["absent"] = [mark.candidate.full_name or mark.candidate.email for mark in marks if mark.status == "absent" and mark.candidate is not None]
    return found


# ----------------------------------------------------------------------
# For the admin and the team
# ----------------------------------------------------------------------

def _overview(db: Session) -> dict:
    from app.models import Professor, Program
    from app.models.user import Attendance
    from app.services import overview as centre
    from app.services.schedule_utils import local_now

    figures = centre.figures(db, datetime.utcnow())
    board = centre.week_board(db, local_now())
    professors = db.query(Professor).all()
    marks = [status for (status,) in db.query(Attendance.status).all()]
    came = sum(1 for status in marks if status in ("present", "late"))
    return {"candidates": figures["candidates"], "new_candidates_this_week": figures["new_candidates"],
            "registrations": figures["registrations"], "paid_in_full": figures["paid_registrations"],
            "money_received_in_all": _money(figures["collected"]), "money_still_due": _money(figures["due"]),
            "candidates_who_owe": figures["owing_candidates"], "share_received": f"{figures['collection_rate']}%",
            "classes_running": figures["classes_running"], "classes_about_to_start": figures["classes_upcoming"],
            "sessions_a_week": board["sessions"], "professors": len(professors),
            "professors_available": sum(1 for person in professors if person.is_available), "programs": db.query(Program).count(),
            "emails_waiting_for_your_answer": figures["emails_waiting"],
            "weekly_questions_understood": f"{figures['understanding']:.0f}% over {figures['answers']} answers" if figures["understanding"] is not None else None,
            "presence_all_classes": f"{round(100 * came / len(marks))}% of {len(marks)} presence marks" if marks else "no presence marked yet"}


def _money_in(db: Session, period: str = "this_month") -> dict:
    from app.models.payment import Payment
    from app.models.registration import Registration, RegistrationStatus
    from app.services import chatbot_data

    words = {"today": "today", "this_week": "this week", "last_week": "last week", "this_month": "this month",
             "last_month": "last month", "all": "all"}.get(period, "this month")
    span = chatbot_data.period(words) if period != "all" else None
    query = db.query(Payment)
    if span is not None:
        query = query.filter(Payment.payment_date >= span[0], Payment.payment_date < span[1])
    rows = query.order_by(Payment.payment_date.desc()).all()
    owed = sum(item.remaining_amount for item in db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all()
               if item.remaining_amount > 0)
    latest = []
    for row in rows[:6]:
        registration = db.get(Registration, row.registration_id) if row.registration_id else None
        person = registration.candidate if registration is not None else None
        latest.append({"on": _day(row.payment_date), "amount": _money(row.amount), "candidate": (person.full_name or person.email) if person else None,
                       "program": registration.program_name if registration else None, "how": row.payment_method or None})
    return {"period": period, "received": _money(sum(row.amount or 0 for row in rows)), "payments": len(rows),
            "still_to_come_in_all": _money(owed), "latest": latest}


def _owing(db: Session) -> dict:
    from app.models.registration import PaymentStatus, Registration, RegistrationStatus

    rows = [row for row in db.query(Registration).filter(Registration.payment_status != PaymentStatus.PAID).all()
            if row.status != RegistrationStatus.CANCELLED and row.candidate is not None]
    people = [{"candidate": row.candidate.full_name or row.candidate.email, "program": row.program_name,
               "total": _money(row.total_amount or row.total_price), "paid": _money(row.paid_amount), "owes": _money(row.remaining_amount),
               "reminded": "yes" if row.reminder_sent_at else "no"} for row in rows]
    return {"not_paid_in_full": people, "people": len({item["candidate"] for item in people}),
            "owed_in_all": _money(sum(row.remaining_amount for row in rows))}


def _registrations(db: Session, period: str = "this_month") -> dict:
    from app.models.registration import Registration, RegistrationStatus

    today = _today()
    start = {"today": today, "this_week": today - timedelta(days=today.weekday()), "last_7_days": today - timedelta(days=7),
             "this_month": today.replace(day=1), "last_30_days": today - timedelta(days=30), "all": date(2000, 1, 1)}.get(period, today.replace(day=1))
    rows = [row for row in db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all()
            if row.registration_date is not None and row.registration_date.date() >= start]
    return {"period": period, "since": _day(start), "registrations": len(rows), "candidates": len({row.candidate_id for row in rows}),
            "list": [{"candidate": row.candidate.full_name if row.candidate else None, "program": row.program_name, "on": _day(row.registration_date)}
                     for row in sorted(rows, key=lambda item: item.registration_date, reverse=True)[:15]]}


def _classes(db: Session, which: str = "all", program: str = "", within_days: int = 0) -> dict:
    from app.models import ClassGroup, ClassStatus
    from app.services import schedule_utils

    today = _today()
    groups = []
    for group in db.query(ClassGroup).order_by(ClassGroup.name).all():
        if which == "running" and group.status != ClassStatus.ONGOING:
            continue
        if which in ("not_started", "starting_soon") and group.status != ClassStatus.NOT_STARTED:
            continue
        if which != "completed" and which != "all_with_completed" and group.status == ClassStatus.COMPLETED:
            continue
        if which == "completed" and group.status != ClassStatus.COMPLETED:
            continue
        if program and program.lower() not in ((group.program.name if group.program else "") or "").lower():
            continue
        start = schedule_utils.class_start_dt(group)
        if within_days and not (start and today <= start.date() <= today + timedelta(days=within_days)):
            continue
        groups.append(_class_facts(db, group))
    return {"which": which, "today": _day(today), "count": len(groups), "classes": groups}


def _class(db: Session, name: str) -> dict:
    from app.models.classsession import ClassSession

    group = _find_class(db, name)
    if group is None:
        return {"error": f"No class is called “{name}”."}
    found = _class_facts(db, group, members=True)
    found["presence"] = _presence(db, group)
    today = _today()
    last = (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date <= today)
            .order_by(ClassSession.session_date.desc()).first())
    upcoming = (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= today)
                .order_by(ClassSession.session_date.asc()).first())
    found["last_session"] = _session_facts(db, last, past=True) if last else None
    found["next_session"] = _session_facts(db, upcoming, past=False) if upcoming else None
    return found


def _sessions(db: Session, when: str = "this_week", class_name: str = "") -> dict:
    from app.models.classsession import ClassSession

    today = _today()
    monday = today - timedelta(days=today.weekday())
    spans = {"today": (today, today), "tomorrow": (today + timedelta(days=1), today + timedelta(days=1)),
             "this_week": (monday, monday + timedelta(days=6)), "next_week": (monday + timedelta(days=7), monday + timedelta(days=13)),
             "next_7_days": (today, today + timedelta(days=7)), "last_7_days": (today - timedelta(days=7), today),
             "last_week": (monday - timedelta(days=7), monday - timedelta(days=1))}
    query = db.query(ClassSession)
    group = _find_class(db, class_name) if class_name else None
    if group is not None:
        query = query.filter(ClassSession.class_group_id == group.id)
    if when == "last":
        rows = query.filter(ClassSession.session_date <= today).order_by(ClassSession.session_date.desc()).limit(1 if group else 6).all()
    else:
        first, last = spans.get(when, spans["this_week"])
        rows = query.filter(ClassSession.session_date >= first, ClassSession.session_date <= last).order_by(ClassSession.session_date.asc()).all()
    return {"when": when, "today": _day(today), "count": len(rows),
            "sessions": [_session_facts(db, row, past=row.session_date <= today) for row in rows[:20]]}


def _professors(db: Session, program: str = "", free_on: str = "") -> dict:
    from app.models import ClassGroup, ClassStatus, Professor
    from app.models.professor_absence import ProfessorAbsence
    from app.services import schedule_utils

    today = _today()
    day = None
    if free_on:
        word = free_on.strip().lower()
        if word in ("today", "aujourd'hui"):
            day = today
        elif word in ("tomorrow", "demain"):
            day = today + timedelta(days=1)
        elif word in _DAY_WORDS:
            day = today + timedelta(days=(_DAY_WORDS[word] - today.weekday()) % 7)
        else:
            try:
                day = datetime.strptime(word, "%d/%m/%Y").date()
            except ValueError:
                day = None
    people = []
    for person in db.query(Professor).order_by(Professor.name).all():
        if (person.name or "").startswith("À nommer"):
            continue
        if program and program.lower() not in (person.specialties or "").lower():
            continue
        groups = [group for group in db.query(ClassGroup).filter(ClassGroup.professor_id == person.id, ClassGroup.status != ClassStatus.COMPLETED).all()]
        found = {"professor": person.name, "teaches": person.specialties or "nothing written",
                 "available": bool(person.is_available), "classes": [f"{group.name} ({schedule_utils.describe_schedule_en(group) or 'hours not set'})" for group in groups]}
        absences = db.query(ProfessorAbsence).filter(ProfessorAbsence.professor_id == person.id, ProfessorAbsence.absence_date >= today).all()
        if absences:
            found["absent_on"] = [_day(row.absence_date) for row in absences[:5]]
        if day is not None:
            teaching = [group.name for group in groups if day.weekday() in schedule_utils.parse_days(group.days_of_week)]
            away = any(row.absence_date == day for row in absences)
            found["free_that_day"] = person.is_available and not teaching and not away
            if teaching:
                found["teaching_that_day"] = teaching
        people.append(found)
    free = [item["professor"] for item in people if item.get("free_that_day")]
    return {"day_asked": _day(day) if day else None, "count": len(people), "professors": people,
            **({"free_that_day": free, "free_count": len(free)} if day is not None else {})}


def _person(db: Session, name: str) -> dict:
    from app.models import Candidate
    from app.models.candidate_email import CandidateEmail
    from app.models.quiz import Quiz
    from app.models.registration import RegistrationStatus
    from app.models.user import Attendance
    from app.services import chatbot, dropout

    # A name that fits several people ("Fares": Fares, Fares Benali, Fares Trabelsi) is asked, never guessed.
    asked = set(chatbot._plain(name).split())
    fitting = [row for row in db.query(Candidate).all() if asked and asked <= set(chatbot._plain(row.full_name or "").split())]
    if len(fitting) > 1:
        return {"several_candidates": [row.full_name or row.email for row in fitting[:6]], "count": len(fitting),
                "note": "several candidates have this name: ask which one"}
    person = fitting[0] if fitting else chatbot._candidate_in(db, chatbot._plain(name))
    if person is None:
        found = [row for row in db.query(Candidate).all() if (name or "").lower().strip() and (name or "").lower().strip() in (row.full_name or "").lower()]
        person = found[0] if len(found) == 1 else None
    if person is None:
        return {"error": f"No candidate is called “{name}”."}
    trainings = []
    for row in person.registrations:
        if row.status == RegistrationStatus.CANCELLED:
            continue
        risk = dropout.of_registration(db, row) if row.class_group_id else {"score": 0, "reasons": []}
        trainings.append({"program": row.program_name, "class": row.class_group.name if row.class_group else "no class yet",
                          "total": _money(row.total_amount or row.total_price), "paid": _money(row.paid_amount), "owes": _money(row.remaining_amount),
                          "may_drop_out": f"{risk['score']}/100" if risk["score"] else "no sign", "why": risk["reasons"]})
    marks = [status for (status,) in db.query(Attendance.status).filter(Attendance.candidate_id == person.id).all()]
    scores = [score for (score,) in db.query(Quiz.score).filter(Quiz.candidate_id == person.id, Quiz.completed_at.isnot(None)).all() if score is not None]
    last = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person.id, CandidateEmail.direction == "inbound")
            .order_by(CandidateEmail.received_at.desc()).first())
    return {"candidate": person.full_name or person.email, "email": person.email, "phone": person.phone, "trainings": trainings,
            "presence": f"{sum(1 for mark in marks if mark in ('present', 'late'))} of {len(marks)} sessions" if marks else "no presence marked",
            "questions_average": f"{round(sum(scores) / len(scores))}%" if scores else "no answers yet",
            "last_message_from_them": {"on": _day(last.received_at), "said": (last.body or "")[:300], "waiting_for_you": bool(last.requires_admin)} if last else None}


def _drop_out(db: Session) -> dict:
    from app.services import dropout, keep_them

    rows = [keep_them._with_step(db, item) for item in dropout.at_risk(db, limit=8)]
    return {"count": len(rows), "may_drop_out": [{"candidate": item["name"], "class": item["class"].name, "score": f"{item['score']}/100",
                              "why": item["reasons"], "what_to_do": item["step"]} for item in rows],
            "what_to_do_by_reason": [text for _, text in keep_them.STEPS] + [keep_them.WANTS_TO_STOP, keep_them.TOO_MANY]}


def _best(db: Session) -> dict:
    from app.models.quiz import Quiz
    from app.models.registration import Registration, RegistrationStatus
    from app.models.user import Attendance

    rows = []
    for registration in db.query(Registration).filter(Registration.status == RegistrationStatus.ASSIGNED, Registration.class_group_id.isnot(None)).all():
        marks = [status for (status,) in db.query(Attendance.status).filter(Attendance.registration_id == registration.id).all()]
        scores = [score for (score,) in db.query(Quiz.score).filter(Quiz.registration_id == registration.id, Quiz.completed_at.isnot(None)).all() if score is not None]
        if not marks and not scores:
            continue
        presence = sum(1 for mark in marks if mark in ("present", "late")) / len(marks) if marks else None
        average = sum(scores) / len(scores) if scores else None
        weight = (presence if presence is not None else 0.5) * 60 + (average if average is not None else 50) * 0.4
        rows.append((weight, {"candidate": registration.candidate.full_name if registration.candidate else None, "class": registration.class_group.name,
                              "presence": f"{round(100 * presence)}%" if presence is not None else "not marked",
                              "questions_average": f"{round(average)}%" if average is not None else "no answers"}))
    rows.sort(key=lambda pair: -pair[0])
    return {"how_ranked": "presence first, then the average of their weekly questions", "count": min(6, len(rows)), "best": [row for _, row in rows[:6]]}


def _messages(db: Session, kind: str = "waiting") -> dict:
    from app.models.candidate_email import CandidateEmail
    from app.models.email_thread import EmailThread

    if kind == "not_reached":
        from app.services import reach

        return {"not_reached": [{"to": item["name"], "what": item["kind"], "why": item["why"], "on": _day(item["when"])}
                                for item in reach.not_reached(db)[:12]]}
    query = db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound")
    if kind == "waiting":
        threads = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).all()
        rows = []
        for thread in threads:
            last = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == thread.id, CandidateEmail.direction == "inbound")
                    .order_by(CandidateEmail.received_at.desc()).first())
            if last is not None:
                rows.append(last)
        return {"waiting_for_your_answer": len(rows), "messages": [_mail(row) for row in rows[:12]]}
    if kind == "complaints":
        rows = [row for row in query.order_by(CandidateEmail.received_at.desc()).limit(200).all()
                if row.category == "complaint" or row.intent == "complaint" or any(word in (row.emotions or "") for word in ("frustrated", "disappointed", "angry"))]
        return {"complaints_or_unhappy": len(rows), "messages": [_mail(row) for row in rows[:12]]}
    if kind == "answered_alone_today":
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.author == "system",
                                                 CandidateEmail.received_at >= start).order_by(CandidateEmail.received_at.desc()).all())
        return {"sent_by_the_assistants_today": len(rows), "messages": [{"to": row.candidate.full_name if row.candidate else row.recipient,
                                                                         "subject": row.subject, "at": _hour(row.received_at)} for row in rows[:15]]}
    rows = query.order_by(CandidateEmail.received_at.desc()).limit(10).all()
    return {"latest_from_candidates": [_mail(row) for row in rows]}


def _mail(row) -> dict:
    return {"from": row.candidate.full_name if row.candidate else row.sender, "on": _day(row.received_at), "subject": row.subject or "",
            "said": (row.body or "")[:240], "waiting_for_you": bool(row.requires_admin), "why_it_came_to_you": row.escalation_reason}


def _reviews(db: Session) -> dict:
    from app.services import feedback, program_feedback

    site = feedback.overview(db)
    own = program_feedback.rating(db)
    return {"end_of_program_reviews": {"stars": own["stars"], "count": own["count"]},
            "published_on_9antra_tn": {"read": site["read"], "happy_share": f"{site['happy_share']}%", "stars": site["stars"],
                                       "themes": [f"{theme['label']} ({theme['share']}%)" for theme in site["themes"][:5]]}}


def _programs(db: Session, name: str = "") -> dict:
    from app.models import ClassGroup, ClassStatus, Program
    from app.models.registration import PaymentStatus, Registration, RegistrationStatus
    from app.services.enrollment_service import teachers_of

    rows = []
    for program in db.query(Program).order_by(Program.name).all():
        if name and name.lower() not in (program.name or "").lower():
            continue
        registrations = [row for row in db.query(Registration).filter(Registration.program_id == program.id).all() if row.status != RegistrationStatus.CANCELLED]
        groups = db.query(ClassGroup).filter(ClassGroup.program_id == program.id, ClassGroup.status != ClassStatus.COMPLETED).all()
        rows.append({"program": program.name, "price_online": _money(program.price_online) if program.price_online else None,
                     "price_on_site": _money(program.price_onsite) if program.price_onsite else None,
                     "weeks": round(program.total_duration_days / 7) if program.total_duration_days else None,
                     "hours_per_week": program.hours_per_week, "session_time": program.class_session_time,
                     "registrations": len(registrations), "paid_in_full": sum(1 for row in registrations if row.payment_status == PaymentStatus.PAID),
                     "teachers": [person.name for person in teachers_of(db, program)], "open_classes": [group.name for group in groups]})
    rows.sort(key=lambda item: -item["registrations"])
    return {"count": len(rows), "programs_most_chosen_first": rows}


def _automatic(db: Session) -> dict:
    from app.services import email_assistant
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    return {"payment_reminder": f"one reminder, {settings.payment_reminder_hours or 24} hours after registering" if settings.enable_automated_reminders else "switched off",
            "level_check": f"sent {settings.level_check_when or 'after full payment'}" + (f", {settings.level_check_days_before} days before the start" if settings.level_check_days_before else ""),
            "class_created_when_someone_pays": bool(settings.auto_create_class_on_payment),
            "professor_given_to_new_classes": bool(settings.auto_assign_professor),
            "gap_between_two_trainings_days": settings.next_class_gap_days,
            "session_reminder_hours_before": settings.session_reminder_hours,
            "email_assistant": {"auto": "answers alone when sure", "review": "prepares, you send", "off": "off"}.get(email_assistant.mode_of(settings)),
            "whatsapp_assistant": {"auto": "answers alone when sure", "review": "prepares, you send", "off": "off"}.get(email_assistant.whatsapp_mode_of(settings)),
            "needed_to_answer_alone": f"{round(email_assistant.threshold_of(settings) * 100)}% sure"}


def _how(db: Session, user, question: str) -> dict:
    from app.services import howto

    task = howto.task_for(question, user, by_meaning=False) or howto.task_for(question, user)
    if task is None:
        return {"error": "No step-by-step guide matches this."}
    return {"guide": task["title"], "steps": task["steps"], "page": task["page"]}


def _case(db: Session, question: str) -> dict:
    from app.services import cases

    case = cases.case_of(question)
    if case is None:
        return {"error": "No case matches this."}
    found = cases.answer(db, question, lang="en")
    return {"case": case.key, "what_to_do": found["reply"]}


def _knows(db: Session, question: str) -> dict:
    from app.services import knowledge_service

    hits = [hit for hit in knowledge_service.search(db, question, questions=[question], limit=4) if hit.get("quality", 0) >= 0.45]
    return {"what_the_application_was_taught": [{"title": hit["title"], "text": (hit["content"] or "")[:900]} for hit in hits[:3]]}


def _marketing(db: Session, question: str) -> dict:
    from app.services import marketing

    return {"marketing": marketing.answer(db, question, lang="en")["reply"]}


def _trends(db: Session, question: str) -> dict:
    from app.services import trends_agent

    return {"trends": trends_agent.answer(db, question, lang="en")["reply"]}


def _search(db: Session, user, words: str = "") -> dict:
    """The application's own search (people, programs, classes, professors, reminders, emails) and the journal."""
    from app.models.news_item import NewsItem
    from app.services import finder

    words = (words or "").strip()[:80]
    if len(words) < 2:
        return {"error": "Nothing to search for."}
    who = user if hasattr(user, "is_admin") else None          # the signed-in person (what they may open), else the admin's view
    found = [{"kind": group["group"], "title": item["title"], "detail": item["detail"], "url": item["url"]}
             for group in finder.find(db, words, who, most=5) for item in group["items"]][:15]
    like = f"%{words}%"
    articles = (db.query(NewsItem).filter((NewsItem.title.ilike(like)) | (NewsItem.summary.ilike(like)))
                .order_by(NewsItem.published_at.desc()).limit(4).all())
    return {"searched": words, "count": len(found), "found": found,
            "articles": [{"title": row.title, "source": row.source, "on": _day(row.published_at), "url": row.url} for row in articles]}


def _job_market(db: Session, field: str = "") -> dict:
    """What the job offers read every day ask: per field, its share of the offers, the skills most asked, and whether it is taught here."""
    from app.services import market

    offers = market.all_offers(db)
    rows = market.trends(db, offers)
    wanted = (field or "").lower().strip()
    if wanted:
        chosen = [row for row in rows if wanted in row["name"].lower() or wanted in row["key"].lower()
                  or any(wanted in skill["label"].lower() for skill in row["skills"])]
        rows = chosen or rows
    read = market.last_read(db)
    return {"offers_read": len(offers), "last_read": _day(read) if read else None,
            "where_they_come_from": "public job boards (Remotive, Arbeitnow, Jobicy, Himalayas): remote and European offers, not Tunisian ones; and offers you pasted",
            "count": len(rows[:10]),
            "fields": [{"field": row["name"], "offers": row["offers"], "share_of_offers": f"{row['pct']}%", "taught_here": row["taught"],
                        "programs_here": row["programs"], "most_asked_skills": [f"{skill['label']} {skill['pct']}%" for skill in row["skills"][:5]],
                        "advice": row["advice"]} for row in rows[:10]]}


def _news(db: Session, topic: str = "") -> dict:
    """The journal: the latest articles of the fields taught, read every six hours from public feeds."""
    from app.models.news_item import NewsItem

    query = db.query(NewsItem)
    topic = (topic or "").strip()[:60]
    if topic:
        like = f"%{topic}%"
        query = query.filter((NewsItem.title.ilike(like)) | (NewsItem.summary.ilike(like)) | (NewsItem.domain == topic.lower()))
    rows = query.order_by(NewsItem.published_at.desc()).limit(8).all()
    return {"topic": topic or "all", "count": len(rows),
            "articles": [{"title": row.title, "source": row.source, "field": row.domain, "on": _day(row.published_at), "url": row.url,
                          "summary": " ".join((row.summary or "").split())[:240]} for row in rows]}


def _advice(db: Session, program: str = "") -> dict:
    """What to add, change, strengthen or reduce in a program, from the job offers and the developers' communities."""
    from app.models import Program
    from app.services import chatbot, recommender

    chosen = chatbot._program_in(db, chatbot._plain(program or ""))
    if chosen is None and program:
        found = [row for row in db.query(Program).all() if program.lower() in (row.name or "").lower()]
        chosen = found[0] if len(found) == 1 else None
    if chosen is None:
        return {"error": f"No program is called “{program}”.", "programs": [row.name for row in db.query(Program).order_by(Program.name).all()]}
    result = recommender.recommend(db, chosen)
    return {"program": chosen.name, "field": result.get("field"), "read_on": _day(result["day"]) if result.get("day") else None,
            "count": len(result["items"][:8]), "advice": [{"what": item["title"], "why": item["why"], "kind": item["kind"]} for item in result["items"][:8]]}


def _waiting_today(db: Session) -> dict:
    """What waits for the admin today, the most pressing first (the dashboard's list)."""
    from app.services import friend, ui_words
    from app.services.schedule_utils import local_now

    token = ui_words.use("en")
    try:
        lines = friend.lines_for_admin(db, friend._WORDS["en"], datetime.utcnow(), local_now())
    finally:
        ui_words.current.reset(token)
    return {"count": len(lines), "waiting_today": [line["text"] for line in lines][:10]}


def _happened(db: Session, since: str = "today") -> dict:
    """What came in and went out since a moment: messages, registrations, payments, proofs, reviews; answers sent."""
    from app.services import digest
    from app.services.reminders import to_utc
    from app.services.schedule_utils import local_now

    now = local_now()
    start = {"last_hour": now - timedelta(hours=1), "yesterday": (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
             "this_week": (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
             }.get(since, now.replace(hour=0, minute=0, second=0, microsecond=0))
    moment = to_utc(start)
    return {"since": f"{_day(start)} {_hour(start)}", "what_happened": [line["text"] for line in digest.lines(db, moment, "en")] or ["nothing"]}


def _connected(db: Session) -> dict:
    from app.services import presence

    rows = presence.connected(db)
    summary = presence.summary(rows)
    others = {row["name"] for row in rows if row["state"] != "online"} - {row["name"] for row in summary["online"]}
    return {"online_now": summary["people"], "online": [{"name": row["name"], "role": row["role"], "page": row["page"], "device": row["device"]}
                                                       for row in summary["online"]],
            "signed_in_but_away": sorted(others)}


def _reminders_now(db: Session) -> dict:
    from app.services import reminder_board, reminders
    from app.services.schedule_utils import utc_to_local

    planned = []
    for row in reminders.upcoming(db)[:10]:
        when = utc_to_local(row.send_at) if row.send_at else None
        planned.append({"subject": row.subject, "to": row.audience_label, "when": f"{_day(when)} {_hour(when)}" if when else None,
                        "repeat": row.repeat or "once", "state": row.status})
    return {"automatic": [{"name": item["name"], "on": item["on"], "when": item["when"], "to": item["who"]} for item in reminder_board.items(db)],
            "planned_by_you": planned, "planned_count": len(planned)}


_EMAIL_OR_PHONE = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\+?\d[\d ]{7,}\d")


def private_in(db: Session, words: str) -> bool:
    """Whether a web search would carry someone's name, email or phone: then it is not sent."""
    from app.models import Candidate, Professor, User
    from app.services import chatbot

    if _EMAIL_OR_PHONE.search(words or ""):
        return True
    said = f" {chatbot._plain(words)} "
    names = [name for (name,) in db.query(Candidate.full_name).all()] + [name for (name,) in db.query(Professor.name).all()] + \
            [name for (name,) in db.query(User.name).all()]
    for name in names:
        parts = [part for part in chatbot._plain(name or "").split() if len(part) >= 3]
        if len(parts) >= 2 and all(f" {part} " in said for part in parts):
            return True
        if len(parts) >= 2 and len(parts[-1]) >= 4 and f" {parts[-1]} " in said:
            return True                                   # the family name alone
    return False


def _web(db: Session, query: str = "") -> dict:
    """Google, through the cloud model: for what the application cannot know (the job market in Tunisia, salaries, a
    technology). Never sent with a person's name, email or phone."""
    from app.services import cloud_model

    query = " ".join((query or "").split())[:200]
    if not query:
        return {"error": "Nothing to search for."}
    if private_in(db, query):
        return {"error": "Not searched: the words name someone of the centre, and their data never goes to a web search."}
    found = cloud_model.search(query)
    if not found or not found.get("text"):
        return {"error": "The web search is not available just now."}
    return {"searched_on_the_web": query, "note": "from the internet (Google), not the centre's records", "found": found["text"][:3000],
            "sources": found["sources"][:6]}


PERIODS = ["today", "this_week", "last_week", "this_month", "last_month", "all"]
ASK_BACK = {"type": "OBJECT", "properties": {
    "question": {"type": "STRING", "description": "One short question back, in the admin's language"},
    "options": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "2 to 4 short answers to tap, in the admin's language"}},
    "required": ["question"]}


def _page_guide(db: Session, user, page: str = "", path: str | None = None) -> dict:
    """A page's tour: what it is for and every part (what it shows, what can be done and changed)."""
    from app.services import navigate, page_help

    named = navigate._page(navigate._fold(page), "en") if page else None
    address = named[0] if named and navigate.may_open(db, user, named[0]) else (page if page.startswith("/") else (path or "/"))
    found = page_help.guide(db, address, "en")
    if found is None:
        return {"error": "No guide for that page."}
    return {"page": found["title"], "address": address, "purpose": found["purpose"], "parts": found["parts"]}


def _pages(db: Session, user) -> dict:
    """Every page of the application this person may open, with its address and what it is for."""
    from app.services import navigate, page_tours

    found = []
    for url, names, _ in navigate.PAGES:
        if navigate.may_open(db, user, url):
            tour = page_tours.for_path(url.split("?", 1)[0].split("#", 1)[0])
            found.append({"page": names["en"], "address": url, "for": tour["purpose"] if tour else None})
    return {"count": len(found), "pages": found}


def for_admin(db: Session, user=None, path: str | None = None) -> dict[str, Tool]:
    text = ("The question, as asked", "STRING", None)
    found = [
        Tool("overview", "The centre at a glance: candidates, registrations, money received and due, classes, professors, programs, emails waiting.",
             None, lambda: _overview(db)),
        Tool("money_received", "Money received in a period, and what is still to come.",
             _params(period=("Which period", "STRING", PERIODS)), lambda period="this_month": _money_in(db, period)),
        Tool("not_paid", "Every candidate who has not paid in full: program, total, paid, owed, reminded or not.", None, lambda: _owing(db)),
        Tool("registrations", "New registrations in a period, how many and who.",
             _params(period=("Which period", "STRING", ["today", "this_week", "last_7_days", "this_month", "last_30_days", "all"])),
             lambda period="this_month": _registrations(db, period)),
        Tool("classes", "The classes: state, first session, days and hours, professor, enrolled, seats left, online or on site. "
                        "Filter: running, not_started (to come), completed, all; within_days keeps those whose first session is in the next N days.",
             _params(which=("Which classes", "STRING", ["all", "running", "not_started", "completed"]),
                     program=("Only this program (part of its name)", "STRING", None), within_days=("First session within this many days", "INTEGER", None)),
             lambda which="all", program="", within_days=0: _classes(db, which, program, int(within_days or 0))),
        Tool("class_detail", "One class in full: its candidates, presence rate, seats left, last session (who was absent) and next session.",
             _params(name=("The class's name, e.g. MD1 or P-20261001-1", "STRING", None)), lambda name="": _class(db, name)),
        Tool("sessions", "Sessions in a period (date, hours, class, week, topic, professor or replacement); past ones say who was absent.",
             _params(when=("Which sessions", "STRING", ["today", "tomorrow", "this_week", "next_week", "next_7_days", "last_7_days", "last_week", "last"]),
                     class_name=("Only this class", "STRING", None)),
             lambda when="this_week", class_name="": _sessions(db, when, class_name)),
        Tool("professors", "The professors: what they teach, their classes and hours, absences; with free_on (a weekday, today, tomorrow or "
                           "DD/MM/YYYY) whether each is free that day.",
             _params(program=("Only those who teach this (part of its name)", "STRING", None), free_on=("A day", "STRING", None)),
             lambda program="", free_on="": _professors(db, program, free_on)),
        Tool("candidate", "One candidate by name: trainings, classes, what they paid and owe, presence, questions average, risk of dropping out, "
                          "their last message.", _params(name=("Their name", "STRING", None)), lambda name="": _person(db, name)),
        Tool("may_drop_out", "Who may drop out (measured: absences, unanswered questions, results, money owed, what they wrote), why, and what to do.",
             None, lambda: _drop_out(db)),
        Tool("best_candidates", "The candidates doing best: presence, then their weekly questions' average.", None, lambda: _best(db)),
        Tool("messages", "Candidates' messages: waiting (for your answer), complaints (unhappy ones), answered_alone_today (sent by the "
                         "assistants today), not_reached (messages that did not arrive), latest.",
             _params(kind=("Which messages", "STRING", ["waiting", "complaints", "answered_alone_today", "not_reached", "latest"])),
             lambda kind="waiting": _messages(db, kind)),
        Tool("reviews", "What candidates think: end-of-program stars and the feedback published on 9antra.tn (share happy, themes).",
             None, lambda: _reviews(db)),
        Tool("programs", "The programs: prices online and on site, weeks, hours, how many chose each (most chosen first), who teaches it, open classes.",
             _params(name=("Only this program (part of its name)", "STRING", None)), lambda name="": _programs(db, name)),
        Tool("automatic", "What the application does by itself and how it is set: payment reminder, level check, classes created on payment, "
                          "professors given, session reminders, how the email and WhatsApp assistants answer.", None, lambda: _automatic(db)),
        Tool("how_to", "The step-by-step guide to do something in the application (add, create, change, export, give access…).",
             _params(question=text), lambda question="": _how(db, user, question)),
        Tool("what_to_do_when", "What to do in a case (a professor absent, a candidate who has not paid, a class without a professor, an unhappy "
                                "candidate, a refund, a missing meeting link, a message that did not arrive).",
             _params(question=text), lambda question="": _case(db, question)),
        Tool("taught", "What the admin taught the assistants and what 9antra.tn says, closest to the question.",
             _params(question=("The question rewritten in French (what is written there is in French and English)", "STRING", None)),
             lambda question="": _knows(db, question)),
        Tool("marketing", "Marketing: what is trending in posts, when to post, this week's plan, advice.",
             _params(question=text), lambda question="": _marketing(db, question)),
        Tool("trends", "What is new in each field taught (the journal) and what job offers ask.",
             _params(question=text), lambda question="": _trends(db, question)),
        Tool("search", "Search the application for words: candidates, programs, classes, professors, reminders, emails (and the journal's articles). "
                       "For a name or words when no other lookup fits.",
             _params(words=("The words to look for (a name, a subject…)", "STRING", None)), lambda words="": _search(db, user, words)),
        Tool("job_market", "The job market, from the offers read every day: per field, its share of the offers, the skills most asked, "
                           "and whether a program here teaches it.", _params(field=("Only this field or skill (data, AI, DevOps, Python…)", "STRING", None)),
             lambda field="": _job_market(db, field)),
        Tool("news", "The journal: the latest articles of the fields taught (title, source, date, link).",
             _params(topic=("Only about this (a field or a word)", "STRING", None)), lambda topic="": _news(db, topic)),
        Tool("program_advice", "What to add, change, strengthen or reduce in one program, from the job offers and the developers' communities.",
             _params(program=("The program's name", "STRING", None)), lambda program="": _advice(db, program)),
        Tool("waiting_today", "What waits for the admin today, the most pressing first.", None, lambda: _waiting_today(db)),
        Tool("what_happened", "What came in (messages, registrations, payments, proofs, reviews) and went out (answers, emails) since a moment.",
             _params(since=("Since when", "STRING", ["last_hour", "today", "yesterday", "this_week"])), lambda since="today": _happened(db, since)),
        Tool("connected", "Who is signed in to the application now, on which page.", None, lambda: _connected(db)),
        Tool("reminders", "The reminders: the automatic ones (on or off, when) and the ones the admin planned (to whom, when).",
             None, lambda: _reminders_now(db)),
        Tool("web_search", "Search the web (Google) for what the application cannot know: the job market in Tunisia, salaries, a technology, "
                           "news outside the journal. Never put a person's name, email or phone in it.",
             _params(query=("The search, in the words a person would type in Google", "STRING", None)), lambda query="": _web(db, query)),
        Tool("page_guide", "What a page of the application is for and every part of it: what it shows, what can be done and changed "
                           "there. For “how does this page work?”, “what can I do on the payments page?”.",
             _params(page=("The page's name or address (empty: the page open now)", "STRING", None)),
             lambda page="": _page_guide(db, user, page, path)),
        Tool("pages", "Every page of the application, its address and what it is for: to say where something is done.", None,
             lambda: _pages(db, user)),
        Tool("ask_back", "ONLY when the message is too vague to know what to look up (no subject, or several meanings): one short question "
                         "back to the admin, with 2 to 4 short options to tap. Never for a clear question.",
             ASK_BACK, lambda question="", options=None: {"asked_back": question, "options": list(options or [])[:4]}),
    ]
    return {tool.name: tool for tool in found}


# ----------------------------------------------------------------------
# For one candidate (the email assistant): their own records and what is public
# ----------------------------------------------------------------------

def _my_trainings(db: Session, candidate) -> dict:
    from app.models import ClassStatus
    from app.models.classsession import ClassSession
    from app.models.registration import RegistrationStatus
    from app.services import course_questions, schedule_utils

    rows = []
    today = _today()
    for registration in candidate.registrations:
        if registration.status == RegistrationStatus.CANCELLED:
            continue
        found = {"program": registration.program_name, "followed": registration.delivery_mode or "not chosen",
                 "total": _money(registration.total_amount or registration.total_price), "paid": _money(registration.paid_amount),
                 "still_to_pay": _money(registration.remaining_amount)}
        group = registration.class_group
        if group is None:
            found["class"] = "not placed in a class yet" + (" (placed once paid in full)" if registration.remaining_amount > 0 else "")
        else:
            facts = _class_facts(db, group)
            found.update({key: facts[key] for key in ("class", "state", "first_session", "planned_end", "week", "days_and_hours", "professor", "room")})
            upcoming = (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= today)
                        .order_by(ClassSession.session_date.asc()).first())
            found["next_session"] = (f"{_day(upcoming.session_date)} {_hour(upcoming.start_time or group.start_time) or ''}".strip()
                                     if upcoming else (_day(schedule_utils.class_start_dt(group)) if group.status == ClassStatus.NOT_STARTED else "not planned yet"))
            professor = course_questions.professor_of(db, group)
            if professor is not None and professor.email:
                found["professor_email"] = professor.email
        rows.append(found)
    return {"candidate": candidate.full_name or candidate.email, "count": len(rows), "trainings": rows}


def _my_payments(db: Session, candidate) -> dict:
    """This candidate's money: per training what it costs, what was paid and when and how, what is left, the reminder, the
    receipts they sent and what became of them, and how to pay."""
    from app.models.payment import Payment
    from app.models.payment_proof import PaymentProof
    from app.models.registration import RegistrationStatus
    from app.services.settings_service import get_settings

    rows = []
    for registration in candidate.registrations:
        if registration.status == RegistrationStatus.CANCELLED:
            continue
        paid = db.query(Payment).filter(Payment.registration_id == registration.id).order_by(Payment.payment_date.asc()).all()
        rows.append({"program": registration.program_name, "total": _money(registration.total_amount or registration.total_price),
                     "paid": _money(registration.paid_amount), "still_to_pay": _money(registration.remaining_amount),
                     "paid_in_full": registration.remaining_amount <= 0,
                     "payments": [{"on": _day(row.payment_date), "amount": _money(row.amount), "how": row.payment_method or "not written"} for row in paid],
                     "payment_reminder_sent": "yes" if registration.reminder_sent_at else "no"})
    proofs = (db.query(PaymentProof).filter(PaymentProof.candidate_id == candidate.id).order_by(PaymentProof.created_at.desc()).limit(5).all())
    said = {"reading": "being read", "waiting": "received, waiting for the team to confirm it", "confirmed": "confirmed: the payment is recorded",
            "refused": "not accepted by the team"}
    settings = get_settings(db)
    ways = ["bank transfer to the centre's account (RIB: " + " / ".join(line.strip() for line in settings.centre_ribs.splitlines() if line.strip()) + ")"
            ] if (settings.centre_ribs or "").strip() else []
    return {"candidate": candidate.full_name or candidate.email, "count": len(rows), "trainings": rows,
            "receipts_sent": [{"on": _day(row.created_at), "amount": _money(row.amount) if row.amount else "not read",
                               "state": said.get(row.status, row.status)} for row in proofs],
            "how_to_pay": ways or ["not written by the centre: the team will say"],
            "after_paying": "send a photo or PDF of the receipt by email or WhatsApp: it is read and the team confirms it; "
                            "once paid in full, a class is found or created at once and an email says which"}


def _my_progress(db: Session, candidate) -> dict:
    """Where this candidate is: their place in a class (or why they wait), the level check, the weekly questions, their
    presence, the course files of their weeks, and the personal link to each online session."""
    from app.models import ClassStatus
    from app.models.quiz import Quiz
    from app.models.registration import RegistrationStatus
    from app.models.user import Attendance
    from app.services import level_check

    trainings = []
    for registration in candidate.registrations:
        if registration.status == RegistrationStatus.CANCELLED:
            continue
        group = registration.class_group
        if group is not None:
            place = f"in the class {group.name}" + (" (finished)" if group.status == ClassStatus.COMPLETED else "")
        elif registration.remaining_amount > 0:
            place = "not in a class yet: placed at once when paid in full"
        else:
            place = "paid, waiting: they follow another training first (one at a time), or a class is being found"
        level = level_check.current(db, registration.id)
        level_state = ("not sent yet (it goes once paid in full, before the start)" if level is None
                       else "answered" + (f": level {level.assessed_level}" if getattr(level, "assessed_level", None) else "")
                       if level.completed_at else "sent, not answered yet (its link is in their email)")
        weekly = (db.query(Quiz).filter(Quiz.registration_id == registration.id, Quiz.kind.in_(("weekly", "session")), Quiz.sent_at.isnot(None)).all())
        done = [row for row in weekly if row.completed_at is not None]
        scores = [row.score for row in done if row.score is not None]
        marks = [status for (status,) in db.query(Attendance.status).filter(Attendance.registration_id == registration.id).all()]
        trainings.append({"program": registration.program_name, "place": place, "level_check": level_state,
                          "weekly_questions": f"{len(done)} answered of {len(weekly)} sent" + (f", average {round(sum(scores) / len(scores))}%" if scores else ""),
                          "presence": f"present {sum(1 for mark in marks if mark in ('present', 'late'))} of {len(marks)} sessions" if marks else "no session marked yet",
                          "session_links": "online: a personal link to each session comes by email within the hour before it"
                          if (registration.delivery_mode or "online") != "onsite" else "on site: no link needed"})
    return {"candidate": candidate.full_name or candidate.email, "count": len(trainings), "trainings": trainings}


def _program_facts(db: Session, name: str = "") -> dict:
    from app.models import Program
    from app.models.program import ProgramLevel
    from app.services import prospects

    programs = db.query(Program).order_by(Program.name).all()
    if name:
        wanted = name.lower()
        chosen = [program for program in programs if wanted in (program.name or "").lower() or (program.name or "").lower() in wanted]
        programs = chosen or programs
    rows = []
    for program in programs[:12]:
        start = prospects._next_start(db, program)
        found = {"program": program.name, "price_online": _money(program.price_online) if program.price_online else None,
                 "price_on_site": _money(program.price_onsite) if program.price_onsite else None,
                 "weeks": round(program.total_duration_days / 7) if program.total_duration_days else None,
                 "hours_per_week": program.hours_per_week, "session_time": program.class_session_time,
                 "next_class_starts": _day(start) if start else "no date fixed yet"}
        if len(programs) <= 2:
            found["about"] = (program.description or "")[:700]
            levels = db.query(ProgramLevel).filter(ProgramLevel.program_id == program.id).order_by(ProgramLevel.level_order).all()
            found["levels"] = [{"level": level.title, "weeks": [week.topic_title for week in sorted(level.weeks, key=lambda item: item.week_number)][:16]}
                               for level in levels][:4]
        rows.append(found)
    return {"count": len(rows), "programs": rows}


def _centre(db: Session) -> dict:
    from app.services import prospects, registration_form
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    return {"name": settings.platform_name or "9antra", "where": prospects._places(db) or "not written",
            "phone": settings.centre_phone or None, "whatsapp": settings.centre_whatsapp or None, "email": settings.centre_email or settings.smtp_email,
            "website": settings.centre_website or "https://9antra.tn", "facebook": "https://www.facebook.com/9antra.tn",
            "registration_form": registration_form.url(db) or None,
            "bank_transfer": "possible (RIB written by the centre)" if (settings.centre_ribs or "").strip() else "not written"}


def _rules(db: Session) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    return {"rules": [
        "A candidate is placed in a class only once they have paid in full; then a class is found or created for them at once.",
        f"One payment reminder is sent {settings.payment_reminder_hours or 24} hours after registering, only one.",
        f"A candidate follows one training at a time: a second one starts in a class that begins about {settings.next_class_gap_days or 10} days after the first one ends.",
        "Before each online session, each online candidate receives their own link to it by email; on-site candidates do not need one.",
        "The level check is sent once the training is paid in full.",
        "Each week, questions about that week's lessons are sent; the answers are graded and the corrections sent back.",
        "The files of each week's course are sent once the professor adds them.",
        "A question about the content of the course goes to the professor of the class, who answers.",
        "A refund, a cancellation or a complaint is always answered by the team, never by the assistant.",
        "To register: the registration form (its link is in the centre's facts), then pay; the payment reminder comes once, a day after registering.",
        "To pay: by the ways the centre wrote; then a photo or PDF of the receipt by email or WhatsApp, which the team confirms.",
        "Once paid in full: placed in a class at once (found or created), with an email saying which class and when it starts.",
        "Questions about the subject taught go to the professor of their class, only once the training is paid in full; the professor answers by email.",
        "At the end of the program, each candidate is asked their opinion once (stars, then a few words if they want).",
    ]}


def _taught_for(db: Session, question: str) -> dict:
    from app.services import email_assistant, knowledge_service

    hits = [hit for hit in knowledge_service.search(db, question, questions=[question], limit=4) if hit.get("quality", 0) >= email_assistant.KNOWS_FROM]
    return {"what_the_team_wrote": [{"title": hit["title"], "text": (hit["content"] or "")[:900]} for hit in hits[:3]]}


def for_candidate(db: Session, candidate) -> dict[str, Tool]:
    found = [
        Tool("my_trainings", "This candidate's own trainings: program, class, first and next session, days and hours, professor (and email), "
                             "room, online or on site, total, paid and still to pay.", None, lambda: _my_trainings(db, candidate)),
        Tool("programs", "The programs: price online and on site, weeks, hours per week, when the next class starts; for one program, "
                         "what it teaches level by level.", _params(name=("Only this program (part of its name)", "STRING", None)),
             lambda name="": _program_facts(db, name)),
        Tool("my_payments", "This candidate's money: per training the price, what was paid (when, how), what is left, the payment reminder, "
                            "the receipts they sent and what became of them, how to pay, and what happens after paying.", None,
             lambda: _my_payments(db, candidate)),
        Tool("my_progress", "Where this candidate is: in a class or why they wait, their level check, weekly questions answered and average, "
                            "their presence, and how they get the link to each online session.", None, lambda: _my_progress(db, candidate)),
        Tool("centre", "The centre: where it is, phone, WhatsApp, email, website, Facebook, the registration form, whether bank transfer is possible.",
             None, lambda: _centre(db)),
        Tool("rules", "How things work at 9antra: placement after payment, the payment reminder, one training at a time, links to online "
                      "sessions, level check, weekly questions, course files, who answers what.", None, lambda: _rules(db)),
        Tool("taught", "What the team wrote for the assistants and what 9antra.tn says, closest to the question (certificate, internship, "
                       "payment by instalments… only if written there).",
             _params(question=("The question rewritten in French (what is written there is in French and English)", "STRING", None)),
             lambda question="": _taught_for(db, question)),
    ]
    return {tool.name: tool for tool in found}
