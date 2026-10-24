"""Yasmine, the animated friend in the corner of every page: what she says.

She greets the person by name, tells the admin what is waiting today (emails,
WhatsApp messages to send, what needs a decision, today's sessions, reminders
planned, money still due, new registrations), explains the page that is open,
and answers questions through the assistant (see api/assistant.py).

Everything she says is counted from the records, like the dashboard; nothing is
invented. A team member or a professor only hears the greeting and the page's
explanation: the counts of the whole centre are the admin's.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

NAME = "Yasmine"

_WORDS = {
    "fr": {
        "morning": "Bonjour", "afternoon": "Bonjour", "evening": "Bonsoir",
        "intro": "Je suis {name}, votre amie de 9antra. Chaque jour je vous dis ce qui vous attend, je vous guide sur chaque page, "
                 "et vous pouvez me poser vos questions.",
        "emails": "{n} email{s} attend{ent} votre réponse.",
        "whatsapp": "{n} message{s} WhatsApp attend{ent} d'être envoyé{s}.",
        "team": "{n} message{s} de l'équipe attend{ent} votre lecture.",
        "proofs": "{n} preuve{s} de paiement à vérifier.",
        "mine": "{n} nouveau{x} message{s} du centre pour vous.",
        "todo": "{n} chose{s} importante{s} aujourd'hui. La première : {first}",
        "session": "Aujourd'hui, séance de {name}{at}.",
        "reminder": "Votre rappel « {subject} » part aujourd'hui{at}.",
        "owing": "{n} candidat{s} doi{vent} encore {amount} au total.",
        "levels": "{n} test{s} de niveau attend{ent} une réponse.",
        "new": "{n} nouvelle{s} inscription{s} cette semaine. Bravo !",
        "missing": "Il reste {n} chose{s} à compléter (formations, classes, formateurs, réglages) : je vous les montre.",
        "fine": "Tout est en ordre pour aujourd'hui. Profitez-en !",
        "page": "Sur cette page : {title}. {text}",
        "open": "Ouvrir", "next": "Suivant", "ask": "Posez-moi une question…", "voice_on": "Voix activée", "voice_off": "Voix coupée",
        "news": "Nouvelle notification : {title}",
        "thinking": "Je cherche…", "sorry": "Je n'ai pas pu répondre. Réessayez dans un instant.",
        "hello_again": "Je suis là si vous avez besoin de moi.",
        "cheers": ["Une pause et un verre d'eau, ça aide aussi à bien travailler.",
                   "Chaque candidat que vous aidez aujourd'hui, c'est un métier de demain.",
                   "Petit à petit, tout avance. On continue ensemble ?"],
    },
    "ar": {
        "morning": "صباح الخير", "afternoon": "مساء الخير", "evening": "مساء الخير",
        "intro": "أنا {name}، صديقتك في 9antra. كل يوم أخبرك بما ينتظرك، وأرشدك في كل صفحة، ويمكنك أن تسألني ما تشاء.",
        "emails": "رسائل تنتظر إجابتك: {n}.",
        "whatsapp": "رسائل WhatsApp تنتظر الإرسال: {n}.",
        "team": "رسائل من الفريق تنتظر قراءتك: {n}.",
        "proofs": "إثباتات دفع للمراجعة: {n}.",
        "mine": "رسائل جديدة من المركز لك: {n}.",
        "todo": "أمور مهمة اليوم: {n}. أولها: {first}",
        "session": "اليوم، حصة {name}{at}.",
        "reminder": "تذكيرك «{subject}» يُرسل اليوم{at}.",
        "owing": "مترشحون ما زال عليهم {amount} في المجموع: {n}.",
        "levels": "اختبارات مستوى تنتظر إجابة: {n}.",
        "new": "تسجيلات جديدة هذا الأسبوع: {n}. أحسنت!",
        "missing": "أمور ما زالت ناقصة (البرامج، الأقسام، المكوّنون، الإعدادات): {n}. سأريك إياها.",
        "fine": "كل شيء على ما يرام اليوم. استمتع!",
        "page": "في هذه الصفحة: {title}. {text}",
        "open": "فتح", "next": "التالي", "ask": "اطرح عليّ سؤالا…", "voice_on": "الصوت مفعّل", "voice_off": "الصوت معطّل",
        "news": "إشعار جديد: {title}",
        "thinking": "دعني أبحث…", "sorry": "لم أتمكن من الإجابة. أعد المحاولة بعد لحظة.",
        "hello_again": "أنا هنا إن احتجتني.",
        "cheers": ["استراحة وكأس ماء يساعدانك أيضا على العمل جيدا.",
                   "كل مترشح تساعده اليوم هو وظيفة الغد.",
                   "شيئا فشيئا يتقدّم كل شيء. هل نواصل معا؟"],
    },
    "en": {
        "morning": "Good morning", "afternoon": "Good afternoon", "evening": "Good evening",
        "intro": "I'm {name}, your friend at 9antra. Every day I tell you what is waiting, I guide you on each page, "
                 "and you can ask me anything.",
        "emails": "{n} email{s} wait{s_verb} for your answer.",
        "whatsapp": "{n} WhatsApp message{s} wait{s_verb} to be sent.",
        "team": "{n} message{s} from the team wait{s_verb} for you.",
        "proofs": "{n} payment proof{s} to check.",
        "mine": "{n} new message{s} from the centre for you.",
        "todo": "{n} important thing{s} today. The first one: {first}",
        "session": "Today, a session of {name}{at}.",
        "reminder": "Your reminder “{subject}” goes out today{at}.",
        "owing": "{n} candidate{s} still owe{s_verb2} {amount} in all.",
        "levels": "{n} level check{s} wait{s_verb} for an answer.",
        "new": "{n} new registration{s} this week. Well done!",
        "missing": "{n} thing{s} still missing (programs, classes, professors, settings): I'll show you.",
        "fine": "Everything is in order for today. Enjoy it!",
        "page": "On this page: {title}. {text}",
        "open": "Open", "next": "Next", "ask": "Ask me a question…", "voice_on": "Voice on", "voice_off": "Voice off",
        "news": "New notification: {title}",
        "thinking": "Let me look…", "sorry": "I could not answer. Try again in a moment.",
        "hello_again": "I'm here if you need me.",
        "cheers": ["A break and a glass of water help you work well too.",
                   "Every candidate you help today is a job of tomorrow.",
                   "Little by little, everything moves forward. Shall we go on together?"],
    },
}


from app.services.howto_tn import FRIEND_WORDS as _TUNISIAN       # her words in Tunisian

_WORDS["tn"] = _TUNISIAN
LANGUAGES = ("fr", "en", "ar", "tn")


def language_of(ui_lang: str | None) -> str:
    return ui_lang if ui_lang in ("fr", "ar") else "en"


def _at(words: dict, when) -> str:
    """“ à 9:00”, “ على الساعة 9:00”, “ at 9:00”."""
    if not when:
        return ""
    return {id(_WORDS["fr"]): f" à {when:%H:%M}", id(_WORDS["ar"]): f" على الساعة {when:%H:%M}",
            id(_WORDS["tn"]): f" 3la {when:%H:%M}"}.get(id(words), f" at {when:%H:%M}")


def _plural(words: dict, key: str, n: int, **values) -> str:
    many = n != 1
    return words[key].format(n=n, s="s" if many else "", ent="ent" if many else "", vent="vent" if many else "t", x="x" if many else "",
                             s_verb="" if many else "s", s_verb2="" if many else "s", **values)


def _money(value: float) -> str:
    return f"{value:,.0f} DT".replace(",", " ")


def _in_language(db: Session, lang: str, text: str) -> str:
    """The page explanations are written in English; their French comes from the interface's translations."""
    if lang == "en" or not text:
        return text
    from app.models.ui_translation import UiTranslation
    row = db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.source == text).first()
    return row.text if row and row.text else text


def _videos_of(db: Session, program) -> int:
    from app.models.website import WebsiteItem

    found = 0
    for item in db.query(WebsiteItem).filter(WebsiteItem.kind == "formation", WebsiteItem.program_id == program.id,
                                             WebsiteItem.gone_at.is_(None)).all():
        found += sum(1 for week in (item.details or {}).get("weeks") or [] if isinstance(week, dict) and week.get("video_url"))
    return found


def place_facts(db: Session, path: str, lang: str) -> str | None:
    """On a program's or a class's page: what there is to know about it, counted from the records."""
    import re

    from app.models import ClassGroup, ClassStatus, Program, Registration
    from app.models.registration import PaymentStatus, RegistrationStatus

    fr = lang == "fr"
    if lang == "ar":
        return _place_facts_ar(db, path)
    found = re.match(r"^/programs/([0-9a-f-]{36})/?$", path or "")
    if found:
        program = db.get(Program, found.group(1))
        if program is None:
            return None
        weeks = sum(len(level.weeks) for level in program.levels)
        registrations = [item for item in db.query(Registration).filter(Registration.program_id == program.id).all()
                         if item.status != RegistrationStatus.CANCELLED]
        paid = sum(1 for item in registrations if item.payment_status == PaymentStatus.PAID)
        classes = db.query(ClassGroup).filter(ClassGroup.program_id == program.id).all()
        running = sum(1 for item in classes if item.status == ClassStatus.ONGOING)
        coming = sorted((item for item in classes if item.status == ClassStatus.NOT_STARTED and item.start_date), key=lambda item: item.start_date)
        videos = _videos_of(db, program)
        parts = [f"« {program.name} »" if fr else f"“{program.name}”"]
        if weeks:
            parts.append(f"{weeks} semaines" if fr else f"{weeks} weeks")
        prices = [value for value in (program.price_online, program.price_onsite) if value]
        if prices:
            parts.append((f"{_money(program.price_online or 0)} en ligne, {_money(program.price_onsite or 0)} en présentiel" if fr
                          else f"{_money(program.price_online or 0)} online, {_money(program.price_onsite or 0)} on site"))
        parts.append((f"{len(registrations)} inscrit{'s' if len(registrations) != 1 else ''} dont {paid} payé{'s' if paid != 1 else ''}" if fr
                      else f"{len(registrations)} registered, {paid} paid"))
        if running:
            parts.append(f"{running} classe{'s' if running != 1 else ''} en cours" if fr else f"{running} class{'es' if running != 1 else ''} running")
        if coming:
            parts.append((f"prochaine classe le {coming[0].start_date:%d/%m}" if fr else f"next class on {coming[0].start_date:%d %b}"))
        if videos:
            parts.append(f"{videos} vidéo{'s' if videos != 1 else ''} à regarder ici" if fr else f"{videos} video{'s' if videos != 1 else ''} to watch here")
        return (parts[0] + " : " + ", ".join(parts[1:]) + ".") if fr else (parts[0] + ": " + ", ".join(parts[1:]) + ".")
    found = re.match(r"^/classes/([0-9a-f-]{36})/?$", path or "")
    if found:
        group = db.get(ClassGroup, found.group(1))
        if group is None:
            return None
        members = db.query(Registration).filter(Registration.class_group_id == group.id, Registration.status != RegistrationStatus.CANCELLED).count()
        state = {ClassStatus.ONGOING: ("en cours", "running"), ClassStatus.NOT_STARTED: ("pas encore commencée", "not started yet"),
                 ClassStatus.COMPLETED: ("terminée", "finished")}.get(group.status, ("", ""))[0 if fr else 1]
        bits = [f"{group.name}", state, f"{members} candidat{'s' if members != 1 else ''}" if fr else f"{members} candidate{'s' if members != 1 else ''}"]
        if group.professor_name:
            bits.append(("avec " if fr else "with ") + group.professor_name)
        if group.start_date and group.status == ClassStatus.NOT_STARTED:
            bits.append((f"début le {group.start_date:%d/%m}" if fr else f"starts on {group.start_date:%d %b}"))
        return bits[0] + (" : " if fr else ": ") + ", ".join(bit for bit in bits[1:] if bit) + "."
    return None


def _place_facts_ar(db: Session, path: str) -> str | None:
    """The same facts, in Arabic."""
    import re

    from app.models import ClassGroup, ClassStatus, Program, Registration
    from app.models.registration import PaymentStatus, RegistrationStatus

    found = re.match(r"^/programs/([0-9a-f-]{36})/?$", path or "")
    if found:
        program = db.get(Program, found.group(1))
        if program is None:
            return None
        weeks = sum(len(level.weeks) for level in program.levels)
        registrations = [item for item in db.query(Registration).filter(Registration.program_id == program.id).all()
                         if item.status != RegistrationStatus.CANCELLED]
        paid = sum(1 for item in registrations if item.payment_status == PaymentStatus.PAID)
        classes = db.query(ClassGroup).filter(ClassGroup.program_id == program.id).all()
        running = sum(1 for item in classes if item.status == ClassStatus.ONGOING)
        coming = sorted((item for item in classes if item.status == ClassStatus.NOT_STARTED and item.start_date), key=lambda item: item.start_date)
        videos = _videos_of(db, program)
        parts = []
        if weeks:
            parts.append(f"{weeks} أسابيع")
        if program.price_online or program.price_onsite:
            parts.append(f"{_money(program.price_online or 0)} عن بعد، {_money(program.price_onsite or 0)} حضوري")
        parts.append(f"{len(registrations)} مسجّلون منهم {paid} دفعوا")
        if running:
            parts.append(f"أقسام جارية: {running}")
        if coming:
            parts.append(f"القسم القادم في {coming[0].start_date:%d/%m}")
        if videos:
            parts.append(f"فيديوهات للمشاهدة هنا: {videos}")
        return f"«{program.name}»: " + "، ".join(parts) + "."
    found = re.match(r"^/classes/([0-9a-f-]{36})/?$", path or "")
    if found:
        group = db.get(ClassGroup, found.group(1))
        if group is None:
            return None
        members = db.query(Registration).filter(Registration.class_group_id == group.id, Registration.status != RegistrationStatus.CANCELLED).count()
        state = {ClassStatus.ONGOING: "جارٍ", ClassStatus.NOT_STARTED: "لم يبدأ بعد", ClassStatus.COMPLETED: "مكتمل"}.get(group.status, "")
        bits = [state, f"{members} مترشحين"]
        if group.professor_name:
            bits.append("مع " + group.professor_name)
        if group.start_date and group.status == ClassStatus.NOT_STARTED:
            bits.append(f"يبدأ في {group.start_date:%d/%m}")
        return f"{group.name}: " + "، ".join(bit for bit in bits if bit) + "."
    return None


def lines_for_admin(db: Session, words: dict, now_utc: datetime, now_local: datetime) -> list[dict]:
    """What is waiting today, the most pressing first: [{"text", "url", "icon", "tone"}]."""
    from app.models.admin_reminder import AdminReminder
    from app.models.candidate_email import CandidateEmail
    from app.services import advisor, level_check, overview
    from app.services.schedule_utils import utc_to_local

    found = []
    figures = overview.figures(db, now_utc)
    if figures["emails_waiting"]:
        found.append({"text": _plural(words, "emails", figures["emails_waiting"]), "url": "/inbox?filter=todo", "icon": "bi-envelope-exclamation", "tone": "bad"})
    from app.services import team_messages

    from app.models.payment_proof import PaymentProof

    proofs = db.query(PaymentProof).filter(PaymentProof.status == "waiting").count()
    if proofs:
        found.append({"text": _plural(words, "proofs", proofs), "url": "/payments#proofs", "icon": "bi-receipt", "tone": "warn"})
    team = team_messages.unread_count(db)
    if team:
        found.append({"text": _plural(words, "team", team), "url": "/inbox/team", "icon": "bi-person-workspace", "tone": "warn"})
    to_send = db.query(CandidateEmail).filter(CandidateEmail.channel.in_(("whatsapp", "sms")), CandidateEmail.status == "to_send").count()
    if to_send:
        found.append({"text": _plural(words, "whatsapp", to_send), "url": "/inbox?filter=to_send", "icon": "bi-whatsapp", "tone": "warn"})
    pressing = [item for item in advisor.for_dashboard(db) if item.tone in ("urgent", "important")]
    if pressing:
        found.append({"text": _plural(words, "todo", len(pressing), first=pressing[0].title), "url": "/", "icon": "bi-compass", "tone": "warn"})
    for session in overview.coming_sessions(db, now_local, days=0, limit=3):
        at = _at(words, session["start_time"])
        found.append({"text": words["session"].format(name=session["class"].name, at=at), "url": f"/classes/{session['class'].id}",
                      "icon": "bi-calendar-event", "tone": "info"})
    end_of_day = now_utc + timedelta(hours=24 - now_local.hour)
    for reminder in (db.query(AdminReminder).filter(AdminReminder.status == "scheduled", AdminReminder.send_at <= end_of_day)
                     .order_by(AdminReminder.send_at.asc()).limit(2).all()):
        when = utc_to_local(reminder.send_at)
        at = _at(words, when)
        found.append({"text": words["reminder"].format(subject=reminder.subject, at=at), "url": "/reminders", "icon": "bi-alarm", "tone": "info"})
    if figures["owing_candidates"]:
        found.append({"text": _plural(words, "owing", figures["owing_candidates"], amount=_money(figures["due"])), "url": "/payments",
                      "icon": "bi-cash-coin", "tone": "info"})
    waiting_levels = level_check.overview(db)["counts"].get("sent", 0)
    if waiting_levels:
        found.append({"text": _plural(words, "levels", waiting_levels), "url": "/quiz/all", "icon": "bi-signpost-split", "tone": "info"})
    if figures["new_candidates"]:
        found.append({"text": _plural(words, "new", figures["new_candidates"]), "url": "/candidate/all", "icon": "bi-stars", "tone": "ok"})
    try:
        from app.services import completeness

        missing = len(completeness.items(db))
    except Exception:
        missing = 0
    if missing:
        found.append({"text": _plural(words, "missing", missing), "url": "/system#missing", "icon": "bi-list-check", "tone": "info"})
    if not found:
        found.append({"text": words["fine"], "url": None, "icon": "bi-emoji-smile", "tone": "ok"})
    return found


def talk(db: Session, user, path: str, ui_lang: str | None, guide_lang: str | None = None) -> dict:
    """Everything she can say on this page, for this person, now: in the language chosen in her window (or the
    pages' language). In Tunisian, what comes from the pages' translations is said in French."""
    from app.services import guides
    from app.services.schedule_utils import local_now

    lang = guide_lang if guide_lang in LANGUAGES else language_of(ui_lang)
    words = _WORDS[lang]
    written = "fr" if lang == "tn" else lang
    now_utc, now_local = datetime.utcnow(), local_now()
    part = "morning" if now_local.hour < 12 else "afternoon" if now_local.hour < 18 else "evening"
    first_name = ((getattr(user, "name", "") or "").split() or [""])[0].capitalize()
    is_admin = bool(getattr(user, "is_admin", False))
    lines = lines_for_admin(db, words, now_utc, now_local) if is_admin else []
    if not is_admin and getattr(user, "id", None):
        from app.services import team_messages

        mine = team_messages.member_unread(db, user)
        if mine:
            lines.append({"text": _plural(words, "mine", mine), "url": "/account/messages", "icon": "bi-chat-heart", "tone": "warn"})
    guide = guides.for_path(path or "/")
    tip = None
    facts = place_facts(db, path, written) if is_admin else None
    if facts:
        tip = {"text": facts, "key": "place:" + (path or "/")}
    elif guide:
        title, text = _in_language(db, written, guide["title"]), _in_language(db, written, guide["text"])
        tip = {"text": words["page"].format(title=title.rstrip("."), text=text), "key": guide["key"]}
    cheer = words["cheers"][now_local.toordinal() % len(words["cheers"])]
    return {
        "name": NAME, "lang": lang, "voice_lang": {"fr": "fr-FR", "tn": "fr-FR", "ar": "ar-SA"}.get(lang, "en-US"),
        "greeting": (f"{words[part]}{' ' + first_name if first_name else ''} !" if lang == "fr" else
                     f"{words[part]}{' ' + first_name if first_name else ''}!" if lang in ("ar", "tn") else
                     f"{words[part]}{', ' + first_name if first_name else ''}!"),
        "intro": words["intro"].format(name=NAME),
        "lines": lines, "tip": tip, "cheer": cheer,
        "can_ask": True,
        # The admin (and whoever was given the assistant) asks the assistant; everyone else asks the guide.
        "ask_url": "/assistant/chat" if is_admin or (getattr(user, "permissions", None) or {}).get("assistant") in ("view", "edit") else "/account/guide",
        "words": {key: words[key] for key in ("open", "next", "ask", "voice_on", "voice_off", "news", "thinking", "sorry", "hello_again")},
        "urgent": sum(1 for line in lines if line["tone"] in ("bad", "warn")),
    }
