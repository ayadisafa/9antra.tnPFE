"""The assistant sends a message for the admin: "envoie un email à Fares pour lui dire que la séance de samedi est
annulée", "send a WhatsApp to Nour saying …", "sms au 98 123 456 : …", "write to amira@example.com that …".

Who: a candidate, a professor, a team member (by name), or an email address or a phone number written in the message.
How: what the message says (email, WhatsApp, SMS), otherwise the way chosen above the inbox (with the next way when
one does not work) for a candidate, the application and email for the team.
What: the admin's words, reworded in French or English (the language asked, French otherwise) by the rewording of the
inbox, which never changes a figure. "tel quel" / "as is" sends the admin's own words.

When the admin's switch "Send at once" is on (the default), it goes at once and the answer says, for each person and each
way, what went and, when something did not, why (no address, the number cannot be read, no Business number, no Android
phone, the mail server refused…). When it is off, nothing goes: the text is ready, with one tap to send it. A name
that matches nobody is asked.

The order may be said any way, in any of the four languages, without "that" or "pour lui dire": "write to ahmed winik",
"ekteb l ahmed winik", "9oul l fares ija ghodwa", "اكتب لأحمد وينك", "écris à ahmed où es-tu", "tell him we start at 9".
Who it is for is read from the names in it (Arabic letters too, by their consonants: أحمد is Ahmed); the rest is what to
say. "him", "lui", "ekteblou" is the person whose page is open, or the one just talked about. When several people have
the name, the one whose page is open is taken, or the one chosen for that name a moment ago (said in the answer);
otherwise she asks, with one tap per person, and goes on with the order once chosen. Without what to say ("écris à
Ahmed"), the email opens with a few things to say to them, one tap each (discussion_help).
"""
from __future__ import annotations

import html
import re
import time
import unicodedata
from datetime import datetime

from sqlalchemy.orm import Session

SEND_START = re.compile(
    r"^\s*(?:yasmine\s*,?\s*)?(?:please |svp |stp |peux[- ]tu |can you |could you )?(?:send|envoie|envoyez|envoyer|envoi|ecris|ecrire|écris|écrire|write|"
    r"dis|tell|informe|previens|préviens|ab3ath|ob3eth|message|msg|dm|texte|text|ask|demande|(?:sms|whats ?app|e-?mail|mail)(?=\s+(?:à|a|au|aux|to)\b))\b", re.I)
# "ask Fares if …", "demande à Fares si …": what to say is a question.
_ASKING = re.compile(r"^\s*(?:yasmine\s*,?\s*)?(?:please |svp |stp |can you |peux[- ]tu )?(ask|demande)\b", re.I)
# "write to him", "écris-lui", "tell them": the person whose page is open, or the one just talked about.
_PRONOUNS = {"him", "her", "them", "lui", "elle", "leur", "eux", "lou", "lha", "lhom"}
_CHANNEL = {"whatsapp": r"\b(whats ?app|wa)\b", "sms": r"\b(sms|texto|text message)\b", "email": r"\b(e-?mail|mail|courriel)\b"}
_SEPARATOR = re.compile(r"\s*(?:pour (?:lui|leur) dire(?: que)?|pour (?:l|les)'?informer(?: que)?|pour dire(?: que)?|en (?:lui )?disant(?: que)?|"
                        r"saying(?: that)?|to say(?: that)?|to tell (?:him|her|them)(?: that)?|that|que|qu'|:)\s+", re.I)
_INTENT = re.compile(r"\s+(?=(?:pour\s+(?:lui|leur|l'|les|le|la)\s*)?(?:rappeler|demander|remercier|inviter|f[ée]liciter|souhaiter|annoncer|"
                     r"pr[ée]venir|encourager|relancer)\b)|\s+(?=to\s+(?:remind|ask|thank|invite|congratulate|wish|encourage)\b)|"
                     r"\s+(?=(?:about|au sujet de|concernant|[àa] propos de)\s)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ]{6,15}\d)(?!\d)")
_AS_IS = re.compile(r"\b(tel quel|telle quelle|sans reformuler|as is|as it is|word for word|mot pour mot)\b", re.I)
_ENGLISH = re.compile(r"\b(in english|en anglais)\b", re.I)
_STRIP = re.compile(r"\b(tel quel|telle quelle|sans reformuler|as is|as it is|word for word|mot pour mot|in english|en anglais|en francais|en français|in french)\b", re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9@.+ ]+", " ", text)


def auto_send(db: Session) -> bool:
    from app.services.settings_service import get_settings

    value = getattr(get_settings(db), "assistant_auto_send", None)
    return True if value is None else bool(value)


# A sending order in Tunisian: “ab3athli (message|sms|whatsapp|mail) l<name> (w) kolo <what>”, “ab3ath sms l amira bech <what>”,
# “ekteb l ahmed winik”, “9oul l fares ija ghodwa”, “es2el amira ken tji”. The ending of the verb may say to whom: “ekteblou” (to him).
_TN_SEND = re.compile(r"^\s*(?:(?:3aychek|3aychk|y3aychek|brabi|svp|please|yasmine\s*,?)\s+)?"
                      r"(?P<verb>ab3ath|ab3eth|eb3ath|b3ath|ob3eth|ab3at|ab3et|ab3a4|ab3th|eb3th|ekteb|ikteb|akteb|kteb|9oul|9ol|goul|gol|qoul|"
                      r"es2el|is2el|as2el|sa2el)(?P<to>lli|li|llou|lou|lo|ou|lha|ha|lhom|hom|lna)?\b", re.I)
_AR_SEND = re.compile(r"^\s*(?:(?:من فضلك|عيشك|يعيشك|برابي|بربي)\s+)?(?P<verb>اكتب|أكتب|إكتب|ابعث|إبعث|أبعث|ابعت|أرسل|ارسل|إرسل|قول|قل|قلّ|اسأل|إسأل|أسأل|اسال)"
                      r"(?P<to>لي|لو|له|لها|ها|لهم|هم|و)?(?=\s|$)")
_TELL_VERBS = {"9oul", "9ol", "goul", "gol", "qoul", "قول", "قل", "قلّ"}
_ASK_VERBS = {"es2el", "is2el", "as2el", "sa2el", "اسأل", "إسأل", "أسأل", "اسال"}
_TO_ME = {"li", "lli", "لي"}
_TO_HIM = {"lou", "llou", "lo", "ou", "لو", "له", "و"}
_TO_HER = {"lha", "ha", "لها", "ها"}
_TO_THEM = {"lhom", "hom", "لهم", "هم"}
_AR_WAY = {"whatsapp": r"(?:واتساب|واتس|الواتساب)", "sms": r"(?:\bsms\b|اس ام اس|إس إم إس)", "email": r"(?:ايميل|إيميل|الايميل|مايل)"}
_AR_TELL = re.compile(r"\s+(?:و\s*)?(?:قلو|قلّو|قولو|قوللو|قلها|قولها|قلهم|قولهم|إنو|انو|إنه|انه|باش|اللي)\s+")
_TN_WAY = {"whatsapp": r"\b(whats ?app|wtsp|wts|watsap)\b", "sms": r"\bsms\b", "email": r"\b(e-?mail|mail|courriel)\b"}
_TN_TELL = re.compile(r"\s+(?:w\s+|o\s+|et\s+)?(?:kolo|kollo|kolou|kollou|9olo|9ollo|9olou|9ollou|9oulou|9oulo|goulou|gollo|qolo|qollo|kolha|kollha|"
                      r"9olha|9ollha|9oulha|kolhom|kollhom|9olhom|9ollhom|9oulhom|bech|bach|bich|elli|illi)\s+", re.I)


def _without_l(head: str, db: Session | None) -> str:
    """“lfares”, “l fares”, “l'fares”, “li fares”, “lel fares”: to Fares; “lotfi” stays Lotfi when someone is called so."""
    head = head.strip()
    if db is not None and head and _names_someone([head.split()[0]], _people(db), _class_names(db)):
        return head
    return re.sub(r"(?i)^\s*(?:lel\s+|lil\s+|li\s+|l['’ -]?\s*)", "", head)


def from_tunisian(message: str, db: Session | None = None) -> str | None:
    """The order in French words when it is said in Tunisian (Latin or Arabic letters), else None: “ab3athli message lfares
    kolo ija” → “envoie un message à fares pour lui dire que viens”, “ekteb l ahmed winik” → “envoie un message à ahmed
    winik” (the names then tell who, the rest is what). The rewording then writes it properly. “9olli …” (tell me) is a
    question, not an order."""
    arabic = False
    found = _TN_SEND.match(message or "")
    if not found:
        found, arabic = _AR_SEND.match(message or ""), True
    if not found:
        return None
    verb, to = found.group("verb").lower(), (found.group("to") or "").lower()
    if to in _TO_ME and (verb in _TELL_VERBS or verb in _ASK_VERBS):
        return None                        # “9olli chkoun ma khallasch”: tell ME, a question for Yasmine
    rest = message[found.end():]
    ways_found = _AR_WAY if arabic else _TN_WAY
    ways = [way for way, pattern in ways_found.items() if re.search(pattern, rest, re.I)]
    tell = (_AR_TELL if arabic else _TN_TELL).search(rest)
    head, what = (rest[:tell.start()], rest[tell.end():]) if tell else (rest, "")
    for pattern in ways_found.values():
        head = re.sub(pattern, " ", head, flags=re.I)
    head = re.sub(r"(?i)\b(message|msg|messages|rsala|risala|mesaj|messaj)\b|ميساج|مساج|مسج|رسالة|رسالتي", " ", head)
    if arabic:
        # “لأحمد”, “ل أحمد”, “لـأحمد”: to Ahmed.
        head = re.sub(r"^\s*لـ?\s*(?=\S)", "", head.strip())
        head = re.sub(r"\s+(?:و\s*)لـ?\s*", " et ", head)
        who = head
    else:
        who = _without_l(head, db)
        who = re.sub(r"(?i)\s+(?:w|o|et)\s+(?:li\s+|l['’ -]?\s*)", " et ", who)
    if not who.strip():
        # “ekteblou winik”: to him (the person on the page, or the one just talked about), the rest is what to say.
        pronoun = "lui" if to in _TO_HIM else "elle" if to in _TO_HER else "eux" if to in _TO_THEM else None
        if pronoun is None or not what.strip():
            return None
        who = pronoun
    elif to in _TO_HIM | _TO_HER | _TO_THEM and not tell and len(who.split()) >= 1 and db is not None \
            and not _names_someone([_plain_token(who.split()[0])], _people(db), _class_names(db)):
        # “9oulou ija ghodwa”: the ending said whom, what follows is the message.
        what, who = who, ("lui" if to in _TO_HIM else "elle" if to in _TO_HER else "eux")
    # The words to send stay as said: the rewording reads Tunisian itself (with the meaning of its words) and writes French.
    words = " ".join(what.split())
    way = {"whatsapp": "un whatsapp", "sms": "un sms", "email": "un email"}.get(ways[0], "un message") if ways else "un message"
    told = "pour lui demander" if verb in _ASK_VERBS else "pour lui dire que"
    return f"envoie {way} à {' '.join(who.split())}" + (f" {told} {words}" if words else "")


_LITTLE = {"pour", "avec", "dans", "votre", "vous", "nous", "notre", "cette", "leur", "leurs", "sont", "être", "etre", "avoir", "bonjour",
           "merci", "plait", "très", "tres", "bien", "tout", "tous", "toute", "faire", "fait", "elle", "elles", "ils", "lui", "mais", "donc"}


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", _plain(text))
    return {word[:5] for word in words if word not in _LITTLE}


def reword_from_tunisian(words: str, first_name: str = "") -> dict | None:
    """{"reply", "warning", "french"} for a message said in Tunisian; None when the assistant does not answer. The
    reply keeps the greeting and only the sentences that carry the order's words; the warning says what was lost."""
    from app.services import derja, reply_writer

    french = " ".join(derja.to_french(words).split())
    out = reply_writer.reformulate(french, first_name=first_name, language="fr")
    if out is None:
        return None
    lines = [line.strip() for line in (out.get("reply") or "").split("\n") if line.strip()]
    greets = bool(lines) and re.match(r"(?i)^(bonjour|bonsoir|salut|cher|chère)\b", lines[0]) is not None
    greeting = lines[0] if greets else (f"Bonjour {first_name}," if first_name else "Bonjour,")
    body = " ".join(lines[1:] if greets else lines)
    wanted = _stems(french)
    kept = [sentence for sentence in re.split(r"(?<=[.!?])\s+", body) if sentence and (_stems(sentence) & wanted or not wanted)]
    text = " ".join(kept).strip()
    lost_words = sorted(wanted - _stems(text))
    from app.services.rewording import figures

    # Figures only: the digits of Tunisian words (“m3a”, “a7na”) are letters, never a figure lost.
    lost_figures = [figure for figure in figures(french) if figure not in figures(text)]
    problems = []
    if not text:
        problems.append("nothing of your order was left")
    if lost_words:
        problems.append("these words were lost: " + ", ".join(lost_words))
    if lost_figures:
        problems.append("these figures were lost: " + ", ".join(lost_figures))
    return {"reply": f"{greeting}\n\n{text}" if text else "", "french": french,
            "warning": ("The rewording changed the order (" + "; ".join(problems) + ").") if problems else None}


def parse(message: str) -> dict | None:
    """{"who": text naming the recipients, "what": the words to send, "ways": [...], "as_is", "english"}, or None."""
    if not SEND_START.search(message or ""):
        return None
    quoted = re.search(r"[«“\"]([^»”\"]{2,})[»”\"]", message)
    head, what = message, ""
    if quoted:
        what = quoted.group(1).strip()
        head = message[:quoted.start()]
    else:
        found = _SEPARATOR.search(message, pos=SEND_START.search(message).end())
        if found:
            head, what = message[:found.start()], message[found.end():].strip()
        else:
            purpose = _INTENT.search(message, pos=SEND_START.search(message).end())
            if purpose:                       # "écris à Fares pour lui rappeler la séance": what it is for, written by Yasmine
                head, what = message[:purpose.start()], message[purpose.end():].strip()
    ways = [way for way, pattern in _CHANNEL.items() if re.search(pattern, head, re.I)]
    who = SEND_START.sub("", head, count=1)
    who = re.sub(r"(?i)\b(un|une|a|an|le|la|the|par|by|on|sur|via|en)\b", " ", who)
    for pattern in _CHANNEL.values():
        who = re.sub(pattern, " ", who, flags=re.I)
    who = re.sub(r"(?i)\b(message|messages|mot|note)\b", " ", who)
    who = re.sub(r"(?i)^\s*(à|a|au|aux|to|pour)\s+", " ", who.strip())
    return {"who": " ".join(who.split()), "what": _STRIP.sub(" ", what).strip(" ."), "ways": ways,
            "as_is": bool(_AS_IS.search(message)), "english": bool(_ENGLISH.search(message))}


_ARABIC = re.compile(r"[\u0600-\u06ff]")
# Arabic letters by the consonant they are written with in Latin letters (vowels, ع and ء are left out on both sides).
_AR_CONSONANT = {"ب": "b", "ت": "t", "ث": "t", "ج": "j", "ح": "h", "خ": "k", "د": "d", "ذ": "d", "ر": "r", "ز": "z", "س": "s",
                 "ش": "c", "ص": "s", "ض": "d", "ط": "t", "ظ": "d", "غ": "g", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m",
                 "ن": "n", "ه": "h", "پ": "b", "ڨ": "g", "ڤ": "f"}


def _skeleton(word: str) -> str:
    """The consonants of a name, the same in both scripts: أحمد and ahmed → “hmd”, محمد and mohamed → “mhmd”."""
    if _ARABIC.search(word or ""):
        letters = []
        for index, char in enumerate(word):
            if char == "و":
                letters.append("w" if index == 0 else "")      # و opens Walid, Wafa; inside it is a vowel (Nour, Youssef)
            else:
                letters.append(_AR_CONSONANT.get(char, ""))
    else:
        text = _plain(word).replace(" ", "")
        for pair, one in (("ch", "c"), ("sh", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("ph", "f"), ("ou", "")):
            text = text.replace(pair, one)
        text = text.translate(str.maketrans({"7": "h", "9": "k", "q": "k", "5": "k", "8": "g", "x": "k"}))
        letters = list(re.sub(r"[aeiouy23']", "", text))
    out = []
    for letter in letters:
        if letter and (not out or out[-1] != letter):
            out.append(letter)
    return "".join(out)


def _plain_token(word: str) -> str:
    return word.strip(" ,;:.!?«»“”\"'()")


def _word_fits(word: str, name_words: list[str]) -> bool:
    if _ARABIC.search(word):
        skeleton = _skeleton(re.sub(r"^لـ?", "", word))
        return len(skeleton) >= 2 and any(_skeleton(part) == skeleton for part in name_words)
    return _plain(word).strip() in name_words


def _class_names(db: Session) -> set[str]:
    from app.models import ClassGroup

    return {" ".join(_plain(name).split()) for (name,) in db.query(ClassGroup.name).all() if name}


def _names_someone(words: list[str], people: list[dict], classes: set[str]) -> bool:
    """These words name a person (every word in their name), a class, an address or a pronoun."""
    words = [_plain_token(word) for word in words if _plain_token(word)]
    if not words:
        return False
    if len(words) == 1 and (_EMAIL.fullmatch(words[0]) or _plain(words[0]).strip() in _PRONOUNS):
        return True
    named_class = " ".join(word for word in _plain(" ".join(words)).split() if word not in _CLASS_WORDS)
    if named_class and named_class in classes:
        return True
    if any(not _ARABIC.search(word) and len(_plain(word).strip()) < 2 for word in words):
        return False
    return any(all(_word_fits(word, _plain(person["name"]).split()) for word in words) for person in people)


def split_names(db: Session, text: str) -> tuple[str, str]:
    """(the names at the start, what follows): “ahmed winik” → (“ahmed”, “winik”); “ahmed naji et fares ija” → (“ahmed naji
    et fares”, “ija”). The longest run of words that names someone is taken; nothing named: ("", text)."""
    tokens = (text or "").split()
    people, classes = _people(db), _class_names(db)
    phone = _PHONE.match(text or "")
    taken, index = [], 0
    if phone and len(re.sub(r"\D", "", phone.group(0))) >= 8:
        taken.append(phone.group(0).strip())
        index = len(phone.group(0).split())
    while index < len(tokens):
        size = next((size for size in range(min(4, len(tokens) - index), 0, -1)
                     if _names_someone(tokens[index:index + size], people, classes)), 0)
        if not size:
            break
        taken.append(" ".join(_plain_token(token) for token in tokens[index:index + size]))
        index += size
        # “ahmed et fares …”, “ahmed, fares …”: another name follows.
        if index + 1 < len(tokens) and tokens[index].lower() in ("et", "and", "w", "o", "&", "و") \
                and _names_someone(tokens[index + 1:index + 2], people, classes):
            index += 1
            continue
        if index < len(tokens) and tokens[index - 1].endswith(",") and _names_someone(tokens[index:index + 1], people, classes):
            continue
        break
    if not taken:
        return "", text or ""
    return " et ".join(taken), " ".join(tokens[index:])


def _people(db: Session) -> list[dict]:
    from app.models import Candidate, Professor, User

    found = [{"kind": "candidate", "id": item.id, "name": item.full_name or item.email or "", "email": item.email, "phone": item.phone, "row": item}
             for item in db.query(Candidate).all()]
    found += [{"kind": "professor", "id": item.id, "name": item.name, "email": item.email, "phone": item.phone, "row": item}
              for item in db.query(Professor).all()]
    found += [{"kind": "user", "id": item.id, "name": item.name, "email": item.email, "phone": getattr(item, "phone", None), "row": item}
              for item in db.query(User).filter(User.is_active.is_(True), User.professor_id.is_(None)).all()]
    return [item for item in found if item["name"]]


def resolve(db: Session, who: str) -> tuple[list[dict], list[str], list[tuple[str, list[dict]]]]:
    """(people found, names nobody has, names several people have)."""
    found, unknown, several = [], [], []
    for address in _EMAIL.findall(who):
        found.append({"kind": "address", "id": address, "name": address, "email": address, "phone": None, "row": None})
    rest = _EMAIL.sub(" ", who)
    for number in _PHONE.findall(rest):
        if len(re.sub(r"\D", "", number)) >= 8:
            found.append({"kind": "number", "id": number, "name": " ".join(number.split()), "email": None, "phone": number, "row": None})
    rest = _PHONE.sub(" ", rest)
    people = _people(db)
    for part in re.split(r"\s*(?:,|\bet\b|\band\b|&|\sو\s)\s*", rest, flags=re.I):
        members = _class_members(db, part)
        if members is not None:
            found.extend(members)               # "la classe MD1": every candidate of that class
            continue
        arabic = [word for word in part.split() if _ARABIC.search(word)]
        words = [word for word in _plain(part).split() if len(word) >= 3]
        if not words and not arabic:
            continue
        if arabic:                              # أحمد: the people whose name has the same consonants
            matches = [item for item in people if all(_word_fits(word, _plain(item["name"]).split()) for word in arabic + words)]
        else:
            exact = [item for item in people if _plain(item["name"]).split() == words]
            matches = exact or [item for item in people if all(word in _plain(item["name"]).split() for word in words)]
        # Whoever is called so first (Ahmed Naji before Naji Ahmed for “ahmed”), then candidates, professors, the team.
        first = (_plain(part).split() or [""])[0]
        matches.sort(key=lambda item: (not _plain(item["name"]).split()[:1] == [first], ("candidate", "professor", "user").index(item["kind"])
                                       if item["kind"] in ("candidate", "professor", "user") else 3))
        if len(matches) == 1 and not arabic:
            found.append(matches[0])
        elif not matches:
            unknown.append(part.strip())
        else:
            # Read by its consonants (عمر could be Amira): asked, one tap, even for one person, unless the page says who.
            several.append((part.strip(), matches[:6]))
    return found, unknown, several


_CLASS_WORDS = {"la", "le", "les", "l", "classe", "class", "the", "candidats", "candidates", "de", "du", "des", "of", "all", "tous", "toute", "groupe", "group"}


def _class_members(db: Session, part: str) -> list[dict] | None:
    """Every candidate of the class the words name ("la classe MD1", "MD1"), or None when they name no class."""
    from app.models import ClassGroup
    from app.services.enrollment_service import class_members

    words = [word for word in _plain(part).split() if word not in _CLASS_WORDS]
    if not words:
        return None
    wanted = " ".join(words)
    group = next((row for row in db.query(ClassGroup).all() if " ".join(_plain(row.name).split()) == wanted), None)
    if group is None:
        return None
    found = []
    for registration in class_members(db, group.id):
        person = registration.candidate
        if person is not None:
            found.append({"kind": "candidate", "id": person.id, "name": person.full_name or person.email or "", "email": person.email,
                          "phone": person.phone, "row": person})
    return found


def _ask(text: str, fr: bool, links=None) -> dict:
    return {"reply": text, "links": links or [], "source": "action", "sources": [{"label": "The application's people (candidates, professors, team)"}]}


def _source(reworded: dict | None) -> str:
    if not reworded:
        return "Your own words"
    return "Written by Yasmine from your words (figures checked)" if reworded.get("by") == "cloud" else "Reworded by the inbox's rewording (figures checked)"


def _why_waiting(way: str) -> str:
    if way == "whatsapp":
        return "no WhatsApp Business number is connected (Settings, WhatsApp): open it from your phone"
    return "no Android phone is connected for SMS (Settings, SMS): open it from your phone"


def _send_one(db: Session, person: dict, text: str, ways: list[str], *, author: str, english: bool) -> list[dict]:
    """[{way, status: sent | waiting | failed, why, link}] for one person."""
    from app.services import channels, email_assistant, email_threads, sms, team_messages, whatsapp, whatsapp_inbox
    from app.services.email_service import send_email

    results = []
    language = "en" if english else "fr"
    if person["kind"] == "candidate":
        candidate = person["row"]
        plain, page = email_assistant.compose_reply(db, candidate, text, language=language)
        subject = "Message from 9antra" if english else "Message de 9antra"
        if not ways:
            message = channels.deliver(db, candidate, {"subject": subject, "text": plain, "html": page, "short": text}, kind="message")
            if message is None:
                return [{"way": "any", "status": "failed", "why": "no email and no usable phone number in their file", "link": f"/candidate/{candidate.id}"}]
            ways_done = [(message.channel or "email", message)]
        else:
            ways_done = []
            for way in ways:
                if way == "email":
                    message = email_threads.deliver(db, candidate, subject, plain, page, kind="message", author=author, language=language) \
                        if candidate.email else None
                elif way == "whatsapp":
                    message = whatsapp_inbox.send(db, candidate, text, kind="message", author=author, language=language)
                else:
                    message = whatsapp_inbox.send_sms(db, candidate, text, kind="message", author=author)
                if message is None:
                    results.append({"way": way, "status": "failed", "link": f"/candidate/{candidate.id}",
                                    "why": "no email address in their file" if way == "email" else "their phone number cannot be used (missing or unreadable)"})
                else:
                    ways_done.append((way, message))
        for way, message in ways_done:
            if message.status == "sent":
                results.append({"way": message.channel or way, "status": "sent", "why": "", "link": f"/inbox?thread={message.thread_id}"})
            elif message.status == "to_send":
                results.append({"way": message.channel or way, "status": "waiting", "why": _why_waiting(message.channel or way),
                                "link": whatsapp_inbox.link_of(message) or f"/inbox?thread={message.thread_id}"})
            else:
                results.append({"way": message.channel or way, "status": "failed", "link": f"/inbox?thread={message.thread_id}",
                                "why": (message.analysis or "it could not be delivered").replace("Not delivered on WhatsApp: ", "").replace("Not delivered by SMS: ", "")})
        return results
    if person["kind"] in ("professor", "user"):
        key = f"professor:{person['id']}" if person["kind"] == "professor" else f"user:{person['id']}"
        found = team_messages.person(db, key)
        if found is None:
            return [{"way": "any", "status": "failed", "why": "they are no longer in the team", "link": "/inbox/team"}]
        wanted = ways or [way for way in ("app", "email") if way in found["ways"]] or found["ways"][:1]
        missing = [way for way in wanted if way not in found["ways"]]
        for way in missing:
            results.append({"way": way, "status": "failed", "link": f"/inbox/team?person={key}",
                            "why": "no email address for them" if way == "email" else "no usable phone number for them" if way in ("whatsapp", "sms")
                            else "they have no account in the application"})
        rows = team_messages.send(db, key, text, [way for way in wanted if way in found["ways"]], author=author) if len(missing) < len(wanted) else []
        for row in rows:
            status = {"sent": "sent", "to_send": "waiting"}.get(row.status, "failed")
            results.append({"way": row.channel, "status": status, "link": team_messages.link_of(row, found["number"]) if status == "waiting" else f"/inbox/team?person={key}",
                            "why": _why_waiting(row.channel) if status == "waiting" else (row.error or "")})
        return results
    if person["kind"] == "address":
        subject = "Message from 9antra" if english else "Message de 9antra"
        try:
            went = send_email(to_email=person["email"], subject=subject, body="<div style=\"font-family:Arial,sans-serif;font-size:15px;line-height:1.55\">"
                              + html.escape(text).replace("\n", "<br>") + "</div>", is_html=True, text_body=text, journal=False)
            if went is False:
                return [{"way": "email", "status": "failed", "why": "the mail server did not take it", "link": "/settings#email"}]
            return [{"way": "email", "status": "sent", "why": "", "link": None}]
        except Exception as error:      # the mail server's own words, to tell the admin why
            return [{"way": "email", "status": "failed", "why": str(error)[:200] or "the mail server refused it", "link": "/settings#email"}]
    number = whatsapp.normalise(person["phone"])
    if number is None:
        return [{"way": ways[0] if ways else "whatsapp", "status": "failed", "why": "this phone number cannot be read", "link": None}]
    for way in ways or ["whatsapp"]:
        if way == "sms":
            if sms.configured():
                problem = sms.send(number, text)
                results.append({"way": "sms", "status": "failed" if problem else "sent", "why": problem or "", "link": "/settings#sms" if problem else None})
            else:
                from urllib.parse import quote

                results.append({"way": "sms", "status": "waiting", "why": _why_waiting("sms"), "link": sms.link(number, text)})
        elif way == "whatsapp":
            if whatsapp.configured():
                problem = whatsapp.send(number, text)
                results.append({"way": "whatsapp", "status": "failed" if problem else "sent", "why": problem or "", "link": "/settings#whatsapp" if problem else None})
            else:
                results.append({"way": "whatsapp", "status": "waiting", "why": _why_waiting("whatsapp"), "link": whatsapp.link(number, text)})
        else:
            results.append({"way": "email", "status": "failed", "why": "a phone number has no email address", "link": None})
    return results


_WAY_NAMES = {"email": "email", "whatsapp": "WhatsApp", "sms": "SMS", "app": "in the application", "any": "any way"}


# ------------------------------------------------------------------
# Who it is for: the page open, a moment ago, or asked with one tap each
# ------------------------------------------------------------------

_PENDING: dict[str, dict] = {}          # who asked → the order waiting for their answer: a person to choose, or "send it?"
_CHOSEN: dict[str, dict] = {}           # who asked → {a name as said: (person key, when)}: “Ahmed” is the Ahmed chosen last
WAIT = 900                              # seconds an order waits for its answer
KEEP_CHOICE = 7200                      # seconds “Ahmed” keeps meaning the one chosen
_YES = re.compile(r"(?i)^\s*(oui|yes|ey|eyh|ih|ok|okay|d'accord|vas[- ]y|go|envoie(?:[- ]le|[- ]la)?|send(?: it)?|ab3ath(?:ha|ou)?|إرسال|ارسل|ابعث|نعم|اي)\b")
_NO = re.compile(r"(?i)^\s*(non|no|la|annule|cancel|stop|laisse|leave it|لا|إلغاء)\b")
_ORDINALS = {"premier": 0, "premiere": 0, "first": 0, "lowel": 0, "louel": 0, "deuxieme": 1, "second": 1, "thani": 1, "troisieme": 2, "third": 2,
             "thelith": 2, "الأول": 0, "الاول": 0, "الثاني": 1, "الثالث": 2}


def _user_key(user) -> str | None:
    return getattr(user, "id", None) or None


def _key_of(person: dict) -> str:
    return f"{person['kind']}:{person['id']}"


def _label(person: dict, fr: bool) -> str:
    """How a person is told apart in a choice: their name, what they are, their program (or their address)."""
    kind = {"candidate": "candidat" if fr else "candidate", "professor": "formateur" if fr else "professor", "user": "équipe" if fr else "team"}
    detail = ""
    row = person.get("row")
    if person["kind"] == "candidate" and row is not None:
        active = [item for item in row.registrations if getattr(item.status, "name", "") != "CANCELLED"]
        latest = max(active, key=lambda item: item.registration_date or datetime.min) if active else None
        detail = ((latest.program.name if latest.program else latest.program_name) if latest is not None else "") or (row.email or "")
    elif person["kind"] == "professor" and row is not None:
        detail = (row.specialties or "").split(",")[0].strip() or (row.email or "")
    return " · ".join(part for part in (person["name"], kind.get(person["kind"], ""), detail) if part)


def _labels(people: list[dict], fr: bool) -> list[str]:
    labels = [_label(person, fr) for person in people]
    for index, label in enumerate(labels):            # two people with the same name and program: their address too
        if labels.count(label) > 1 and people[index].get("email"):
            labels[index] = f"{label} · {people[index]['email']}"
    return labels


def _on_page(db: Session, path: str | None) -> list[dict]:
    """The person whose page (or discussion) is open."""
    from urllib.parse import parse_qs, urlparse

    from app.models import Candidate, EmailThread, Registration

    if not path:
        return []
    parts = urlparse(path)
    query = parse_qs(parts.query)
    wanted = None
    found = re.match(r"^/candidate/([0-9a-f-]{8,})", parts.path)
    if found:
        wanted = ("candidate", found.group(1))
    elif parts.path.startswith("/inbox") and query.get("thread"):
        thread = db.get(EmailThread, query["thread"][0])
        wanted = ("candidate", thread.candidate_id) if thread is not None else None
    elif parts.path.startswith("/inbox") and query.get("candidate"):
        wanted = ("candidate", query["candidate"][0])
    elif re.match(r"^/classes/registration/", parts.path):
        registration = db.get(Registration, parts.path.rstrip("/").split("/")[-1])
        wanted = ("candidate", registration.candidate_id) if registration is not None else None
    elif parts.path.startswith("/inbox/team") and query.get("person"):
        kind, _, ident = query["person"][0].partition(":")
        wanted = (kind, ident) if kind in ("professor", "user") else None
    elif re.match(r"^/professors/[0-9a-f-]{8,}", parts.path):
        wanted = ("professor", parts.path.rstrip("/").split("/")[2])
    if wanted is None:
        return []
    return [person for person in _people(db) if (person["kind"], str(person["id"])) == wanted]


def _talked_about(db: Session, history: list[dict] | None) -> list[dict]:
    """The people named in full in the last lines of the conversation, the latest first."""
    if not history:
        return []
    people = [person for person in _people(db) if len(_plain(person["name"]).split()) >= 2]
    found = []
    for item in reversed(history[-6:]):
        said = " " + " ".join(_plain(item.get("content") or "").split()) + " "
        for person in people:
            if f" {' '.join(_plain(person['name']).split())} " in said and person not in found:
                found.append(person)
    return found


def _pick(db: Session, said: str, matches: list[dict], user, path, history, fr: bool) -> tuple[dict | None, str]:
    """One of several people with the name, when it is surely them: (the person, why), else (None, "")."""
    keys = {_key_of(person): person for person in matches}
    for person in _on_page(db, path):
        if _key_of(person) in keys:
            return keys[_key_of(person)], ("celui/celle dont la page est ouverte" if fr else "the one whose page is open")
    remembered = (_CHOSEN.get(_user_key(user) or "") or {}).get(" ".join(_plain(said).split()))
    if remembered and time.time() - remembered[1] < KEEP_CHOICE and remembered[0] in keys:
        return keys[remembered[0]], ("celui/celle que vous avez choisi(e) tout à l'heure" if fr else "the one you chose a moment ago")
    for person in _talked_about(db, history):
        if _key_of(person) in keys:
            return keys[_key_of(person)], ("celui/celle dont nous venons de parler" if fr else "the one we just talked about")
    return None, ""


def _which(said: str, matches: list[dict], order: dict, user, fr: bool) -> dict:
    """Several people have the name: she asks which, one tap each, and keeps the order for the answer."""
    labels = _labels(matches, fr)
    if _user_key(user):
        _PENDING[_user_key(user)] = {"kind": "who", "said": said, "order": order, "choices": list(zip(labels, [_key_of(person) for person in matches])),
                                     "at": time.time()}
    listing = "\n".join(f"• {label}" for label in labels)
    if len(matches) == 1:                 # a name in Arabic letters, read by its consonants: who it is, said before it goes
        found = _ask((f"« {said} », c'est bien {matches[0]['name']} ? Touchez son nom pour continuer, ou écrivez son nom en lettres latines.\n{listing}" if fr
                      else f"“{said}”: is it {matches[0]['name']}? Tap their name to go on, or write their name in Latin letters.\n{listing}"), fr)
    else:
        found = _ask((f"Plusieurs personnes s'appellent « {said} » : laquelle ? Touchez son nom, ou écrivez son nom complet.\n{listing}" if fr
                      else f"Several people are called “{said}”: which one? Tap their name, or write their full name.\n{listing}"), fr)
    found["options"] = [{"label": label, "say": label} for label in labels]
    return found


def _chosen(message: str, choices: list[tuple[str, str]]) -> str | None:
    """The person chosen among the choices: their whole label, their number, “the first”, or words only one of them has."""
    said = " ".join(message.split()).strip(" .!")
    for label, key in choices:
        if said.lower() == label.lower():
            return key
    if said.isdigit() and 1 <= int(said) <= len(choices):
        return choices[int(said) - 1][1]
    plain = _plain(said).split()
    for word in plain + said.split():
        if word in _ORDINALS and _ORDINALS[word] < len(choices):
            return choices[_ORDINALS[word]][1]
    words = [word for word in plain if len(word) >= 3 and word not in ("the", "one", "celui", "celle", "lui", "elle", "candidate", "candidat")]
    if not words:
        return None
    fits = [key for label, key in choices if all(word in _plain(label).split() for word in words)]
    return fits[0] if len(fits) == 1 else None


def looks_like_order(message: str) -> bool:
    """Said like an order to write (by its first words only, no names looked up)."""
    return bool(SEND_START.search(message or "") or _TN_SEND.match(message or "") or _AR_SEND.match(message or ""))


def wants(db: Session, message: str, user=None) -> bool:
    """An order to write to someone (or the answer to her question about it): the writing step takes it."""
    key = _user_key(user)
    waiting = _PENDING.get(key) if key else None
    if waiting and time.time() - waiting["at"] <= WAIT:
        if waiting["kind"] == "who" and _chosen(message, waiting["choices"]) is not None:
            return True
        if waiting["kind"] == "confirm" and (_YES.match(message) or _NO.match(message)):
            return True
    french = from_tunisian(message, db)
    order = parse(french or message)
    if order is None or not order["who"] or re.match(r"(?i)^\s*-?\s*(me|moi|nous|us)\b", order["who"]):
        return False
    if re.match(r"(?i)\s*(how|comment|where|ou|où)\b", message):
        return False
    named, _ = split_names(db, order["who"])
    if not named and document_order(message):
        # “send the DevOps program to Fares”: the tools make the PDF (the writing step hands it to them).
        return any(_names_someone([word], _people(db), set()) for word in (french or message).split()[-3:])
    return bool(named)


# ------------------------------------------------------------------
# The order
# ------------------------------------------------------------------

def handle(db: Session, message: str, fr: bool, *, author: str = "Admin", allowed: bool = True, user=None,
           history: list[dict] | None = None, path: str | None = None) -> dict | None:
    """The assistant's answer to a "send … to …" message, or None when the message is not one."""
    key = _user_key(user)
    waiting = _PENDING.get(key) if key else None
    if waiting and time.time() - waiting["at"] > WAIT:
        _PENDING.pop(key, None)
        waiting = None
    if waiting:
        answered = _follow_up(db, waiting, message, author=author, allowed=allowed, user=user)
        if answered is not None:
            return answered
    asked = message
    french = from_tunisian(message, db)
    tunisian = bool(french)
    if french:
        message, fr = french, True          # said in Tunisian: done like the same order in French, answered in French
    parsed = parse(message)
    if parsed is None or not parsed["who"]:
        return None
    if re.match(r"(?i)^\s*-?\s*(me|moi|nous|us)\b", parsed["who"]):
        return None              # "tell me about…", "dis-moi…": a question, not a message to send
    if re.search(r"(?i)\b(programs?|programmes?|formations?|brochures?|catalogues?|summary|résumé|resume|pdf|documents?)\b", parsed["who"]):
        return None              # "send the DevOps program to Yasmine": the program's PDF (chatbot_actions.document)
    named, rest = split_names(db, parsed["who"])
    if not parsed["what"] and named and rest.strip():
        # “write to ahmed winik”: the names first, the rest is what to say.
        parsed["who"], parsed["what"] = named, _STRIP.sub(" ", rest).strip(" .")
    elif (not parsed["what"] and not named and len(parsed["who"].split()) > 1 and not _EMAIL.search(parsed["who"]) and not _PHONE.search(parsed["who"])
          and not message.rstrip().endswith("?") and _plain(parsed["who"]).split()[:1] and _plain(parsed["who"]).split()[0] not in _NOT_A_NAME):
        # “écris à Zorro bonjour”: nobody is called so; only the first word can be the name (it is said so).
        parsed["who"], parsed["what"] = parsed["who"].split()[0], _STRIP.sub(" ", " ".join(parsed["who"].split()[1:])).strip(" .")
    asking = _ASKING.match(message)
    if asking and parsed["what"] and not re.match(r"(?i)(pour (?:lui|leur) demander|to ask)\b", parsed["what"]):
        parsed["what"] = f"{asking.group(1).lower()} {parsed['what']}"          # “ask Fares if he comes”: what to say is a question
    order = {**parsed, "tunisian": tunisian or bool(_derja_words(parsed["what"])), "fr": fr, "asked": asked}
    if not parsed["what"] and parsed["ways"] not in ([], ["email"]):
        pass                     # "send an SMS to Fares": they are asked what to say, below
    elif not parsed["what"]:
        if not named or rest.strip():
            return None          # "send it to Yasmine": not only a name (the documents' follow-up, or the composer, as before)
        return _without_words(db, order, fr, user=user, history=history, path=path, allowed=allowed)
    if not allowed:
        return _ask("Sending messages from here is for those who may change things: ask the admin." if not fr
                    else "Envoyer des messages d'ici est réservé à ceux qui peuvent modifier : demandez à l'admin.", fr)
    people, notes = [], []
    if " ".join(_plain(parsed["who"]).split()) in _PRONOUNS:
        people = (_on_page(db, path) or _talked_about(db, history))[:1]
        if not people:
            return _ask(("À qui dois-je écrire ? Dites son nom, par exemple : « écris à Fares : la séance commence à 9h »." if fr
                         else "Who should I write to? Say their name, for example: “write to Fares: the session starts at 9”."), fr)
        if _on_page(db, path):
            why = "celui/celle dont la page est ouverte" if fr else "the one whose page is open"
        else:
            why = "celui/celle dont nous venons de parler" if fr else "the one we just talked about"
        notes.append(_note(people[0], why, fr))
        unknown, several = [], []
    else:
        people, unknown, several = resolve(db, parsed["who"])
    for said, matches in several:
        person, why = _pick(db, said, matches, user, path, history, fr)
        if person is None:
            return _which(said, matches, {**order, "people": [_key_of(item) for item in people], "unknown": unknown}, user, fr)
        people.append(person)
        notes.append(_note(person, why, fr))
    if not people and not unknown:
        return None              # "send it to Yasmine" after a PDF: nobody named to write to (the documents' follow-up takes it)
    if unknown and not people:
        return _ask((f"Je ne trouve personne qui s'appelle « {unknown[0]} » (candidats, formateurs, équipe). Écrivez son nom complet, son email ou son numéro."
                     if fr else f"I find nobody called “{unknown[0]}” (candidates, professors, team). Write their full name, email or number."), fr)
    if not parsed["what"]:
        return _ask(("Que faut-il leur dire ? Écrivez par exemple : « envoie un sms à Fares pour lui dire que la séance de samedi est annulée »."
                     if fr else "What should I tell them? For example: “send an SMS to Fares saying that Saturday's session is cancelled”."), fr)
    return _deliver(db, order, people, unknown, fr, author=author, user=user, notes=notes)


# Words a name never starts with: “message from Fares?” is a question, not an order to someone called “from”.
_NOT_A_NAME = {"from", "de", "du", "des", "about", "sur", "for", "pour", "with", "avec", "of", "the", "le", "la", "les", "my", "mon", "ma", "mes",
               "your", "ton", "ta", "tes", "votre", "vos", "this", "ce", "cette", "ces", "all", "tous", "toutes", "every", "chaque", "un", "une", "a",
               "it", "ca", "that", "these", "those", "ceci", "cela", "le", "y"}


def document_order(message: str) -> bool:
    """“envoie le programme DevOps à Fares”: a program's PDF to send (chatbot_actions.document), not words to write."""
    return looks_like_order(message) and re.search(r"(?i)\b(programs?|programmes?|formations?|brochures?|catalogues?|pdf|documents?)\b", message or "") is not None


def _note(person: dict, why: str, fr: bool) -> str:
    return (f"Pour « {person['name']} », j'ai pris {why}." if fr else f"For “{person['name']}”, I took {why}.")


def _derja_words(text: str) -> list:
    from app.services import derja

    return derja.words_in(text or "")


def _without_words(db: Session, order: dict, fr: bool, *, user=None, history=None, path=None, allowed=True) -> dict | None:
    """“écris à Ahmed”: the email to them opens, with a few things to say, one tap each (sent at once, reworded). Several
    Ahmeds: which one first. Not a candidate: the way it always was (the composer, by the tools)."""
    people, unknown, several = resolve(db, order["who"]) if " ".join(_plain(order["who"]).split()) not in _PRONOUNS else \
        ((_on_page(db, path) or _talked_about(db, history))[:1], [], [])
    notes = []
    for said, matches in several:
        person, why = _pick(db, said, matches, user, path, history, fr)
        if person is None:
            return _which(said, matches, {**order, "people": [_key_of(item) for item in people], "unknown": unknown}, user, fr)
        people.append(person)
        notes.append(_note(person, why, fr))
    candidates = [person for person in people if person["kind"] == "candidate"]
    if len(people) != 1 or not candidates:
        return None
    found = _compose(db, candidates[0], fr, allowed=allowed)
    if notes:
        found["reply"] = "\n".join(notes) + "\n\n" + found["reply"]
    return found


def _compose(db: Session, person: dict, fr: bool, *, allowed: bool = True) -> dict:
    from app.services import chatbot_actions, discussion_help

    found = chatbot_actions.write_to(db, person["row"], fr)
    if not allowed:
        return found
    said = discussion_help.to_say(db, person["row"], most=4)
    if said:
        found["reply"] += ("\n\nOu dites-moi quoi lui écrire, ou touchez une idée : elle part reformulée." if fr
                           else "\n\nOr tell me what to write, or tap an idea: it goes reworded.")
        found["options"] = [{"label": f"{item['label']} · {item['text'][:70]}{'…' if len(item['text']) > 70 else ''}",
                             "say": f"écris à {person['name']} : {item['text']}"} for item in said]
    return found


def _follow_up(db: Session, waiting: dict, message: str, *, author: str, allowed: bool, user) -> dict | None:
    """Her question answered: the person chosen (the order goes on), or “send it” / “no” for a text she prepared."""
    key = _user_key(user)
    fr = waiting["order"].get("fr", True)
    if waiting["kind"] == "confirm":
        if _NO.match(message):
            _PENDING.pop(key, None)
            return _ask("D'accord, rien n'est parti." if fr else "All right, nothing was sent.", fr)
        if not _YES.match(message):
            return None
        _PENDING.pop(key, None)
        people = [person for person in _people(db) if _key_of(person) in waiting["people"]] + waiting.get("others", [])
        return _send_now(db, people, waiting["text"], waiting["order"], waiting.get("reworded"), [], fr, author=author, notes=[])
    chosen = _chosen(message, waiting["choices"])
    if chosen is None:
        if SEND_START.search(message) or _TN_SEND.match(message) or _AR_SEND.match(message):
            _PENDING.pop(key, None)            # a new order instead of an answer
        return None
    _PENDING.pop(key, None)
    everyone = {_key_of(person): person for person in _people(db)}
    remembered = _CHOSEN.setdefault(key, {})
    remembered[" ".join(_plain(waiting["said"]).split())] = (chosen, time.time())
    if chosen in everyone:           # its whole name too: two people may share it (“ahmed naffeti”)
        remembered[" ".join(_plain(everyone[chosen]["name"]).split())] = (chosen, time.time())
    order = waiting["order"]
    people = [everyone[item] for item in order.get("people", []) if item in everyone] + ([everyone[chosen]] if chosen in everyone else [])
    if not order.get("what"):
        candidates = [person for person in people if person["kind"] == "candidate"]
        if len(people) == 1 and candidates:
            return _compose(db, candidates[0], fr, allowed=allowed)
        return _ask(("Que faut-il leur dire ?" if fr else "What should I tell them?"), fr)
    if not allowed:
        return _ask("Envoyer des messages d'ici est réservé à ceux qui peuvent modifier : demandez à l'admin." if fr
                    else "Sending messages from here is for those who may change things: ask the admin.", fr)
    return _deliver(db, order, people, order.get("unknown", []), fr, author=author, user=user, notes=[])


def _deliver(db: Session, order: dict, people: list[dict], unknown: list[str], fr: bool, *, author: str, user=None, notes: list[str]) -> dict:
    """The words reworded (figures checked), then sent, or ready with one tap when “Send at once” is off."""
    from app.services import reminder_writer, reply_writer

    text = order["what"]
    english = order["english"]
    single = people[0] if len(people) == 1 else None
    if not english and single is not None and single["kind"] == "candidate" and not re.search(r"(?i)\b(en fran[cç]ais|in french)\b", order.get("asked", "")):
        from app.services import discussion_help

        english = discussion_help.language_of(db, single["row"]) == "en"     # they write in English: they are written to in English
    reworded = None
    if not order["as_is"]:
        first = single["name"].split()[0].capitalize() if single is not None and single["kind"] not in ("address", "number") else ""
        # Yasmine writes it herself from the words (the cloud writer, figures checked); without it, the inbox's rewording.
        reworded = reminder_writer.reword(text, first, "en" if english else "fr")
        if reworded is None:
            reworded = (reword_from_tunisian(text, first) if order.get("tunisian") and not english else
                        reply_writer.reformulate(text, first_name=first, language="en" if english else "fr"))
        if reworded is None:
            return _ask((f"Je n'ai pas pu reformuler (l'assistant ne répond pas en ce moment) : rien n'est parti. Pour envoyer vos mots tels quels, "
                         f"ajoutez « tel quel ».\n\nVos mots : {text}") if fr else
                        (f"I could not reword it (the assistant is not answering right now): nothing was sent. To send your own words, add “as is”.\n\n"
                         f"Your words: {text}"), fr)
        if reworded.get("warning"):
            return _ask((f"Rien n'est parti : la reformulation a changé quelque chose. {reworded['warning']}\n\nLe texte reformulé :\n{reworded['reply']}\n\n"
                         f"Pour envoyer vos mots tels quels, ajoutez « tel quel ».") if fr else
                        (f"Nothing was sent: the rewording changed something. {reworded['warning']}\n\nThe reworded text:\n{reworded['reply']}\n\n"
                         f"To send your own words, add “as is”."), fr)
        text = reworded["reply"]
    understood = _understood(order["what"], fr)
    if understood:
        notes = notes + [understood]
    names = ", ".join(item["name"] for item in people)
    if not auto_send(db):
        key = _user_key(user)
        if key:
            _PENDING[key] = {"kind": "confirm", "people": [_key_of(item) for item in people if item["kind"] not in ("address", "number")],
                             "others": [item for item in people if item["kind"] in ("address", "number")],
                             "text": text, "order": {**order, "english": english, "fr": fr}, "reworded": reworded, "at": time.time()}
        found = {"reply": "\n".join(notes + [(f"« Envoyer tout de suite » est désactivé : rien n'est parti. Voici le texte pour {names} :\n\n{text}" if fr else
                                               f"“Send at once” is off: nothing was sent. Here is the text for {names}:\n\n{text}")]),
                 "links": [{"label": f"Write to {item['name'].split()[0]}", "url": f"/inbox/compose?candidate={item['id']}"} for item in people if item["kind"] == "candidate"][:3],
                 "source": "action", "sources": [{"label": _source(reworded)}]}
        if key:
            found["options"] = [{"label": "Envoyer" if fr else "Send it", "say": "envoie-le" if fr else "send it"},
                                {"label": "Annuler" if fr else "Cancel", "say": "non" if fr else "no"}]
        return found
    return _send_now(db, people, text, {**order, "english": english}, reworded, unknown, fr, author=author, notes=notes)


def _understood(words: str, fr: bool) -> str:
    """What the Tunisian words were read as, so the admin sees it was understood (“winik” = où es-tu)."""
    found = _derja_words(words)
    phrases = [word for word, _, _ in found if " " in word]
    found = [item for item in found if " " in item[0] or not any(item[0] in phrase.split() for phrase in phrases)]   # “kifech l7al”, not “kifech” again
    if not found:
        return ""
    pairs = ", ".join(f"« {word} » = {meaning_fr}" if fr else f"“{word}” = {meaning_en}" for word, meaning_fr, meaning_en in found[:4])
    return (f"J'ai compris : {pairs}." if fr else f"I read: {pairs}.")


def _send_now(db: Session, people: list[dict], text: str, order: dict, reworded, unknown: list[str], fr: bool, *, author: str, notes: list[str]) -> dict:
    lines, links = [], []
    sent_any = False
    for person in people:
        for result in _send_one(db, person, text, order["ways"], author=author, english=order["english"]):
            way = _WAY_NAMES.get(result["way"], result["way"])
            if result["status"] == "sent":
                sent_any = True
                lines.append((f"✓ {person['name']} · {way} : envoyé." if fr else f"✓ {person['name']} · {way}: sent."))
            elif result["status"] == "waiting":
                lines.append((f"⏳ {person['name']} · {way} : prêt, il part de votre téléphone ({result['why']})." if fr
                              else f"⏳ {person['name']} · {way}: ready, it goes from your phone ({result['why']})."))
                if result.get("link"):
                    links.append({"label": f"Open {way} for {person['name'].split()[0]}", "url": result["link"]})
            else:
                lines.append((f"✗ {person['name']} · {way} : pas envoyé, parce que {result['why']}." if fr
                              else f"✗ {person['name']} · {way}: not sent, because {result['why']}."))
                if result.get("link") and result["link"].startswith("/"):
                    links.append({"label": f"Fix it for {person['name'].split()[0]}", "url": result["link"]})
    db.commit()
    for name in unknown:
        lines.append((f"? « {name} » : personne de ce nom, rien n'est parti pour lui." if fr else f"? “{name}”: nobody by that name, nothing went to them."))
    head = ("Voici ce qui s'est passé :" if fr else "Here is what happened:") if sent_any or lines else ""
    told = "\n".join(notes) + "\n\n" if notes else ""
    return {"reply": told + f"{head}\n" + "\n".join(lines) + ("\n\n" + ("Le texte envoyé :\n" if fr else "The text sent:\n") + text),
            "links": links[:5], "source": "action",
            "sources": [{"label": _source(reworded)},
                        {"label": "The way chosen above the inbox" if not order["ways"] else "The way you asked", "url": "/inbox#ways"}]}
