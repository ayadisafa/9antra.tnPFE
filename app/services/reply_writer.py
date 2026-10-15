"""Turning the admin's short phrase into a proper reply.

The admin types what to answer in a few words ("oui en 2 fois, 200 puis 190
avant le debut"); the local model writes it as a complete, polite email in
the candidate's language. It only rephrases: a figure the admin did not give
is pointed out, so nothing invented is sent without being seen.
"""
from __future__ import annotations

import html
import re
import unicodedata

from app.services import email_assistant

MAX_PHRASE = 2000

_SYSTEM = (
    "You write emails for 9antra, a training centre. You turn a staff member's short note into a "
    "complete, polite email reply. You only say what the note says: you never add facts, prices, "
    "dates, promises or conditions that are not in the note. You answer with one JSON object and "
    "nothing else."
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SIGN_OFF = re.compile(
    r"^(bien )?(cordialement|sinc[èe]res salutations|salutations|best regards|kind regards|regards|"
    r"l['’]?[ée]quipe|the team|9antra)\b",
    re.IGNORECASE,
)


def language_for(candidate_text: str | None, phrase: str) -> str:
    """English when the candidate (or, failing that, the admin) wrote in English, else French."""
    detected = email_assistant._detect_language(candidate_text or phrase or "")
    return "English" if detected == "en" else "French"


def _without_signature(text: str) -> str:
    """The signature is added when the email is sent: the model's own is removed."""
    lines = text.strip().splitlines()
    while lines and (not lines[-1].strip() or _SIGN_OFF.match(lines[-1].strip().rstrip(",."))):
        lines.pop()
    return "\n".join(lines).strip()


def added_figures(phrase: str, reply: str) -> list[str]:
    """Numbers in the reply that the admin never wrote."""
    given = {number.replace(",", ".") for number in _NUMBER.findall(phrase)}
    return sorted({number for number in _NUMBER.findall(reply) if number.replace(",", ".") not in given})


_EXAMPLES = {
    "French": (
        'Note: "oui c possible samedi, salle 2, ramenez votre pc"\n'
        'Reply: "Bonjour Amine,\\n\\nOui, c\'est tout à fait possible samedi. La séance aura lieu en salle 2 ; '
        'pensez à apporter votre ordinateur.\\n\\nNous restons à votre disposition pour toute question."\n\n'
        'Note: "pas encore de date, on vous previent par mail des que la classe est complete. prix 390 dt"\n'
        'Reply: "Bonjour Sarra,\\n\\nLa date n\'est pas encore fixée : nous vous préviendrons par email dès que la '
        'classe sera complète. Le prix de la formation est de 390 DT.\\n\\nMerci pour votre patience."'
    ),
    "English": (
        'Note: "yes possible saturday, room 2, bring your laptop"\n'
        'Reply: "Hello Amine,\\n\\nYes, Saturday is possible. The session takes place in room 2; please bring '
        'your laptop.\\n\\nWe remain at your disposal for any question."'
    ),
}
# Letters that do not belong in a French or English email (the model sometimes slips).
_ODD_LETTER = re.compile(r"[\u0370-\u03ff\u0400-\u04ff\u2000-\u200a\u20d0-\u20ff\ufffd]")
_SMALL_WORDS = {
    "avant", "apres", "aussi", "alors", "avec", "cette", "comme", "dans", "donc", "elle", "encore", "entre", "etre",
    "leur", "mais", "merci", "notre", "nous", "pour", "quand", "sans", "sera", "sont", "tout", "tous", "tres", "votre",
    "vous", "voulez", "pouvez", "bonjour", "about", "after", "before", "from", "have", "that", "their", "there",
    "this", "what", "when", "will", "with", "your", "please", "hello",
}


def _plain(text: str) -> str:
    """Lower case, without accents: "Début" and "debut" are the same word."""
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in folded if not unicodedata.combining(char))


def left_out(phrase: str, reply: str) -> list[str]:
    """Words of the admin's phrase that carry meaning and are nowhere in the reply.

    A word counts as present when the reply has a word starting the same way
    ("inscris" / "inscrire"), so normal rewording is not reported.
    """
    reply_words = set(re.findall(r"[a-z0-9]+", _plain(reply)))
    missing = []
    for word in re.findall(r"[^\W\d_]{5,}", phrase):
        plain = _plain(word)
        if plain in _SMALL_WORDS or plain in missing:
            continue
        stem = plain[:5]
        if not any(other.startswith(stem) for other in reply_words):
            missing.append(word)
    return missing[:6]


def _trouble(found: dict) -> int:
    return len(found["missing"]) + 2 * len(found["added"]) + (2 if found["odd"] else 0)


_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:dt|tnd|dinars?)\b", re.IGNORECASE)


def _amounts_in_dinars(phrase: str, reply: str) -> str:
    """Amounts the admin gave in dinars are written "390 DT", whatever sign the model chose."""
    for amount in {match.group(1) for match in _AMOUNT.finditer(phrase)}:
        reply = re.sub(
            rf"(?<![\d.,]){re.escape(amount)}(?![\d])\s*(?:DT\b|TND\b|dinars?\b|[^\w\s.,;:!?()\"'«»-]+)?",
            f"{amount} DT", reply, flags=re.IGNORECASE,
        )
    return reply


def reformulate(phrase: str, *, first_name: str = "", question: str = "", language: str | None = None) -> dict | None:
    """The phrase as a full reply: {"reply": text, "warning": text or None}.

    None when the local model did not answer.
    """
    phrase = " ".join((phrase or "").split())[:MAX_PHRASE]
    if not phrase:
        return None
    language = language or language_for(question, phrase)
    asked = f'The candidate wrote: "{" ".join(question.split())[:600]}"\n\n' if question.strip() else ""
    prompt = (
        "A staff member of the training centre wrote a quick note of what to answer. Rewrite it as the email "
        f"reply, in {language}. In the note, 'on', 'je' and 'we' are the training centre: write 'nous' / 'we'. "
        "'DT' is the currency (Tunisian dinars): write amounts like '390 DT'.\n\n"
        f"Examples of the expected result:\n{_EXAMPLES[language]}\n\n"
        f"{asked}Now the real one.\n"
        f'Note: "{phrase}"\n'
        f"The candidate's first name: {first_name or '(unknown: greet without a name)'}\n\n"
        "Rules: full, correct, friendly sentences; the same meaning as the note, nothing added, nothing "
        "left out; every number, date, time and name exactly as in the note; a greeting first, one short "
        "polite sentence last; no signature and no 'Cordialement' (they are added automatically).\n\n"
        'Answer with: {"reply": "..."}'
    )
    best = None
    attempt_prompt = prompt
    for _ in range(2):
        data = email_assistant.llm_json(_SYSTEM, attempt_prompt, max_tokens=120 + len(phrase) // 2 + 200)
        if not isinstance(data, dict) or not str(data.get("reply") or "").strip():
            continue
        reply = _without_signature(str(data["reply"]).replace("\\n", "\n"))
        reply = _amounts_in_dinars(phrase, reply)
        found = {"reply": reply, "added": added_figures(phrase, reply), "missing": left_out(phrase, reply),
                 "odd": bool(_ODD_LETTER.search(reply))}
        if best is None or _trouble(found) < _trouble(best):
            best = found
        if not _trouble(found):
            break
        # Say what went wrong, so the second try is not the same text again.
        attempt_prompt = prompt + "\n\nYour previous answer was not right: " + "; ".join(filter(None, [
            ("it left out: " + ", ".join(found["missing"])) if found["missing"] else "",
            ("it added figures that are not in the note: " + ", ".join(found["added"])) if found["added"] else "",
            "it contained wrong letters" if found["odd"] else "",
        ])) + ". Write it again, correctly."
    if best is None:
        return None

    remarks = []
    if best["missing"]:
        remarks.append("these words of yours were reworded or left out: " + ", ".join(best["missing"]))
    if best["added"]:
        remarks.append("the system wrote " + ", ".join(best["added"]) + ", which is not in your phrase")
    if best["odd"]:
        remarks.append("some letters look wrong")
    return {"reply": best["reply"], "warning": ("Check before sending: " + "; ".join(remarks) + ".") if remarks else None}


# ------------------------------------------------------------------
# An answer the admin taught, written afresh for each candidate
# ------------------------------------------------------------------
#
# The admin's answer is often a quick note ("2 centres: lac 1 <link>, sousse
# <link>"). The model writes it as a proper reply, in other words each time;
# the result is only used when nothing was added and nothing was lost, and the
# admin's own words go out otherwise.

_LINK = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)]")
_MARK = re.compile(r"[\[(]\s*(?:lien|link)\s*(\d+)\s*[\])]", re.IGNORECASE)
_VOICES = {
    "French": ["chaleureux et enthousiaste", "simple et direct", "accueillant et rassurant", "dynamique et positif"],
    "English": ["warm and enthusiastic", "simple and direct", "welcoming and reassuring", "lively and positive"],
}
_TAUGHT_SYSTEM = (
    "You write emails for 9antra, a training centre. A candidate asked a question and a staff member "
    "wrote a short note with the answer. You rewrite the note as the body of a warm, well written email "
    "reply. You only say what the note says: you never add facts, places, prices, dates, opening hours, "
    "promises or conditions that are not in the note. You answer with one JSON object and nothing else."
)
_TAUGHT_EXAMPLES = {
    "French": (
        'Question: "vous acceptez quoi comme paiement ?"\n'
        'Note: "virement, d17 ou especes au centre. recu envoye par mail [lien 1]"\n'
        'Reply: "Vous pouvez régler par virement bancaire, par D17 ou en espèces directement au centre. '
        'Votre reçu vous est ensuite envoyé par email : [lien 1]\\n\\nNous restons à votre écoute pour la suite."\n\n'
        'Question: "il y a quoi comme niveaux ?"\n'
        'Note: "3 niveaux: debutant [lien 1], intermediaire [lien 2], avance [lien 3]. test gratuit avant"\n'
        'Reply: "Nous proposons 3 niveaux :\\n\\n- Débutant : [lien 1]\\n- Intermédiaire : [lien 2]\\n- Avancé : [lien 3]\\n\\n'
        'Un test gratuit vous est proposé avant de commencer. À très bientôt !"'
    ),
    "English": (
        'Question: "what can I pay with?"\n'
        'Note: "bank transfer, d17 or cash at the centre. receipt sent by mail [link 1]"\n'
        'Reply: "You can pay by bank transfer, by D17 or in cash directly at the centre. Your receipt is then '
        'sent to you by email: [link 1]\\n\\nWe are here if you need anything else."\n\n'
        'Question: "which levels do you have?"\n'
        'Note: "3 levels: beginner [link 1], intermediate [link 2], advanced [link 3]. free test before"\n'
        'Reply: "We offer 3 levels:\\n\\n- Beginner: [link 1]\\n- Intermediate: [link 2]\\n- Advanced: [link 3]\\n\\n'
        'A free test is offered before you start. See you soon!"'
    ),
}
# Words of the examples above: in a reply they are a leak unless the note has them.
_EXAMPLE_WORDS = {
    "virement", "especes", "recu", "debutant", "intermediaire", "avance", "gratuit", "niveaux",
    "transfer", "cash", "receipt", "beginner", "intermediate", "advanced", "free", "levels",
}
# Capital letters that are not names.
_NOT_NAMES = {
    "nous", "vous", "notre", "nos", "votre", "vos", "merci", "bonjour", "bienvenue", "dt", "9antra", "the", "we", "you",
    "our", "your", "i", "au", "a", "n'hesitez", "n", "si", "pour", "please", "thank", "thanks", "feel", "if", "see",
}


def protect_links(text: str, word: str = "lien") -> tuple[str, list[str]]:
    """The text with each web link replaced by a short mark, and the links.

    A small model garbles a long address; a mark it either keeps or loses,
    and losing it is noticed.
    """
    links: list[str] = []

    def swap(match: re.Match) -> str:
        links.append(match.group(0))
        return f"[{word} {len(links)}]"

    return _LINK.sub(swap, text or ""), links


def restore_links(text: str, links: list[str]) -> str | None:
    """Put the links back. None unless every mark is there exactly once."""
    seen = [int(number) for number in _MARK.findall(text)]
    if sorted(seen) != list(range(1, len(links) + 1)):
        return None
    return _MARK.sub(lambda match: links[int(match.group(1)) - 1], text)


def added_names(source: str, reply: str) -> list[str]:
    """Names in the reply (a capital letter inside a sentence) that the note never gave."""
    known = set(re.findall(r"[a-z0-9]+", _plain(source)))
    added = []
    for piece in re.split(r"(?<=[.!?:;])\s+|\n+", _MARK.sub(" ", reply)):
        words = re.findall(r"[^\W\d_][\w'’-]*", piece.lstrip("-•– \t\"«("))
        for word in words[1:]:     # the first word of a sentence has its capital anyway
            if not word[0].isupper():
                continue
            parts = [part for part in re.split(r"['’-]", _plain(word)) if part]
            if all(part in known or part in _NOT_NAMES or len(part) <= 2 for part in parts):
                continue
            if word not in added:
                added.append(word)
    return added


def tidy(note: str) -> str:
    """The admin's own words, made presentable: capitals, "DT", a full stop."""
    text = re.sub(r"[ \t]+", " ", (note or "").replace("\r\n", "\n")).strip()
    text = re.sub(r"(\d)\s*(?:dt|tnd)\b", r"\1 DT", text, flags=re.IGNORECASE)
    # A capital after each full stop, never inside a web address.
    text = re.sub(r"(?<=[.!?]\s)([a-zà-ÿ])(?![^\s]*://)", lambda match: match.group(1).upper(), text)
    text = text[:1].upper() + text[1:]
    return text if not text or text[-1] in ".!?…:" or _LINK.search(text.split()[-1]) else text + "."


_FRAMES = {
    "French": (
        ["Merci pour votre message !", "Merci de nous avoir écrit.", "Avec plaisir, voici la réponse à votre question.",
         "Bonne question, voici ce qu'il faut savoir."],
        ["Nous restons à votre disposition pour toute autre question.", "N'hésitez pas à nous écrire si vous avez besoin de plus de détails.",
         "À très bientôt !", "Nous serons ravis de vous aider pour la suite."],
    ),
    "English": (
        ["Thank you for your message!", "Thanks for writing to us.", "Happy to help, here is the answer to your question.",
         "Good question, here is what you need to know."],
        ["We remain at your disposal for any other question.", "Feel free to write to us if you need more details.",
         "See you very soon!", "We will be glad to help with the next steps."],
    ),
}


def framed(note: str, *, language: str = "French", seed: str = "") -> str:
    """The admin's own words between a friendly first and last sentence.

    Used when the model's rewriting cannot be trusted with this answer: the
    facts stay exactly as the admin wrote them, and two candidates still do
    not receive the very same email.
    """
    openings, closings = _FRAMES.get(language, _FRAMES["French"])
    pick = sum(seed.encode("utf-8")) if seed else 0
    return f"{openings[pick % len(openings)]}\n\n{tidy(note)}\n\n{closings[(pick // len(openings)) % len(closings)]}"


_MONEY = re.compile(r"\d\s*(?:dt|tnd|dinars?|eur|euros?|€|\$|%)(?![a-z])", re.IGNORECASE)
_NO = re.compile(r"\b(?:pas|aucun|aucune|jamais|sans|non|ni|not|no|never|without|cannot|none)\b|n['’]t\b", re.IGNORECASE)
# Polite formulas that say "not" without denying anything.
_POLITE_NO = re.compile(
    r"h[eé]site[zs]? pas|do(?:n['’]t| not) hesitate|can(?:['’]t|not) wait|no problem|pas de (?:probleme|souci)|"
    r"sans (?:probleme|souci|hesiter|hesitation|doute|plus attendre)|non seulement|not only|no doubt|pourquoi pas|without hesitation",
    re.IGNORECASE,
)


def says_no(text: str) -> int:
    """How many times a text denies something ("pas", "sans", "not"…): a
    rewriting that loses one, or gains one, says something else. "ne" is not
    counted, since a quick note leaves it out ("pas un diplôme")."""
    return len(_NO.findall(_POLITE_NO.sub(" ", _plain(text))))


def rewrite(note: str, *, question: str = "", language: str = "French", seed: str = "") -> str | None:
    """The taught answer as a fresh, complete reply. None when the model did
    not answer, or when what it wrote is not exactly what the note says.

    `seed` (the email's id) picks the tone, so two candidates asking the same
    thing do not receive the same sentences.
    """
    note = (note or "").strip()[:MAX_PHRASE]
    # Amounts stay in the admin's own words: on a real trial the model turned
    # "200 DT à l'inscription" into "vous recevez 200 DT", with every word in place.
    if not note or _MONEY.search(note):
        return None
    language = language if language in _TAUGHT_EXAMPLES else "French"
    word = "link" if language == "English" else "lien"
    marked, links = protect_links(" ".join(note.split()), word)
    voices = _VOICES[language]
    voice = voices[sum(seed.encode("utf-8")) % len(voices)] if seed else voices[0]
    asked = f'The candidate wrote: "{" ".join(question.split())[:500]}"\n' if question.strip() else ""
    prompt = (
        f"Rewrite the staff member's note as the body of the email reply, in {language}. In the note, 'on', 'je' "
        "and 'we' are the training centre: write 'nous' / 'we'. 'DT' is the currency (Tunisian dinars): write "
        f"amounts like '390 DT'. A mark like [{word} 1] stands for a web link: copy each mark exactly, once, "
        "right after the thing it belongs to.\n\n"
        f"Examples of the expected result:\n{_TAUGHT_EXAMPLES[language]}\n\n"
        f"Now the real one.\n{asked}"
        f'Note: "{marked}"\n'
        f"Tone: {voice}.\n\n"
        "Rules: full, correct, friendly sentences; the same meaning as the note, nothing added, nothing left "
        "out; every number, name and mark exactly as in the note; when the note gives several places or "
        "choices, write them as a list, one per line starting with '- '; no greeting line at the start (it is "
        "added automatically); finish with one short friendly sentence; no signature.\n\n"
        'Answer with: {"reply": "..."}'
    )
    source = f"{marked} {question}"
    attempt_prompt = prompt
    for _ in range(2):
        with email_assistant.livelier():
            data = email_assistant.llm_json(_TAUGHT_SYSTEM, attempt_prompt, max_tokens=160 + len(marked) // 2 + 200)
        if not isinstance(data, dict) or not str(data.get("reply") or "").strip():
            continue
        reply = _without_signature(html.unescape(str(data["reply"])).replace("\\n", "\n"))
        reply = _amounts_in_dinars(marked, email_assistant.clean_answer(reply))
        leaked = sorted(
            word_ for word_ in set(re.findall(r"[a-z]+", _plain(reply))) & _EXAMPLE_WORDS
            if word_ not in _plain(source)
        )
        lost_figures = added_figures(reply, _MARK.sub(" ", marked))
        problems = list(filter(None, [
            ("it left out: " + ", ".join(left_out(marked, reply))) if left_out(marked, reply) else "",
            ("it left out these figures: " + ", ".join(lost_figures)) if lost_figures else "",
            "it does not say 'not' where the note does, or says it where the note does not"
            if says_no(reply) != says_no(marked) else "",
            ("it added figures that are not in the note: " + ", ".join(added_figures(marked, reply)))
            if added_figures(marked, reply) else "",
            ("it added names that are not in the note: " + ", ".join(added_names(source, reply)))
            if added_names(source, reply) else "",
            ("it copied words of the examples: " + ", ".join(leaked)) if leaked else "",
            "it contained wrong letters" if _ODD_LETTER.search(reply) else "",
            "each mark must be copied exactly once" if restore_links(reply, links) is None else "",
        ]))
        if not problems:
            return restore_links(reply, links)
        attempt_prompt = prompt + "\n\nYour previous answer was not right: " + "; ".join(problems) + ". Write it again, correctly."
    return None


def other_wordings(question: str, answer: str = "", limit: int = 6) -> list[str] | None:
    """Other ways a candidate could ask the same question, in French and English.

    They are guesses: the assistant uses them to recognise a question and
    prepare a draft, never to answer alone. None when the model did not answer.
    """
    question = " ".join((question or "").split())[:300]
    if not question:
        return []
    about = " ".join(protect_links(answer)[0].split())[:240]
    data = email_assistant.llm_json(
        "You help a training centre in Tunisia recognise the questions its candidates ask by email. "
        "You answer with one JSON object and nothing else.",
        f'A candidate asked: "{question}"\n'
        + (f'The answer given was: "{about}"\n' if about else "")
        + f"\nWrite {limit} other ways a candidate could ask the SAME question, so that the same answer still "
        f"fits: {limit // 2} in French and {limit - limit // 2} in English. Short, natural, as written in an "
        "email. Do not name a course, a price or a date that the question does not name.\n\n"
        'Answer with: {"questions": ["...", "..."]}',
        max_tokens=60 + 40 * limit,
    )
    if data is None:
        return None
    found: list[str] = []
    for item in data.get("questions") if isinstance(data.get("questions"), list) else []:
        text = " ".join(str(item or "").split()).strip('"«» ')
        if not 6 <= len(text) <= 200 or _ODD_LETTER.search(text) or added_figures(question, text):
            continue
        if text.lower() not in (other.lower() for other in found) and text.lower() != question.lower():
            found.append(text)
    return found[:limit]
