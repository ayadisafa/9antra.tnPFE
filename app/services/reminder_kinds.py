"""The kinds of reminders a training centre sends, to suggest when a reminder is added, and which of them this centre
needs now, from how its candidates behave (counted from the records, never guessed by a model).

Each kind has a trigger the application watches by itself (services/reminders.py, "watching" reminders):
  session     some time before each of their sessions            absence     some time after each absence marked
  start       some time before their class starts                 after_start some time after it started
  end         some time before their class ends                   after_end   some time after it ended
  unanswered  some time after weekly questions sent and not answered
  registered  some time after they registered                     every       every so often (a day, a week…)
  once        at a date you choose
Once set up, a kind's reminder appears among the reminders the application sends by itself, with its picture.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

TRIGGERS = {
    "session": ("before each of their sessions", "bi-easel"),
    "absence": ("after each absence", "bi-person-x"),
    "start": ("before their class starts", "bi-flag"),
    "after_start": ("after their class started", "bi-graph-up-arrow"),
    "end": ("before their class ends", "bi-hourglass-bottom"),
    "after_end": ("after their class ended", "bi-mortarboard"),
    "unanswered": ("after weekly questions left unanswered", "bi-patch-question"),
    "registered": ("after they registered", "bi-person-plus"),
    "every": ("every", "bi-arrow-repeat"),
    "once": ("once, on a date", "bi-1-circle"),
}

KINDS = [
    {"key": "first_session", "name": "Ready for the first session", "icon": "bi-rocket-takeoff", "motion": "bounce", "trigger": "start", "offset": "2d",
     "purpose": "The days before a start are when candidates drop out quietly: what to bring, where, at what time.",
     "subject": "Votre formation commence bientôt", "title": "Prêt(e) pour la première séance ?",
     "body": "Bonjour {first_name},\n\nVotre formation **{program_name}** commence le **{start_date}** à {start_time}.\n\n"
             "[box]\n**Pour bien démarrer**\n- votre ordinateur chargé\n- de quoi noter\n- vos questions\n\nNous avons hâte de vous accueillir !"},
    {"key": "homework", "name": "Before each session: what to prepare", "icon": "bi-journal-check", "motion": "flip", "trigger": "session", "offset": "1d",
     "purpose": "A short nudge the day before keeps the class at the same pace.",
     "subject": "Demain : votre séance de {program_name}", "title": "À préparer pour demain",
     "body": "Bonjour {first_name},\n\nVotre prochaine séance a lieu le **{session_date} à {session_time}** : {session_topic}.\n\n"
             "[box]\n**Avant la séance**\n- revoyez la séance précédente\n- notez une question à poser\n\nÀ demain !"},
    {"key": "on_time", "name": "Starts in 30 minutes", "icon": "bi-alarm", "motion": "ring", "trigger": "session", "offset": "30min",
     "purpose": "For classes where people come late or never open their link.",
     "subject": "On commence dans 30 minutes", "title": "C'est bientôt l'heure",
     "body": "Bonjour {first_name},\n\nVotre séance de **{program_name}** commence à **{session_time}**. Installez-vous dès maintenant : "
             "votre lien personnel arrive dans l'heure qui précède.\n\nÀ tout de suite !"},
    {"key": "missed", "name": "We missed you", "icon": "bi-emoji-smile", "motion": "pulse", "trigger": "absence", "offset": "2h",
     "purpose": "Two absences in a row is the first sign of a drop-out: a kind word the same day brings people back.",
     "subject": "Vous nous avez manqué", "title": "Tout va bien ?",
     "body": "Bonjour {first_name},\n\nNous ne vous avons pas vu(e) à la dernière séance de **{program_name}** et nous espérons que tout va bien.\n\n"
             "[box green]\nLes supports sont disponibles : dites-nous ce qu'il vous manque, nous vous aidons à rattraper.\n\nRépondez simplement à ce message."},
    {"key": "weekly", "name": "Weekly questions waiting", "icon": "bi-patch-question", "motion": "bounce", "trigger": "unanswered", "offset": "2d",
     "purpose": "The weekly questions measure what was understood: a reminder doubles the answers.",
     "subject": "Vos questions de la semaine vous attendent", "title": "2 minutes pour vos questions",
     "body": "Bonjour {first_name},\n\nLes questions de la semaine de **{program_name}** vous attendent : 2 minutes suffisent, et votre formateur "
             "voit ce qui reste à revoir.\n\nMerci !"},
    {"key": "check_in", "name": "How is it going? (mid-program)", "icon": "bi-chat-heart", "motion": "pulse", "trigger": "after_start", "offset": "3w",
     "purpose": "Halfway is where doubts appear: asking early keeps candidates and gives you their feedback.",
     "subject": "Comment se passe votre formation ?", "title": "Votre avis compte",
     "body": "Bonjour {first_name},\n\nVous êtes à mi-parcours de **{program_name}**. Comment ça se passe ? Le rythme vous convient-il ?\n\n"
             "Répondez en une phrase : nous ajustons ce qui peut l'être."},
    {"key": "last_week", "name": "The last week", "icon": "bi-hourglass-bottom", "motion": "flip", "trigger": "end", "offset": "1w",
     "purpose": "The final stretch: the project, the last session, what comes next.",
     "subject": "Dernière ligne droite", "title": "Votre formation se termine bientôt",
     "body": "Bonjour {first_name},\n\nVotre formation **{program_name}** se termine bientôt. Bravo pour le chemin parcouru !\n\n"
             "[box]\n**Pour bien finir**\n- terminez votre projet\n- préparez vos dernières questions\n\nÀ très bientôt."},
    {"key": "project", "name": "Project to hand in", "icon": "bi-flag", "motion": "swing", "trigger": "end", "offset": "2w",
     "purpose": "Projects handed in on time make the certificates and the success stories.",
     "subject": "Votre projet : la date approche", "title": "Votre projet",
     "body": "Bonjour {first_name},\n\nLa fin de **{program_name}** approche : pensez à votre projet.\n\n"
             "[box gold]\n**À rendre :** le lien de votre dépôt et une courte présentation\n\nBloqué(e) sur un point ? Répondez à ce message."},
    {"key": "certificate", "name": "Your certificate", "icon": "bi-award", "motion": "bounce", "trigger": "after_end", "offset": "3d",
     "purpose": "Tell them how and when they receive their certificate: it is what they share on LinkedIn.",
     "subject": "Votre certificat {program_name}", "title": "Félicitations !",
     "body": "Bonjour {first_name},\n\nFélicitations pour la fin de **{program_name}** ! Votre certificat est en préparation : "
             "nous vous l'envoyons très vite.\n\nPartagez-le sur LinkedIn : c'est votre réussite."},
    {"key": "next_program", "name": "What comes next", "icon": "bi-signpost-2", "motion": "swing", "trigger": "after_end", "offset": "10d",
     "purpose": "The best moment to propose the next program is just after a success.",
     "subject": "Et maintenant ?", "title": "La suite de votre parcours",
     "body": "Bonjour {first_name},\n\nAprès **{program_name}**, beaucoup poursuivent avec une formation complémentaire. "
             "Répondez à ce message : nous vous conseillons la suite la plus utile pour votre projet."},
    {"key": "alumni", "name": "Opportunities for alumni", "icon": "bi-briefcase", "motion": "bounce", "trigger": "after_end", "offset": "1mo",
     "purpose": "Keeping in touch a month later brings success stories, reviews and new registrations.",
     "subject": "Des nouvelles de votre parcours ?", "title": "Un mois après",
     "body": "Bonjour {first_name},\n\nUn mois après **{program_name}**, où en êtes-vous ? Stage, emploi, projet : racontez-nous !\n\n"
             "Nous partageons régulièrement des offres et des ateliers pour nos anciens."},
    {"key": "documents", "name": "Documents to complete", "icon": "bi-folder-check", "motion": "flip", "trigger": "registered", "offset": "2d",
     "purpose": "A complete file the first week avoids chasing papers at the certificate.",
     "subject": "Votre dossier d'inscription", "title": "Il nous manque peut-être un document",
     "body": "Bonjour {first_name},\n\nPour compléter votre dossier **{program_name}**, merci de nous envoyer en réponse :\n\n"
             "[box gold]\n- une pièce d'identité\n- [autre document]\n\nMerci !"},
    {"key": "news", "name": "News of the week", "icon": "bi-megaphone", "motion": "ring", "trigger": "every", "offset": "1w",
     "purpose": "One message a week (events, workshops, tips) keeps the community alive.",
     "subject": "Les nouvelles de la semaine", "title": "Cette semaine au centre",
     "body": "Bonjour {first_name},\n\nCette semaine au centre :\n\n- [atelier ou événement]\n- [conseil de la semaine]\n\nÀ très vite !"},
    {"key": "exam", "name": "Assessment day", "icon": "bi-clipboard-check", "motion": "pulse", "trigger": "once", "offset": "",
     "purpose": "A date not to miss: the place, the time, what to bring.",
     "subject": "Jour d'évaluation", "title": "Votre évaluation",
     "body": "Bonjour {first_name},\n\nVotre évaluation de **{program_name}** a lieu le [date] à [heure].\n\n"
             "[box gold]\n**À apporter :** votre ordinateur chargé et vos identifiants\n\nBonne préparation !"},
    {"key": "holiday", "name": "No session (holiday)", "icon": "bi-calendar-x", "motion": "flip", "trigger": "once", "offset": "",
     "purpose": "So nobody comes for nothing.",
     "subject": "Pas de séance", "title": "Pas de séance",
     "body": "Bonjour {first_name},\n\nIl n'y aura pas de séance le [date].\n\n[box grey]\n**Reprise :** [date de reprise]\n\nÀ très vite !"},
]
_BY_KEY = {kind["key"]: kind for kind in KINDS}


def when_words(trigger: str, offset: str | None) -> str:
    """When a kind goes, in words: "Every week", "2 days before their class starts", "On a date you choose"."""
    from app.services import durations

    if trigger == "once":
        return "On a date you choose"
    found = durations.parse(offset or "", "days")
    if trigger == "every":
        if found is None or found.value <= 0:
            return "Again and again"
        return "Every " + (found.label().split(" ", 1)[1] if found.value == 1 else found.label())
    words = TRIGGERS.get(trigger, ("", ""))[0]
    if found is None or found.value == 0:
        return words[:1].upper() + words[1:]
    return f"{found.label()} {words}"


for _kind in KINDS:
    _kind["when"] = when_words(_kind["trigger"], _kind["offset"])


def get(key: str | None) -> dict | None:
    return _BY_KEY.get(key or "")


def set_up(db: Session) -> set[str]:
    """The kinds already running (a reminder of that kind still on or paused)."""
    from app.models.admin_reminder import AdminReminder

    rows = db.query(AdminReminder.kind).filter(AdminReminder.kind.isnot(None),
                                               AdminReminder.status.in_(("scheduled", "sending", "watching", "paused"))).all()
    return {row[0] for row in rows}


def _signals(db: Session) -> dict:
    """What the records say about the candidates' behaviour (counted, never guessed)."""
    from app.models import ClassGroup, ClassStatus, Registration
    from app.models.session_join import SessionJoin
    from app.models.user import Attendance
    from app.models.quiz import Quiz
    from app.models.registration import RegistrationStatus
    from app.services.schedule_utils import class_end_dt, class_start_dt, local_now

    now, utcnow = local_now(), datetime.utcnow()
    since = utcnow - timedelta(days=30)
    absences = db.query(Attendance).filter(Attendance.status == "absent", Attendance.marked_at >= since).all()
    absent_people = {row.candidate_id for row in absences}
    joins = db.query(SessionJoin).filter(SessionJoin.sent_at.isnot(None), SessionJoin.sent_at >= since).all()
    late = {row.candidate_id for row in joins if (row.minutes_late or 0) >= 10}
    never = {row.candidate_id for row in joins if row.first_joined_at is None}
    unanswered = db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.sent_at.isnot(None), Quiz.sent_at >= since, Quiz.completed_at.is_(None)).count()
    groups = db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).all()
    starting = [group for group in groups if group.status == ClassStatus.NOT_STARTED and class_start_dt(group)
                and timedelta(0) <= class_start_dt(group) - now <= timedelta(days=21)]
    ending = [group for group in groups if group.status == ClassStatus.ONGOING and class_end_dt(group)
              and timedelta(0) <= class_end_dt(group) - now <= timedelta(days=21)]
    halfway = [group for group in groups if group.status == ClassStatus.ONGOING and class_start_dt(group)
               and now - class_start_dt(group) >= timedelta(days=14)]
    finished = db.query(Registration).filter(Registration.status == RegistrationStatus.COMPLETED).all()
    alumni = {row.candidate_id for row in finished}
    continuing = {row.candidate_id for row in db.query(Registration).filter(Registration.status.in_((RegistrationStatus.PENDING, RegistrationStatus.ASSIGNED))).all()}
    from app.models.candidate_email import CandidateEmail

    wrote = db.query(CandidateEmail.channel).filter(CandidateEmail.direction == "inbound", CandidateEmail.received_at >= since).all()
    on_whatsapp = sum(1 for row in wrote if row[0] in ("whatsapp", "sms"))
    return {"absent": len(absent_people), "absences": len(absences), "late": len(late), "never": len(never), "unanswered": unanswered,
            "starting": starting, "ending": ending, "halfway": halfway, "alumni_alone": len(alumni - continuing),
            "whatsapp_share": round(100 * on_whatsapp / len(wrote)) if wrote else 0, "new": db.query(Registration).filter(
                Registration.registration_date >= since).count()}


def recommend(db: Session, limit: int = 6) -> list[dict]:
    """The kinds worth setting up now, the strongest reason first: {"kind", "why", "score", "channel"}."""
    running = set_up(db)
    seen = _signals(db)
    channel = "both" if seen["whatsapp_share"] >= 40 else "email"
    found = []

    def add(key, score, why):
        if key in running:
            return
        if not why:          # nothing in the records points to it: what training centres do, after what the records say
            why, score = _t("Common in training centres: {0}", _t(get(key)["purpose"])), 3
        found.append({"kind": get(key), "why": why, "score": score, "channel": channel, "seen": score > 3})

    add("missed", 30 + seen["absent"] * 10, _t("{0} candidates were absent in the last 30 days ({1} absences): a kind word the same day brings them back.",
                                               seen["absent"], seen["absences"]) if seen["absent"] else "")
    add("on_time", 25 + (seen["late"] + seen["never"]) * 8, _t("{0} arrived late and {1} never opened their link lately: a reminder 30 minutes before helps.",
                                                               seen["late"], seen["never"]) if seen["late"] or seen["never"] else "")
    add("weekly", 20 + seen["unanswered"] * 5, _t("{0} weekly questions were not answered this month.", seen["unanswered"]) if seen["unanswered"] else "")
    add("first_session", 20 + len(seen["starting"]) * 12, _t("{0} classes start within three weeks: {1}.", len(seen["starting"]),
                                                              ", ".join(group.name for group in seen["starting"][:3])) if seen["starting"] else "")
    add("last_week", 18 + len(seen["ending"]) * 10, _t("{0} classes end within three weeks: {1}.", len(seen["ending"]),
                                                       ", ".join(group.name for group in seen["ending"][:3])) if seen["ending"] else "")
    add("project", 12 + len(seen["ending"]) * 8, _t("Their end is near: a reminder two weeks before keeps the projects on time.") if seen["ending"] else "")
    add("check_in", 12 + len(seen["halfway"]) * 6, _t("{0} classes are halfway or more: ask how it goes before doubts grow.", len(seen["halfway"]))
        if seen["halfway"] else "")
    add("next_program", 10 + seen["alumni_alone"] * 6, _t("{0} candidates finished a program and have no other one yet.", seen["alumni_alone"])
        if seen["alumni_alone"] else "")
    add("certificate", 8 + len(seen["ending"]) * 5, _t("Classes are ending: tell them how they receive their certificate.") if seen["ending"] else "")
    add("documents", 6 + min(seen["new"], 20), _t("{0} people registered in the last 30 days.", seen["new"]) if seen["new"] else "")
    # Always worth knowing, with less weight: the habits of training centres.
    for key in ("homework", "alumni", "news"):
        add(key, 0, "")
    found.sort(key=lambda item: -item["score"])
    return found[:limit]


def catalogue(db: Session) -> list[dict]:
    """Every kind, with whether it is already set up, for the "add a reminder" choice."""
    from app.services import durations

    running = set_up(db)
    found = []
    for kind in KINDS:
        amount, unit = durations.rows(durations.parse(kind["offset"] or "", "days"), durations.Duration(1, "days"))
        found.append({**kind, "running": kind["key"] in running, "trigger_words": TRIGGERS[kind["trigger"]][0], "amount": amount, "unit": unit})
    return found
