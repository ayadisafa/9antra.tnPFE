"""What the centre can post, made from its own records (no model writes it): the success in figures, what former
candidates said in public, “you said, we did”, a program that starts soon, a trend the centre already teaches, and how to
join. Each idea has its lines for a picture (social_images), its scenes for a video (social_video) and a caption with
hashtags in French, English or Arabic.

Testimonials: only words already public (9antra.tn, Facebook) or a review whose author agreed to its publication, and
the first name only.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

LANGUAGES = ("fr", "en", "ar")
TOPICS = {
    "success": ("Their success", "bi-trophy"),
    "testimonial": ("What they say", "bi-chat-quote"),
    "listen": ("We listen", "bi-ear"),
    "program": ("A program starts", "bi-journal-bookmark"),
    "trend": ("What is new", "bi-graph-up-arrow"),
    "join": ("Join us", "bi-person-plus"),
}
_W = {
    "fr": {"trained": "candidats formés", "training": "en formation", "programs": "formations", "happy": "satisfaits", "stars": "note moyenne",
           "recommend": "nous recommandent", "said": "ont donné leur avis", "classes": "classes en cours",
           "success_kicker": "ILS RÉUSSISSENT AVEC NOUS", "success_title": "Votre réussite, notre fierté",
           "testimonial_kicker": "ILS EN PARLENT", "listen_kicker": "VOUS PARLEZ, NOUS ÉCOUTONS", "listen_title": "Vous avez dit, nous avons fait",
           "you_said": "Vous avez dit", "we_did": "Nous avons fait", "program_kicker": "NOUVELLE SESSION", "starts": "Début", "weeks": "semaines",
           "online": "En ligne", "onsite": "Sur place", "both": "En ligne et sur place", "trend_kicker": "CE QUI MONTE", "join_kicker": "REJOIGNEZ-NOUS",
           "join_title": "Votre prochaine compétence commence ici", "cta": "Inscrivez-vous", "centre": "Centre de formation",
           "taught": "déjà au programme de", "dt": "DT"},
    "en": {"trained": "candidates trained", "training": "in training", "programs": "programs", "happy": "happy", "stars": "average rating",
           "recommend": "recommend us", "said": "gave their opinion", "classes": "classes running",
           "success_kicker": "THEY SUCCEED WITH US", "success_title": "Your success, our pride",
           "testimonial_kicker": "THEY SAY IT BEST", "listen_kicker": "YOU SPEAK, WE LISTEN", "listen_title": "You said, we did",
           "you_said": "You said", "we_did": "We did", "program_kicker": "NEW SESSION", "starts": "Starts", "weeks": "weeks",
           "online": "Online", "onsite": "On site", "both": "Online and on site", "trend_kicker": "WHAT IS RISING", "join_kicker": "JOIN US",
           "join_title": "Your next skill starts here", "cta": "Register now", "centre": "Training centre",
           "taught": "already taught in", "dt": "DT"},
    "ar": {"trained": "مترشح تكوّن", "training": "في التكوين", "programs": "برامج", "happy": "راضون", "stars": "التقييم المتوسط",
           "recommend": "ينصحون بنا", "said": "أبدوا رأيهم", "classes": "أقسام جارية",
           "success_kicker": "ينجحون معنا", "success_title": "نجاحكم فخرنا",
           "testimonial_kicker": "يتحدثون عنا", "listen_kicker": "تتكلمون، نسمعكم", "listen_title": "قلتم، ففعلنا",
           "you_said": "قلتم", "we_did": "فعلنا", "program_kicker": "دورة جديدة", "starts": "البداية", "weeks": "أسابيع",
           "online": "عن بعد", "onsite": "حضوريا", "both": "عن بعد وحضوريا", "trend_kicker": "الأكثر رواجا", "join_kicker": "انضموا إلينا",
           "join_title": "مهارتك القادمة تبدأ هنا", "cta": "سجّل الآن", "centre": "مركز تكوين",
           "taught": "يُدرَّس في", "dt": "د.ت"},
}
_CAPTIONS = {
    "success": {
        "fr": "{brand} 💪 {lines}\n\nMerci à chaque candidat qui nous fait confiance. Votre réussite est notre plus belle fierté ! 🎓\n\n👉 Rejoignez-nous : {link}",
        "en": "{brand} 💪 {lines}\n\nThank you to every candidate who trusts us. Your success is our proudest achievement! 🎓\n\n👉 Join us: {link}",
        "ar": "{brand} 💪 {lines}\n\nشكرا لكل مترشح وثق بنا. نجاحكم هو أكبر فخر لنا! 🎓\n\n👉 انضموا إلينا: {link}",
    },
    "testimonial": {
        "fr": "💬 « {quote} »\n— {name}\n\nMerci pour ces mots qui nous motivent chaque jour ! ❤️\n\n👉 Vous aussi, lancez-vous : {link}",
        "en": "💬 “{quote}”\n— {name}\n\nThank you for words that motivate us every day! ❤️\n\n👉 Your turn: {link}",
        "ar": "💬 «{quote}»\n— {name}\n\nشكرا على هذه الكلمات التي تحفزنا كل يوم! ❤️\n\n👉 جاء دورك: {link}",
    },
    "listen": {
        "fr": "👂 Vous parlez, nous écoutons.\n\nVous avez dit : {said}\n✅ Nous avons fait : {did}\n\nVos avis nous font grandir. Continuez à nous écrire ! 🙏",
        "en": "👂 You speak, we listen.\n\nYou said: {said}\n✅ We did: {did}\n\nYour feedback makes us better. Keep telling us! 🙏",
        "ar": "👂 تتكلمون، نسمعكم.\n\nقلتم: {said}\n✅ ففعلنا: {did}\n\nآراؤكم تجعلنا أفضل. واصلوا الكتابة لنا! 🙏",
    },
    "program": {
        "fr": "🚀 Nouvelle session : {program} !\n📅 {starts}\n⏱️ {weeks}\n📍 {mode}{price}\n\nPlaces limitées, inscrivez-vous dès maintenant 👉 {link}",
        "en": "🚀 New session: {program}!\n📅 {starts}\n⏱️ {weeks}\n📍 {mode}{price}\n\nLimited seats, register now 👉 {link}",
        "ar": "🚀 دورة جديدة: {program}!\n📅 {starts}\n⏱️ {weeks}\n📍 {mode}{price}\n\nالأماكن محدودة، سجّلوا الآن 👉 {link}",
    },
    "trend": {
        "fr": "📈 On en parle partout : {term}.\nBonne nouvelle : c'est {taught} {program} chez {brand} ! 💡\n\n👉 Découvrez la formation : {link}",
        "en": "📈 Everyone is talking about it: {term}.\nGood news: it is {taught} {program} at {brand}! 💡\n\n👉 Discover the program: {link}",
        "ar": "📈 الجميع يتحدث عنه: {term}.\nخبر سار: إنه {taught} {program} في {brand}! 💡\n\n👉 اكتشفوا البرنامج: {link}",
    },
    "join": {
        "fr": "✨ Votre prochaine compétence commence ici.\n{programs}\n\n📝 Inscription : {link}\n📞 {phone}",
        "en": "✨ Your next skill starts here.\n{programs}\n\n📝 Register: {link}\n📞 {phone}",
        "ar": "✨ مهارتك القادمة تبدأ هنا.\n{programs}\n\n📝 التسجيل: {link}\n📞 {phone}",
    },
}


def words(lang: str) -> dict:
    return _W.get(lang, _W["fr"])


def _brand(db: Session) -> str:
    from app.services.settings_service import get_settings

    return (getattr(get_settings(db), "platform_name", None) or "9antra").strip() or "9antra"


def contact(db: Session) -> dict:
    from app.services import registration_form
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    return {"brand": _brand(db), "link": registration_form.url(db), "phone": (settings.centre_phone or settings.centre_whatsapp or "").strip(),
            "website": (settings.centre_website or "9antra.tn").strip()}


def _tag(text: str) -> str:
    return "#" + re.sub(r"[^\w]+", "", (text or "").title().replace(" ", ""))


def hashtags(db: Session, topic: str, programs: list[str] | None = None) -> str:
    tags = ["#" + re.sub(r"\W+", "", _brand(db)), "#Formation", "#Tunisie", "#Training"]
    tags += {"success": ["#Réussite", "#Success"], "testimonial": ["#Témoignage"], "listen": ["#VousÉcouter", "#Feedback"],
             "program": ["#NouvelleSession"], "trend": ["#Tech", "#Tendances"], "join": ["#Inscription"]}.get(topic, [])
    tags += [_tag(name) for name in (programs or [])[:2]]
    return " ".join(dict.fromkeys(tag for tag in tags if len(tag) > 2))


def success_figures(db: Session) -> list[dict]:
    """The figures worth telling: [{"value", "suffix", "key"}], the strongest first, only those that are true and say something."""
    from app.models import ClassGroup, ClassStatus, Program, Registration
    from app.models.registration import RegistrationStatus
    from app.services import feedback_hub

    found = []
    trained = db.query(Registration).filter(Registration.status == RegistrationStatus.COMPLETED).count()
    training = db.query(Registration).filter(Registration.status == RegistrationStatus.ASSIGNED).count()
    voices = feedback_hub.overview(db)
    if voices["happy_share"] is not None and voices["judged"] >= 5:
        found.append({"value": voices["happy_share"], "suffix": "%", "key": "happy"})
    if voices["stars"] and voices["rated"] >= 3:
        found.append({"value": voices["stars"], "suffix": "/5", "key": "stars"})
    if trained:
        found.append({"value": trained, "suffix": "", "key": "trained"})
    if training:
        found.append({"value": training, "suffix": "", "key": "training"})
    if voices["recommend_pct"] is not None and voices["rated"] >= 3:
        found.append({"value": voices["recommend_pct"], "suffix": "%", "key": "recommend"})
    if voices["total"] >= 5:
        found.append({"value": voices["total"], "suffix": "", "key": "said"})
    programs = db.query(Program).count()
    if programs:
        found.append({"value": programs, "suffix": "", "key": "programs"})
    running = db.query(ClassGroup).filter(ClassGroup.status.in_([ClassStatus.ONGOING])).count() if hasattr(ClassStatus, "ONGOING") else 0
    if running:
        found.append({"value": running, "suffix": "", "key": "classes"})
    return found[:4]


def testimonials(db: Session, most: int = 6) -> list[dict]:
    """Public words of happy people: [{"quote", "name", "stars", "source"}] (first names only)."""
    from app.services import feedback_hub

    found = []
    for row in feedback_hub.items(db):
        text = (row["text"] or "").strip()
        if row["sentiment"] != "positive" or not (40 <= len(text) <= 320):
            continue
        public = row["source"] in ("website", "facebook") or (row["source"] == "review" and row.get("public"))
        if not public:
            continue
        first = (row["name"] or "").split()[0].capitalize() if (row["name"] or "").strip() else ""
        found.append({"quote": text, "name": first or "", "stars": row.get("stars"), "source": row["source"]})
    found.sort(key=lambda item: (-(item["stars"] or 0), abs(len(item["quote"]) - 140)))
    return found[:most]


def answers(db: Session) -> list:
    from app.models.social import FeedbackAnswer

    return (db.query(FeedbackAnswer).filter(FeedbackAnswer.active.is_(True), FeedbackAnswer.we_did.isnot(None))
            .order_by(FeedbackAnswer.updated_at.desc()).all())


def next_sessions(db: Session, most: int = 4) -> list[dict]:
    """The programs whose next class starts soon (else none): [{"program", "starts", "weeks", "mode", "price"}]."""
    from app.models import ClassGroup, ClassStatus, Program

    found, seen = [], set()
    now = datetime.utcnow()
    for group in (db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.NOT_STARTED, ClassGroup.start_date.isnot(None),
                                              ClassGroup.start_date >= now).order_by(ClassGroup.start_date).all()):
        program = db.get(Program, group.program_id) if group.program_id else None
        if program is None or program.id in seen:
            continue
        seen.add(program.id)
        weeks = sum(len(level.weeks) for level in program.levels) or group.total_weeks
        found.append({"program": program.name, "program_id": program.id, "starts": group.start_date, "weeks": weeks,
                      "online": getattr(program, "price_online", None), "onsite": getattr(program, "price_onsite", None)})
        if len(found) >= most:
            break
    return found


def _date(moment: datetime, lang: str) -> str:
    months = {"fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
              "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
              "ar": ["جانفي", "فيفري", "مارس", "أفريل", "ماي", "جوان", "جويلية", "أوت", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]}[lang]
    return f"{moment.day} {months[moment.month - 1]} {moment.year}" if lang != "en" else f"{months[moment.month - 1]} {moment.day}, {moment.year}"


def _term(term: str) -> str:
    """A subject as people write it: “React”, “LLM”, “Next.js”."""
    if term != term.lower():
        return term
    if len(term) <= 4 and term.isalpha():
        return term.upper()
    return term[:1].upper() + term[1:]


def _figure_text(figure: dict, w: dict) -> str:
    value = figure["value"]
    shown = (f"{value:g}" if isinstance(value, float) else str(value)) + figure["suffix"]
    return f"{shown} {w[figure['key']]}"


def ideas(db: Session, lang: str = "fr") -> list[dict]:
    """Every post the records allow today: [{"key", "topic", "title", "kicker", "headline", "figures", "quote", "said", "did",
    "facts", "caption", "hashtags"}]."""
    lang = lang if lang in LANGUAGES else "fr"
    w = words(lang)
    info = contact(db)
    found = []
    figures = success_figures(db)
    if figures:
        lines = " · ".join(_figure_text(item, w) for item in figures)
        found.append({"key": "success", "topic": "success", "kicker": w["success_kicker"], "headline": w["success_title"],
                      "figures": [{"value": item["value"], "suffix": item["suffix"], "label": w[item["key"]]} for item in figures],
                      "caption": _CAPTIONS["success"][lang].format(brand=info["brand"], lines=lines, link=info["link"]),
                      "hashtags": hashtags(db, "success")})
    for index, item in enumerate(testimonials(db, 3)):
        found.append({"key": f"testimonial:{index}", "topic": "testimonial", "kicker": w["testimonial_kicker"], "headline": "",
                      "quote": item["quote"], "name": item["name"], "stars": item["stars"],
                      "caption": _CAPTIONS["testimonial"][lang].format(quote=item["quote"], name=item["name"] or info["brand"], link=info["link"]),
                      "hashtags": hashtags(db, "testimonial")})
    for answer in answers(db)[:3]:
        found.append({"key": f"listen:{answer.id}", "topic": "listen", "kicker": w["listen_kicker"], "headline": w["listen_title"],
                      "said": answer.you_said, "did": answer.we_did,
                      "caption": _CAPTIONS["listen"][lang].format(said=answer.you_said, did=answer.we_did), "hashtags": hashtags(db, "listen")})
    for session in next_sessions(db, 3):
        price = f"\n💳 {session['online']:g} {w['dt']}" if session["online"] else ""
        found.append({"key": f"program:{session['program_id']}", "topic": "program", "kicker": w["program_kicker"], "headline": session["program"],
                      "facts": [(w["starts"], _date(session["starts"], lang)), ("", f"{session['weeks']} {w['weeks']}"), ("", w["both"])]
                      + ([("", f"{session['online']:g} {w['dt']}")] if session["online"] else []),
                      "caption": _CAPTIONS["program"][lang].format(program=session["program"], starts=_date(session["starts"], lang),
                                                                   weeks=f"{session['weeks']} {w['weeks']}", mode=w["both"], price=price, link=info["link"]),
                      "hashtags": hashtags(db, "program", [session["program"]])})
    try:
        from app.services import journal

        taught, seen = [], set()
        for item in journal.suggestions(db, most=8):
            if item["taught"] and item["term"].lower() not in seen:          # one idea per subject, whatever the programs
                seen.add(item["term"].lower())
                taught.append({**item, "term": _term(item["term"])})
        taught = taught[:2]
    except Exception:
        taught = []
    for item in taught:
        found.append({"key": f"trend:{item['term']}", "topic": "trend", "kicker": w["trend_kicker"], "headline": item["term"],
                      "facts": [("", f"{w['taught']} {item['program'].name}")],
                      "caption": _CAPTIONS["trend"][lang].format(term=item["term"], taught=w["taught"], program=item["program"].name,
                                                                 brand=info["brand"], link=info["link"]),
                      "hashtags": hashtags(db, "trend", [item["program"].name])})
    from app.models import Program

    names = [program.name for program in db.query(Program).order_by(Program.name).limit(6).all()]
    found.append({"key": "join", "topic": "join", "kicker": w["join_kicker"], "headline": w["join_title"],
                  "facts": [("", name) for name in names[:5]],
                  "caption": _CAPTIONS["join"][lang].format(programs="\n".join("✔️ " + name for name in names), link=info["link"],
                                                            phone=info["phone"] or info["website"]),
                  "hashtags": hashtags(db, "join", names)})
    for idea in found:
        idea["title"] = TOPICS[idea["topic"]][0]
        idea["icon"] = TOPICS[idea["topic"]][1]
        idea["lang"] = lang
        idea["contact"] = info
        idea["cta"] = w["cta"]
        idea["centre"] = w["centre"]
        idea["you_said"], idea["we_did"] = w["you_said"], w["we_did"]
    return found


def listen_proposals(db: Session, most: int = 6) -> list[dict]:
    """What people asked to improve and nobody answered yet: [{"theme", "label", "icon", "count", "quote"}], the most said
    first (from the reviews' “what to improve”, and the subjects of the feedback that is not fully happy)."""
    from app.models.social import FeedbackAnswer
    from app.services import feedback, feedback_hub

    answered = {row.theme for row in db.query(FeedbackAnswer).filter(FeedbackAnswer.active.is_(True)).all() if row.theme}
    found: dict[str, dict] = {}
    for row in feedback_hub.items(db):
        keys = list(row.get("improve_themes") or [])
        if row["sentiment"] in ("negative", "mixed"):
            keys += row["themes"]
        for key in dict.fromkeys(keys):
            entry = found.setdefault(key, {"count": 0, "quotes": []})
            entry["count"] += 1
            text = (row.get("improve") or row["text"] or "").strip()
            if text and len(entry["quotes"]) < 2:
                entry["quotes"].append(text[:200])
    rows = [{"theme": key, "label": feedback.THEMES[key]["label"], "icon": feedback.THEMES[key]["icon"], "count": value["count"],
             "quote": value["quotes"][0] if value["quotes"] else ""}
            for key, value in found.items() if key in feedback.THEMES and key not in answered]
    return sorted(rows, key=lambda row: -row["count"])[:most]


def save_answer(db: Session, *, you_said: str, we_did: str = "", theme: str | None = None, answer_id: str | None = None):
    """“You said …, we did …” kept for the posts and videos. Raises ValueError with a message for the admin."""
    from app.models.social import FeedbackAnswer
    from app.services import feedback

    said = " ".join((you_said or "").split())[:300]
    did = " ".join((we_did or "").split())[:300]
    if len(said) < 4:
        raise ValueError("Write what people said, in a few words.")
    row = db.get(FeedbackAnswer, answer_id) if answer_id else None
    if row is None:
        row = FeedbackAnswer()
        db.add(row)
    row.you_said, row.we_did = said, did or None
    row.theme = theme if theme in feedback.THEMES else row.theme
    row.active = True
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def idea(db: Session, key: str, lang: str = "fr") -> dict | None:
    return next((item for item in ideas(db, lang) if item["key"] == key), None)
