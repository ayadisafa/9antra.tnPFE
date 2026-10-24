"""Professors are reminded to add their resources (slides, PDFs, videos, exercises) to a class week before it is taught.

Some days before a week's session (2 unless the admin changed it), when that week of the class still has no resource,
its professor gets one message (in the application, by email and on WhatsApp, as the team chat sends), with the
admin's own words: the text and the timing are changed on the Professors page, and the reminders can be switched off.
Once per class and per week, never twice.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.resource_reminder import ResourceReminder

DEFAULT_DAYS = 2
DEFAULT_TEXT = (
    "Bonjour {professor},\n\n"
    "La semaine {week} de la classe {class_name} ({program}) commence le {date} : « {topic} ».\n"
    "Pensez à y ajouter vos ressources (support, exercices, vidéo) pour vos candidats :\n{link}\n\n"
    "Merci !"
)
VARIABLES = ("professor", "class_name", "program", "week", "topic", "date", "link")


def settings_of(db: Session) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    on = getattr(settings, "resource_reminder_on", None)
    days = getattr(settings, "resource_reminder_days", None) or DEFAULT_DAYS
    return {"on": True if on is None else bool(on), "days": max(1, min(14, int(days))),
            "text": (getattr(settings, "resource_reminder_text", None) or DEFAULT_TEXT)}


def save_settings(db: Session, *, on: bool, days, text: str) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    settings.resource_reminder_on = bool(on)
    settings.resource_reminder_days = max(1, min(14, days))
    text = (text or "").strip()
    settings.resource_reminder_text = None if not text or text == DEFAULT_TEXT.strip() else text[:3000]
    db.commit()
    return settings_of(db)


def _fill(text: str, values: dict) -> str:
    for name in VARIABLES:
        text = text.replace("{" + name + "}", str(values.get(name, "")))
    return text


def due(db: Session, today: date | None = None) -> list[dict]:
    """The weeks whose session comes within the chosen days and that have no resource yet, with their professor."""
    from app.models import ClassGroup, ClassStatus, Professor
    from app.models.classsession import ClassSession
    from app.models.course_material import ClassMaterial
    from app.services import team_messages

    today = today or date.today()
    plan = settings_of(db)
    horizon = today + timedelta(days=plan["days"])
    found, seen = [], set()
    sessions = (db.query(ClassSession).filter(ClassSession.session_date >= today, ClassSession.session_date <= horizon)
                .order_by(ClassSession.session_date.asc()).all())
    sent = {(row.class_group_id, row.week_number) for row in db.query(ResourceReminder).all()}
    for session in sessions:
        key = (session.class_group_id, session.week_number)
        if key in seen or key in sent:
            continue
        seen.add(key)
        group = db.get(ClassGroup, session.class_group_id)
        if group is None or group.status == ClassStatus.COMPLETED or not group.professor_id:
            continue
        if db.query(ClassMaterial.id).filter(ClassMaterial.class_group_id == group.id, ClassMaterial.week_number == session.week_number).first():
            continue                      # the week already has its resources
        professor = db.get(Professor, group.professor_id)
        if professor is None:
            continue
        reach = (team_messages.person(db, f"professor:{professor.id}") or {}).get("ways") or []
        found.append({"group": group, "professor": professor, "week": session.week_number, "date": session.session_date,
                      "topic": session.topic or "", "reachable": bool(reach)})
    return found


def send_due(db: Session, today: date | None = None, base_url: str = "") -> int:
    """Send the reminders that are due. Returns how many went."""
    from app.services import team_messages

    plan = settings_of(db)
    if not plan["on"]:
        return 0
    count = 0
    for item in due(db, today):
        group, professor = item["group"], item["professor"]
        if not item["reachable"]:
            continue           # no account, email or phone yet: kept due, it goes once they can be reached
        text = _fill(plan["text"], {
            "professor": professor.name.split()[0] if professor.name else "", "class_name": group.name,
            "program": group.program.name if group.program else "", "week": item["week"], "topic": item["topic"] or "—",
            "date": item["date"].strftime("%d/%m/%Y"), "link": f"{base_url.rstrip('/')}/classes/{group.id}/course#week-{item['week']}",
        })
        db.add(ResourceReminder(class_group_id=group.id, week_number=item["week"], professor_id=professor.id))
        db.commit()            # kept first: a reminder is never sent twice, whatever happens next
        team_messages.send(db, f"professor:{professor.id}", text, ["app", "email", "whatsapp"], author="The assistant")
        count += 1
    return count


def recent(db: Session, limit: int = 8) -> list[dict]:
    from app.models import ClassGroup, Professor

    rows = db.query(ResourceReminder).order_by(ResourceReminder.sent_at.desc()).limit(limit).all()
    found = []
    for row in rows:
        group = db.get(ClassGroup, row.class_group_id)
        professor = db.get(Professor, row.professor_id) if row.professor_id else None
        found.append({"class": group.name if group else "?", "week": row.week_number, "professor": professor.name if professor else "?", "when": row.sent_at})
    return found
