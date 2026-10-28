"""What is still missing in the application, and where to complete it: programs, classes, professors, candidates and
the connections. Counted from the records and the settings, never guessed. Shown on the System page, said by Yasmine,
and given by the guide and the assistant when asked (“what is missing?”, “chnowa na9es?”, “qu'est-ce qui manque ?”)."""
from __future__ import annotations

import os
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

_ASKS = re.compile(r"\b(missing|manque\w*|manquant\w*|to update|a mettre a jour|incomplet\w*|incomplete|what (is|are) (still )?(left|missing)|"
                   r"what (should|must) i (update|complete|fill)|que (dois|faut) (je|il) (completer|remplir|mettre a jour)|to complete|a completer)\b"
                   r"|ناقص|ينقص|نقص")
_NAMES = 4


def _n(count: int, one: str, many: str) -> str:
    """The sentence for one, or for several, in the page's language."""
    return _t(one if count == 1 else many, count)


def asks(text: str) -> bool:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[^a-z0-9 ]+", " ", plain)
    return bool(_ASKS.search(plain) or _ASKS.search(text or ""))


def _item(area: str, icon: str, text: str, url: str, names: list[str] | None = None, count: int = 1) -> dict:
    return {"area": area, "icon": icon, "text": text, "url": url, "names": (names or [])[:_NAMES], "count": count}


def items(db: Session) -> list[dict]:
    """Everything still missing, the most useful first."""
    from app.models import ClassGroup, ClassStatus, Professor, Program, Registration, User
    from app.models.classsession import ClassSession
    from app.models.course_material import ClassMaterial
    from app.models.registration import PaymentStatus, RegistrationStatus
    from app.services import enrollment_service

    found: list[dict] = []
    programs = db.query(Program).order_by(Program.name).all()
    professors = db.query(Professor).all()
    real = [professor for professor in professors if not (professor.name or "").startswith("À nommer")]

    # Programs
    waiting = [program.name for program in programs if not program.is_approved]
    if waiting:
        found.append(_item("programs", "bi-hourglass-split", _n(len(waiting), "{0} program waits for your approval", "{0} programs wait for your approval"), "/programs/all", waiting, len(waiting)))
    approved = [program for program in programs if program.is_approved]
    no_plan = [program.name for program in approved if not any(level.weeks for level in program.levels)]
    if no_plan:
        found.append(_item("programs", "bi-calendar-week", _n(len(no_plan), "{0} program has no weekly plan", "{0} programs have no weekly plan"), "/programs/all", no_plan, len(no_plan)))
    no_text = [program.name for program in approved if not (program.description or "").strip()]
    if no_text:
        found.append(_item("programs", "bi-card-text", _n(len(no_text), "{0} program has no description", "{0} programs have no description"), "/programs/all", no_text, len(no_text)))
    no_price = [program.name for program in approved if not (program.price_online or program.price_onsite)]
    if no_price:
        found.append(_item("programs", "bi-cash-coin", _n(len(no_price), "{0} program has no price", "{0} programs have no price"), "/programs/all", no_price, len(no_price)))
    nobody = [program.name for program in approved if not any(enrollment_service.teaches(professor, program) for professor in real)]
    if nobody:
        found.append(_item("programs", "bi-person-exclamation", _n(len(nobody), "{0} program has no teacher yet", "{0} programs have no teacher yet"),
                           "/classes/?tab=professors#coverage", nobody, len(nobody)))

    # Classes that are not finished
    classes = db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).all()
    no_professor = [group.name for group in classes if not group.professor_id]
    if no_professor:
        found.append(_item("classes", "bi-person-x", _n(len(no_professor), "{0} class has no professor", "{0} classes have no professor"), "/classes/", no_professor, len(no_professor)))
    no_time = [group.name for group in classes if not group.start_time]
    if no_time:
        found.append(_item("classes", "bi-clock", _n(len(no_time), "{0} class has no session time", "{0} classes have no session time"), "/classes/", no_time, len(no_time)))
    soon, today = [], date.today()
    for session in (db.query(ClassSession).filter(ClassSession.session_date >= today, ClassSession.session_date <= today + timedelta(days=7)).all()):
        group = db.get(ClassGroup, session.class_group_id)
        if group is None or group.status == ClassStatus.COMPLETED:
            continue
        if not db.query(ClassMaterial.id).filter(ClassMaterial.class_group_id == group.id, ClassMaterial.week_number == session.week_number).first():
            label = f"{group.name} · {session.week_number}"
            if label not in soon:
                soon.append(label)
    if soon:
        found.append(_item("classes", "bi-folder2-open", _n(len(soon), "{0} week of the next seven days has no resources yet", "{0} weeks of the next seven days have no resources yet"),
                           "/classes/?tab=professors#resources", soon, len(soon)))

    # Professors
    to_name = [professor.name for professor in professors if (professor.name or "").startswith("À nommer")]
    if to_name:
        found.append(_item("professors", "bi-question-circle", _n(len(to_name), "{0} place of a professor still to name", "{0} places of professors still to name"),
                           "/classes/?tab=professors#coverage", to_name, len(to_name)))
    no_email = [professor.name for professor in real if not (professor.email or "").strip()]
    if no_email:
        found.append(_item("professors", "bi-envelope-x", _n(len(no_email), "{0} professor has no email", "{0} professors have no email"), "/classes/?tab=professors", no_email, len(no_email)))
    no_phone = [professor.name for professor in real if not (professor.phone or "").strip()]
    if no_phone:
        found.append(_item("professors", "bi-telephone-x", _n(len(no_phone), "{0} professor has no phone", "{0} professors have no phone"), "/classes/?tab=professors", no_phone, len(no_phone)))
    with_access = {row[0] for row in db.query(User.professor_id).filter(User.professor_id.isnot(None), User.is_active.is_(True)).all()}
    no_access = [professor.name for professor in real if professor.id not in with_access]
    if no_access:
        found.append(_item("professors", "bi-key", _n(len(no_access), "{0} professor cannot sign in yet", "{0} professors cannot sign in yet"), "/classes/?tab=professors", no_access, len(no_access)))

    # Candidates
    placed = (db.query(Registration).filter(Registration.payment_status == PaymentStatus.PAID, Registration.class_group_id.is_(None),
                                            Registration.status != RegistrationStatus.CANCELLED).all())
    if placed:
        names = [item.candidate.full_name for item in placed if item.candidate]
        found.append(_item("candidates", "bi-person-check", _n(len(placed), "{0} paid candidate waits for a class", "{0} paid candidates wait for a class"), "/classes/?tab=decisions", names, len(placed)))
    from app.models import Candidate

    no_number = [candidate.full_name for candidate in db.query(Candidate).filter((Candidate.phone.is_(None)) | (Candidate.phone == "")).all()]
    if no_number:
        found.append(_item("candidates", "bi-phone", _n(len(no_number), "{0} candidate has no phone number (no WhatsApp, no SMS)", "{0} candidates have no phone number (no WhatsApp, no SMS)"),
                           "/candidate/all", no_number, len(no_number)))

    # Connections and settings
    from app.services import email_threads, mailbox_service, sms, whatsapp
    from app.services.settings_service import get_settings, public_base_url

    settings = get_settings(db)
    if not email_threads.smtp_configured():
        found.append(_item("settings", "bi-envelope-slash", _t("No email can leave: the sending address is not set"), "/settings#email"))
    elif not mailbox_service.configured():
        found.append(_item("settings", "bi-inbox", _t("Candidates' emails are not read: the mailbox is not connected"), "/settings#email"))
    if not whatsapp.configured():
        found.append(_item("settings", "bi-whatsapp", _t("No WhatsApp Business number: WhatsApp messages wait for your phone"), "/settings#whatsapp"))
    if not sms.configured():
        found.append(_item("settings", "bi-chat-dots", _t("No Android phone for SMS: SMS wait for your phone"), "/settings#sms"))
    if not (getattr(settings, "centre_ribs", None) or "").strip():
        found.append(_item("settings", "bi-bank", _t("The centre's RIB is not written: payment proofs cannot be checked against it"), "/settings#centre"))
    if not (getattr(settings, "centre_phone", None) or "").strip():
        found.append(_item("settings", "bi-telephone", _t("The centre's phone is not written under the emails"), "/settings#centre"))
    if re.search(r"//(127\.0\.0\.1|localhost)(:|/|$)", public_base_url(db)):
        found.append(_item("settings", "bi-link-45deg", _t("Links in emails only open on this computer: set the address for candidates"), "/settings#public"))
    if len((os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()) <= 40:
        found.append(_item("settings", "bi-facebook", _t("The Facebook page's posts and reviews are not read: its access token is missing"), "/website"))
    return found


AREAS = {"programs": ("Programs", "bi-mortarboard"), "classes": ("Classes", "bi-easel2"), "professors": ("Professors", "bi-person-badge"),
         "candidates": ("Candidates", "bi-people"), "settings": ("Connections and settings", "bi-gear")}


def grouped(db: Session) -> list[dict]:
    """The same, by part of the application, for the System page."""
    found = items(db)
    return [{"key": key, "label": _t(label), "icon": icon, "items": [item for item in found if item["area"] == key]}
            for key, (label, icon) in AREAS.items() if any(item["area"] == key for item in found)]


def for_professor(db: Session, user) -> list[dict]:
    """What a professor still has to add: the resources of their coming weeks, and their own numbers."""
    from app.models import ClassGroup, ClassStatus, Professor
    from app.models.classsession import ClassSession
    from app.models.course_material import ClassMaterial

    found = []
    professor = db.get(Professor, getattr(user, "professor_id", None)) if getattr(user, "professor_id", None) else None
    if professor is None:
        return found
    today, weeks = date.today(), []
    for group in db.query(ClassGroup).filter(ClassGroup.professor_id == professor.id, ClassGroup.status != ClassStatus.COMPLETED).all():
        for session in db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= today,
                                                      ClassSession.session_date <= today + timedelta(days=7)).all():
            if not db.query(ClassMaterial.id).filter(ClassMaterial.class_group_id == group.id, ClassMaterial.week_number == session.week_number).first():
                weeks.append(f"{group.name} · {session.week_number}")
    if weeks:
        found.append(_item("classes", "bi-folder2-open", _n(len(weeks), "{0} week of the next seven days has no resources yet", "{0} weeks of the next seven days have no resources yet"), "/teacher", weeks, len(weeks)))
    if not (professor.phone or "").strip():
        found.append(_item("professors", "bi-telephone-x", _t("Your phone number is missing"), "/teacher/profile"))
    return found


from app.services.howto_tn import MISSING_HEAD as _HEAD      # in each language


def answer(db: Session, user, lang: str) -> dict:
    """The guide's and the assistant's answer to “what is missing?”: a checklist with where to complete each line."""
    from app.services import ui_words

    token = ui_words.use("fr" if lang == "tn" else lang)          # the lines in the language asked (Tunisian reads them in French)
    try:
        professor = bool(getattr(user, "is_professor", False))
        found = for_professor(db, user) if professor else items(db)
        texts = [item["text"] + (f" ({', '.join(item['names'])}{'…' if item['count'] > len(item['names']) else ''})" if item["names"] else "")
                 for item in found]
    finally:
        ui_words.current.reset(token)
    head, empty, more = _HEAD.get(lang, _HEAD["en"])
    page = "/teacher" if professor else "/system#missing"
    if not found:
        return {"reply": empty, "links": [], "source": "system", "lang": lang, "sources": [{"label": "The application's records and settings"}]}
    title = head.format(len(found))
    return {"reply": "\n".join([title + ":"] + [f"- {text}" for text in texts]), "source": "system", "lang": lang,
            "links": [{"label": more, "url": page}],
            "schema": {"kind": "checklist", "title": title, "url": page, "label": more,
                       "steps": [{"text": text, "url": item["url"]} for text, item in zip(texts, found)]},
            "sources": [{"label": "The application's records and settings", "url": page}]}
