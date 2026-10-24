"""The emotions in what a candidate writes (email, WhatsApp, SMS), in French, English, Tunisian or Arabic.

Read with word lists written by hand, never by a model: each emotion found comes with the words that showed it, so
the admin can see why. Exclamation marks and capitals make it stronger. The assistant uses it to answer in the right
tone (a worried candidate is reassured first), and a frustrated or angry candidate is always answered by a person.
"""
from __future__ import annotations

import re
import unicodedata

# key: (label in English, label in French, the words that show it)
EMOTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "frustrated": ("Frustrated, angry", "Frustré(e), en colère", (
        "énervé", "enerve", "frustré", "frustre", "marre", "ras le bol", "inadmissible", "inacceptable", "scandale", "honte", "nul",
        "toujours pas", "personne ne répond", "personne ne repond", "angry", "annoyed", "ridiculous", "unacceptable", "fed up",
        "terrible", "worst", "no one answers", "still waiting", "ma3adech", "ta3ebt", "zhe9t", "zha9t", "تعبت", "زهقت", "مش معقول", "عيب")),
    "disappointed": ("Disappointed", "Déçu(e)", (
        "déçu", "decu", "déception", "deception", "dommage", "regrette", "pas à la hauteur", "pas a la hauteur", "disappointed",
        "let down", "expected more", "not worth", "ma 3jebnich", "ma3jebnich", "khayba", "خيبة", "ما عجبنيش")),
    "worried": ("Worried", "Inquiet / inquiète", (
        "inquiet", "inquiète", "inquiete", "peur", "stress", "stressé", "angoiss", "pas sûr", "pas sur", "est-ce que c'est grave",
        "worried", "anxious", "afraid", "nervous", "concerned", "scared", "khayef", "5ayef", "khayfa", "m9ala9", "mkala9", "قلق", "خايف", "خايفة")),
    "confused": ("Confused", "Perdu(e)", (
        "je ne comprends pas", "je comprends pas", "pas compris", "confus", "perdu", "je ne sais pas comment", "c'est flou",
        "don't understand", "dont understand", "confused", "lost", "unclear", "not clear", "mafhemtch", "mafhamtech", "ma fhemtech",
        "منفهمش", "مافهمتش", "ما فهمتش")),
    "urgent": ("In a hurry", "Pressé(e)", (
        "urgent", "urgence", "au plus vite", "rapidement", "dès que possible", "des que possible", "immédiatement", "immediatement",
        "asap", "as soon as possible", "right away", "today please", "fissa3", "fisa3", "b sor3a", "عاجل", "بسرعة")),
    "eager": ("Motivated", "Motivé(e)", (
        "hâte", "hate de", "motivé", "motive", "impatient", "pressé de commencer", "très envie", "tres envie", "can't wait", "cant wait",
        "excited", "looking forward", "eager", "motivated", "nheb nabda", "n7eb nabda", "metsharref", "متحمس", "متحمسة")),
    "happy": ("Happy", "Content(e)", (
        "content", "contente", "ravi", "ravie", "génial", "genial", "super", "parfait", "excellent", "top", "j'adore", "adore",
        "happy", "great", "awesome", "amazing", "love it", "perfect", "behi barcha", "mrigel", "fer7an", "far7ana", "فرحان", "ممتاز", "برشا باهي")),
    "grateful": ("Grateful", "Reconnaissant(e)", (
        "merci beaucoup", "mille mercis", "merci infiniment", "je vous remercie", "thank you so much", "thanks a lot", "grateful",
        "ya3tik essa7a", "ya3tik sa7a", "y3aychek", "3aychek", "barkallahoufik", "يعطيك الصحة", "يعيشك", "شكرا", "بارك الله فيك")),
}
NEGATIVE = {"frustrated", "disappointed", "worried", "confused"}
# What the assistant says first, before the answer, to a candidate who feels this way.
OPENINGS = {
    "worried": ("Je comprends votre inquiétude : voici ce qu'il en est.", "I understand your concern: here is where things stand."),
    "confused": ("Pas de souci, je vous explique simplement.", "No problem, let me explain simply."),
    "disappointed": ("Nous sommes désolés que cela ne vous ait pas convenu.", "We are sorry this did not suit you."),
    "urgent": ("Nous vous répondons tout de suite.", "We are answering you right away."),
    "eager": ("Merci pour votre enthousiasme !", "Thank you for your enthusiasm!"),
}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def read(text: str) -> dict:
    """{"emotions": [{"key", "label", "label_fr", "words", "strength"}], "main": key or None, "how": text}, strongest first."""
    plain = _plain(text)
    raw = text or ""
    shouting = len(re.findall(r"\b[A-ZÀ-Ý]{4,}\b", raw)) >= 2
    bangs = raw.count("!") >= 3 or "!!" in raw
    found = []
    for key, (label, label_fr, words) in EMOTIONS.items():
        hits, seen = [], set()
        for word in words:
            pattern = _plain(word)
            if pattern in seen:
                continue                      # one spelling of a word is enough
            if re.search(r"(?<![0-9a-z؀-ۿ])" + re.escape(pattern) + r"(?![0-9a-z؀-ۿ])", plain):
                hits.append(word)
                seen.add(pattern)
        if not hits:
            continue
        strength = min(3, len(hits) + (1 if (shouting or bangs) and key in NEGATIVE | {"urgent"} else 0))
        found.append({"key": key, "label": label, "label_fr": label_fr, "words": hits[:4], "strength": strength})
    # "Merci" alone in "merci de me répondre" is politeness, not gratitude: only the strong thanks are kept above.
    order = list(EMOTIONS)
    found.sort(key=lambda item: (-item["strength"], order.index(item["key"])))
    return {
        "emotions": found, "main": found[0]["key"] if found else None,
        "how": ("Read with word lists in French, English, Tunisian and Arabic (no model): each emotion shows the words that "
                "revealed it; exclamation marks and capitals make it stronger."),
    }


def opening(main: str | None, language: str = "fr") -> str:
    pair = OPENINGS.get(main or "")
    if not pair:
        return ""
    return pair[1] if language == "en" else pair[0]


def short(found: dict) -> str:
    """"Worried (inquiet, peur) · In a hurry (urgent)" for the inbox."""
    return " · ".join(f"{item['label']} ({', '.join(item['words'][:3])})" for item in found.get("emotions") or [])
