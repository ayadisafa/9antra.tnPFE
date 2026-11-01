"""The professors' agent (orchestrator.py): a professor asks the assistant about their own classes, their candidates and
what the courses teach, and gets the guide's steps for what a professor may do.

It never reads what is not theirs: no payments, no prices owed, no other professor's classes, no candidates outside
their classes, no settings, no inbox. A question about those gets a plain “ask the admin”, never a guess. A candidate
is named with their attendance and level, never with a phone number or an email address.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from sqlalchemy.orm import Session

from app.models import Attendance, ClassGroup, ClassSession, Program, Quiz, Registration
from app.services.ui_words import t as _t, use as _use_language

_NOT_FOR_PROFESSORS = (r"pai?ement|paye|payer|prix|tarif|cout|combien (coute|doit|reste)|reste a payer|impaye|argent|recette|"
                       r"facture|rembours|cheque|virement|salaire|payment|paid|price|owe|money|revenue|invoice|refund|salary|"
                       r"flous|khlas|5las|الدفع|فلوس|السعر|خلاص|"
                       r"parametres|settings|reglages|inbox|boite de reception|whatsapp du centre|tous les candidats|all (the )?candidates|"
                       r"autres? (professeurs?|formateurs?|classes)|other (professors?|teachers?|classes)")
_CLASSES = r"mes classes|my classes|classes?\b|groupes?|groups?|9smi|a9sem|أقسام|قسمي"
_NEXT = (r"prochaine|next|demain|tomorrow|aujourd.?hui|today|quand|when|seances?|sessions?|planning|horaire|schedule|"
         r"emploi du temps|ghodwa|wa9tech|الحصة|موعد|غدا|اليوم")
_ABSENT = r"absent|absence|presence|presences|attendance|present|ghayeb|ghaybin|غياب|حضور"
_PEOPLE = r"mes candidats|my candidates|candidats?|students?|etudiants?|eleves?|apprenants?|liste|list|qui (est|sont)|who|talaba|المترشحين|الطلبة"
_LEVEL = r"niveau|level|test de niveau|resultats?|results?|score|المستوى"
_CONTENT = r"programme|program|contenu|content|semaine|week|module|chapitre|topic|sujet|cours|course|enseigne|teach|support|fichiers?|files?"


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", (text or "").lower()).encode("ascii", "ignore").decode()
    return f" {folded} {(text or '').lower()} "


def _has(text: str, pattern: str) -> bool:
    return re.search(r"(?<![a-z])(?:" + pattern + r")", text) is not None


def _language(message: str, lang: str | None) -> str:
    from app.services import howto

    return lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(message)


def _when(day: date, start) -> str:
    return day.strftime("%d/%m/%Y") + (f" {start.strftime('%H:%M')}" if start else "")


def suggestions(lang: str = "en") -> list[str]:
    if lang in ("fr", "tn"):
        return ["Quelle est ma prochaine séance ?", "Qui était absent ?", "Mes candidats", "Que vois-je cette semaine ?", "Comment ajouter un fichier au cours ?"]
    if lang == "ar":
        return ["متى حصتي القادمة؟", "من كان غائبا؟", "المترشحون في أقسامي", "ماذا ندرس هذا الأسبوع؟", "كيف أضيف ملفا للدرس؟"]
    return ["When is my next session?", "Who was absent?", "My candidates", "What do we cover this week?", "How do I add a file to the course?"]


def answer(db: Session, message: str, history: list[dict] | None = None, *, user=None, lang: str | None = None) -> dict:
    """What a professor's assistant says. Always an answer (the guide when nothing else fits)."""
    spoken = _language(message, lang)
    token = _use_language("fr" if spoken in ("fr", "tn") else spoken)
    try:
        return _answer(db, message, user, spoken)
    finally:
        from app.services.ui_words import current

        current.reset(token)


def _answer(db: Session, message: str, user, spoken: str) -> dict:
    from app.services import howto

    text = _plain(message)
    professor_id = getattr(user, "professor_id", None)
    classes = (db.query(ClassGroup).filter(ClassGroup.professor_id == professor_id).order_by(ClassGroup.name).all()
               if professor_id else [])
    mine = {group.id for group in classes}
    if _has(text, _NOT_FOR_PROFESSORS):
        refused = "That is not in your assistant: payments, the centre's figures, the settings and the other classes stay with the admin. Write to the admin from your space if you need them."
        return _said(_t(refused),
                     sources=[_t("What a professor's assistant may read")], links=[("Your space", "/teacher")])
    # A candidate of their classes, by name (another candidate is not found at all: their names are not read).
    named = _candidate_in(db, text, mine)
    if named is not None:
        return _candidate(db, named, mine)
    if not classes and (_has(text, _CLASSES) or _has(text, _NEXT) or _has(text, _ABSENT) or _has(text, _PEOPLE)):
        return _said(_t("No class is given to you yet: the admin gives you one from the Classes page, and it appears in your space."),
                     sources=[_t("Your classes")], links=[("Your space", "/teacher")])
    by_words = howto.task_for(message, user, by_meaning=False)
    if by_words is None:
        if _has(text, _ABSENT):
            return _absences(db, classes)
        if _has(text, _NEXT) and not _has(text, _CONTENT):
            return _next_sessions(db, classes)
        if _has(text, _LEVEL):
            return _levels(db, classes)
        if _has(text, _PEOPLE):
            return _people(db, classes)
        if _has(text, _CLASSES):
            return _classes(db, classes)
        if _has(text, _CONTENT):
            found = _content(db, message, classes, spoken)
            if found is not None:
                return found
    found = howto.answer(db, message, user, lang=spoken)
    found.setdefault("suggestions", suggestions(spoken)[:3])
    return found


def _said(reply: str, *, sources: list[str], links: list[tuple[str, str]] | None = None) -> dict:
    return {"reply": reply, "links": [{"label": label, "url": url} for label, url in (links or [])], "source": "system",
            "sources": [{"label": label} for label in sources], "resources": []}


def _registrations(db: Session, class_ids: set[str]) -> list[Registration]:
    if not class_ids:
        return []
    return db.query(Registration).filter(Registration.class_group_id.in_(class_ids)).all()


def _candidate_in(db: Session, text: str, class_ids: set[str]) -> Registration | None:
    best = None
    for registration in _registrations(db, class_ids):
        candidate = registration.candidate
        name = _plain(getattr(candidate, "full_name", "") or "").strip()
        parts = [part for part in name.split() if len(part) >= 3]
        if not parts:
            continue
        if f" {name} " in text or (len(parts) >= 2 and all(f" {part} " in text for part in parts[:2])):
            return registration
        if best is None and parts and f" {parts[0]} " in text and len(parts[0]) >= 4:
            best = registration
    return best


def _program_name(db: Session, group: ClassGroup) -> str:
    program = db.get(Program, group.program_id) if group.program_id else None
    return program.name if program else ""


def _classes(db: Session, classes: list[ClassGroup]) -> dict:
    lines = [_t("Your classes:")]
    for group in classes:
        count = db.query(Registration).filter(Registration.class_group_id == group.id).count()
        status = getattr(group.status, "value", group.status) or ""
        hours = f" · {group.start_time.strftime('%H:%M')}–{group.end_time.strftime('%H:%M')}" if group.start_time and group.end_time else ""
        people = _t("{0} candidates", count)
        lines.append(f"• {group.name} ({_program_name(db, group)}) · {people} · {status.replace('_', ' ').lower()}{hours}")
    return {**_said("\n".join(lines), sources=[_t("Your classes")], links=[("Your space", "/teacher")]),
            "suggestions": suggestions()[:2]}


def _next_sessions(db: Session, classes: list[ClassGroup]) -> dict:
    today = date.today()
    rows = (db.query(ClassSession).filter(ClassSession.class_group_id.in_([group.id for group in classes]),
                                          ClassSession.session_date >= today)
            .order_by(ClassSession.session_date, ClassSession.start_time).limit(5).all())
    names = {group.id: group.name for group in classes}
    if not rows:
        return _said(_t("No session is planned in your classes from today."), sources=[_t("Your classes' sessions")],
                     links=[("Your space", "/teacher")])
    lines = [_t("Your next sessions:")]
    links = []
    for row in rows:
        week = _t("week {0}", row.week_number)
        lines.append(f"• {_when(row.session_date, row.start_time)} · {names.get(row.class_group_id, '')} · {week}: {row.topic}")
        if row.meeting_url and not links:
            links.append(("Open the online class", row.meeting_url))
    links.append(("The course of the class", f"/classes/{rows[0].class_group_id}/course"))
    return _said("\n".join(lines), sources=[_t("Your classes' sessions")], links=links)


def _absences(db: Session, classes: list[ClassGroup]) -> dict:
    today = date.today()
    lines = []
    for group in classes:
        last = (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date <= today)
                .order_by(ClassSession.session_date.desc()).first())
        if last is None:
            continue
        missing = (db.query(Attendance).filter(Attendance.session_id == last.id, Attendance.status == "absent").all())
        names = [_name_of(db, row) for row in missing]
        if missing:
            lines.append(_t("{0}, session of {1}: absent: {2}.", group.name, last.session_date.strftime("%d/%m"), ", ".join(names)))
        else:
            marked = db.query(Attendance).filter(Attendance.session_id == last.id).count()
            lines.append(_t("{0}, session of {1}: nobody was absent.", group.name, last.session_date.strftime("%d/%m")) if marked else
                         _t("{0}, session of {1}: the attendance is not marked yet.", group.name, last.session_date.strftime("%d/%m")))
    if not lines:
        return _said(_t("No session of your classes has taken place yet."), sources=[_t("The attendance of your classes")])
    return _said("\n".join(lines), sources=[_t("The attendance of your classes")],
                 links=[("Mark the attendance", f"/classes/{classes[0].id}/attendance")])


def _name_of(db: Session, attendance) -> str:
    from app.models import Candidate

    candidate = db.get(Candidate, attendance.candidate_id)
    return candidate.full_name if candidate else "?"


def _people(db: Session, classes: list[ClassGroup]) -> dict:
    lines = []
    for group in classes:
        rows = db.query(Registration).filter(Registration.class_group_id == group.id).all()
        names = sorted(filter(None, (getattr(row.candidate, "full_name", None) for row in rows)))
        lines.append(f"{group.name} ({len(names)}): " + (", ".join(names) if names else "—"))
    return _said("\n".join(lines), sources=[_t("Your classes")], links=[("Mark the attendance", f"/classes/{classes[0].id}/attendance")])


def _levels(db: Session, classes: list[ClassGroup]) -> dict:
    lines = []
    for group in classes:
        for registration in db.query(Registration).filter(Registration.class_group_id == group.id).all():
            quiz = (db.query(Quiz).filter(Quiz.registration_id == registration.id, Quiz.kind == "level")
                    .order_by(Quiz.prepared_at.desc()).first())
            level = getattr(quiz, "assessed_level", None)
            if level:
                lines.append(f"• {registration.candidate.full_name} ({group.name}): {level}")
    if not lines:
        return _said(_t("No level check of your candidates has an answer yet."), sources=[_t("The level checks of your candidates")])
    return _said(_t("The level of your candidates, from their level check:") + "\n" + "\n".join(lines[:30]),
                 sources=[_t("The level checks of your candidates")])


def _candidate(db: Session, registration: Registration, class_ids: set[str]) -> dict:
    candidate = registration.candidate
    group = db.get(ClassGroup, registration.class_group_id)
    rows = db.query(Attendance).filter(Attendance.registration_id == registration.id).all()
    absent = sum(1 for row in rows if row.status == "absent")
    late = sum(1 for row in rows if row.status == "late")
    active = [row.participation for row in rows if row.participation]
    quiz = (db.query(Quiz).filter(Quiz.registration_id == registration.id, Quiz.kind == "level")
            .order_by(Quiz.prepared_at.desc()).first())
    lines = [f"{candidate.full_name} · {group.name if group else ''}",
             _t("Sessions marked: {0}; absent {1}, late {2}.", len(rows), absent, late)]
    if active:
        average = sum(active) / len(active)
        lines.append(_t("Takes part: {0}.", _t("very active") if average >= 2.5 else _t("takes part") if average >= 1.5 else _t("quiet")))
    if quiz is not None and getattr(quiz, "assessed_level", None):
        lines.append(_t("Level check: {0}.", quiz.assessed_level))
    lines.append(_t("Their contact details and payments stay with the admin."))
    return _said("\n".join(lines), sources=[_t("The attendance of your classes"), _t("The level checks of your candidates")],
                 links=[("Mark the attendance", f"/classes/{registration.class_group_id}/attendance")])


def _content(db: Session, message: str, classes: list[ClassGroup], spoken: str) -> dict | None:
    """What the program of one of their classes teaches (another program is answered by the guide, like for everyone)."""
    from app.services import chatbot_sources
    from app.services.chatbot import _plain as chatbot_plain, _program_in

    fr = spoken in ("fr", "tn")
    programs = [db.get(Program, group.program_id) for group in classes if group.program_id]
    program = _program_in(db, chatbot_plain(message)) or (programs[0] if len({p.id for p in programs if p}) == 1 else None)
    found = chatbot_sources.program_content(db, message, program, fr) if program is not None else None
    if found is None and program is not None:
        group = next((group for group in classes if group.program_id == program.id), None)
        week = getattr(group, "current_week", 0) or 1
        weeks = [item for level in program.levels for item in level.weeks]
        if weeks:
            now = weeks[min(week, len(weeks)) - 1]
            found = {"reply": _t("{0}, week {1}: {2}.", program.name, week, now.topic_title) + "\n"
                     + "\n".join(_t("week {0}", index) + f": {item.topic_title}" for index, item in enumerate(weeks[:12], start=1)),
                     "links": [], "source": "knowledge", "sources": [{"label": _t("The program in the application: {0}", program.name)}]}
    if found is not None:
        found.setdefault("resources", [])
        if classes:
            found.setdefault("links", []).append({"label": "The course of the class", "url": f"/classes/{classes[0].id}/course"})
    return found
