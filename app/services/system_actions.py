"""Yasmine changes the system when the admin asks: "mets le seuil de confiance à 80 %", "turn off the session reminder",
"passe le prix en ligne à 420", "the menu on the right", "désactive les réponses aux prospects".

Each setting she may change is in `SETTINGS`: its name in a few words (English, French, Arabic, Tunisian), its kind of
value and its limits, and the page where it lives. The words are read with these lists, never by the local model; the
change is said back exactly (what it is now, what it was), written down, and "undo" puts it back. Passwords, keys,
mail accounts and people's data are never changed from here. Only the admin, or a team member who may change the
settings, can do it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

KEEP = 40
ON = re.compile(r"\b(on|active[rz]?|activ[ée]e?s?|r[ée]active[rz]?|allume[rz]?|enable[ds]?|turn (?:it )?on|switch (?:it )?on|mets? en marche|d[ée]marre[rz]?|start)\b"
                r"|شغّ?ل|فعّ?ل|\b(?:7ell|ch3el|cha3el)\b", re.I)
OFF = re.compile(r"\b(off|d[ée]sactive[rz]?|d[ée]sactiv[ée]e?s?|[ée]teins|[ée]teindre|disable[ds]?|turn (?:it )?off|switch (?:it )?off|arr[êe]te[rz]?|stop|coupe[rz]?)\b"
                 r"|أوقف|عطّ?ل|\b(?:sakker|sakkar|9os|qos)\b", re.I)
VERB = re.compile(r"\b(change[rz]?|chang[ée]|modifie[rz]?|mets?|mettre|set|passe[rz]?|r[èe]gle[rz]?|active[rz]?|d[ée]sactive[rz]?|r[ée]active[rz]?|allume[rz]?|"
                  r"[ée]teins|turn|switch|enable|disable|increase|augmente[rz]?|baisse[rz]?|decrease|lower|raise|make|rends?|put|fais|update|move|d[ée]place[rz]?|"
                  r"arr[êe]te[rz]?|stop|start|d[ée]marre[rz]?|call|appelle|deviens|become|sois|wear|porte[rz]?|enl[èe]ve[rz]?|remove|take off|"
                  r"give|donne[rz]?|prends?|take|use|utilise[rz]?|choisis|choose)\b|بدّ?ل|غيّ?ر|خلّ?ي|حطّ?|فعّ?ل|أوقف|\b(?:sakker|ghayer|ghayyer|baddel|7ot|7ell|ch3el)\b", re.I)
_REMOVE = re.compile(r"\b(enl[èe]ve[rz]?|remove|take off|sans|without|plus de|no more)\b|نحّ?ي|انزع", re.I)
_NAME = re.compile(r"(?:call yourself|appelle[- ]toi|tu t'appelles?|ton (?:nouveau )?nom (?:est|sera|devient|c'est)|your (?:new )?name (?:is|will be)|rename yourself(?: to)?|"
                   r"change (?:ton|your) nom (?:en|to|à|pour)|change your name to|سمّي روحك|اسمك)\s*:?\s*[\"“«]?([^\W\d_][\w' -]{1,23}?)[\"”»]?\s*[.!]?\s*$", re.I)
_LIST = re.compile(r"(what|which|que|quoi|qu'est-ce que|quels?|quelles?).{0,25}(can you|peux-tu|tu peux|pouvez-vous|could you|settings?|r[ée]glages?|param[èe]tres?).{0,20}"
                   r"(change|modif|r[ée]gl)|what (?:settings|can you change)|liste des r[ée]glages|ماذا يمكنك أن تغير", re.I)
_ASK = re.compile(r"\b(what is|what's|what are|quel est|quelle est|quels sont|combien|current|actuel\w*)\b|ما هو|قداش|\b9adech\b", re.I)
_UNDO = re.compile(r"\b(undo|annuler?|annule|annulez|reviens|revenir en arri[èe]re|cancel (?:that|it)|remets? comme avant)\b|تراجع|\b(?:batel|rajja3)\b", re.I)
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)")


def _setting(key, label, words, kind, where, attr=None, **extra):
    return {"key": key, "label": label, "words": re.compile(words, re.I), "kind": kind, "where": where, "attr": attr, **extra}


_MODES = {"auto": r"\b(auto\w*|alone|seul|tout seul|by itself|wa7dou|wa7dha)\b", "review": r"\b(review|relire|relecture|v[ée]rif\w*|check|before sending|avant (?:l')?envoi|draft|brouillon)\b",
          "off": r"\b(off|d[ée]sactiv\w*|arr[êe]t\w*|stop|[ée]teint|never|jamais|sakker)\b"}
SETTINGS = [
    _setting("reply_mode", "How the assistant answers emails", r"(mode|r[ée]ponses?|answers?|assistant).{0,30}(e-?mails?|courriels?|mails?)|"
             r"(e-?mails?|mails?).{0,20}(mode|r[ée]ponses?|answers?|replies)|auto[- ]?reply|r[ée]ponse automatique", "choice", "/settings#assistant",
             attr="email_auto_reply_mode", choices=_MODES, names={"auto": "answers alone when sure", "review": "prepares, you send", "off": "off"}),
    _setting("whatsapp_mode", "How the assistant answers on WhatsApp", r"whatsapp.{0,25}(mode|r[ée]ponses?|answers?|replies|assistant)|"
             r"(mode|r[ée]ponses?|answers?|assistant).{0,25}whatsapp", "choice", "/settings#assistant", attr="whatsapp_auto_reply", choices=_MODES,
             names={"auto": "answers alone when sure", "review": "prepares, you send", "off": "off"}),
    _setting("threshold", "The confidence needed to answer alone", r"\b(seuil|threshold|confiance|confidence)\b|ثقة", "percent", "/settings#assistant",
             attr="email_confidence_threshold", least=50, most=95),
    _setting("reword_send", "Reword and send at once", r"reformul\w* et envo\w*|reword\w* and send", "bool", "/inbox", attr="reword_and_send"),
    _setting("prospects", "Answers to people not registered yet", r"\b(prospects?|inconnus|strangers|non[- ]inscrits?|not registered)\b", "bool",
             "/settings#prospects", attr="prospect_answers_on"),
    _setting("team_reply", "The assistant answers the team", r"(équipe|equipe|team).{0,25}(r[ée]pon\w*|answers?|repl\w*)", "bool", "/settings#assistant",
             attr="team_auto_reply"),
    _setting("send_way", "The way messages go first", r"(moyen|way|canal|channel).{0,25}(d'envoi|envoi|send\w*|messages?)|send (?:way|by)|envoyer par", "choice",
             "/inbox#ways", attr="send_way", choices={"auto": r"\b(auto\w*|system|syst[èe]me|choose|choisi\w*)\b", "email": r"\be-?mails?\b|courriel",
                                                         "whatsapp": r"\bwhatsapp\b|واتساب", "sms": r"\bsms\b"},
             names={"auto": "the system chooses", "email": "Email", "whatsapp": "WhatsApp", "sms": "SMS"}),
    _setting("send_fallback", "Try the next way when one fails", r"\b(fallback|secours|next way|autre moyen|another way)\b", "bool", "/inbox#ways", attr="send_fallback"),
    _setting("class_capacity", "Seats in a new class", r"(capacit\w*|places?|seats?|taille).{0,20}(class\w*)|class(?:e|es)? (?:size|capacity)", "int",
             "/settings#classes", attr="default_class_capacity", least=1, most=200),
    _setting("min_start", "Candidates needed to start a class", r"(minimum|min\b|au moins).{0,30}(d[ée]marr\w*|start\w*|commencer|ouvrir)", "int",
             "/settings#classes", attr="min_students_to_start", least=1, most=100),
    _setting("start_notice", "Days of notice before a class starts", r"(pr[ée]avis|notice)", "int", "/settings#classes", attr="min_start_notice_days", least=0, most=60),
    _setting("program_weeks", "Weeks of a new program", r"(semaines?|weeks?).{0,25}(programmes?|programs?|formations?)|(programmes?|programs?).{0,25}(semaines?|weeks?)",
             "int", "/settings#classes", attr="default_program_weeks", least=1, most=104),
    _setting("price_online", "The online price", r"prix.{0,20}(en ligne|online)|(online|en ligne).{0,15}(price|prix)|سعر.{0,15}(أونلاين|عن بعد)", "price",
             "/programs/all", attr="price_online", least=0, most=100000),
    _setting("price_onsite", "The on-site price", r"prix.{0,20}(pr[ée]sentiel|sur place|on ?site)|(on ?site|pr[ée]sentiel).{0,15}(price|prix)|سعر.{0,15}حضوري",
             "price", "/programs/all", attr="price_onsite", least=0, most=100000),
    _setting("level_days", "Days before the class for the level check", r"(test|check) (?:de )?niveau.{0,30}(jours?|days?)|level check.{0,30}days?",
             "int", "/quiz/all", attr="level_check_days_before", least=1, most=30),
    _setting("weekly_every", "Weekly questions every (weeks)", r"questions? (?:de la semaine|hebdo\w*)|weekly questions?", "int", "/quiz/weekly#wk-how",
             attr="weekly_every", allowed=(1, 2, 4)),
    _setting("session_check", "Questions after each session", r"questions? apr[èe]s (?:la |chaque )?s[ée]ance|questions? after (?:each |the )?session|session check",
             "bool", "/settings#session-check", attr="session_check_on"),
    _setting("report", "The report by email", r"\b(rapport|report)\b", "bool", "/settings#report", attr="report_email_on"),
    _setting("look", "The look of the pages", r"\b(th[èe]me|theme|apparence|appearance|look|mode sombre|dark mode|mode nuit|night mode)\b", "choice", "/settings#appearance",
             attr="ui_mode", choices={"night-blue": r"bleu nuit|night blue|dark blue", "night": r"\b(sombre|dark|nuit|night|noir)\b",
                                      "colours": r"\b(couleurs?|colou?rs|colou?rful|color[ée])\b", "soft": r"\b(doux|soft|clair|light|normal)\b"},
             names={"soft": "soft", "colours": "colours", "night": "night", "night-blue": "night blue"}, empty="soft"),
    _setting("menu_side", "The side of the menu", r"\bmenu\b", "choice", "/settings#appearance", attr="ui_menu_side",
             choices={"right": r"\b(droite|right)\b|يمين", "left": r"\b(gauche|left)\b|يسار"}, names={"left": "left", "right": "right"}, empty="left"),
    _setting("background", "The background", r"\b(fond|background|arri[èe]re-plan)\b", "choice", "/settings#appearance", attr="ui_background",
             choices={"plain": r"\b(fixe|plain|still|static|immobile|simple)\b", "animated": r"\b(anim[ée]e?|animated|moving|bouge)\b"},
             names={"animated": "animated", "plain": "still"}, empty="animated"),
    _setting("app_name", "The name of the application", r"nom (?:de l'|de la |du )?(?:application|appli|plateforme|logiciel)|(?:app|application) name", "text",
             "/settings#identity", attr="app_name", most=40),
    _setting("centre_phone", "The centre's phone", r"(t[ée]l[ée]phone|phone|num[ée]ro).{0,15}(du centre|of the centre|centre)|centre'?s? (?:phone|number)", "text",
             "/settings#centre", attr="centre_phone", most=30),
    _setting("centre_address", "The centre's address", r"adresse (?:du centre)?|centre'?s? address|\baddress\b|عنوان", "text", "/settings#centre",
             attr="centre_address", most=200),
    _setting("form_link", "The registration form's link", r"(lien|link).{0,25}(formulaire|form|inscription|registration)", "url", "/settings#identity",
             attr="registration_form_url", most=500),
    # The automatic reminders (services/reminder_board.py): on or off, and their timing.
    _setting("payment_reminder", "The payment reminder", r"rappel (?:de |du )?paiement|payment reminder|تذكير (?:بال)?دفع", "reminder", "/reminders#auto", unit="hours"),
    _setting("session_reminder", "The reminder before each session", r"rappel (?:avant|de) (?:la |chaque )?s[ée]ance|session reminder|reminder before (?:each |the )?session",
             "reminder", "/reminders#auto", unit="hours"),
    _setting("training_reminder", "The countdown before the first session", r"compte [àa] rebours|countdown|premi[èe]re s[ée]ance|first session", "reminder",
             "/reminders#auto", unit="days"),
    _setting("session_link", "The session link", r"lien (?:de |de la )?s[ée]ance|session link", "reminder", "/reminders#auto"),
    _setting("level_reminder", "The level check reminder", r"rappel (?:du |de )?test (?:de )?niveau|level check reminder", "reminder", "/reminders#auto"),
    _setting("resources", "The week's resources reminder", r"rappel (?:des )?ressources|resources? reminder", "reminder", "/reminders#auto", unit="days"),
    # Yasmine herself (services/friend.py): "call yourself Salma", "put on glasses", "a man's voice".
    _setting("my_name", "My name", r"call yourself|appelle[- ]toi|tu t'appelles?|(?:ton|your) (?:nouveau |new )?(?:nom|name)|rename yourself|اسمك|سمّي روحك", "friend",
             "/settings#character", field="name", most=24),
    _setting("my_voice", "My voice", r"(?:ta|your|sa|her) voix|\bvoice\b|\bvoix\b|صوتك", "friend", "/settings#character", field="voice",
             choices={"male": r"\b(homme|man|male|masculine?)\b|رجل|راجل", "female": r"\b(femme|woman|female|f[ée]minine)\b|امرأة|مرا",
                      "auto": r"\b(auto\w*|normal\w*|comme avant|as (?:the|my) character|ma voix)\b"},
             names={"auto": "As the character", "female": "A woman's voice", "male": "A man's voice"}),
    _setting("my_glasses", "My glasses", r"\b(lunettes|glasses)\b|نظارات|نظّارات", "friend", "/settings#character", field="glasses", flag=True),
    _setting("my_gender", "Me as a man or a woman", r"(?:sois|be|deviens|become|transforme[- ]toi en)\s+(?:un |une |a )?(?:homme|femme|man|woman)\b", "friend",
             "/settings#character", field="gender", choices={"male": r"\b(homme|man)\b", "female": r"\b(femme|woman)\b"}, names={"female": "Woman", "male": "Man"}),
    _setting("my_hair", "My hair", r"\b(cheveux|hair|coiffure|voile|hijab|headscarf|foulard|chignon)\b|شعر|حجاب", "friend", "/settings#character", field="hair",
             choices={"hijab": r"voile|hijab|headscarf|foulard|حجاب", "bun": r"chignon|\bbun\b", "curly": r"boucl|fris[ée]|curly",
                      "bald": r"ras[ée]|bald|shaved|chauve", "long": r"\blong", "short": r"\bcourts?\b|\bshort"},
             names={"bun": "Bun", "long": "Long", "short": "Short", "curly": "Curly", "hijab": "Headscarf", "bald": "Shaved"}),
    _setting("my_outfit", "My outfit", r"\b(tenue|outfit|v[êe]tements?|clothes|habits?|blazer|hoodie|sweat|t-?shirt|chemise|cravate|chemisier|blouse)\b", "friend",
             "/settings#character", field="outfit", choices={"blazer": r"blazer|veste", "hoodie": r"hoodie|sweat", "shirt": r"chemise|cravate|shirt and tie|\btie\b",
                                                          "tshirt": r"t-?shirt", "blouse": r"blouse|chemisier"},
             names={"blouse": "Blouse", "blazer": "Blazer", "hoodie": "Hoodie", "shirt": "Shirt and tie", "tshirt": "T-shirt"}),
]
_BY_KEY = {item["key"]: item for item in SETTINGS}


def _log(db: Session) -> list[dict]:
    from app.services.settings_service import get_settings

    try:
        found = json.loads(get_settings(db).assistant_changes or "[]")
    except (TypeError, ValueError):
        found = []
    return [item for item in found if isinstance(item, dict)][:KEEP]


def _write_log(db: Session, items: list[dict]) -> None:
    from app.services.settings_service import get_settings

    get_settings(db).assistant_changes = json.dumps(items[:KEEP], ensure_ascii=False)


def find(text: str) -> dict | None:
    """The setting the words name (the longest match wins)."""
    best, length = None, 0
    for item in SETTINGS:
        match = item["words"].search(text or "")
        if match and len(match.group(0)) > length:
            best, length = item, len(match.group(0))
    return best


def may(user) -> bool:
    from app.services import access

    if user is None or getattr(user, "is_admin", False):
        return True
    if getattr(user, "is_professor", False):
        return False
    return access.allows(access.clean(getattr(user, "permissions", None)), "/settings/x", "POST")


def wants(db: Session, text: str) -> bool:
    """Is this a change of the system, a question about a setting, the list, or "undo" of the last change made here?"""
    text = text or ""
    if _LIST.search(text):
        return True
    if _UNDO.search(text) and len(text) < 60:
        return _latest(db) is not None
    item = find(text)
    return item is not None and bool(VERB.search(text) or _ASK.search(text) or re.search(r"\s(?:à|a|to|=|:)\s*\S", text))


def _latest(db: Session) -> str | None:
    """Where the last change of all was made: "system" (here) or "inbox" (the inbox's rules), or None."""
    from app.services import reply_rules

    mine = _log(db)
    theirs = reply_rules.rules(db)["changes"]
    if not mine and not theirs:
        return None
    if not theirs:
        return "system"
    if not mine:
        return "inbox"
    return "system" if mine[0].get("at", "") >= theirs[0].get("at", "") else "inbox"


def current(db: Session, item: dict):
    from app.services import reminder_board
    from app.services.settings_service import get_settings

    if item["kind"] == "reminder":
        row = next((row for row in reminder_board.items(db) if row["key"] == item["key"]), None)
        return None if row is None else {"on": row["on"], "timing": (row.get("timing") or {}).get("value")}
    if item["kind"] == "friend":
        from app.services import friend

        return friend.look(db).get(item["field"])
    value = getattr(get_settings(db), item["attr"], None)
    if item["kind"] == "choice" and value in (None, ""):
        return item.get("empty", "auto")
    return value


def shown(item: dict, value) -> str:
    """A value as the admin reads it."""
    if item["kind"] == "bool" or (item["kind"] == "friend" and item.get("flag")):
        return _t("on") if value else _t("off")
    if item["kind"] == "friend":
        return _t(item["names"].get(value, str(value))) if item.get("names") else (str(value) if value not in (None, "") else "—")
    if item["kind"] == "choice":
        return _t(item["names"].get(value, str(value)))
    if item["kind"] == "percent":
        return f"{round(float(value or 0) * 100)}%"
    if item["kind"] == "price":
        return f"{float(value or 0):g} DT"
    if item["kind"] == "reminder":
        if not isinstance(value, dict):
            return "—"
        state = _t("on") if value.get("on") else _t("off")
        return state + (f" · {value['timing']} {_t(item.get('unit', ''))}" if value.get("timing") not in (None, "") and item.get("unit") else "")
    return str(value) if value not in (None, "") else "—"


def _value(item: dict, text: str):
    """The new value the words give, or raises ValueError with what is missing."""
    kind = item["kind"]
    if kind == "bool":
        if OFF.search(text):
            return False
        if ON.search(text):
            return True
        raise ValueError(_t("Say “on” or “off” for {0}.", _t(item["label"])))
    if kind == "choice":
        rest = item["words"].sub(" ", text)
        for where in (rest, text):              # the value may be in the setting's own name ("mode sombre")
            for value, words in item["choices"].items():
                if re.search(words, where, re.I):
                    return value
        raise ValueError(_t("Say which: {0}.", ", ".join(_t(name) for name in item["names"].values())))
    if kind in ("int", "percent", "price"):
        numbers = [float(found.replace(",", ".")) for found in _NUMBER.findall(item["words"].sub(" ", text))]
        if not numbers:
            raise ValueError(_t("Give the number for {0}.", _t(item["label"])))
        number = numbers[-1]
        if kind == "percent":
            number = number * 100 if number <= 1 else number
            if not item["least"] <= number <= item["most"]:
                raise ValueError(_t("{0} must be between {1}% and {2}%.", _t(item["label"]), item["least"], item["most"]))
            return round(number / 100, 2)
        if "allowed" in item and int(number) not in item["allowed"]:
            raise ValueError(_t("{0} can be: {1}.", _t(item["label"]), ", ".join(str(value) for value in item["allowed"])))
        if not item.get("least", 0) <= number <= item.get("most", 10 ** 9):
            raise ValueError(_t("{0} must be between {1} and {2}.", _t(item["label"]), item.get("least", 0), item.get("most")))
        return int(number) if kind == "int" else round(number, 2)
    if kind in ("text", "url"):
        quoted = re.search(r"[\"“«]\s*(.+?)\s*[\"”»]", text)
        after = re.search(r"(?:\s(?:à|a|to|est|is|devient|becomes|=|:)\s+|[:=]\s*)(.+)$", item["words"].sub(" ", text), re.I)
        value = (quoted.group(1) if quoted else after.group(1) if after else "").strip().strip(".")
        if not value:
            raise ValueError(_t("Say the new {0}, after “to”: “{1} to …”.", _t(item["label"]).lower(), _t(item["label"])))
        if kind == "url" and not re.match(r"^https?://", value, re.I):
            raise ValueError(_t("A link starts with http:// or https://."))
        return value[: item.get("most", 200)]
    if kind == "friend":
        if item.get("flag"):                     # her glasses: "put on glasses", "enlève tes lunettes"
            return not (_REMOVE.search(text) or OFF.search(text))
        if item.get("choices"):
            for value, words in item["choices"].items():
                if re.search(words, item["words"].sub(" ", text), re.I) or re.search(words, text, re.I):
                    return value
            raise ValueError(_t("Say which: {0}.", ", ".join(_t(name) for name in item["names"].values())))
        named = _NAME.search(text.strip())
        quoted = re.search(r"[\"“«]\s*(.+?)\s*[\"”»]", text)
        value = (quoted.group(1) if quoted else named.group(1) if named else "").strip()
        if len(value) < 2:
            raise ValueError(_t("Which name? Say “call yourself Salma”."))
        return value[: item.get("most", 24)]
    if kind == "reminder":
        if OFF.search(text):
            return {"on": False}
        numbers = _NUMBER.findall(item["words"].sub(" ", text))
        if numbers and item.get("unit"):
            return {"timing": numbers[-1].replace(",", ".")}
        if ON.search(text):
            return {"on": True}
        raise ValueError(_t("Say “on”, “off”, or its timing ({0}).", _t(item.get("unit") or "on")))
    raise ValueError(_t("This setting cannot be changed from here."))


def _apply(db: Session, item: dict, value) -> None:
    from app.services import reminder_board
    from app.services.settings_service import get_settings

    if item["kind"] == "reminder":
        if "on" in value:
            reminder_board.switch(db, item["key"], value["on"])
        if "timing" in value:
            reminder_board.set_timing(db, item["key"], value["timing"])
        return
    if item["kind"] == "friend":
        from app.services import friend

        chosen = friend.look(db)
        chosen[item["field"]] = value
        if item["field"] == "gender" and value == "male" and chosen.get("hair") == "hijab":
            chosen["hair"] = "short"
        friend.save_look(db, chosen)
        return
    stored = None if item["kind"] == "choice" and value == item.get("empty") else value
    setattr(get_settings(db), item["attr"], stored)


def _restore(db: Session, item: dict, before) -> None:
    if item["kind"] == "reminder":
        if isinstance(before, dict):
            _apply(db, item, {key: value for key, value in (("on", before.get("on")), ("timing", before.get("timing"))) if value is not None})
        return
    _apply(db, item, before)


def change(db: Session, item: dict, value, *, said: str = "", by: str | None = None) -> dict:
    """Changes one setting, writes down what it was, and returns {"before", "after"} as read by the admin."""
    before = current(db, item)
    _apply(db, item, value)
    db.flush()
    after = current(db, item)
    if after == before:                         # nothing changed: nothing to write down, nothing to undo
        db.commit()
        return {"before": shown(item, before), "after": shown(item, after)}
    _write_log(db, [{"at": datetime.utcnow().isoformat(timespec="microseconds"), "key": item["key"], "before": before, "after": after,
                     "said": (said or "")[:300], "by": by}] + _log(db))
    db.commit()
    return {"before": shown(item, before), "after": shown(item, after)}


def undo(db: Session) -> dict | None:
    log = _log(db)
    if not log:
        return None
    last = log.pop(0)
    item = _BY_KEY.get(last.get("key"))
    if item is not None:
        _restore(db, item, last.get("before"))
    _write_log(db, log)
    db.commit()
    return {"item": item, "before": shown(item, last.get("before")) if item else "", "after": shown(item, last.get("after")) if item else ""}


def catalogue() -> list[dict]:
    """What Yasmine can change, by page."""
    return [{"key": item["key"], "label": _t(item["label"]), "where": item["where"]} for item in SETTINGS]


def answer(db: Session, text: str, *, user=None, lang: str | None = None) -> dict:
    """What Yasmine says (after doing it) in the language of the question."""
    from app.services import howto, ui_words

    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(text)
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        reply, links = _answer(db, text or "", user)
    finally:
        ui_words.current.reset(token)
    return {"reply": reply, "links": links, "source": "system", "sources": [{"label": _t("Settings"), "url": "/settings"}], "resources": []}


def _answer(db: Session, text: str, user) -> tuple[str, list[dict]]:
    if not may(user):
        return _t("Changing the system needs the right to change the settings: ask the admin."), []
    if _LIST.search(text):
        names = "; ".join(item["label"] for item in catalogue())
        return _t("I can change, when you ask: {0}. Say for example “set the confidence to 80%” or “turn off the session reminder”; “undo” puts the last change back.",
                  names), [{"label": "Settings", "url": "/settings"}]
    if _UNDO.search(text) and len(text) < 60:
        if _latest(db) == "inbox":                  # the last change was one of the inbox's rules: that one is undone
            from app.services import reply_rules

            return reply_rules.talk(db, text, by=getattr(user, "email", None))["reply"], [{"label": "Set-aside messages", "url": "/inbox?filter=aside"}]
        done = undo(db)
        if done is None or done["item"] is None:
            return _t("There is no change to undo."), []
        return _t("Undone: {0} is back to {1}.", _t(done["item"]["label"]), done["before"]), [{"label": _t(done["item"]["label"]), "url": done["item"]["where"]}]
    item = find(text)
    if item is None:
        return _t("Which setting? Say “what can you change?” for the list."), [{"label": "Settings", "url": "/settings"}]
    link = [{"label": _t(item["label"]), "url": item["where"]}]
    if _ASK.search(text) and not VERB.search(text):
        return _t("{0}: {1}.", _t(item["label"]), shown(item, current(db, item))), link
    try:
        value = _value(item, text)
        done = change(db, item, value, said=text, by=getattr(user, "email", None))
    except ValueError as error:
        db.rollback()
        return str(error), link
    if done["before"] == done["after"]:
        return _t("{0} is already {1}.", _t(item["label"]), done["after"]), link
    return _t("Done: {0} is now {1} (it was {2}). Say “undo” to put it back.", _t(item["label"]), done["after"], done["before"]), link
