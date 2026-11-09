"""Yasmine takes you where you ask: "ouvre les paiements", "take me to Ahmed Naji's file", "7ell el classe MD1",
"افتح الإعدادات", "reviens", "fais-moi visiter" (the visit, page after page, by itself when asked), and "prépare un email
pour Fares : …" (the email page, already filled in: nothing goes until you press Send).

Read with word lists in French, English, Arabic and Tunisian, never by a model. A page someone may not open is never
offered to them. Her window goes with you: on the next page it opens again on the same conversation.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

# (address, {lang: name}, the words that name it, folded: no accents, lower case)
PAGES = [
    ("/", {"en": "Dashboard", "fr": "Tableau de bord", "ar": "لوحة القيادة", "tn": "Dashboard"},
     r"dashboard|tableau de bord|accueil|home|page d accueil|لوحة القيادة|الرئيسية"),
    ("/candidate/all", {"en": "Candidates", "fr": "Candidats", "ar": "المترشحون", "tn": "Candidats"},
     r"candidats?|candidates?|etudiants?|students?|inscrits|المترشحين|المترشحون|الطلبة"),
    ("/candidate/all?add=1", {"en": "Add a candidate", "fr": "Ajouter un candidat", "ar": "إضافة مترشح", "tn": "Zid candidat"},
     r"(?:ajouter|nouveau|add|new|zid)\s+(?:un\s+|a\s+)?candidat\w*|إضافة مترشح"),
    ("/classes/?tab=professors", {"en": "Professors", "fr": "Formateurs", "ar": "الأساتذة", "tn": "Les profs"},
     r"professeurs?|profs?|formateurs?|enseignants?|teachers?|professors?|trainers?|الأساتذة|الاساتذة|المكونين"),
    ("/classes/", {"en": "Classes", "fr": "Classes", "ar": "الأقسام", "tn": "Classes"},
     r"classes?|groupes?|groups?|timetable|emploi du temps|الأقسام|الاقسام"),
    ("/programs/market", {"en": "What the market asks", "fr": "Ce que demande le marché", "ar": "ما يطلبه سوق الشغل", "tn": "Chnowa yotlob el marché"},
     r"marches?|market|job market|marche de l emploi|offres? d emploi|job offers?|emplois?|سوق الشغل|السوق"),
    ("/programs/documents", {"en": "PDF documents", "fr": "Documents PDF", "ar": "وثائق PDF", "tn": "Documents PDF"},
     r"documents?|brochures?|الوثائق"),
    ("/programs/videos", {"en": "Videos", "fr": "Vidéos", "ar": "الفيديوهات", "tn": "Vidéos"}, r"videos?|الفيديوهات|الفيديو"),
    ("/programs/expertise", {"en": "What the assistant knows", "fr": "Ce que sait l'assistant", "ar": "ما يعرفه المساعد", "tn": "Chnowa ya3ref el assistant"},
     r"expertise|ce que (?:l assistant|tu) sai[ts]|what (?:the assistant|you) knows?"),
    ("/programs/all", {"en": "Programs", "fr": "Programmes", "ar": "البرامج", "tn": "Programmes"},
     r"programmes?|programs?|formations?|catalogue|البرامج|التكوينات"),
    ("/journal", {"en": "The journal", "fr": "Le journal", "ar": "الجريدة", "tn": "El journal"},
     r"journal|news|actualites?|nouvelles|articles?|akhbar|الأخبار|الجريدة"),
    ("/quiz/weekly", {"en": "Weekly questions", "fr": "Questions de la semaine", "ar": "أسئلة الأسبوع", "tn": "As2la mta3 el jem3a"},
     r"questions? (?:de la semaine|hebdo\w*)|weekly questions?|أسئلة الأسبوع"),
    ("/quiz/all", {"en": "Level checks", "fr": "Tests de niveau", "ar": "اختبارات المستوى", "tn": "Tests de niveau"},
     r"tests? de niveau|level checks?|quiz+|niveaux?|placement|اختبارات المستوى"),
    ("/inbox/compose", {"en": "Write an email", "fr": "Écrire un email", "ar": "كتابة رسالة", "tn": "Ekteb email"},
     r"composer|(?:ecrire|nouvel?|write|new) (?:un |an |a )?(?:e ?mail|mail|courriel)|كتابة رسالة"),
    ("/inbox/templates", {"en": "Automatic emails", "fr": "Emails automatiques", "ar": "الرسائل الآلية", "tn": "Emails automatiques"},
     r"modeles?|templates?|e ?mails? automatiques?|automatic e ?mails?|القوالب"),
    ("/inbox/knowledge", {"en": "What the assistant was taught", "fr": "Ce qu'on a appris à l'assistant", "ar": "ما تعلّمه المساعد", "tn": "Chnowa t3allem el assistant"},
     r"connaissances?|knowledge|المعارف"),
    ("/inbox/team", {"en": "Team chat", "fr": "Discussion d'équipe", "ar": "محادثة الفريق", "tn": "Chat mta3 l equipe"},
     r"chat (?:d )?equipe|team chat|discussion (?:d )?equipe|messages? (?:de l |of the )?(?:equipe|team)|محادثة الفريق"),
    ("/inbox/not-reached", {"en": "Didn't reach them", "fr": "Non reçus", "ar": "لم تصلهم", "tn": "Ma wsolhomch"},
     r"pas (?:atteints?|recus?)|non recus?|not reached|didn t reach|ma wsolhomch|لم تصل\w*"),
    ("/inbox", {"en": "Inbox", "fr": "Boîte de réception", "ar": "صندوق الرسائل", "tn": "Inbox"},
     r"boite (?:de reception|mail)|inbox|e ?mails?|mails?|messages?|courriels?|messagerie|الرسائل|البريد"),
    ("/payments", {"en": "Payments", "fr": "Paiements", "ar": "المدفوعات", "tn": "Paiements"},
     r"paiements?|payments?|argent|money|finances?|flous|khlas|المدفوعات|الدفوعات|الخلاص"),
    ("/reminders", {"en": "Reminders", "fr": "Rappels", "ar": "التذكيرات", "tn": "Rappels"}, r"rappels?|reminders?|relances?|التذكيرات"),
    ("/social", {"en": "Social media", "fr": "Réseaux sociaux", "ar": "وسائل التواصل", "tn": "Réseaux sociaux"},
     r"reseaux sociaux|social(?: media)?|publications?|posts?|facebook|instagram|tiktok|وسائل التواصل"),
    ("/website", {"en": "Website", "fr": "Site web", "ar": "الموقع", "tn": "Site web"}, r"site(?: web| internet)?|website|9antra tn|الموقع"),
    ("/assistant/agents", {"en": "The agents", "fr": "Les agents", "ar": "الوكلاء", "tn": "Les agents"}, r"agents?|الوكلاء"),
    ("/whats-new", {"en": "What's new", "fr": "Nouveautés", "ar": "الجديد", "tn": "Chnowa jdid"},
     r"nouveautes?|what s new|quoi de neuf|chnowa jdid|الجديد"),
    ("/system#automatic", {"en": "What it does by itself", "fr": "Ce qu'elle fait seule", "ar": "ما يفعله وحده", "tn": "Chnowa ya3mel wa7dou"},
     r"(?:ce qu (?:elle|il|l application) fait|what it does) (?:seule?|tout seul|by itself)|automatisations?"),
    ("/system", {"en": "How it is working", "fr": "Comment ça marche", "ar": "كيف يعمل", "tn": "Kifech ye5dem"},
     r"systeme|system|comment (?:ca|elle|il) (?:marche|fonctionne)|how it (?:is )?work\w*|etat|status|النظام"),
    ("/settings/team#connected", {"en": "Who is connected", "fr": "Qui est connecté", "ar": "من متصل", "tn": "Chkoun connecté"},
     r"qui est (?:connecte|en ligne)|who is (?:connected|online)|connectes?|chkoun connecte|المتصلين|من متصل"),
    ("/settings/team", {"en": "Team and access", "fr": "Équipe et accès", "ar": "الفريق والصلاحيات", "tn": "L'équipe w les accès"},
     r"equipe|team|acces|access|utilisateurs?|users?|comptes|الفريق"),
    ("/settings/translations", {"en": "Translations", "fr": "Traductions", "ar": "الترجمات", "tn": "Traductions"}, r"traductions?|translations?|الترجمات"),
    ("/settings#character", {"en": "Her character", "fr": "Son personnage", "ar": "شخصيتها", "tn": "Personnage mte3ha"},
     r"(?:ton|son|your|her) (?:personnage|look|apparence|character)|personnage|شخصيتك|شخصيتها"),
    ("/settings", {"en": "Settings", "fr": "Paramètres", "ar": "الإعدادات", "tn": "Paramètres"},
     r"parametres?|settings?|reglages?|configuration|الإعدادات|الاعدادات"),
    ("/data", {"en": "All the data", "fr": "Toutes les données", "ar": "كل البيانات", "tn": "Les données el kol"},
     r"donnees|data|base de donnees|database|البيانات"),
    ("/account/messages", {"en": "My messages", "fr": "Mes messages", "ar": "رسائلي", "tn": "Messages mte3i"}, r"mes messages|my messages|رسائلي"),
    ("/teacher/profile", {"en": "My profile", "fr": "Mon profil", "ar": "ملفي", "tn": "Profil mte3i"}, r"mon profil|my profile|ملفي"),
    ("/teacher", {"en": "My classes", "fr": "Mes classes", "ar": "أقسامي", "tn": "Classes mte3i"}, r"mes classes|my classes|أقسامي"),
    ("/account", {"en": "Your account", "fr": "Votre compte", "ar": "حسابك", "tn": "Compte mte3ek"},
     r"(?:mon|my) (?:compte|account)|compte|account|mot de passe|password|حسابي"),
    ("/guide", {"en": "The guide", "fr": "Le guide", "ar": "الدليل", "tn": "El guide"}, r"guide|aide|help|mode d emploi|الدليل"),
    ("/find", {"en": "Search", "fr": "Recherche", "ar": "البحث", "tn": "Recherche"}, r"recherche|search|البحث"),
]


def _marks_off(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _rx(pattern: str) -> re.Pattern:
    """A word list read on folded words: its own Arabic letters folded the same way (إ → ا, no shadda)."""
    return re.compile(_marks_off(pattern))


_PAGE_WORDS = [(url, names, _rx(rf"(?<![\w])(?:{words})(?![\w])")) for url, names, words in PAGES]

_LEAD = (r"^\W*(?:(?:please|svp|stp|s il (?:te|vous) plait|yasmine|yassmine|yasmin|3aychek|brabi|men fadhlek|من فضلك|ياسمين|"
         r"(?:est ce que )?(?:tu peux|peux tu|pouvez vous|vous pouvez|can you|could you|tnajem|tnajjem|tnajmi))\W+)*")
GO = _rx(_LEAD + r"(?:ouvre(?:\s*moi)?|ouvrez|ouvrir|vas?\s+(?:a|au|aux|sur|vers|dans)|allons\s+(?:a|au|sur|vers)|aller\s+(?:a|au|sur)|"
                r"emmene\s*moi|amene\s*moi|conduis\s*moi|direction|affiche(?:\s*moi)?\s+(?:la\s+)?page|montre\s*moi\s+la\s+page|"
                r"open|go\s+to|take\s+me\s+to|bring\s+me\s+to|navigate\s+to|jump\s+to|show\s+me\s+the\s+page|"
                r"7ell(?:li)?|7el|hell|hel|ddini|eddini|hezni|ehezni|e5edhni|افتح(?:ي)?(?:\s+لي)?|خذني|خذيني|اذهب(?:ي)?\s+إلى|انتقل(?:ي)?\s+إلى|وديني)"
                r"(?![\w])")
WRITE = _rx(_LEAD + r"(?:prepare(?:\s*moi)?|redige|draft|ouvre(?:\s*moi)?\s+un|open\s+an?|7ell(?:li)?|7adherli|7adharli)\s*"
                   r"(?:un|une|an?|the|le)?\s*(?:e ?mail|mail|message|courriel)\s+(?:a|au|pour|to|for|l)\s+(?P<who>.+)$")
BACK = _rx(_LEAD + r"(?:reviens(?:\s+en\s+arriere)?|retour(?:ne)?(?:\s+en\s+arriere)?|page\s+precedente|go\s+back|back|previous\s+page|"
                  r"rja3|arja3|ارجع(?:ي)?|رجوع)\W*$")
TOUR = _rx(r"(?<![\w])(?:fais\s*moi\s+(?:visiter|faire\s+le\s+tour|le\s+tour)|visite\s+(?:guidee|de\s+l application|des\s+pages)|"
                  r"fai(?:s|re)\s+le\s+tour(?:\s+des\s+pages)?|show\s+me\s+around|give\s+me\s+a\s+tour|tour\s+of\s+the\s+(?:app|application|pages)|"
                  r"walk\s+me\s+through|visit\s+(?:all\s+)?the\s+pages|go\s+(?:around|through)\s+(?:all\s+)?the\s+pages|"
                  r"warrini\s+el\s+(?:application|app|pages)|dourni\s+fil\s+pages|jawla|جولة|عرفيني\s+بالتطبيق|وريني\s+التطبيق)(?![\w])")
AUTO = _rx(r"(?<![\w])(?:automatique(?:ment)?|tout\s+seule?|toute\s+seule|automatic(?:ally)?|by\s+yourself|on\s+your\s+own|wa7dek|wahdek|وحدك|تلقائي\w*)(?![\w])")
_ARTICLES = _rx(r"^(?:(?:la|le|les|l|du|de|des|d|the|page|pages|el|l|fiche|profil|profile|file|dossier|of|du|au|aux|sur|a|mon|ma|mes|my|"
                       r"صفحة)\s+)+")
_CLASS_PARTS = {"course": r"cours|course|lessons?|lecons?|support", "attendance": r"presences?|attendance|appel|absences?|feuille"}

SAY = {
    "fr": {"going": "Je vous emmène : {name} 🚀", "here": "Nous y sommes : {name}.", "back": "Je reviens à la page d'avant.",
           "tour": "C'est parti pour la visite ! Je vous montre chaque page, l'une après l'autre.",
           "tour_auto": "C'est parti ! Je passe d'une page à l'autre toute seule ; « Arrêter la visite » l'arrête à tout moment.",
           "which": "Quelle page voulez-vous ouvrir ? Voici les principales :", "not_allowed": "Cette page ne vous est pas ouverte : demandez à l'admin.",
           "nobody": "Je ne trouve personne qui s'appelle « {name} ». Je vous ouvre la page des emails : choisissez la personne là-bas.",
           "compose": "J'ouvre l'email pour {name}, déjà rempli : relisez, puis appuyez sur Envoyer. Rien ne part sans vous.",
           "compose_empty": "J'ouvre l'email pour {name} : écrivez votre message, puis appuyez sur Envoyer."},
    "en": {"going": "Taking you there: {name} 🚀", "here": "Here we are: {name}.", "back": "Going back to the page before.",
           "tour": "Let's start the visit! I'll show you each page, one after the other.",
           "tour_auto": "Off we go! I'll move from page to page by myself; “Stop the visit” ends it at any time.",
           "which": "Which page do you want to open? Here are the main ones:", "not_allowed": "That page is not open to you: ask the admin.",
           "nobody": "I can't find anyone called “{name}”. I'm opening the email page: pick the person there.",
           "compose": "Opening the email to {name}, already written: read it, then press Send. Nothing goes without you.",
           "compose_empty": "Opening the email to {name}: write your message, then press Send."},
    "ar": {"going": "سآخذك إلى: {name} 🚀", "here": "ها نحن في: {name}.", "back": "أعود إلى الصفحة السابقة.",
           "tour": "لنبدأ الجولة! سأريك كل صفحة، الواحدة تلو الأخرى.",
           "tour_auto": "هيا بنا! سأنتقل من صفحة إلى أخرى وحدي؛ «إيقاف الجولة» يوقفها في أي وقت.",
           "which": "أي صفحة تريد أن تفتح؟ هذه أهمها:", "not_allowed": "هذه الصفحة غير مفتوحة لك: اطلبها من المسؤول.",
           "nobody": "لم أجد أحدا اسمه «{name}». سأفتح صفحة الرسائل: اختر الشخص هناك.",
           "compose": "أفتح الرسالة إلى {name}، مكتوبة: اقرأها ثم اضغط إرسال. لا يُرسل شيء دونك.",
           "compose_empty": "أفتح الرسالة إلى {name}: اكتب رسالتك ثم اضغط إرسال."},
    "tn": {"going": "Taw nhezzek: {name} 🚀", "here": "Hana wselna: {name}.", "back": "Nraja3 lel page elli 9bal.",
           "tour": "Yalla nebdew el visite! Nwarrik kol page, wa7da ba3d wa7da.",
           "tour_auto": "Yalla! Net3adda men page l page wa7di; « Ywa9ef el visite » ywa9fha wa9t ma t7eb.",
           "which": "Ama page t7eb t7ell? Hedhom el mohemmin:", "not_allowed": "El page hedhi mahich m7oula lik: otlobha mel admin.",
           "nobody": "Ma l9itch 7ad esmou « {name} ». Taw n7ellek page mta3 el emails: ekhtar el chakhs ghadi.",
           "compose": "Taw n7ell l'email l {name}, maktoub: a9rah, ba3d ekbes Envoyer. 7ata chay ma yemchi blech bik.",
           "compose_empty": "Taw n7ell l'email l {name}: ekteb el message, ba3d ekbes Envoyer."},
}
_MAIN = ["/", "/candidate/all", "/classes/", "/payments", "/inbox", "/programs/all", "/reminders", "/settings",
         "/teacher", "/teacher/profile", "/account/messages", "/account", "/guide"]


def _fold(text: str) -> str:
    """Lower case, no accents (Arabic letters kept), words apart: what the word lists read."""
    return " ".join(re.sub(r"[^\w]+", " ", _marks_off(text).lower()).replace("_", " ").split())


def _lang(lang: str | None) -> str:
    return lang if lang in SAY else "fr"


def wants(message: str) -> bool:
    """A move: open a page or someone's file, go back, the visit, an email to prepare."""
    text = _fold(message)
    if not text or len(text) > 220 or re.search(r"(?<![\w])pdf(?![\w])", text):
        return False
    return bool(BACK.match(text) or WRITE.match(text) or TOUR.search(text) or GO.match(text))


def may_open(db: Session, user, url: str) -> bool:
    """Whether this person may open that address (the same rules as the pages themselves)."""
    from app.services import access, auth

    path = url.split("?", 1)[0].split("#", 1)[0] or "/"
    if user is None or getattr(user, "is_admin", False):
        return True
    if getattr(user, "is_professor", False):
        return auth.professor_may_open(db, user, path)
    return access.allows(access.clean(getattr(user, "permissions", None)), path, "GET")


_NAMES_PROGRAM = re.compile(r"(?<![\w])(?:programmes?|programs?|formations?|trainings?)(?![\w])")
_NAMES_MESSAGES = re.compile(r"(?<![\w])(?:e ?mails?|mails?|messages?|conversations?|discussions?|courriels?)(?![\w])")


def _record(db: Session, text: str) -> tuple[str, str] | None:
    """(address, name) of the class, program, candidate or professor the words name."""
    from app.models import ClassGroup, Professor
    from app.services import chatbot

    words = set(text.split())
    program = chatbot._program_in(db, chatbot._plain(text))
    if program is not None and _NAMES_PROGRAM.search(text):
        return f"/programs/{program.id}", program.name           # "le programme IoT": the program, not a class called IoT
    for group in db.query(ClassGroup).all():
        name = _fold(group.name)
        if name and (name in words or (" " in name and f" {name} " in f" {text} ")):
            for part, pattern in _CLASS_PARTS.items():
                if re.search(rf"(?<![\w])(?:{pattern})(?![\w])", text):
                    return f"/classes/{group.id}/{part}", group.name
            return f"/classes/{group.id}", group.name
    if program is not None:
        if re.search(r"(?<![\w])(?:fichiers?|files?)(?![\w])", text):
            return f"/programs/{program.id}/files", program.name
        return f"/programs/{program.id}", program.name
    person = chatbot._candidate_in(db, chatbot._plain(text))
    if person is not None:
        if _NAMES_MESSAGES.search(text):
            return f"/inbox?candidate={person.id}", person.full_name or person.email     # "ouvre les emails de Fares"
        return f"/candidate/{person.id}", person.full_name or person.email
    for professor in db.query(Professor).all():
        parts = [part for part in _fold(professor.name).split() if len(part) >= 3]
        if parts and all(part in words for part in parts):
            return "/classes/?tab=professors", professor.name
    return None


def _page(text: str, lang: str) -> tuple[str, str] | None:
    for url, names, pattern in _PAGE_WORDS:
        if pattern.search(text):
            return url, names[lang]
    return None


def _arrived(db: Session, url: str, name: str, lang: str) -> str:
    """What she says once there: the page's name and what it is for (its tour), in her language."""
    from app.services import page_tours, translations

    said = SAY[lang]["here"].format(name=name)
    tour = page_tours.for_path(url.split("?", 1)[0].split("#", 1)[0])
    if tour is None or not tour.get("purpose"):
        return said
    if lang == "en":
        return f"{said} {tour['purpose']}"
    try:
        words = translations.dictionary(db, "fr" if lang == "tn" else lang)["t"]
    except Exception:
        words = {}
    purpose = words.get(tour["purpose"])
    return f"{said} {purpose}" if purpose else said


def _go(db: Session, user, url: str, name: str, lang: str) -> dict:
    if not may_open(db, user, url):
        return {"reply": SAY[lang]["not_allowed"], "links": [], "source": "guide", "sources": [], "resources": []}
    return {"reply": SAY[lang]["going"].format(name=name), "navigate": {"url": url, "label": name, "arrived": _arrived(db, url, name, lang)},
            "went": {"url": url, "label": name}, "links": [], "source": "guide", "sources": [], "resources": [], "scene": "link"}


def _compose(db: Session, user, name: str, words: str, lang: str) -> dict:
    """The email page for that person, with the words given already written (the admin reads and presses Send)."""
    from urllib.parse import urlencode

    from app.services import chatbot

    name = name.strip(" ,.!?")
    person = chatbot._candidate_in(db, chatbot._plain(name))
    if person is None:
        found = _go(db, user, "/inbox/compose", next(names[lang] for url, names, _ in PAGES if url == "/inbox/compose"), lang)
        if found.get("navigate"):
            found["reply"] = SAY[lang]["nobody"].format(name=name[:60])
        return found
    query = {"candidate": person.id}
    if words:
        query["body"] = words[:3000]
    found = _go(db, user, "/inbox/compose?" + urlencode(query), person.full_name or person.email, lang)
    if found.get("navigate"):
        first = (person.full_name or person.email).split()[0]
        found["reply"] = SAY[lang]["compose" if words else "compose_empty"].format(name=first)
    return found


def answer(db: Session, message: str, user=None, lang: str | None = None) -> dict:
    """The move asked, or the question back "which page?" with the main ones to choose."""
    lang = _lang(lang)
    raw = re.sub(r"[’`]", "'", message or "")
    text = _fold(raw)
    if BACK.match(text):
        return {"reply": SAY[lang]["back"], "back": True, "links": [], "source": "guide", "sources": [], "resources": [], "scene": "link"}
    # An email to prepare: the name is read from the folded words, the words to write are kept as typed (accents, capitals).
    from app.services import chatbot_send

    parts = chatbot_send._SEPARATOR.split(raw, maxsplit=1)
    written = WRITE.match(_fold(parts[0]))
    if written:
        return _compose(db, user, written.group("who"), parts[1].strip() if len(parts) > 1 else "", lang)
    if TOUR.search(text):
        auto = bool(AUTO.search(text))
        return {"reply": SAY[lang]["tour_auto" if auto else "tour"], "tour": {"auto": auto}, "links": [], "source": "guide",
                "sources": [], "resources": [], "scene": "assistant"}
    going = GO.match(text)
    rest = _ARTICLES.sub("", text[going.end():].strip()) if going else text
    target = _record(db, rest) or _page(rest, lang) if rest else None
    if target is None:
        names = {url: names[lang] for url, names, _ in PAGES}
        options = [names[url] for url in _MAIN if may_open(db, user, url)][:8]
        return {"reply": SAY[lang]["which"], "options": options, "links": [], "source": "guide", "sources": [], "resources": [], "scene": "link"}
    return _go(db, user, target[0], target[1], lang)
