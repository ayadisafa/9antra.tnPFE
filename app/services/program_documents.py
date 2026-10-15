"""Programs as documents, asked for in words.

A candidate writes "can you send me all the programs in PDF?", the admin asks
the assistant "send the DevOps program to Fares": this says whether a message
asks for that, which programs it means, and makes the file (one program's PDF,
or several in one catalogue). Rules only: no language model decides.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.email_file import EmailFile
from app.models.program import Program
from app.models.registration import RegistrationStatus
from app.services import program_pdf

# Other documents people ask for: never answered with a program.
_OTHER = (r"recus?|receipts?|factures?|invoices?|attestations?|certificats?|certificates?|diplomes?|liens?|links?|meet|zoom|"
          r"passwords?|mots? de passe|contrats?|contracts?|cv|photos?|videos?|enregistrements?|recordings?|"
          r"support de cours|slides?|exercices?|exercises?|corrections?|projets?|projects?|"
          r"week \d+|semaine \d+|(cours|seances?|sessions?|class|course)( d| of)? (aujourd hui|today|hier|yesterday)|"
          r"(today|yesterday) s (course|class|session)")
# Words of a registration or a payment: "I want to join the DevOps program" is not a request for its PDF.
_JOINING = r"inscri\w*|register\w*|enrol\w*|join|rejoindre|pay\w*|paiement\w*"
_NOUN = r"programs?|programmes?|syllabus|curriculum|cursus|catalogu\w*|brochures?|plaquettes?"
_SOFT_NOUN = r"formations?|courses?|cours|trainings?|bootcamps?"
_FILE = r"pdf|brochures?|catalogu\w*|plaquettes?|fiches?|documents?|fichiers?|files?|attach\w*|pieces? jointes?|en piece jointe"
_SEND = r"send|envo\w*|share|partag\w*|forward|transf\w*|receive|recevoir|faire parvenir|mail|email"
_ASK = (r"get|avoir|donn\w*|give|passer|can i have|could i have|may i have|je veux|je voudrais|j aimerais|i want|"
        r"i would like|i d like|besoin|need|show|montre\w*")
_DETAIL = r"details?|detaille\w*|detailed|en detail|complete?|complets?|full|contenus?|contents?"
_ALL = r"all|every|everything|each|toutes?|tous|chaque|ensemble|whole|liste|list|catalogu\w*|les formations|vos formations|your programs|your courses"
_THEIRS = r"my programs?|my courses|my formations?|mes formations?|mes programmes?|mes cours|ma formation|mon programme|mes inscriptions"
SUMMARY = (r"summar\w*|resume\w*|en bref|in short|briefly|overview|apercu|short version|version courte|l essentiel|the essentials|"
           r"recap\w*|synthese|synthetis\w*")
_ENGLISH = r"in english|en anglais|english version|version anglaise"
_FRENCH = r"in french|en francais|french version|version francaise"


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", folded).split())


def _has(text: str, words: str) -> bool:
    return re.search(r"(?<![a-z0-9])(?:" + words + r")(?![a-z0-9])", text) is not None


def asks_for_program(message: str) -> bool:
    """Whether the message asks to receive a program's description as a document."""
    text = plain(message)
    if not text or _has(text, _OTHER):
        return False
    noun, soft = _has(text, _NOUN), _has(text, _SOFT_NOUN)
    wants_file, send, ask, detail = _has(text, _FILE), _has(text, _SEND), _has(text, _ASK), _has(text, _DETAIL) or _has(text, SUMMARY)
    if wants_file:
        return noun or soft
    if _has(text, _JOINING):
        return False
    if (noun or soft) and _has(text, SUMMARY):     # "can you summarize your programs?"
        return True
    return (send and noun) or ((send or ask) and (noun or soft) and detail)


def language_asked(message: str, default: str = "fr") -> str:
    """The language the message asks the PDF in; `default` when it names none (give "" to know it named none)."""
    text = plain(message)
    if _has(text, _ENGLISH):
        return "en"
    if _has(text, _FRENCH):
        return "fr"
    return default


def named_programs(db: Session, message: str) -> list[Program]:
    """The programs a message names: by their whole name, else by a word only they have."""
    from app.services import knowledge_service

    text = plain(message)
    programs = db.query(Program).all()
    whole = [program for program in programs if plain(program.name) and _has(text, re.escape(plain(program.name)))]
    if whole:
        return whole
    tokens = knowledge_service.tokenize(message)
    words = {program.id: knowledge_service.tokenize(program.name) for program in programs}
    frequency: dict[str, int] = {}
    for names in words.values():
        for word in names:
            frequency[word] = frequency.get(word, 0) + 1
    # A word only one program has ("devops", "flutter"); "web" or "intelligence" belong to two.
    found = [program for program in programs
             if any(frequency[word] == 1 and not word.startswith("_") and not word.isdigit() and len(word) >= 2
                    for word in words[program.id] & tokens)]
    if found:
        return found
    # The words of a field, as the assistant knows them ("flutter", "docker", "react"...).
    from app.services import chatbot

    one = chatbot._program_in(db, text)
    return [one] if one is not None else []


def their_programs(candidate: Candidate | None) -> list[Program]:
    if candidate is None:
        return []
    seen, programs = set(), []
    for registration in candidate.registrations:
        if registration.status == RegistrationStatus.CANCELLED or registration.program is None or registration.program_id in seen:
            continue
        seen.add(registration.program_id)
        programs.append(registration.program)
    return programs


def programs_meant(db: Session, message: str, candidate: Candidate | None = None, *, about: Program | None = None) -> tuple[list[Program], str]:
    """(the programs, how they were chosen: named | all | theirs | about).

    Named in the message; else all of them when it says so; else the one the
    discussion is about (a single "the program"); else the candidate's own;
    else all of them.
    """
    text = plain(message)
    named = named_programs(db, message)
    if named:
        return sorted(named, key=lambda program: program.name.lower()), "named"
    everything = sorted(db.query(Program).all(), key=lambda program: program.name.lower())
    theirs = sorted(their_programs(candidate), key=lambda program: program.name.lower())
    if _has(text, _THEIRS) and theirs:
        return theirs, "theirs"
    if _has(text, _ALL):
        return everything, "all"
    plural = _has(text, r"programs|programmes|formations|courses|trainings")
    if about is not None and not plural:
        return [about], "about"
    if theirs:
        return theirs, "theirs"
    return everything, "all"


def wants_summary(message: str) -> bool:
    """Whether the message asks for the programs in short rather than in detail."""
    return _has(plain(message), SUMMARY)


def make_file(db: Session, programs: list[Program], lang: str = "fr", candidate_name: str | None = None, summary: bool = False) -> EmailFile:
    """The PDF kept as a file an email can carry: one program in the default look (on one page when asked
    in short), several in one catalogue, or in one short summary."""
    if len(programs) == 1:
        from app.services.settings_service import get_settings

        style = "one_page" if summary else program_pdf.clean_style(getattr(get_settings(db), "program_pdf_style", None))
        return program_pdf.store(db, programs[0], style, lang, candidate_name)
    return program_pdf.store_catalogue(db, programs, lang, candidate_name, summary=summary)


def reply_text(programs: list[Program], how: str, lang: str = "fr", summary: bool = False) -> str:
    """The words of an answer that carries the PDF (the greeting and signature are added around them)."""
    count = len(programs)
    if lang == "en":
        if count == 1:
            what = (f"the program of **{program_pdf.plain(programs[0].name)}** on one page, as a PDF: what it teaches, its pace and its prices" if summary else
                    f"the detailed program of **{program_pdf.plain(programs[0].name)}**, as a PDF: its content week by week, its pace and its prices")
        else:
            whose = "your" if how == "theirs" else "our"
            what = (f"a short summary of {whose} {count} programs, in one PDF: for each one, what it teaches, how long it lasts and its prices" if summary else
                    f"the detailed program of {whose} {count} programs, together in one PDF: for each one, its content week by week, its pace and its prices")
        return f"As you asked, you will find attached {what}.\n\nIf you have any question, simply reply to this email."
    if count == 1:
        what = (f"le programme de la formation **{program_pdf.plain(programs[0].name)}** sur une page (PDF) : ce qu'elle enseigne, le rythme et les tarifs" if summary else
                f"le programme détaillé de la formation **{program_pdf.plain(programs[0].name)}** (PDF) : le contenu semaine par semaine, le rythme et les tarifs")
    else:
        whose = "vos" if how == "theirs" else "nos"
        what = (f"un résumé de {whose} {count} formations, dans un seul PDF : pour chacune, ce qu'elle enseigne, sa durée et ses tarifs" if summary else
                f"le programme détaillé de {whose} {count} formations, réunies dans un seul PDF : pour chacune, le contenu semaine par semaine, le rythme et les tarifs")
    return f"Comme demandé, vous trouverez en pièce jointe {what}.\n\nSi vous avez la moindre question, répondez simplement à cet email."
