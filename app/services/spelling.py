"""Understanding words written with mistakes: "inscripton", "paimant", "formtion", "pyhton", "certificta".

A word is put right only when it is close to one of the words the application understands (the subjects it answers
about, the programs' names, the usual words of a training centre), starts with the same two letters, and is not
itself a real word: every word of the application's own texts, in English and French, counts as real. Nothing else is
touched: names, numbers, emails, Arabic letters and Tunisian words stay as they were (the Tunisian list reads those).
What was put right is said, so a person can see how the message was understood.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from functools import lru_cache

# Words a candidate often writes about, beyond the subjects' own lists (knowledge_service._CONCEPTS).
_DOMAIN = """inscription inscrire inscrit inscrite paiement payer paye payement virement cheque recu formation formations programme programmes
seance seances session sessions horaire horaires professeur professeurs formateur formatrice semaine semaines niveau niveaux presentiel
distanciel enligne remboursement rembourser annulation annuler attestation certificat certification disponible disponibles places
commence commencer debut demarrage duree combien quand comment pourquoi adresse lien liens password motdepasse connexion
enregistrement enregistrements video videos support supports exercice exercices projet projets stage stages emploi travail
groupe groupes classe classes absence absences retard examen examens diplome inscriptions tarif tarifs reduction promotion
registration register payment payments training course courses schedule teacher professor certificate refund cancel
python devops docker kubernetes react angular flutter javascript typescript django spring node marketing cybersecurity
intelligence artificielle donnees business powerbi arduino reseau reseaux securite developpement mobile""".split()
_WORD = re.compile(r"[A-Za-zÀ-ÿ']{5,}")


def _fold(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(char))


@lru_cache(maxsize=1)
def _known() -> tuple[frozenset, tuple]:
    """(every real word the application knows, the words a mistake may be put right to)."""
    from app.services import knowledge_service
    from app.services.translation_seed import MORE, SEED

    real = set(_DOMAIN) | set(knowledge_service._CONCEPT_OF) | set(knowledge_service._STOPWORDS)
    for source in (SEED, MORE):
        for english, pair in source.items():
            for text in (english, *pair):
                # What a text quotes (“inscripton”, an example of a mistake) is not taken as a real word.
                plain = re.sub(r"“[^”]*”|«[^»]*»|\"[^\"]*\"", " ", text or "")
                real.update(_fold(word) for word in re.findall(r"[A-Za-zÀ-ÿ']+", plain))
    targets = sorted({word for word in set(_DOMAIN) | set(knowledge_service._CONCEPT_OF) if len(word) >= 5 and word.isalpha()})
    return frozenset(real), tuple(targets)


@lru_cache(maxsize=1)
def _program_words() -> tuple:
    try:
        from app.database import SessionLocal
        from app.models import Program

        with SessionLocal() as db:
            names = [row[0] for row in db.query(Program.name).all()]
    except Exception:
        return ()
    return tuple(sorted({_fold(word) for name in names for word in re.findall(r"[A-Za-zÀ-ÿ]{5,}", name or "")}))


def forget() -> None:
    """After programs change: their names are read again."""
    _program_words.cache_clear()


def fix_word(word: str) -> str | None:
    """The word it was meant to be, or None."""
    folded = _fold(word)
    real, targets = _known()
    if len(folded) < 5 or folded in real or not folded.isalpha():
        return None
    pool = [target for target in targets + _program_words() if target[:2] == folded[:2] and abs(len(target) - len(folded)) <= 2]
    found = difflib.get_close_matches(folded, pool, n=1, cutoff=0.8)
    return found[0] if found else None


def correct(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The text with its mistakes put right, and what was put right: [(written, understood)]."""
    if not text:
        return text, []
    fixes: list[tuple[str, str]] = []

    def put_right(match: re.Match) -> str:
        word = match.group(0)
        if "@" in text[max(0, match.start() - 1): match.end() + 1]:
            return word
        right = fix_word(word)
        if right is None:
            return word
        fixes.append((word, right))
        return right

    fixed = _WORD.sub(put_right, text)
    return fixed, fixes
