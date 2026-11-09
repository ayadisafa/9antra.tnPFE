"""Any question, read by the cloud model and answered from the application's own facts (Yasmine and the email assistant).

Two steps, so the model never answers from what it believes:

1. Reading: the model is given the question and the lookups (lookups.py: read-only functions over the records) and
   calls the ones that hold what is needed; the application runs them.
2. Writing: the model is given only what the lookups returned (FACTS) and writes the answer, in the language of the
   question, saying for each thing asked whether FACTS answer it and copying the words of FACTS that do.

Then the application checks, without the model: every figure, date, time, link, email and phone number of the answer
must be in FACTS (or in the question), and every quoted fact must really be in FACTS. A thing asked that FACTS do not
answer is said so ("the team will answer that"), never guessed. The callers decide what to do with it: the email
assistant sends alone only an answer that passed every check and covers everything asked (cloud_reply).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime

from app.services import cloud_model

MAX_ROUNDS = 1                 # rounds of lookups: one (several lookups at once) keeps each question to two calls
MAX_CALLS = 8

_TUNISIAN = ("Tunisian is often written in Latin letters with digits for some sounds (3 = ع, 7 = ح, 9 = ق, 5 = خ, 2 = ء): "
             "\"wa9tech\" = when, \"9addech\" = how much, \"win\" = where, \"fama\" = is there, \"chkoun\" = who, \"kifech\" = how, "
             "\"khallas\" = paid, \"ybattel\" = drop out, \"ghayeb\" = absent, \"chna3mel\" = what do I do, \"soum\" = price.")

READ = {
    "admin": ("You help the admin of 9antra, a training centre in Tunisia (programs, classes, sessions, professors, candidates, payments, "
              "emails, the job market, the journal). Read the admin's message and call the lookups that hold the facts needed to answer "
              "it: call every one that may help, several at once if useful. Never answer from memory. If the message is only small "
              "talk, call none. If it is too vague to know what to look up (\"how many?\" with no subject, several possible meanings), "
              "call only ask_back. Use web_search only for what the application cannot know (the job market in Tunisia, salaries, a "
              "technology, news outside the journal), never with a person's name. Earlier turns of the conversation say what a short "
              "message refers to (\"and last month?\", \"the second one\"). "
              "Messages may be in French, English, Arabic or Tunisian. " + _TUNISIAN),
    "candidate": ("You help 9antra, a training centre in Tunisia, answer a message from one of its registered candidates. Read it and "
                  "call the lookups that hold the facts needed to answer it (several at once if useful); the lookups see only this "
                  "candidate's own records and what is public. Never answer from memory. Messages may be in French, English, "
                  "Arabic or Tunisian. " + _TUNISIAN),
}

WRITE = {
    "admin": ("Write Yasmine's answer to the admin of 9antra using ONLY the FACTS given. Rules:\n"
              "- Answer in the language of the admin's message: French, English, Arabic, or Tunisian in Latin letters when they wrote so.\n"
              "- Use only FACTS. Never add a figure, date, name, yes/no or promise that FACTS do not say. Copy every figure, date, "
              "time, price, link and phone number exactly as FACTS write it; do not compute new figures (FACTS give the counts).\n"
              "- If FACTS do not answer something asked, say plainly that the application does not record it. But when FACTS list "
              "the items and none fits (no class starts next week, nobody is absent), say there is none.\n"
              "- FACTS are written in English: put their words in the language of the answer, keeping names and figures as they are.\n"
              "- Complete, natural sentences, like a helpful colleague (\"You have 4 candidates; none registered this week.\").\n"
              "- When FACTS say something only for one program, class or person, say it for that one by name; do not make it general.\n"
              "- Answer exactly what was asked: every part of it, and nothing it did not ask (no extra figures, lists, advice or "
              "offers to help). The answer first; a short list (up to 8 lines) only when a list is asked. No greeting.\n"
              "- FACTS from web_search come from the internet, not the centre's records: say so (\"on the web, …\") and keep them apart "
              "from the centre's figures.\n"
              "- If FACTS show several possible matches for what was asked (two candidates with that name, several classes) and you "
              "cannot tell which, put a short question in `clarify` with the options (copied from FACTS), and make `reply` that question.\n"
              "- For each thing asked, give `answered` and `evidence`: the exact words of FACTS that answer it (copied, short)."),
    "candidate": ("Write the reply to the candidate using ONLY the FACTS given. Rules:\n"
                  "- Write in the language of the candidate's message: French, English, Modern Standard Arabic, or Tunisian in Latin "
                  "letters when they wrote so. Warm, simple and short (1 to 5 sentences, or a short list). No greeting, no "
                  "signature: they are added.\n"
                  "- Use only FACTS. Never add a figure, date, name, yes/no or promise that FACTS do not say. Copy every figure, "
                  "date, time, price, link and phone number exactly as FACTS write it.\n"
                  "- FACTS are written in English: put their words in the language of the reply, keeping names and figures as they are.\n"
                  "- If FACTS do not answer something asked (a certificate, an internship, paying in instalments… when FACTS say "
                  "nothing of it), do not guess: mark it answered=false and say in the reply that the team will answer that point.\n"
                  "- When FACTS say something only for one program or one case, say it for that one by name and do not make it "
                  "general (\"the Summer Camp includes an internship\" is not \"yes, there is an internship after your training\"); "
                  "a general question answered only for another program is answered=false.\n"
                  "- A problem they report (they cannot connect, a link does not work, an error, something missing) is never "
                  "answered by a general rule: give what FACTS say that helps, mark the problem itself answered=false, and say "
                  "the team will help them.\n"
                  "- For each thing asked, give `answered` and `evidence`: the exact words of FACTS that answer it (copied, short).\n"
                  "- `route`: course_files when they ask for the course, files, slides or recording of a week or session; "
                  "course_question when they ask about the subject taught itself (how something works in the course); no_reply when "
                  "they only thank or acknowledge and ask nothing; answer otherwise.\n"
                  "- `sensitive`: refund when they ask for money back, cancellation when they want to cancel or stop, complaint when "
                  "they complain or are unhappy (with the trainer, the pace, the service); none otherwise."),
}

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language": {"type": "STRING", "enum": ["fr", "en", "ar", "tn"]},
        "route": {"type": "STRING", "enum": ["answer", "course_files", "course_question", "no_reply"]},
        "sensitive": {"type": "STRING", "enum": ["none", "refund", "cancellation", "complaint"]},
        "asks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "ask": {"type": "STRING"}, "answered": {"type": "BOOLEAN"}, "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["ask", "answered", "evidence"]}},
        "reply": {"type": "STRING"},
    },
    "required": ["language", "route", "sensitive", "asks", "reply"],
}


SCHEMA_ADMIN = {**SCHEMA, "properties": {**SCHEMA["properties"], "clarify": {"type": "OBJECT", "properties": {
    "question": {"type": "STRING"}, "options": {"type": "ARRAY", "items": {"type": "STRING"}}}}}}


def _plain(text: str) -> str:
    """Words and figures only, in order: a quoted fact is found whatever its punctuation ("still_to_pay": "490 DT")."""
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return " ".join(re.findall(r"[^\W_]+", folded))


_NUMBER = re.compile(r"\+?\d[\d .,:/h-]*\d|(?<![^\W\d_])\d(?![^\W\d_])")   # one digit inside a word is Tunisian ("3la", "9addech")
_LINK = re.compile(r"https?://[^\s)>\]]+|[\w.+-]+@[\w-]+\.[\w.]+")


def _numbers(text: str) -> list[str]:
    found = []
    for token in _NUMBER.findall(text or ""):
        digits = re.sub(r"\D", "", token)
        if digits:
            found.append(digits.lstrip("0") or "0")
    return found


def check(reply: str, facts: dict, question: str, asks: list[dict]) -> dict:
    """What of the answer is not in FACTS: {"ok", "unsupported": [...], "asks": [... with "verified"]}."""
    said = json.dumps(facts, ensure_ascii=False, default=str) + "\n" + (question or "")
    known = set(_numbers(said))                               # the lookups say their own counts ("count": 4)
    unsupported = []
    for number in _numbers(reply):
        if number in known or any(number in other for other in known if len(number) >= 3):
            continue
        unsupported.append(number)
    plain_said = f" {_plain(said)} "
    raw_said = said.lower()
    for link in _LINK.findall(reply or ""):
        if link.rstrip(".,").lower() not in raw_said:
            unsupported.append(link)
    checked = []
    for item in asks or []:
        evidence = [text for text in item.get("evidence") or [] if _plain(text)]
        real = item.get("answered") and evidence and all(f" {_plain(text)} " in plain_said for text in evidence)
        checked.append({**item, "verified": bool(real)})
    return {"ok": not unsupported, "unsupported": unsupported, "asks": checked}


def answer(question: str, tools: dict, role: str, *, history: list[dict] | None = None, subject: str = "",
           today: str | None = None, lang: str | None = None) -> dict | None:
    """The answer, written from the lookups' facts, with its checks; None when the cloud model is off or did not answer.

    {"reply", "language", "route", "sensitive", "asks", "covered", "ok", "unsupported", "facts", "used"}"""
    if not cloud_model.available() or not (question or "").strip():
        return None
    today = today or datetime.now().strftime("%a %d/%m/%Y")
    asked = (f"Subject: {subject}\n" if subject else "") + question.strip()
    earlier = [item for item in (history or []) if (item.get("content") or "").strip()]
    if earlier and earlier[-1].get("role") == "user" and earlier[-1]["content"].strip() == question.strip():
        earlier = earlier[:-1]                              # the window sends the question itself last
    turns = []
    for item in earlier[-5:]:
        side = "model" if item.get("role") == "assistant" else "user"
        if turns and turns[-1]["role"] == side:             # two turns of the same side: joined
            turns[-1]["parts"][0]["text"] += "\n\n" + item["content"].strip()[:1200]
        else:
            turns.append({"role": side, "parts": [{"text": item["content"].strip()[:1200]}]})
    if turns and turns[-1]["role"] == "user":
        turns.append({"role": "model", "parts": [{"text": "(noted)"}]})
    turns.append({"role": "user", "parts": [{"text": f"Today is {today}.\n\n{asked}"}]})
    declarations = [tool.declaration() for tool in tools.values()]
    facts: dict = {}
    used: list[str] = []
    asked_back = None
    for _ in range(MAX_ROUNDS):
        content = cloud_model.generate(turns, system=READ[role], tools=declarations, models=cloud_model.READING)
        if content is None:
            return None
        calls = cloud_model.calls_of(content)
        if not calls:
            break
        turns.append(content)
        responses = []
        if all(call.get("name") == "ask_back" for call in calls):
            asked_back = calls[0].get("args") or {}
            break                               # too vague to look anything up: the question back is the answer
        for call in calls[:MAX_CALLS - len(used)]:
            if call.get("name") == "ask_back":
                continue                        # asked with real lookups: the lookups answer
            tool = tools.get(call.get("name"))
            arguments = call.get("args") or {}
            try:
                result = tool.run(**arguments) if tool is not None else {"error": "no such lookup"}
            except TypeError:
                result = tool.run()                         # it was given arguments it does not take
            except Exception as error:                      # a lookup that fails never stops the answer
                result = {"error": f"the lookup failed ({type(error).__name__})"}
            label = call.get("name") + (f"({', '.join(f'{key}={value}' for key, value in arguments.items())})" if arguments else "")
            facts[label] = result
            used.append(call.get("name"))
            responses.append({"functionResponse": {"name": call.get("name"), "response": {"result": json.loads(json.dumps(result, default=str))}}})
        turns.append({"role": "user", "parts": responses})
        if len(used) >= MAX_CALLS:
            break
    if asked_back is not None and asked_back.get("question"):
        # The question back: no figure the message did not say, and short options to tap.
        options = [str(option).strip()[:80] for option in asked_back.get("options") or [] if str(option).strip()][:4]
        reply = str(asked_back["question"]).strip()[:300]
        checked = check(reply + "\n" + "\n".join(options), {}, asked, [])
        return {"reply": reply, "language": lang or "fr", "route": "answer", "sensitive": "none", "asks": [], "ok": checked["ok"],
                "unsupported": checked["unsupported"], "covered": False, "facts": {}, "used": ["ask_back"], "options": options, "clarify": True}
    if not facts and role == "admin":
        return None                     # nothing to look up for it: the application's other ways answer
    # (a candidate's message is still read: a thank-you needs no answer, a refund comes to the admin)
    written_for = "the admin's" if role == "admin" else "the candidate's"
    if lang not in ("fr", "en", "ar", "tn"):
        from app.services import howto

        seen = howto.language_of(question)
        lang = seen if seen in ("ar", "tn") else None      # Arabic letters and Tunisian are told apart for sure; French or English the model sees
    chosen = {"fr": "French", "en": "English", "ar": "Arabic",
              "tn": "Tunisian written in Latin letters, the way they wrote (e.g. \"3andek 4 candidats\", \"ma khallsouch\")"}.get(lang or "")
    prompt = (f"Today is {today}.\n\nFACTS (what the application's records say; web_search results come from the internet):\n{json.dumps(facts, ensure_ascii=False, default=str, indent=1)}"
              f"\n\n{written_for.capitalize()} message:\n{asked}" + (f"\n\nWrite the answer in {chosen}." if chosen else ""))
    content = cloud_model.generate([{"role": "user", "parts": [{"text": prompt}]}], system=WRITE[role],
                                   schema=SCHEMA_ADMIN if role == "admin" else SCHEMA, temperature=0.3)
    found = cloud_model.json_of(content)
    if not found or not isinstance(found.get("reply"), str):
        return None
    clarify = found.get("clarify") if role == "admin" and isinstance(found.get("clarify"), dict) else {}
    options = [str(option).strip()[:80] for option in clarify.get("options") or [] if str(option).strip()][:4] if clarify.get("question") else []
    checked = check(found["reply"] + ("\n" + "\n".join(options) if options else ""), facts, asked, found.get("asks") or [])
    asks = checked["asks"]
    return {"reply": found["reply"].strip(), "language": found.get("language") or "fr", "route": found.get("route") or "answer",
            "sensitive": found.get("sensitive") or "none", "asks": asks, "ok": checked["ok"], "unsupported": checked["unsupported"],
            "covered": bool(asks) and all(item["verified"] for item in asks), "facts": facts, "used": used,
            "options": options, "clarify": bool(options)}
