"""Yasmine talks like a person.

She answers "how are you", a greeting, thanks, "who are you", a goodbye, a compliment, how someone feels, in the
language they wrote in (French, English, Arabic, Tunisian in Latin letters), with their first name, "tu" or "vous" as
they used it, and the time of day. She tells a social sentence from a real question: "ça va ?" is small talk, but
"ça va les paiements ?" or "comment vont les paiements ?" asks about payments. A message with both ("Salut ! combien
ont payé ?") gets both: a word back, then the answer.

The sentences are written by hand and chosen among a few, so she does not repeat herself, but never by a model: she
never answers that she is "only an assistant without feelings". An order to send or write something ("envoie à Ahmed :
bonjour, ça va ?") is never read as small talk: those words belong to the message.
"""
from __future__ import annotations

import random
import re
from datetime import datetime

from sqlalchemy.orm import Session

# The social parts of a message, in the order they are looked for. A greeting counts only at the start.
_PATTERNS = {
    "greeting": re.compile(
        r"^\W*(bonjour|bonsoir|salut|coucou|hello|hi|hey|good (?:morning|afternoon|evening)|aslema|ahla|ahlan|marhba|marhaba|"
        r"sba7 el khir|saba7 el khir|mse el khir|salam|slm|السلام عليكم|سلام|مرحبا|اهلا|أهلا|صباح الخير|مساء الخير)\b[\s,!.]*", re.IGNORECASE),
    "im_fine": re.compile(
        r"\b(je vais (?:très |tres )?bien|(?:ça|ca) va (?:très |tres )?bien|bien merci|i'?m (?:fine|good|great|well)|labes el 7amdoulillah|"
        r"labes 7amdoulillah|ena labes|ena behi|الحمد لله بخير|بخير الحمد لله|أنا بخير|انا بخير)\b", re.IGNORECASE),
    "how_are_you": re.compile(
        r"\b(comment (?:vas[- ]tu|tu vas|allez[- ]vous|(?:ça|ca) va)|tu vas bien|vous allez bien|(?:ça|ca) va(?=\s*(?:[?!.,;:]|$|et\s+(?:toi|vous)|toi\b))|"
        r"how are you|how r u|how'?s it going|how is it going|how are things|are you ok|labes(?:s)?(?=\s*(?:[?!.,]|$))|labas|chnowa a7welek|"
        r"chna7welek|chnahwalek|chniya a7welek|chnia ahwalek|kifek|kifech 7alek|ça roule|ca roule)"
        r"|كيف حالك|كيفك|شنحوالك|شنية أحوالك|لاباس", re.IGNORECASE),
    "who_are_you": re.compile(
        r"\b(tu es qui|t'es qui|qui es[- ]tu|qui êtes[- ]vous|qui etes[- ]vous|who are you|what are you|chkoun enti|chkun enti|enti chkoun|"
        r"comment tu t'appelles|tu t'appelles comment|what'?s your name)\b|شكون انتي|من أنت|من انت|ما اسمك", re.IGNORECASE),
    "thanks": re.compile(
        r"\b(merci(?!\s+d[e'])(?: beaucoup| bien| infiniment)?|thanks(?: a lot)?|thank you(?: so much)?|thx|y3aychek|choukran|chokran|"
        r"barcha merci)\b|شكرا|شكرًا|يعيشك", re.IGNORECASE),
    "compliment": re.compile(
        r"\b(tu es (?:géniale?|geniale?|super|top|parfaite?|la meilleure|le meilleur|gentille?)|t'es (?:géniale?|top|la meilleure)|bravo|"
        r"good job|well done|you'?re (?:great|amazing|the best|awesome)|ya3tik sa77a|behya barcha)\b|برافو|يعطيك الصحة", re.IGNORECASE),
    "feeling_bad": re.compile(
        r"\b(?:je suis|je me sens|i am|i'm|i feel|ena)\s+(?:très |tres |trop |so |very |barcha )?"
        r"(fatigu\w*|crev\w*|épuis\w*|epuis\w*|stress\w*|débord\w*|debord\w*|triste|tired|exhausted|stressed|overwhelmed|sad|ta3ban\w*|"
        r"m9ala9|mkala9|mcharbek)\b|تعبان|تعبانة|مقلق|مقلقة", re.IGNORECASE),
    "feeling_good": re.compile(
        r"\b(?:je suis|je me sens|i am|i'm|i feel|ena)\s+(?:très |tres |trop |so |very |barcha )?"
        r"(content\w*|heureu\w*|ravie?|happy|great|fer7an\w*|far7an\w*|mabsout\w*)\b|فرحان|مبسوط", re.IGNORECASE),
    "sorry": re.compile(r"\b(désolée?|desolee?|pardon|sorry|sama7ni|smahli)\b|سامحني|آسف|اسف", re.IGNORECASE),
    "goodbye": re.compile(
        r"\b(au revoir|bye(?: bye)?|à demain|a demain|bonne (?:nuit|soirée|soiree|journée|journee)|good ?night|see you|beslema|bislema|"
        r"tsba7 3la khir|ciao)\b|مع السلامة|تصبح على خير|بالسلامة", re.IGNORECASE),
    "laugh": re.compile(r"\b(ha(?:ha)+|hi(?:hi)+|lol|mdr|ptdr)\b|😂|🤣|😄|😆", re.IGNORECASE),
}
# An order to send or write: its words are the message's, never small talk.
_ORDER = re.compile(r"\b(envoie|envoyer|envoyez|écris|ecris|écrivez|send|write|dis[- ]lui|dis à|dis a|tell|ab3ath|ekteb|rappelle|répond|reponds|reply)\b",
                    re.IGNORECASE)
# Words that tell the language of a short social message (the general reader needs longer sentences).
_FRENCH = re.compile(r"\b(salut|bonjour|bonsoir|coucou|merci|bonne|ça|ca|tu|vous|toi|comment|désolée?|desolee?|je|suis|qui|es|revoir|demain|bravo|très|tres|bien|contente?|géniale?)\b", re.IGNORECASE)
_ENGLISH = re.compile(r"\b(hello|hi|hey|how|thanks|thank|you|good|night|morning|evening|bye|sorry|who|are|i'?m|great|fine)\b", re.IGNORECASE)
_TUNISIAN = re.compile(r"\b(aslema|ahla|labes|labess|chnowa|chniya|a7welek|chna7welek|y3aychek|beslema|bislema|ena|enti|behi|barcha|sba7|mse|tsba7|ta3ban\w*|far7an\w*|sama7ni|chkoun|kifek|khallas)\b", re.IGNORECASE)
_ARABIC = re.compile("[؀-ۿ]")
# Words around a social part that do not make a question by themselves ("je suis fatiguée aujourd'hui").
_AROUND = {"aujourd", "hui", "aujourd'hui", "today", "ce", "soir", "matin", "un", "peu", "vraiment", "trop", "beaucoup", "bit",
           "so", "very", "really", "barcha", "chwaya", "maintenant", "now", "encore", "quand", "même", "meme", "el", "youm",
           "lyoum", "tonight", "again", "too", "aussi", "also", "zeda", "et", "and", "mais", "but", "oui", "yes", "non", "no",
           "ok", "okay", "alors", "bon", "ben", "là", "la"}
# What is left around the social parts that does not make a question.
_FILLER = re.compile(r"\b(et toi|et vous|toi|vous|ma chère|ma chere|dear|please|stp|svp|ya|w enti|enti|and you|you)\b|[^\w؀-ۿ]+",
                     re.IGNORECASE)


def read(text: str, name: str | None = None) -> dict:
    """{"kinds": the social parts found, "question": what is asked besides (or ""), "only": nothing but small talk,
    "formal": they said "vous", "lang": the language they wrote in}."""
    from app.services import howto

    text = (text or "").strip()
    found = {"kinds": [], "question": text, "only": False, "formal": bool(re.search(r"\bvous\b|allez-vous", text, re.IGNORECASE)),
             "lang": language_of(text) if text else "fr", "text": text}
    if not text or _ORDER.search(text) or len(text) > 400:
        return found
    rest = text
    for kind, pattern in _PATTERNS.items():
        match = pattern.search(rest)
        if match:
            found["kinds"].append(kind)
            rest = rest[:match.start()] + " " + rest[match.end():]
    if not found["kinds"]:
        return found
    if "im_fine" in found["kinds"] and re.search(r"\b(et toi|et vous|and you|w enti)\b|وأنت|و انت", text, re.IGNORECASE):
        found["kinds"].append("me_too")             # "ça va bien, et toi ?": she says how she is, without asking back
    for called in {name or "", "yasmine", "yassmine", "yasmin"} - {""}:
        rest = re.sub(rf"\b{re.escape(called)}\b", " ", rest, flags=re.IGNORECASE)
    leftover = _FILLER.sub(" ", rest).strip()
    meaningful = [word for word in re.findall(r"[A-Za-zÀ-ÿ؀-ۿ']{2,}", leftover) if word.lower() not in _AROUND]
    # A question is left when it asks (a question mark and a word) or says enough (two words that are not filler).
    asks = bool(meaningful) and bool(re.search(r"[?؟]", rest))
    question = rest.strip(" ,;:!.-") if (asks or len(meaningful) >= 2) else ""
    found.update(question=question, only=not question)
    return found


# ------------------------------------------------------------------
# What she says, in each language: {kind: [variants]}; {me} her name, {name} theirs, {tu|vous} as they spoke
# ------------------------------------------------------------------

_SAY = {
    "fr": {
        "how_are_you": ["Je vais très bien, merci{, name} ! {Et toi ?|Et vous ?}",
                        "Ça va super bien, merci de demander{, name} 😊 {Et toi, ça va ?|Et vous, comment allez-vous ?}",
                        "Très bien, merci{, name} ! {Ça fait plaisir de te voir.|Ça fait plaisir de vous voir.} {Et toi ?|Et vous ?}"],
        "me_too": ["Moi aussi, très bien, merci 😊 {Qu'est-ce que je peux faire pour toi ?|Que puis-je faire pour vous ?}",
                   "Ça va bien de mon côté aussi, merci{, name} !"],
        "im_fine": ["Tant mieux, ça fait plaisir 😊 {Qu'est-ce que je peux faire pour toi ?|Que puis-je faire pour vous ?}",
                    "Super ! {Dis-moi si tu as besoin de quelque chose.|Dites-moi si vous avez besoin de quelque chose.}"],
        "greeting": ["{hello}{ name} ! 😊 {Qu'est-ce que je peux faire pour toi ?|Que puis-je faire pour vous ?}",
                     "{hello}{ name} ! {Ça fait plaisir de te voir.|Ça fait plaisir de vous voir.}"],
        "who_are_you": ["Je suis {me}, {ton|votre} {assistant} à 9antra 😊 Je connais les candidats, les classes, les paiements et les emails : "
                        "{pose-moi|posez-moi} une question, ou {demande-moi|demandez-moi} un email, un PDF ou un réglage."],
        "thanks": ["Avec plaisir{, name} !", "{Je t'en prie|Je vous en prie} 😊", "Toujours là pour {toi|vous} !"],
        "compliment": ["Oh merci, ça me touche ! 😊", "Merci ! On forme une belle équipe."],
        "feeling_bad": ["Oh, courage{, name} 💛 {Prends|Prenez} une petite pause si {tu peux|vous pouvez}.",
                        "Je comprends… {Respire|Respirez}, on avance une chose à la fois."],
        "feeling_good": ["Ça fait plaisir à entendre ! 😄", "Super, j'adore ça ! 😊"],
        "sorry": ["Pas de souci 😊", "Aucun problème !"],
        "goodbye_night": ["Bonne nuit{, name}, {repose-toi|reposez-vous} bien 🌙"],
        "goodbye": ["À bientôt{, name} ! Bonne {day} 👋", "À tout à l'heure{, name} ! 👋"],
        "laugh": ["😄", "Haha 😄"],
        "hello": {"morning": "Bonjour", "afternoon": "Bonjour", "evening": "Bonsoir"},
        "assistant": {"female": "assistante", "male": "assistant"},
        "day": {"day": "journée", "evening": "soirée"},
        "bridge": "Côté travail : {line}", "pressing": "Le plus pressé : {line} Le reste peut attendre.",
        "prefix": {"greeting": "{hello}{ name} !", "how_are_you": "Je vais bien, merci !", "im_fine": "Tant mieux !", "thanks": "Avec plaisir !",
                   "feeling_bad": "Courage 💛", "feeling_good": "Ça fait plaisir !", "compliment": "Merci 😊", "sorry": "Pas de souci."},
    },
    "en": {
        "how_are_you": ["I'm doing really well, thanks{, name}! And you?", "All good here, thanks for asking{, name} 😊 How are you?",
                        "Great, thanks{, name}! Happy to see you. How about you?"],
        "me_too": ["I'm good too, thanks 😊 What can I do for you?", "All good on my side too, thanks{, name}!"],
        "im_fine": ["Glad to hear it 😊 What can I do for you?", "Great! Tell me if you need anything."],
        "greeting": ["{hello}{, name}! 😊 What can I do for you?", "{hello}{, name}! Good to see you."],
        "who_are_you": ["I'm {me}, your assistant at 9antra 😊 I know the candidates, classes, payments and emails: ask me a question, "
                        "or ask me for an email, a PDF or a setting."],
        "thanks": ["My pleasure{, name}!", "You're welcome 😊", "Always here for you!"],
        "compliment": ["Oh, thank you, that's kind! 😊", "Thanks! We make a good team."],
        "feeling_bad": ["Oh, hang in there{, name} 💛 Take a short break if you can.", "I understand… one thing at a time."],
        "feeling_good": ["Love to hear it! 😄", "That's great! 😊"],
        "sorry": ["No worries 😊", "No problem at all!"],
        "goodbye_night": ["Good night{, name}, rest well 🌙"],
        "goodbye": ["See you soon{, name}! Have a nice {day} 👋", "Bye{, name}! 👋"],
        "laugh": ["😄", "Haha 😄"],
        "hello": {"morning": "Good morning", "afternoon": "Hello", "evening": "Good evening"},
        "assistant": {"female": "assistant", "male": "assistant"},
        "day": {"day": "day", "evening": "evening"},
        "bridge": "On the work side: {line}", "pressing": "The most pressing: {line} The rest can wait.",
        "prefix": {"greeting": "{hello}{, name}!", "how_are_you": "I'm well, thanks!", "im_fine": "Glad to hear it!", "thanks": "My pleasure!",
                   "feeling_bad": "Hang in there 💛", "feeling_good": "Love to hear it!", "compliment": "Thank you 😊", "sorry": "No worries."},
    },
    "ar": {
        "how_are_you": ["أنا بخير، شكرًا{ name}! وأنت؟", "الحمد لله، كل شيء على ما يرام{ name} 😊 وأنت، كيف حالك؟"],
        "me_too": ["وأنا أيضًا بخير، شكرًا 😊 كيف أستطيع مساعدتك؟"],
        "im_fine": ["الحمد لله، يسعدني ذلك 😊 كيف أستطيع مساعدتك؟", "رائع! أنا هنا إن احتجت شيئًا."],
        "greeting": ["{hello}{ name}! 😊 كيف أستطيع مساعدتك؟", "{hello}{ name}! يسعدني أن أراك."],
        "who_are_you": ["أنا {me}، {assistant} في 9antra 😊 أعرف المترشحين والأقسام والمدفوعات والرسائل: يمكنك أن تسألني، أو أن تطلب مني رسالة أو ملف PDF أو تغيير إعداد."],
        "thanks": ["بكل سرور{ name}!", "العفو 😊", "أنا دائمًا هنا من أجلك!"],
        "compliment": ["شكرًا، هذا لطيف منك! 😊", "شكرًا! نحن فريق رائع."],
        "feeling_bad": ["شجاعة{ name} 💛 ربما تفيدك استراحة قصيرة.", "أفهمك… خطوة بخطوة."],
        "feeling_good": ["يسعدني سماع ذلك! 😄", "رائع! 😊"],
        "sorry": ["لا عليك 😊", "لا مشكلة أبدًا!"],
        "goodbye_night": ["ليلة سعيدة{ name} 🌙"],
        "goodbye": ["إلى اللقاء{ name}! 👋", "مع السلامة{ name}! 👋"],
        "laugh": ["😄"],
        "hello": {"morning": "صباح الخير", "afternoon": "أهلًا", "evening": "مساء الخير"},
        "assistant": {"female": "مساعدتك", "male": "مساعدك"},
        "day": {"day": "", "evening": ""},
        "bridge": "في العمل: {line}", "pressing": "الأكثر استعجالًا: {line} والباقي يمكنه الانتظار.",
        "prefix": {"greeting": "{hello}{ name}!", "how_are_you": "أنا بخير، شكرًا!", "im_fine": "الحمد لله!", "thanks": "بكل سرور!",
                   "feeling_bad": "شجاعة 💛", "feeling_good": "يسعدني ذلك!", "compliment": "شكرًا 😊", "sorry": "لا عليك."},
    },
    "tn": {
        "how_are_you": ["Labes, el 7amdoulillah{ name}! W enti, chniya a7welek?", "Ena behya barcha, y3aychek{ name} 😊 W enti labes?"],
        "me_too": ["W ena zeda labes, y3aychek 😊 Chnowa najem na3mellek?"],
        "im_fine": ["El 7amdoulillah, ferr7tni 😊 Chnowa najem na3mellek?", "Behi barcha! Ken t7eb 7aja 9olli."],
        "greeting": ["{hello}{ name}! 😊 Chnowa najem na3mellek?", "{hello}{ name}! Yefra7 bik."],
        "who_are_you": ["Ena {me}, el {assistant} mte3ek fi 9antra 😊 Na3ref el candidats, el classes, el flous w el emails: es2elni, "
                        "wala otlob menni email, PDF wala tbadel réglage."],
        "thanks": ["Bel 3afya{ name}!", "Ma3leh, dima hna 😊"],
        "compliment": ["Y3aychek, ferr7tni! 😊", "Y3aychek! A7na équipe behya."],
        "feeling_bad": ["Bon courage{ name} 💛 Odhrob pause sghira ken tnajem.", "Nefhmek… chwaya b chwaya."],
        "feeling_good": ["Ya rabbi dima! 😄", "Behi barcha! 😊"],
        "sorry": ["Ma famma 7ata mochkla 😊", "Ma3lich!"],
        "goodbye_night": ["Tsba7 3la khir{ name} 🌙"],
        "goodbye": ["Beslema{ name}! 👋", "Netla9aw 9rib{ name}! 👋"],
        "laugh": ["😄"],
        "hello": {"morning": "Sba7 el khir", "afternoon": "Aslema", "evening": "Mse el khir"},
        "assistant": {"female": "assistante", "male": "assistant"},
        "day": {"day": "", "evening": ""},
        "bridge": "Fil khedma: {line}", "pressing": "El aktar urgent: {line} El be9i yestanna.",
        "prefix": {"greeting": "{hello}{ name}!", "how_are_you": "Labes, y3aychek!", "im_fine": "El 7amdoulillah!", "thanks": "Bel 3afya!",
                   "feeling_bad": "Courage 💛", "feeling_good": "Ya rabbi dima!", "compliment": "Y3aychek 😊", "sorry": "Ma3lich."},
    },
}
# The kind that leads the answer when a message has several (a greeting and "how are you": she answers how she is).
_LEAD = ("who_are_you", "feeling_bad", "how_are_you", "me_too", "im_fine", "goodbye", "feeling_good", "compliment", "thanks", "sorry", "greeting", "laugh")


def language_of(text: str) -> str:
    """fr, en, ar or tn: Arabic letters, then Tunisian, French and English small-talk words, then the general reader."""
    from app.services import howto

    if _ARABIC.search(text or ""):
        return "ar"
    scores = {"tn": len(_TUNISIAN.findall(text or "")), "fr": len(_FRENCH.findall(text or "")), "en": len(_ENGLISH.findall(text or ""))}
    best = max(scores, key=lambda lang: (scores[lang], lang == "fr"))
    return best if scores[best] else howto.language_of(text)


def _part_of_day(hour: int) -> str:
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def _fill(text: str, *, me: str, name: str, formal: bool, hello: str, assistant: str, day: str) -> str:
    # {tu|vous}: the first when they said "tu" (or nothing), the second when they said "vous".
    text = re.sub(r"\{([^{}|]*)\|([^{}|]*)\}", lambda match: match.group(2) if formal else match.group(1), text)
    text = text.replace("{, name}", f", {name}" if name else "").replace("{ name}", f" {name}" if name else "")
    return text.replace("{me}", me).replace("{hello}", hello).replace("{assistant}", assistant).replace("{day}", day)


def _context(db: Session, user, lang: str) -> dict:
    from app.services import friend
    from app.services.schedule_utils import local_now

    look = friend.look(db)
    now = local_now()
    words = _SAY[lang]
    first = ((getattr(user, "name", None) or "").split() or [""])[0].capitalize()
    return {"me": look.get("name") or friend.NAME, "name": first, "hello": words["hello"][_part_of_day(now.hour)],
            "assistant": words["assistant"].get(look.get("gender") or "female", words["assistant"]["female"]),
            "day": words["day"]["evening" if now.hour >= 18 else "day"], "now": now}


def _waiting(db: Session, user, lang: str, now) -> str | None:
    """The first thing waiting today, for the admin and the team (never for a professor)."""
    from app.services import friend

    if user is not None and (getattr(user, "is_professor", False) or not (getattr(user, "is_admin", False) or getattr(user, "is_staff", False))):
        return None
    from app.services import ui_words

    token = ui_words.use({"tn": "fr"}.get(lang, lang))        # what the advisor says, in the reply's language
    try:
        lines = friend.lines_for_admin(db, friend._WORDS[lang], datetime.utcnow(), now)
    except Exception:
        return None
    finally:
        ui_words.current.reset(token)
    return lines[0]["text"] if lines else None


def answer(db: Session, social: dict, *, user=None, lang: str | None = None) -> dict:
    """Her answer to a message that is only small talk."""
    lang = lang if lang in _SAY else (social.get("lang") if social.get("lang") in _SAY else "fr")
    words, context = _SAY[lang], _context(db, user, lang)
    kind = next((kind for kind in _LEAD if kind in social["kinds"]), "greeting")
    if kind == "goodbye" and re.search(r"nuit|night|tsba7|تصبح", social.get("text") or "", re.IGNORECASE):
        kind = "goodbye_night"
    rng = random.Random(f"{social.get('text')}|{context['now']:%Y-%m-%d}")       # varies, but the same words get the same answer today
    said = _fill(rng.choice(words[kind]), me=context["me"], name=context["name"], formal=social.get("formal", False),
                 hello=context["hello"], assistant=context["assistant"], day=context["day"])
    if "greeting" in social["kinds"] and kind not in ("greeting", "goodbye", "goodbye_night"):
        said = context["hello"] + (" ! " if lang == "fr" else "! ") + said         # "Bonjour ! Je vais très bien…"
    if kind in ("how_are_you", "greeting", "feeling_bad", "im_fine", "me_too"):
        line = _waiting(db, user, lang, context["now"])
        if line:
            said += "\n\n" + (words["pressing"] if kind == "feeling_bad" else words["bridge"]).format(line=line)
    return {"reply": said, "links": [], "source": "talk", "sources": [], "resources": [], "language": lang}


def prefix(db: Session, social: dict, *, user=None, lang: str | None = None) -> str:
    """The word back that comes before the answer, when a message has small talk and a question."""
    lang = lang if lang in _SAY else (social.get("lang") if social.get("lang") in _SAY else "fr")
    words, context = _SAY[lang], _context(db, user, lang)
    kind = next((kind for kind in _LEAD if kind in social["kinds"] and kind in words["prefix"]), None)
    if kind is None:
        return ""
    return _fill(words["prefix"][kind], me=context["me"], name=context["name"], formal=social.get("formal", False),
                 hello=context["hello"], assistant=context["assistant"], day=context["day"])
