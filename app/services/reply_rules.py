"""Messages the assistant sets aside without an answer: why each one was, and the rules, which the admin changes by
saying so (a button, a few words under the message in the inbox, or to Yasmine).

A message is set aside (`candidate_emails.status` "no_reply"; why in `candidate_emails.set_aside`) when:
- "thanks": it only thanks or acknowledges: it starts with one of `thanks_words`, asks nothing, and is shorter than
  `max_length` (with `only_thanks`, it must also say nothing else);
- "automatic": its headers say it is an automatic reply (out of office, a mailing list);
- "professor": it was a course question sent on to the professor, who answers in the application;
- "opinion": it was the candidate's opinion, answering the end-of-program email;
- "payment": it said which program a payment proof was for;
- "admin": the admin chose it.

"It needed an answer": the message comes back with an answer prepared for the admin to read, and the rule that set it
aside changes so the next ones like it are kept: the narrowest change that would have kept this one (only messages
made of thanks alone are set aside; or every message from this person is answered; or this sender is never taken for
an automatic reply). Each change is written down with what it was before, and can be undone.

The admin's words are read with word lists (English, French, Arabic, Tunisian), never by the local model: a change
of rule is said back exactly, before anything else happens.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

DEFAULT_WORDS = ["merci", "thanks", "thank you", "ok", "d'accord", "bien reçu", "bien recu", "parfait", "super", "top", "nickel",
                 "c'est noté", "c est noté", "noté", "شكرا", "يعطيك الصحة", "3aychek", "aychek"]
DEFAULTS = {"thanks_words": DEFAULT_WORDS, "only_thanks": False, "max_length": 200, "always_answer": [], "read_automatic": []}
RULES = ("thanks", "automatic", "professor", "opinion", "payment", "admin")
KEEP_CHANGES = 30
# Words that may stand around a thank-you without making it more than one ("merci beaucoup pour votre retour !").
_AROUND = set("""beaucoup bcp infiniment encore mille fois pour votre vos ton ta tes réponse reponse retour aide informations information
info infos message rapide much very so lot a for your the reply answer help quick you vous toi et and bien très tres bonne journée journee
soirée soiree have nice day good cordialement regards best salutations madame monsieur à a de du des la le les mrc thx bjr bonjour bonsoir
hello hi salam slm cher chère chere dear okay ok all tout tous جزيلا برشا لكم ليك عليكم""".split())


# ------------------------------------------------------------------
# The rules
# ------------------------------------------------------------------

def rules(db: Session) -> dict:
    """The rules as they are now (the defaults, with what the admin changed)."""
    from app.services.settings_service import get_settings

    try:
        saved = json.loads(get_settings(db).reply_rules or "{}")
    except (TypeError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    found = {key: saved.get(key, value) for key, value in DEFAULTS.items()}
    found["thanks_words"] = [str(word).strip().lower() for word in found["thanks_words"] or [] if str(word).strip()][:80]
    try:
        found["max_length"] = max(20, min(2000, int(found["max_length"])))
    except (TypeError, ValueError):
        found["max_length"] = DEFAULTS["max_length"]
    found["only_thanks"] = bool(found["only_thanks"])
    for key in ("always_answer", "read_automatic"):
        found[key] = sorted({str(item).strip().lower() for item in found[key] or [] if str(item).strip()})
    found["changes"] = [item for item in saved.get("changes") or [] if isinstance(item, dict)][:KEEP_CHANGES]
    return found


def _save(db: Session, found: dict) -> None:
    from app.services.settings_service import get_settings

    get_settings(db).reply_rules = json.dumps(found, ensure_ascii=False)


def pattern(found: dict) -> re.Pattern:
    """A message that starts with a word of thanks and asks nothing (the same test as before, with the admin's words)."""
    words = sorted(found["thanks_words"], key=len, reverse=True)
    if not words:
        return re.compile(r"(?!)")
    return re.compile(r"^\W*(" + "|".join(re.escape(word) for word in words) + r")\b[^?]*$", re.IGNORECASE)


def pure_thanks(text: str, found: dict, name: str = "") -> bool:
    """Nothing but thanks: once the words of thanks and what stands around them are taken away, nothing is left but a
    name (the person's, or two words written with a capital, like a signature)."""
    folded = (text or "").lower()
    for phrase in sorted(found["thanks_words"], key=len, reverse=True):
        folded = folded.replace(phrase, " ")
    names = {part for part in (name or "").lower().split() if part}
    signed = [word.lower() for word in re.findall(r"\b[A-ZÀ-Ý][a-zà-ÿ]+", text or "")]
    left = [word for word in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", folded) if word not in _AROUND and word not in names]
    return all(word in signed for word in left) and len(left) <= 2


def _who(message, candidate=None) -> str:
    person = candidate if candidate is not None else getattr(message, "candidate", None)
    return ((getattr(message, "sender", None) or getattr(person, "email", None) or "")).strip().lower()


def always_answered(found: dict, message, candidate=None) -> bool:
    person = candidate if candidate is not None else getattr(message, "candidate", None)
    known = {_who(message, candidate), (getattr(person, "email", None) or "").strip().lower(), (getattr(person, "phone", None) or "").strip().lower()}
    return bool(known - {""} & set(found["always_answer"]))


def reads_automatic(db: Session, sender: str) -> bool:
    """This sender's messages are never taken for automatic replies (the admin said so)."""
    return (sender or "").strip().lower() in rules(db)["read_automatic"]


# ------------------------------------------------------------------
# Why a message was set aside
# ------------------------------------------------------------------

def mark(message, rule: str, **details) -> None:
    message.set_aside = json.dumps({"rule": rule, **{key: value for key, value in details.items() if value}}, ensure_ascii=False)


def reason_of(message) -> dict:
    try:
        found = json.loads(message.set_aside or "{}")
    except (TypeError, ValueError):
        found = {}
    if isinstance(found, dict) and found.get("rule") in RULES:
        return found
    # Set aside before the reasons were kept: told from what was written then.
    analysis = message.analysis or ""
    if analysis.startswith("Automatic reply"):
        return {"rule": "automatic"}
    if message.intent == "greeting":
        return {"rule": "thanks"}
    if analysis.startswith("Sent to the professor"):
        return {"rule": "professor"}
    if analysis.startswith("Their opinion"):
        return {"rule": "opinion"}
    if analysis.startswith("The payment proof is for"):
        return {"rule": "payment"}
    return {"rule": "admin"}


def explain(message) -> str:
    """Why it was set aside, in a sentence."""
    why = reason_of(message)
    rule = why["rule"]
    if rule == "thanks":
        word = why.get("word") or ""
        if word:
            return _t("It starts with “{0}” and asks nothing, so it was taken for a thank-you and not answered.", word)
        return _t("It only thanks or says it is noted, and asks nothing, so it was not answered.")
    if rule == "automatic":
        return _t("It came marked as an automatic reply (an out-of-office message or a mailing list), so it was not answered.")
    if rule == "professor":
        return _t("It was a question about the course: it went to the professor, {0}, who answers in the application.", why.get("who") or "")
    if rule == "opinion":
        return _t("It was their opinion on the program, answering the email that asked for it: it is kept with the class.")
    if rule == "payment":
        return _t("It said which program their payment was for: the proof waits for you on the Payments page.")
    return _t("It was marked by hand as not needing an answer.")


def proposal(message, found: dict) -> dict | None:
    """The narrowest change of rule that would have kept this message from being set aside, or None."""
    rule = reason_of(message)["rule"]
    who = _who(message)
    if rule == "thanks":
        name = getattr(getattr(message, "candidate", None), "full_name", "") or ""
        if not found["only_thanks"] and not pure_thanks(message.body or "", found, name):
            return {"key": "only_thanks", "value": True}
        if who and who not in found["always_answer"]:
            return {"key": "always_answer", "add": who}
    elif rule == "automatic" and who and who not in found["read_automatic"]:
        return {"key": "read_automatic", "add": who}
    return None


def describe(change: dict) -> str:
    """A change of rule, said plainly (in the page's language)."""
    key = change.get("key")
    if key == "only_thanks":
        return (_t("From now on, only a message made of thanks alone is set aside; one that says anything more is answered.") if change.get("value")
                else _t("From now on, a short message that starts with a word of thanks and asks nothing is set aside again."))
    if key == "always_answer":
        return (_t("From now on, every message from {0} is answered, even a simple thank-you.", change["add"]) if "add" in change
                else _t("{0} is treated like everyone again.", change.get("remove", "")))
    if key == "read_automatic":
        return (_t("From now on, the messages from {0} are never taken for automatic replies.", change["add"]) if "add" in change
                else _t("The messages from {0} marked as automatic are set aside again.", change.get("remove", "")))
    if key == "thanks_words":
        return (_t("From now on, a message that starts with “{0}” is answered.", change["remove"]) if "remove" in change
                else _t("From now on, a short message that only says “{0}” is set aside.", change.get("add", "")))
    if key == "max_length":
        return _t("From now on, only messages shorter than {0} characters can be set aside.", change.get("value"))
    return ""


def apply(db: Session, change: dict | None, *, said: str = "", message_id: str | None = None, by: str | None = None) -> dict | None:
    """Change one rule and write down what it was before (for "undo"). None when nothing changes."""
    if not change or change.get("key") not in DEFAULTS:
        return None
    found = rules(db)
    key = change["key"]
    before = found[key]
    if "add" in change:
        after = sorted(set(before) | {str(change["add"]).strip().lower()}) if key != "thanks_words" else before + [str(change["add"]).strip().lower()]
    elif "remove" in change:
        after = [item for item in before if item != str(change["remove"]).strip().lower()]
    else:
        after = change.get("value")
    if after == before:
        return None
    found[key] = after
    found["changes"] = ([{"at": datetime.utcnow().isoformat(timespec="microseconds"), "key": key, "before": before, "after": after,
                          "change": change, "said": (said or "")[:300], "message_id": message_id, "by": by}] + found["changes"])[:KEEP_CHANGES]
    _save(db, found)
    return {**change, "text": describe(change)}


def undo(db: Session) -> dict | None:
    """Put back the rule as it was before the last change."""
    found = rules(db)
    if not found["changes"]:
        return None
    last = found["changes"].pop(0)
    if last.get("key") in DEFAULTS:
        found[last["key"]] = last.get("before", DEFAULTS[last["key"]])
    _save(db, found)
    return last


def summary(found: dict) -> list[str]:
    """The rules as sentences."""
    lines = [_t("Words of thanks that start a message set aside: {0}.", ", ".join(f"“{word}”" for word in found["thanks_words"]) or "—"),
             _t("Only messages shorter than {0} characters, that ask nothing.", found["max_length"])]
    if found["only_thanks"]:
        lines.append(_t("And only when they say nothing but thanks."))
    if found["always_answer"]:
        lines.append(_t("Always answered, even for a thank-you: {0}.", ", ".join(found["always_answer"])))
    if found["read_automatic"]:
        lines.append(_t("Never taken for automatic replies: {0}.", ", ".join(found["read_automatic"])))
    return lines


# ------------------------------------------------------------------
# "It needed an answer"
# ------------------------------------------------------------------

def needs_answer(db: Session, message, *, said: str = "", by: str | None = None, learn: bool = True) -> dict:
    """The admin says this set-aside message needed an answer: the rule changes (when one set it aside) and the message
    comes back with an answer prepared for the admin to read. Returns {"change", "how"}."""
    from app.models.email_thread import EmailThread
    from app.services import email_assistant

    found = rules(db)
    change = apply(db, proposal(message, found), said=said, message_id=message.id, by=by) if learn else None
    why = reason_of(message)
    message.set_aside = json.dumps({**why, "reopened": True}, ensure_ascii=False)
    thread = db.get(EmailThread, message.thread_id)
    if why["rule"] in ("thanks", "automatic") and thread is not None:
        # Read again by the assistant: this time it prepares an answer, and the admin reads it before it goes.
        message.status, message.requires_admin, message.suggested_reply, message.escalation_reason = "processing", False, None, None
        thread.status, thread.needs_admin = "processing", False
        db.commit()
        email_assistant.queue_processing(message.id)
        return {"change": change, "how": "again"}
    if thread is not None:
        email_assistant._ask_admin(db, thread, message, message.candidate, "You said this message needs an answer.", None)
    db.commit()
    return {"change": change, "how": "you"}


def set_aside_messages(db: Session, days: int = 60, limit: int = 40) -> list:
    """The messages set aside lately, newest first."""
    from app.models.candidate_email import CandidateEmail

    since = datetime.utcnow() - timedelta(days=days)
    return (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.status == "no_reply",
                                            CandidateEmail.received_at >= since)
            .order_by(CandidateEmail.received_at.desc()).limit(limit).all())


# ------------------------------------------------------------------
# Talking about it
# ------------------------------------------------------------------

_UNDO = re.compile(r"\b(undo|annuler?|annulez|annule|reviens|revenir|remets?|cancel)\b|تراجع|ألغ|الغ|\bbatel\b|\brajja3\b", re.I)
_SHOW = re.compile(r"\b(rules?|r[eè]gles?|param[eè]tres?|settings?|parameters?)\b|القواعد|الإعدادات|القوانين", re.I)
_WHY = re.compile(r"\b(why|pourquoi|pq|pk|reason|raison|explain|explique[rz]?)\b|علاش|لماذا|لاش|\b3l[ae]?ch\b|\b3leh\b", re.I)
_ANSWER = re.compile(r"\b(answer\w*|repl(?:y|ies|ied)|respon[sd]\w*|r[ée]pon[sd]\w*|jaw[e]?b\w*|jeweb\w*)\b|ردّ?|جاوب|إجابة|اجابة|يجاوب|الرد", re.I)
_NO_ANSWER = re.compile(r"(\b(?:no|not|pas|aucune?|sans|without|never|jamais|mech|mouch|ma)\b|n't|لا|ما|مش|موش)\W+(?:[\w'’]+\W+){0,3}?"
                        r"(need\w*|requir\w*|besoin|n[ée]cessit\w*|faut|demand\w*|m[ée]rit\w*|answer\w*|repl\w*|respon\w*|r[ée]pon\w*|jaweb\w*|رد|يحتاج|تحتاج|يلزم|لازم)", re.I)
_ALWAYS = re.compile(r"\b(always|toujours|tjrs?|dima|each time|chaque fois)\b|دائما|ديما|ديمة", re.I)
_STRICT = re.compile(r"\b(only|seulement|uniquement|juste|just|ken|ghir)\b.{0,30}\b(thanks?|thank you|merci|remerci\w*)\b|غير شكرا", re.I)
_AUTOMATIC = re.compile(r"\b(automatic|automatique|out of office|absence|auto-?reply)\b|تلقائي", re.I)
_YES = re.compile(r"^\s*(yes|yeah|yep|oui|ouais|d'accord|okay|ok|vas-y|go|sure|bien s[ûu]r|e+y+h?|aywa|na3am|behi)\b|^\s*(نعم|إي|اي|ايه|باهي)", re.I)
_QUOTED = re.compile(r"[\"“«‘]\s*([^\"”»’]{1,30}?)\s*[\"”»’]")
_LENGTH = re.compile(r"\b(\d{2,4})\s*(characters?|caract[eè]res?|letters?|lettres?|chars?)\b|(\d{2,4})\s*(حرف|حروف)", re.I)
_ABOUT = re.compile(r"set aside|mis(es)? de c[ôo]t[ée]|sans r[ée]ponse|no answer needed|r[èe]gles? de r[ée]ponse|reply rules|answer rules|"
                    r"messages? ignor[ée]s?|ignored messages?|(do(es)?n'?t|do(es)? not|ne) (need|require|n[ée]cessite)\w* (pas )?(of )?(an? |de |une )?(answer|reply|response|r[ée]ponse)|"
                    r"n'a pas besoin de r[ée]ponse|pas besoin de r[ée]pon|"
                    r"(message|email|mail|e-mail)s? (de|from|of|d') ?[\w'’ -]{2,40}? (needs?|needed|requires?|n[ée]cessite|demande|m[ée]rite|doit avoir|devait avoir) (an? |une )?(answer|reply|response|r[ée]ponse)|"
                    r"(why|pourquoi|علاش|3lech)\b.{0,40}\b(not|pas|didn'?t|n'as|n'a|ma|mech)\b.{0,25}(answer|repl|r[ée]pon|jaweb)|"
                    r"بدون رد|ما يحتاجش رد|ما تحتاجش رد", re.I)


def wants(text: str) -> bool:
    """Is this about the messages set aside, or their rules? (For Yasmine.)"""
    return bool(_ABOUT.search(text or ""))


def _named(db: Session, text: str):
    """The latest set-aside message of a person named in the words ("the message from Amira needs an answer")."""
    words = {word for word in re.findall(r"[^\W\d_]{3,}", (text or "").lower())}
    for message in set_aside_messages(db):
        person = message.candidate
        names = {part for part in (getattr(person, "full_name", "") or "").lower().split() if len(part) >= 3}
        if names & words:
            return message
    if re.search(r"\b(last|latest|dernier|derni[eè]re)\b|الأخير|الاخير", text or "", re.I):
        found = set_aside_messages(db, limit=1)
        return found[0] if found else None
    return None


def talk(db: Session, text: str, *, message=None, by: str | None = None) -> dict:
    """The answer to the admin's words about set-aside messages, after doing what they ask.
    {"reply": str, "changed": str|None, "reload": bool}"""
    said = (text or "").strip()
    if message is None and not (_SHOW.search(said) or _UNDO.search(said) or _LENGTH.search(said) or _QUOTED.search(said) or _STRICT.search(said)):
        message = _named(db, said)
    who = (getattr(getattr(message, "candidate", None), "full_name", None) or _who(message)) if message is not None else ""

    def done(reply: str, changed: dict | None = None, reload: bool = False) -> dict:
        db.commit()
        return {"reply": reply, "changed": changed["text"] if changed else None, "reload": reload}

    if _UNDO.search(said):
        last = undo(db)
        if last is None:
            return done(_t("There is no change to undo."))
        return done(_t("Undone: the rule is back as it was. The change was: {0}", describe(last.get("change") or {"key": last.get("key")})),
                    {"text": _t("The last change was undone.")})
    length = _LENGTH.search(said)
    if length:
        value = int(length.group(1) or length.group(3))
        changed = apply(db, {"key": "max_length", "value": max(20, min(2000, value))}, said=said, by=by)
        return done(changed["text"] if changed else _t("It is already so."), changed)
    quoted = _QUOTED.search(said)
    if quoted and (_ANSWER.search(said) or _NO_ANSWER.search(said)):
        word = quoted.group(1).strip().lower()
        if _NO_ANSWER.search(said):
            changed = apply(db, {"key": "thanks_words", "add": word}, said=said, by=by)
            return done(changed["text"] if changed else _t("“{0}” is already one of the words of thanks.", word), changed)
        if word not in rules(db)["thanks_words"]:
            return done(_t("“{0}” is not one of the words that set a message aside, so such messages are already read and answered.", word))
        changed = apply(db, {"key": "thanks_words", "remove": word}, said=said, by=by)
        return done(changed["text"], changed)
    if _STRICT.search(said):
        changed = apply(db, {"key": "only_thanks", "value": True}, said=said, by=by)
        return done(changed["text"] if changed else _t("It is already so: only a message made of thanks alone is set aside."), changed)
    if message is not None and _AUTOMATIC.search(said) and not _WHY.search(said):
        changed = apply(db, {"key": "read_automatic", "add": _who(message)}, said=said, message_id=message.id, by=by)
        result = needs_answer(db, message, said=said, by=by, learn=False) if message.status == "no_reply" else None
        return done((changed["text"] if changed else _t("It is already so.")) + (" " + _t("This message is read again now.") if result else ""), changed, bool(result))
    if message is not None and _ALWAYS.search(said) and (_ANSWER.search(said) or _YES.search(said)) and not _NO_ANSWER.search(said):
        changed = apply(db, {"key": "always_answer", "add": _who(message)}, said=said, message_id=message.id, by=by)
        result = needs_answer(db, message, said=said, by=by, learn=False) if message.status == "no_reply" else None
        return done((changed["text"] if changed else _t("It is already so.")) + (" " + _t("This message is read again now.") if result else ""), changed, bool(result))
    if _WHY.search(said):
        if message is None:
            return done(" ".join([_t("A message is set aside when it only thanks and asks nothing, when it is an automatic reply, or when it went to the professor or was an opinion asked for.")]
                                 + summary(rules(db))))
        change = proposal(message, rules(db))
        reply = explain(message)
        if message.status == "no_reply" and change:
            reply += " " + _t("If it needed an answer, say so: {0}", describe(change))
        elif message.status == "no_reply":
            reply += " " + _t("If it needed an answer, say so: it comes back to you, and no rule needs to change.")
        return done(reply)
    if _SHOW.search(said):
        found = rules(db)
        lines = summary(found)
        if found["changes"]:
            lines.append(_t("Last change: {0}", describe(found["changes"][0].get("change") or {"key": found["changes"][0].get("key")})))
        return done(" ".join(lines))
    if _NO_ANSWER.search(said) and not _YES.search(said):
        if message is not None:
            return done(_t("Fine: it stays set aside, and nothing changes."))
        return done(_t("Fine: nothing changes."))
    if _ANSWER.search(said) or _YES.search(said) or _ALWAYS.search(said):
        if message is None:
            return done(_t("Which message? Name the person (“the message from Amira needs an answer”), or say it under the message in the inbox."))
        if message.status != "no_reply":
            return done(_t("This message is not set aside: it was answered or waits for you."))
        result = needs_answer(db, message, said=said, by=by)
        reply = (_t("Done: {0}'s message comes back to you with an answer prepared, to read before it goes.", who) if result["how"] == "again"
                 else _t("Done: {0}'s message is now in the messages waiting for your answer.", who))
        if result["change"]:
            reply += " " + result["change"]["text"] + " " + _t("Say “undo” to put it back.")
        return done(reply, result["change"], True)
    return done(_t("I can explain why a message was set aside (“why?”), bring it back (“it needs an answer”), always answer a person (“always answer them”), change the words (“‘ok’ needs an answer”), show the rules (“the rules”), or undo the last change (“undo”)."))


def for_chat(db: Session, text: str, *, user=None, lang: str | None = None) -> dict:
    """The same, in Yasmine's window: in the language of the question (or the one chosen there); Tunisian in French."""
    from app.services import howto, ui_words

    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(text)
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        answer = talk(db, text, by=getattr(user, "email", None))
    finally:
        ui_words.current.reset(token)
    return {"reply": answer["reply"], "links": [{"label": "Set-aside messages", "url": "/inbox?filter=aside"}], "source": "system",
            "sources": [{"label": _t("The inbox"), "url": "/inbox?filter=aside"}], "resources": []}
