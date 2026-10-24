"""Questions about the programs, answered from the facts, in the language they were asked: French, English, Arabic or
Tunisian. For people not registered yet (by email, WhatsApp or SMS) and for candidates asking about a program.

What a message asks is recognised by its words (prices, length, next start, what is taught, which programs, online or
on site, where, how to register); the answer is built from the records only (the program's prices, its weeks, the next
class, the centre's places and numbers, the registration form) with sentences written by hand in each language. No
language model writes it, so nothing is invented. A message that asks nothing of this is not answered here.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from sqlalchemy.orm import Session

LANGS = ("fr", "en", "ar", "tn")

# What a message asks, by its words (Latin letters folded; Tunisian and Arabic too).
_ASKS = {
    "price": r"\b(prix|tarifs?|couts?|coute\w*|combien|price|prices|cost|costs|how much|fees?|frais|9adech|9addech|qadech|qaddech|soum|souma|el soum|flous)\b",
    "duration": r"\b(duree|combien de (temps|semaines|mois)|how long|duration|weeks long|semaines|9addech (wa9t|mod+a)|qaddech dour|tdoum|ydoum|dure)\b",
    "start": r"\b(quand (commence|demarre|debute)|(commence|demarre|debute)\w* quand|date de (debut|demarrage)|prochaine (session|classe|formation)|when (does|will|do)\b.{0,30}\bstart|start date|next (session|class|intake)|"
             r"waqtech tebda|wa9tech tebda|wa9teh tebda|imta tebda|tebda waqtech|tebda wa9tech|waqtech yebda|wa9tech yebda)\b",
    "content": r"\b(programme de|contenu|qu est ce qu on (apprend|etudie)|on apprend quoi|what (will|do) (i|we) learn|syllabus|curriculum|modules?|chnowa n9raw|chnowa na9raw|chnia fiha|chnowa fiha)\b",
    "list": r"\b(quelles? formations?|vos formations|les formations|liste des formations|formations (disponibles|proposees)|which (programs|courses|trainings)|what (programs|courses|trainings)|"
            r"(programs|courses) do you (have|offer)|chnowa (el )?formations?|chnouma (les |el )?formations?|fama chnowa)\b",
    "mode": r"\b(en ligne|online|a distance|distanciel|presentiel|sur place|onsite|on site|in person|en ligne wala)\b",
    "location": r"\b(ou (etes|est|se trouve|se situe)\w*|(vous etes|etes vous|situes|c est|se trouve|se situe) ou|votre adresse|l adresse|where (are|is) you|your address|located|location|"
                r"win (jayin|kayin|el centre|blasetkom|el blasa)|(jayin|kayin|el centre|blasetkom) win)\b",
}
_ASKS_ARABIC = {
    "price": r"بقداش|قداش السوم|السوم|سعر|الثمن|ثمن|كم سعر|كم ثمن|بكم",
    "duration": r"مدة|المدة|قداش تدوم|تدوم|كم من (أسبوع|شهر)",
    "start": r"وقتاش تبدا|متى تبدأ|متى يبدأ|وقتاش يبدا|موعد البداية|تاريخ البداية",
    "content": r"شنوة نقراو|ماذا سأتعلم|ماذا نتعلم|المحتوى|محتوى",
    "list": r"شنوة الفورماسيونات|ما هي (البرامج|الدورات|التكوينات)|الفورماسيونات|الدورات المتوفرة|التكوينات",
    "mode": r"عن بعد|حضوري|أونلاين|اونلاين",
    "location": r"وين|أين|العنوان|عنوانكم|مكانكم",
}
_NOT_FOR_US = re.compile(r"\b(rembours\w*|refund\w*|annul\w*|cancel\w*|plainte|complain\w*|arnaque|scam)\b")

# The sentences, written by hand, in each language. {…} are filled from the records.
_SAY = {
    "fr": {
        "hello": "Bonjour et merci pour votre message !", "hello_name": "Bonjour {name}, et merci pour votre message !",
        "price_both": "« {program} » coûte {online} DT en ligne et {onsite} DT sur place.", "price_one": "« {program} » coûte {price} DT ({mode}).",
        "online": "en ligne", "onsite": "sur place",
        "weeks": "La formation dure {weeks} semaines{hours}.", "hours": ", à raison de {hours} heures par semaine",
        "start": "La prochaine classe de « {program} » démarre le {date}.",
        "start_open": "Une nouvelle classe de « {program} » ouvre dès que le groupe est prêt : inscrivez-vous et nous vous donnons la date.",
        "content": "Au programme de « {program} » : {topics}…",
        "modes": "Elle se suit en ligne ou sur place{places}.", "places": " ({places})",
        "list": "Nos formations : {names}.", "which": "Dites-nous celle qui vous intéresse et nous vous donnons tous les détails.",
        "list_prices": "Nos formations et leurs prix (en ligne) : {lines}.",
        "where": "Où nous trouver : {places}.", "where_unknown": "Appelez-nous et nous vous indiquons comment venir.",
        "register": "Pour vous inscrire, il suffit de remplir notre formulaire d'inscription : {link}",
        "contact": "Pour toute question, appelez-nous au {phone}{wa}. À très bientôt !", "wa": " ou écrivez-nous sur WhatsApp au {wa}",
        "sign": "L'équipe 9antra",
    },
    "en": {
        "hello": "Hello, and thank you for your message!", "hello_name": "Hello {name}, and thank you for your message!",
        "price_both": "“{program}” costs {online} DT online and {onsite} DT on site.", "price_one": "“{program}” costs {price} DT ({mode}).",
        "online": "online", "onsite": "on site",
        "weeks": "The program lasts {weeks} weeks{hours}.", "hours": ", {hours} hours a week",
        "start": "The next “{program}” class starts on {date}.",
        "start_open": "A new “{program}” class opens as soon as the group is ready: register and we will give you the date.",
        "content": "What “{program}” covers: {topics}…",
        "modes": "You can follow it online or on site{places}.", "places": " ({places})",
        "list": "Our programs: {names}.", "which": "Tell us which one interests you and we will give you all the details.",
        "list_prices": "Our programs and their prices (online): {lines}.",
        "where": "Where to find us: {places}.", "where_unknown": "Call us and we will tell you how to come.",
        "register": "To register, simply fill in our registration form: {link}",
        "contact": "For any question, call us on {phone}{wa}. See you soon!", "wa": " or write to us on WhatsApp on {wa}",
        "sign": "The 9antra team",
    },
    "ar": {
        "hello": "مرحبا، وشكرا على رسالتك!", "hello_name": "مرحبا {name}، وشكرا على رسالتك!",
        "price_both": "سعر «{program}» هو {online} دينار عن بعد و{onsite} دينار حضوريا.", "price_one": "سعر «{program}» هو {price} دينار ({mode}).",
        "online": "عن بعد", "onsite": "حضوريا",
        "weeks": "مدة التكوين {weeks} أسبوعا{hours}.", "hours": "، بمعدل {hours} ساعات في الأسبوع",
        "start": "يبدأ القسم القادم من «{program}» يوم {date}.",
        "start_open": "يُفتح قسم جديد من «{program}» بمجرد اكتمال المجموعة: سجّل ونعلمك بالتاريخ.",
        "content": "في برنامج «{program}»: {topics}…",
        "modes": "يمكنك متابعته عن بعد أو حضوريا{places}.", "places": " ({places})",
        "list": "برامجنا: {names}.", "which": "أخبرنا بالبرنامج الذي يهمك ونعطيك كل التفاصيل.",
        "list_prices": "برامجنا وأسعارها (عن بعد): {lines}.",
        "where": "مكاننا: {places}.", "where_unknown": "اتصل بنا ونرشدك إلى مكاننا.",
        "register": "للتسجيل، يكفي ملء استمارة التسجيل: {link}",
        "contact": "لأي سؤال، اتصل بنا على {phone}{wa}. إلى اللقاء قريبا!", "wa": " أو راسلنا على واتساب {wa}",
        "sign": "فريق 9antra",
    },
    "tn": {
        "hello": "Aslema, w y3aychek 3al message mte3ek!", "hello_name": "Aslema {name}, w y3aychek 3al message mte3ek!",
        "price_both": "« {program} » soumha {online} DT en ligne w {onsite} DT sur place.", "price_one": "« {program} » soumha {price} DT ({mode}).",
        "online": "en ligne", "onsite": "sur place",
        "weeks": "El formation todhrob {weeks} jem3a{hours}.", "hours": ", {hours} swaye3 fel jem3a",
        "start": "El classe ejjeya mta3 « {program} » tebda nhar {date}.",
        "start_open": "Classe jdida mta3 « {program} » tet7al ki yekmel el groupe: sajjel w n9oulouk el date.",
        "content": "Fel programme mta3 « {program} »: {topics}…",
        "modes": "Tnajjem tetba3ha en ligne wala sur place{places}.", "places": " ({places})",
        "list": "El formations mte3na: {names}.", "which": "9olna chnowa y3ajbek w na3tiwk el détails lkol.",
        "list_prices": "El formations mte3na w soumhom (en ligne): {lines}.",
        "where": "Blasetna: {places}.", "where_unknown": "Kallemna w n9oulouk kifech tji.",
        "register": "Bech tsajjel, ya3mer el formulaire mta3 el inscription: {link}",
        "contact": "Ken 3andek ay sou2el, kallemna 3al {phone}{wa}. Nchoufouk 9rib!", "wa": " wala ab3athelna 3al WhatsApp {wa}",
        "sign": "Equipe 9antra",
    },
}
# Around an answer to a registered candidate (their greeting and where it comes from), in Arabic and Tunisian.
SOURCE = {"fr": "Source : les informations de 9antra sur ses formations.", "en": "Source: 9antra's information about its programs.",
          "ar": "المصدر: معلومات 9antra عن برامجها.", "tn": "Source: les informations mta3 9antra 3al formations."}


# What the facts answer, as the assistant's concepts (knowledge_service._CONCEPTS); anything else is for a person.
COVERED = {"_price", "_start", "_duration", "_schedule", "_online", "_onsite", "_location", "_curriculum", "_register", "_class"}


def uncovered(text: str) -> list[str]:
    """The concepts a message asks that the facts do not answer (trainers, one's own payment, certificates, refunds…)."""
    from app.services import derja, knowledge_service

    tokens = knowledge_service.tokenize(f"{text} {derja.to_french(text or '')}")
    return sorted(token for token in tokens if token.startswith("_") and token not in COVERED)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def language_of(text: str) -> str:
    from app.services import howto

    return howto.language_of(text or "")


def asks(text: str) -> set[str]:
    """What the message asks about the programs (empty when nothing of this)."""
    from app.services import derja, registration_form

    plain = _plain(f"{text} {derja.to_french(text or '')}")
    if _NOT_FOR_US.search(plain):
        return set()
    found = {key for key, pattern in _ASKS.items() if re.search(pattern, plain)}
    found |= {key for key, pattern in _ASKS_ARABIC.items() if re.search(pattern, text or "")}
    if registration_form.asks_how(text or "") or registration_form.asks_how(derja.to_french(text or "")):
        found.add("register")
    return found


def _money(value) -> str:
    return f"{float(value):,.0f}".replace(",", " ") if value else ""


def _places(db: Session) -> str:
    """Where the centre is, as written by the admin (Settings, or the welcome message): never guessed."""
    from app.services import email_templates
    from app.services.settings_service import get_settings

    written = (getattr(get_settings(db), "centre_address", None) or "").strip()
    if written:
        return written
    welcome = email_templates.get(db, "welcome")
    body = f"{welcome.body or ''}\n{welcome.closing or ''}"
    line = next((item.strip() for item in body.splitlines()
                 if re.search(r"\b(Berges du Lac|Lac \d|Tunis|Sousse|Sahloul|Ariana|Sfax|Monastir|Nabeul|Bizerte|Manouba|Ben Arous)\b", item)), "")
    return line.strip(" -–·") if len(line) <= 120 else ""


def _next_start(db: Session, program) -> datetime | None:
    from app.models import ClassGroup, ClassStatus
    from app.services.schedule_utils import class_start_dt, local_now

    now = local_now()
    starts = [class_start_dt(group) for group in db.query(ClassGroup).filter(ClassGroup.program_id == program.id, ClassGroup.status == ClassStatus.NOT_STARTED).all()]
    starts = [start for start in starts if start is not None and start > now]
    return min(starts) if starts else None


def _date(value: datetime, lang: str) -> str:
    from app.services.schedule_utils import format_date_en, format_date_fr

    if lang == "en":
        return format_date_en(value)
    if lang == "ar":
        return f"{value:%d/%m/%Y}"
    return format_date_fr(value)


def reply_for(db: Session, text: str, lang: str | None = None, *, registered: bool = False, name: str = "", signed: bool = True) -> dict | None:
    """{"text", "lang", "asks", "program_ids"}: the answer built from the facts; None when the message asks nothing of this
    (or the facts it needs are missing)."""
    from app.models import Program
    from app.services import enrollment_service, program_documents, registration_form
    from app.services.settings_service import get_settings

    wanted = asks(text)
    if not wanted:
        return None
    lang = lang if lang in LANGS else language_of(text)
    say = _SAY[lang]
    programs = [item for item in program_documents.named_programs(db, text or "") if item.is_approved]
    if not programs:
        from app.services import derja

        programs = [item for item in program_documents.named_programs(db, derja.to_french(text or "")) if item.is_approved]
    lines = []
    places = _places(db)
    for program in programs[:2]:
        weeks = len(enrollment_service.curriculum_weeks(db, program.id))
        if "price" in wanted:
            if program.price_online and program.price_onsite:
                lines.append(say["price_both"].format(program=program.name, online=_money(program.price_online), onsite=_money(program.price_onsite)))
            elif program.price_online or program.price_onsite:
                lines.append(say["price_one"].format(program=program.name, price=_money(program.price_online or program.price_onsite),
                                                     mode=say["online"] if program.price_online else say["onsite"]))
        if "duration" in wanted and weeks:
            hours = say["hours"].format(hours=_money(program.hours_per_week)) if program.hours_per_week else ""
            lines.append(say["weeks"].format(weeks=weeks, hours=hours))
        if "start" in wanted:
            start = _next_start(db, program)
            lines.append(say["start"].format(program=program.name, date=_date(start, lang)) if start else say["start_open"].format(program=program.name))
        if "content" in wanted:
            topics = [week.topic_title for week in enrollment_service.curriculum_weeks(db, program.id) if week.topic_title][:5]
            if topics:
                lines.append(say["content"].format(program=program.name, topics=", ".join(topics)))
        if "mode" in wanted and (program.price_online or program.price_onsite):
            lines.append(say["modes"].format(places=say["places"].format(places=places) if places else ""))
    if not programs and wanted & {"price", "duration", "start", "content", "list", "mode"}:
        approved = db.query(Program).filter(Program.is_approved.is_(True)).order_by(Program.name).all()
        if not approved:
            return None
        if "price" in wanted:
            lines.append(say["list_prices"].format(lines="; ".join(f"{item.name} {_money(item.price_online or item.price_onsite)} DT"
                                                                   for item in approved if item.price_online or item.price_onsite)))
        else:
            lines.append(say["list"].format(names=", ".join(item.name for item in approved)))
        lines.append(say["which"])
    if "location" in wanted:
        lines.append(say["where"].format(places=places) if places else say["where_unknown"])
    if not lines and "register" not in wanted:
        return None
    if not registered or "register" in wanted:
        lines.append(say["register"].format(link=registration_form.url(db)))
    settings = get_settings(db)
    phone = (getattr(settings, "centre_phone", None) or "").strip()
    whatsapp = (getattr(settings, "centre_whatsapp", None) or "").strip()
    if phone or whatsapp:
        lines.append(say["contact"].format(phone=phone or whatsapp, wa=say["wa"].format(wa=whatsapp) if phone and whatsapp and whatsapp != phone else ""))
    head = [] if registered else [say["hello_name"].format(name=name) if name else say["hello"]]
    tail = [] if registered or not signed else [say["sign"]]
    return {"text": "\n\n".join(head + lines + tail), "lang": lang, "asks": sorted(wanted), "program_ids": [item.id for item in programs]}


def answer_stranger(db: Session, *, channel: str, to: str, text: str, subject: str = "", name: str = "") -> dict | None:
    """Somebody not registered yet asks about the programs: they get the answer by the way they wrote, when the admin lets
    the assistant answer alone there (and has not switched this off). None when the message asks nothing of this.
    Else {"sent": bool, "problem", "text"}."""
    from app.services import registration_form
    from app.services.settings_service import get_settings

    if getattr(get_settings(db), "prospect_answers_on", True) is False:
        return None
    found = reply_for(db, f"{subject}\n{text}" if subject else text, name=name, signed=channel != "email")   # an email has its own signature
    if found is None:
        return None
    body = found["text"]
    if not registration_form._may_answer_alone(db, channel):
        return {"sent": False, "problem": "the assistant does not answer alone", "text": body}
    from app.services import email_assistant

    rule = email_assistant.never_alone(get_settings(db), f"{subject} {text}")
    if rule:
        return {"sent": False, "problem": f"your rule “{rule}” says a person answers it", "text": body}
    others = uncovered(f"{subject} {text}")
    if others:
        return {"sent": False, "problem": "they also ask about " + ", ".join(token.strip("_") for token in others), "text": body}
    problem = None
    try:
        if channel == "whatsapp":
            from app.services import whatsapp

            problem = whatsapp.send(to, body, free=True) if whatsapp.configured() else "no WhatsApp Business number"
        elif channel == "sms":
            from app.services import sms

            problem = sms.send(to, body) if sms.configured() else "no Android phone connected"
        else:
            from email.utils import make_msgid

            from app.services import email_assistant, email_threads

            if not email_threads.smtp_configured():
                problem = "email is not set up"
            else:
                language = "en" if found["lang"] == "en" else "fr"
                plain, html_body = email_assistant.compose_reply(db, None, body, language=language)
                title = (subject or "").strip()
                title = title if title.lower().startswith("re:") else f"Re: {title}" if title else (
                    "Our programs at 9antra" if language == "en" else "Nos formations chez 9antra")
                problem, _ = email_threads._attempt(to, title, plain, html_body, make_msgid(domain="9antra.tn"), None, None)
    except Exception as error:          # never stops the message from being kept
        import logging

        logging.getLogger(__name__).exception("The answer about the programs could not be sent to %s.", to)
        problem = str(error) or type(error).__name__
    return {"sent": problem is None, "problem": problem, "text": body}
