"""The assistant sends a message for the admin: "envoie un email à Fares pour lui dire que la séance de samedi est
annulée", "send a WhatsApp to Nour saying …", "sms au 98 123 456 : …", "write to amira@example.com that …".

Who: a candidate, a professor, a team member (by name), or an email address or a phone number written in the message.
How: what the message says (email, WhatsApp, SMS), otherwise the way chosen above the inbox (with the next way when
one does not work) for a candidate, the application and email for the team.
What: the admin's words, reworded in French or English (the language asked, French otherwise) by the rewording of the
inbox, which never changes a figure. "tel quel" / "as is" sends the admin's own words.

When the admin's switch "Send at once" is on (the default), it goes at once and the answer says, for each person and each
way, what went and, when something did not, why (no address, the number cannot be read, no Business number, no Android
phone, the mail server refused…). When it is off, nothing goes: the text is ready, with where to send it from. A name
that matches several people, or nobody, is asked, never guessed.
"""
from __future__ import annotations

import html
import re
import unicodedata

from sqlalchemy.orm import Session

SEND_START = re.compile(
    r"^\s*(?:please |svp |stp |peux[- ]tu |can you |could you )?(?:send|envoie|envoyez|envoyer|envoi|ecris|ecrire|écris|écrire|write|"
    r"dis|tell|informe|previens|préviens|ab3ath|ob3eth|message|texte|text|(?:sms|whats ?app|e-?mail|mail)(?=\s+(?:à|a|au|aux|to)\b))\b", re.I)
_CHANNEL = {"whatsapp": r"\b(whats ?app|wa)\b", "sms": r"\b(sms|texto|text message)\b", "email": r"\b(e-?mail|mail|courriel)\b"}
_SEPARATOR = re.compile(r"\s*(?:pour (?:lui|leur) dire(?: que)?|pour (?:l|les)'?informer(?: que)?|pour dire(?: que)?|en (?:lui )?disant(?: que)?|"
                        r"saying(?: that)?|to say(?: that)?|to tell (?:him|her|them)(?: that)?|that|que|qu'|:)\s+", re.I)
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


# A sending order in Tunisian: “ab3athli (message|sms|whatsapp|mail) l<name> (w) kolo <what>”, “ab3ath sms l amira bech <what>”.
_TN_SEND = re.compile(r"^\s*(?:(?:3aychek|3aychk|brabi|svp|please)\s+)?(?:ab3ath|ab3eth|eb3ath|b3ath|ob3eth|ab3at|ab3et|ab3a4)(?:li|lli)?\b", re.I)
_TN_WAY = {"whatsapp": r"\b(whats ?app|wtsp|wts|watsap)\b", "sms": r"\bsms\b", "email": r"\b(e-?mail|mail|courriel)\b"}
_TN_TELL = re.compile(r"\s+(?:w\s+|o\s+|et\s+)?(?:kolo|kollo|kolou|kollou|9olo|9ollo|9olou|9ollou|9oulou|9oulo|goulou|gollo|qolo|qollo|kolha|kollha|"
                      r"9olha|9ollha|9oulha|kolhom|kollhom|9olhom|9ollhom|9oulhom|bech|bach|bich|elli|illi)\s+", re.I)


def from_tunisian(message: str) -> str | None:
    """The order in French words when it is said in Tunisian (Latin letters), else None: “ab3athli message lfares kolo
    ija” → “envoie un message à fares pour lui dire que viens”. The rewording then writes it properly."""
    from app.services import derja

    found = _TN_SEND.match(message or "")
    if not found:
        return None
    rest = message[found.end():]
    ways = [way for way, pattern in _TN_WAY.items() if re.search(pattern, rest, re.I)]
    tell = _TN_TELL.search(rest)
    head, what = (rest[:tell.start()], rest[tell.end():]) if tell else (rest, "")
    for pattern in _TN_WAY.values():
        head = re.sub(pattern, " ", head, flags=re.I)
    head = re.sub(r"(?i)\b(message|msg|messages|rsala|risala)\b", " ", head)
    # “lfares”, “l fares”, “l'fares”, “li fares”: to Fares.
    who = re.sub(r"(?i)^\s*(?:li\s+|l['’ -]?\s*)", "", head.strip())
    who = re.sub(r"(?i)\s+(?:w|o|et)\s+(?:li\s+|l['’ -]?\s*)", " et ", who)
    if not who.strip():
        return None
    # The words to send stay as said: the rewording reads Tunisian itself (with the meaning of its words) and writes French.
    words = " ".join(what.split())
    way = {"whatsapp": "un whatsapp", "sms": "un sms", "email": "un email"}.get(ways[0], "un message") if ways else "un message"
    return f"envoie {way} à {' '.join(who.split())}" + (f" pour lui dire que {words}" if words else "")


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
    lost_figures = [figure for figure in re.findall(r"\d+", french) if figure not in re.findall(r"\d+", text)]
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
    ways = [way for way, pattern in _CHANNEL.items() if re.search(pattern, head, re.I)]
    who = SEND_START.sub("", head, count=1)
    who = re.sub(r"(?i)\b(un|une|a|an|le|la|the|par|by|on|sur|via|en)\b", " ", who)
    for pattern in _CHANNEL.values():
        who = re.sub(pattern, " ", who, flags=re.I)
    who = re.sub(r"(?i)\b(message|messages|mot|note)\b", " ", who)
    who = re.sub(r"(?i)^\s*(à|a|au|aux|to|pour)\s+", " ", who.strip())
    return {"who": " ".join(who.split()), "what": _STRIP.sub(" ", what).strip(" ."), "ways": ways,
            "as_is": bool(_AS_IS.search(message)), "english": bool(_ENGLISH.search(message))}


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
    for part in re.split(r"\s*(?:,|\bet\b|\band\b|&)\s*", rest, flags=re.I):
        words = [word for word in _plain(part).split() if len(word) >= 3]
        if not words:
            continue
        exact = [item for item in people if _plain(item["name"]).split() == words]
        matches = exact or [item for item in people if all(word in _plain(item["name"]).split() for word in words)]
        if len(matches) == 1:
            found.append(matches[0])
        elif not matches:
            unknown.append(part.strip())
        else:
            several.append((part.strip(), matches[:6]))
    return found, unknown, several


def _ask(text: str, fr: bool, links=None) -> dict:
    return {"reply": text, "links": links or [], "source": "action", "sources": [{"label": "The application's people (candidates, professors, team)"}]}


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

                results.append({"way": "sms", "status": "waiting", "why": _why_waiting("sms"), "link": f"sms:{number}?body={quote(text)}"})
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


def handle(db: Session, message: str, fr: bool, *, author: str = "Admin", allowed: bool = True) -> dict | None:
    """The assistant's answer to a "send … to …" message, or None when the message is not one."""
    from app.services import reply_writer

    french = from_tunisian(message)
    tunisian = bool(french)
    if french:
        message, fr = french, True          # said in Tunisian: done like the same order in French, answered in French
    parsed = parse(message)
    if parsed is None or not parsed["who"]:
        return None
    if re.match(r"(?i)^\s*(me|moi|nous|us)\b", parsed["who"]):
        return None              # "tell me about…", "dis-moi…": a question, not a message to send
    if re.search(r"(?i)\b(programs?|programmes?|formations?|brochures?|catalogues?|summary|résumé|resume|pdf|documents?)\b", parsed["who"]):
        return None              # "send the DevOps program to Yasmine": the program's PDF (chatbot_actions.document)
    if not parsed["what"] and parsed["ways"] in ([], ["email"]):
        return None              # "write (an email) to Fares": the email opens in the composer (chatbot_actions.write_to)
    if not allowed:
        return _ask("Sending messages from here is for those who may change things: ask the admin." if not fr
                    else "Envoyer des messages d'ici est réservé à ceux qui peuvent modifier : demandez à l'admin.", fr)
    people, unknown, several = resolve(db, parsed["who"])
    if several:
        name, matches = several[0]
        listing = "\n".join(f"• {item['name']} ({ {'candidate': 'candidat' if fr else 'candidate', 'professor': 'formateur' if fr else 'professor', 'user': 'équipe' if fr else 'team'}[item['kind']] })"
                            for item in matches)
        return _ask((f"Plusieurs personnes s'appellent « {name} » : laquelle ? Écrivez son nom complet.\n{listing}" if fr
                     else f"Several people are called “{name}”: which one? Write their full name.\n{listing}"), fr)
    if unknown and not people:
        return _ask((f"Je ne trouve personne qui s'appelle « {unknown[0]} » (candidats, formateurs, équipe). Écrivez son nom complet, son email ou son numéro."
                     if fr else f"I find nobody called “{unknown[0]}” (candidates, professors, team). Write their full name, email or number."), fr)
    if not parsed["what"]:
        if not parsed["ways"]:
            return None          # "write to Fares": the email opens in the composer (chatbot_actions.write_to)
        return _ask(("Que faut-il leur dire ? Écrivez par exemple : « envoie un sms à Fares pour lui dire que la séance de samedi est annulée »."
                     if fr else "What should I tell them? For example: “send an SMS to Fares saying that Saturday's session is cancelled”."), fr)
    text = parsed["what"]
    reworded = None
    if not parsed["as_is"]:
        first = people[0]["name"].split()[0].capitalize() if len(people) == 1 and people[0]["kind"] not in ("address", "number") else ""
        reworded = (reword_from_tunisian(text, first) if tunisian else
                    reply_writer.reformulate(text, first_name=first, language="en" if parsed["english"] else "fr"))
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
    names = ", ".join(item["name"] for item in people)
    if not auto_send(db):
        links = [{"label": f"Write to {item['name'].split()[0]}", "url": f"/inbox/compose?candidate={item['id']}"} for item in people if item["kind"] == "candidate"][:3]
        return {"reply": ((f"« Envoyer tout de suite » est désactivé : rien n'est parti. Voici le texte pour {names} :\n\n{text}" if fr else
                           f"“Send at once” is off: nothing was sent. Here is the text for {names}:\n\n{text}")),
                "links": links, "source": "action", "sources": [{"label": "Reworded by the inbox's rewording (figures checked)" if reworded else "Your own words"}]}
    lines, links = [], []
    sent_any = False
    for person in people:
        for result in _send_one(db, person, text, parsed["ways"], author=author, english=parsed["english"]):
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
    return {"reply": f"{head}\n" + "\n".join(lines) + ("\n\n" + ("Le texte envoyé :\n" if fr else "The text sent:\n") + text),
            "links": links[:5], "source": "action",
            "sources": [{"label": "Reworded by the inbox's rewording (figures checked)" if reworded else "Your own words"},
                        {"label": "The way chosen above the inbox" if not parsed["ways"] else "The way you asked", "url": "/inbox#ways"}]}
