"""Yasmine on the candidates who may drop out: who they are, why, and what to do for each.

"When someone is about to drop out, what can I do?", "qui risque d'abandonner ?", "Fares veut arrêter", "chkoun bech
ybattel", "شكون بش يبطّل": she answers from what the application measured (dropout: absences, questions not answered,
results, money still owed, what they wrote) and with what it already prepared for each person (smart_reminders: the
message, the way they answer, the hour). What to do follows the reason measured, never a guess, and nothing is sent from
here: the message is prepared, the admin sends it.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

_COURSE = r"(?:the |his |her |their |our |this |la |sa |leur |cette |el |l )?(?:training|course|class|program\w*|bootcamp|formation|classe|cours)"
_ASKING = r"(?: (?:what|how|que|quoi|comment|je|on|i|we|chnowa|chna3mel|na3mel)\b|$)"
# Read on the plain question (lower case, no accents, no punctuation).
_LEAVE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"drop(?:s|ped|ping)? ?(?:out|off)|dropouts?|giv(?:e|es|ing) up|gave up|stop(?:s|ped|ping)? (?:coming|attending|showing up)|"
    r"(?:wants?|wanting|about|going|ready|thinking|plans?|planning|threatens?|threatening|decided|considering) (?:to |of |about )?"
    r"(?:leav\w*|stop\w*|quit\w*)(?=" + _ASKING + r"| " + _COURSE + r")|"
    r"(?:quit\w*|leav\w*|stop\w*) " + _COURSE + r"|"
    r"(?:keep|retain|hold on to) (?:a |the |my |our |this |these )?(?:candidates?|students?|learners?|trainees?)|retention|"
    r"(?:candidates?|students?|learners?|who|anyone|someone) (?:\w+ ){0,3}at risk|at risk (?:candidates?|students?|learners?)|"
    r"abandon\w*|decroch\w*|ne (?:vient|viennent) plus|fideliser|"
    r"(?:candidates?|candidats?|students?|learners?|etudiants?|apprenants?|trainees?|stagiaires?) (?:\w+ ){0,3}"
    r"(?:absent\w*|missed|missing|didn t come|did not come|doesn t come|n est pas venue?|ne vient pas|ghayeb|ghayba|ma jech|ma jetech)|"
    r"(?:veu(?:t|lent)|va|vont|compte|pense a|menace d|risquen?t? d|decide d|souhaite) ?(?:arreter|partir|quitter|lacher)(?=" + _ASKING + r"| " + _COURSE + r")|"
    r"(?:quitter|lacher|arreter) " + _COURSE + r"|"
    r"(?:retenir|garder) (?:un |une |le |la |les |ce |cette |mes |nos )?(?:candidats?|candidates?|etudiants?|apprenants?)|"
    r"[yt]e?bat+[ae]l\w*|ma ?3a?de?ch [yt](?:ji|jiw|e9ra|a9ra|9ra)|(?:y7eb|t7eb|bech|bch|bich) [yt](?:o|ou)?9[eo]f"
    r")(?![a-z0-9])")
# Read on the question as written (Arabic letters).
_LEAVE_AR = re.compile(r"ينقطع|انقطاع|ينسحب|انسحاب|يتخلى|التخلي|يبطل|يبطّل|ما عادش يجي|يترك (?:التكوين|الدورة|القسم|البرنامج)|"
                       r"يغادر (?:التكوين|الدورة|القسم)|يحب يوقف|يحب يحبس")
_WHO = re.compile(r"^(?:(?:and|et|so|alors|donc|ok|bon) )?(?:who|which|qui|lesquels|lesquelles|quels|quelles|chkoun|chkun|chkon|combien|how many)\b|"
                  r"\b(?:list|liste|names|noms|is anyone|is someone|are there|any candidates?|y a t il|il y a des|est ce que quelqu un)\b")
_WHO_AR = re.compile(r"^\s*(?:شكون|من هم|من )|قائمة|أسماء")
_HOW = re.compile(r"\bwhat (?:can|should|do|could|must|shall|would) (?:i|we|you)\b|\bwhat to do\b|\bwhat do you (?:suggest|advise|recommend)\b|"
                  r"\bhow (?:do|can|could|should|would|to)\b|\bque (?:faire|dois|peux|puis|fait)\b|\b(?:je|on) fai[ts] quoi\b|\bquoi faire\b|"
                  r"\bcomment\b|\bqu est ce que (?:je|on) (?:peux|peut|dois|doit|fais|fait)\b|"
                  r"\b(?:advice|advise|tips?|conseils?|suggestions?|ideas?|idees?|steps?|options?|strategy|strategie)\b|"
                  r"\b(?:chna3mel|chnowa na3mel|chnoua na3mel|na3mel chnowa|kifech|kifeh|kifach)\b")
_HOW_AR = re.compile(r"ماذا أفعل|ماذا نفعل|شنوة نعمل|شنعمل|كيف نتصرف|كيف أتصرف|نصائح|نصيحة|كيفاش")
# They said they want to stop (not only measured at risk).
_SAID_STOP = re.compile(r"\b(?:wants?|about|going|decided|threatens?) to (?:leave|stop|quit|drop)|\bquit|\bgive up|\bveu(?:t|lent) (?:arreter|partir|quitter|abandonner)|"
                        r"\babandonn\w*|\bbech [yt]e?bat+[ae]l|\b[yt]e?bat+[ae]l|\bdrop(?:s|ping)? (?:out|off)")
IN_SHORT = ("Act on why, without waiting: absent, “We missed you” or a call; questions waiting, a reminder; low results, a catch-up; "
            "wants to stop, a call and a later class.")
MOST = 3                                   # people named in the answer before "and N more"

# What to do, by what was measured (dropout's keys), in the order they weigh.
STEPS = [
    (("absences", "absent", "presence"), "Absent: send “We missed you” before their next session, or call them."),
    (("silent",), "Questions not answered: remind them their questions are waiting."),
    (("struggles",), "Low results: offer them a moment to catch up on the week."),
    (("feelings",), "Unhappy in what they wrote: ask what would help, by phone rather than by message."),
    (("owes",), "Still owes money: the one payment reminder has gone, so call rather than write again."),
]
WANTS_TO_STOP = "Says they want to stop: call them, listen to why, and offer a later class of the same program rather than stopping."
TOO_MANY = "Several messages without an answer: a call does better than one more message."


def wants(message: str) -> bool:
    from app.services.chatbot import _plain

    return bool(_LEAVE.search(_plain(message)) or _LEAVE_AR.search(message or ""))


def answer(db: Session, message: str, history: list[dict] | None = None, *, lang: str | None = None) -> dict:
    """Her answer in the language of the question (or the one chosen in the window)."""
    from app.services import howto, ui_words

    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(message or "")
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        reply, short, links = _answer(db, message or "")
    finally:
        ui_words.current.reset(token)
    return {"reply": reply, "short": short, "links": links, "source": "system", "scene": "feelings",
            "sources": [{"label": _t("Who may drop out"), "url": "/#live-dashboard-risk"}], "resources": []}


def _answer(db: Session, message: str) -> tuple[str, str, list[dict]]:
    from app.services import dropout
    from app.services.chatbot import _candidate_in, _plain

    plain = _plain(message)
    person = _candidate_in(db, plain)
    if person is not None:
        return _person(db, person, said_stop=bool(_SAID_STOP.search(plain) or _LEAVE_AR.search(message)))
    risky = [_with_step(db, item) for item in dropout.at_risk(db, limit=8)]
    asks_who = bool(_WHO.search(plain) or _WHO_AR.search(message))
    asks_how = bool(_HOW.search(plain) or _HOW_AR.search(message))
    if asks_who and not asks_how:
        return _who(risky)
    return _what_to_do(risky)


def _lower(text: str) -> str:
    """A sentence put inside another one ("absent from…"): its first letter in lower case (not in Arabic)."""
    first = text.split(" ", 1)[0] if text else ""
    return text if not first or (len(first) > 1 and first.isalpha() and first.isupper()) else text[:1].lower() + text[1:]


def _step_of(keys: list[str]) -> str | None:
    for wanted, text in STEPS:
        if any(key in wanted for key in keys):
            return _t(text)
    return None


def _with_step(db: Session, item: dict) -> dict:
    """One person at risk, with the first thing to do: the message already prepared for them, else the step for why."""
    from app.services import dropout, smart_reminders

    row = smart_reminders.for_candidate(db, item["candidate"])
    prepared = next((suggestion for suggestion in row["suggestions"] if suggestion["key"] != "bravo"), None)
    keys = dropout.of_registration(db, item["registration"])["keys"]
    if prepared is not None:
        step = _t("{0}, by {1}, {2}.", prepared["title"], row["channel"]["label"].lower(), smart_reminders._when_text(prepared["when"]))
        if prepared.get("call"):
            step += " " + _t(TOO_MANY)
    else:
        step = _step_of(keys) or _t("Call them and ask how it is going.")
    name = item["candidate"].full_name or item["candidate"].email
    return {**item, "name": name, "step": step, "prepared": prepared}


def _line(item: dict) -> str:
    level = _t("likely to drop out") if item["level"] == "high" else _t("could drop out")
    from app.services import ui_words

    joined = {"fr": " ; ", "ar": "؛ "}.get(ui_words.language(), "; ")
    why = joined.join(_lower(reason) for reason in item["reasons"][:3])
    return "• " + _t("{0} ({1}), {2}: {3}.", item["name"], item["class"].name, level, why) + f"\n  → {item['step']}"


def _links(risky: list[dict]) -> list[dict]:
    links = [{"label": _t("Who may drop out"), "url": "/#live-dashboard-risk"}]
    for item in risky[:2]:
        if item["prepared"] is not None and item["prepared"].get("href"):
            links.append({"label": _t("The message for {0}", item["name"]), "url": item["prepared"]["href"]})
        else:
            links.append({"label": _t("The file of {0}", item["name"]), "url": f"/candidate/{item['candidate'].id}"})
    return links


def _nobody() -> str:
    return _t("Right now nobody seems about to drop out: absences, questions, results and payments are watched for each candidate.")


def _what_to_do(risky: list[dict]) -> tuple[str, str, list[dict]]:
    lines = [_t("When someone may drop out, act on why, without waiting:")]
    lines += ["• " + _t(text) for _, text in STEPS] + ["• " + _t(WANTS_TO_STOP), "• " + _t(TOO_MANY)]
    lines.append("")
    if risky:
        lines.append(_t("Right now:"))
        lines += [_line(item) for item in risky[:MOST]]
        if len(risky) > MOST:
            lines.append(_t("And {0} more on the dashboard.", len(risky) - MOST))
        now = _t("Right now: {0}.", ", ".join(item["name"] for item in risky[:MOST]) + (f" +{len(risky) - MOST}" if len(risky) > MOST else ""))
    else:
        lines.append(_nobody())
        now = _t("Nobody seems about to drop out right now.")
    short = _t(IN_SHORT) + " " + now
    return "\n".join(lines), short, _links(risky)


def _who(risky: list[dict]) -> tuple[str, str, list[dict]]:
    if not risky:
        return _nobody(), None, _links(risky)
    lines = [_t("They may drop out, the likeliest first:")] + [_line(item) for item in risky[:5]]
    if len(risky) > 5:
        lines.append(_t("And {0} more on the dashboard.", len(risky) - 5))
    first = risky[0]
    short = _t("May drop out: {0}.", ", ".join(item["name"] for item in risky[:MOST]) + (f" +{len(risky) - MOST}" if len(risky) > MOST else ""))
    short += " " + _t("First, {0}: {1}", first["name"], first["step"])
    return "\n".join(lines), short, _links(risky)


def _person(db: Session, person, *, said_stop: bool) -> tuple[str, str, list[dict]]:
    """One person named: how likely they are to drop out, why, and what to do for them."""
    from app.services import dropout, smart_reminders

    from app.models.classgroup import ClassStatus
    from app.models.registration import RegistrationStatus

    name = person.full_name or person.email
    links = [{"label": _t("The file of {0}", name), "url": f"/candidate/{person.id}"}]
    following = [registration for registration in person.registrations
                 if registration.class_group is not None and registration.class_group.status != ClassStatus.COMPLETED
                 and registration.status not in (RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED)]
    if not following:
        reply = _t("{0} follows no class right now.", name)
        if said_stop:
            reply += " " + _t(WANTS_TO_STOP)
        return reply, None, links
    # Their most worrying class, as the dashboard shows them.
    registration, risk = max(((item, dropout.of_registration(db, item)) for item in following), key=lambda pair: pair[1]["score"])
    row = smart_reminders.for_candidate(db, person)
    prepared = [suggestion for suggestion in row["suggestions"] if suggestion["key"] != "bravo"]
    where = registration.class_group.name
    if risk["score"] >= dropout.HIGH:
        head = _t("{0} ({1}) is likely to drop out ({2}/100):", name, where, risk["score"])
    elif risk["score"] >= dropout.SHOWN:
        head = _t("{0} ({1}) could drop out ({2}/100):", name, where, risk["score"])
    elif risk["score"]:
        head = _t("{0} ({1}) shows a few signs ({2}/100):", name, where, risk["score"])
    else:
        head = _t("Nothing recorded says {0} is about to drop out: present, answering their questions and paid.", name)
    lines = [head] + [f"• {reason}" for reason in risk["reasons"]]
    steps = []
    if said_stop:
        steps.append(_t(WANTS_TO_STOP))
    for suggestion in prepared[:3]:
        steps.append(_t("{0}, by {1}, {2}.", suggestion["title"], row["channel"]["label"].lower(), smart_reminders._when_text(suggestion["when"])))
        if suggestion.get("href"):
            links.append({"label": _t("The message: {0}", suggestion["title"]), "url": suggestion["href"]})
    if not prepared:
        step = _step_of(risk["keys"])
        if step:
            steps.append(step)
    if any(suggestion.get("call") for suggestion in prepared):
        steps.append(_t(TOO_MANY))
    if steps:
        lines.append(_t("What to do:"))
        lines += [f"• {step}" for step in steps]
    short = head.rstrip(":") + (". " + steps[0] if steps else "")
    return "\n".join(lines), short, links[:3]
