"""How a candidate lived a week: what they understood, how they feel, what they need.

With their weekly questions, each candidate says how the week went (five
faces), how the pace was, whether they could redo it alone, and, in their
own words, what was hard. This module reads all of it together:

- the questions say what was understood;
- the faces and the two choices say how they feel, without any guessing;
- their words are read by rules (French, English and Tunisian written in
  Latin letters): tone, what the difficulty is about, a call for help, signs
  of giving up. The local model then gives a second opinion on the words; it
  can only add a summary and flag a disagreement, never overrule the rules;
- putting result and confidence side by side tells apart the candidate who
  struggles and knows it from the one who thinks they understood and did
  not, and from the one who knows more than they believe.

Every conclusion is shown with the facts and the candidate's own words, so a
person can always check it.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup
from app.models.quiz import Quiz
from app.services import email_assistant

logger = logging.getLogger(__name__)

# What the candidate is asked, in French (the page and the Google Form use the same words).
MOODS = [
    (1, "😣", "Très difficile", "Very hard"), (2, "😕", "Difficile", "Hard"), (3, "😐", "Correcte", "Fine"),
    (4, "🙂", "Bien", "Good"), (5, "😄", "Très bien", "Very good"),
]
PACES = [("slow", "Trop lent", "Too slow"), ("right", "Bon rythme", "Right pace"), ("fast", "Trop rapide", "Too fast")]
CONFIDENCES = [
    (1, "Pas du tout", "Not at all"), (2, "Un peu", "A little"),
    (3, "Oui, avec de l'aide", "Yes, with help"), (4, "Oui, seul(e)", "Yes, alone"),
]
QUESTIONS = {
    "mood": "Comment s'est passée cette semaine pour vous ?",
    "pace": "Le rythme du cours était…",
    "confidence": "Pourriez-vous refaire seul(e) ce qui a été vu cette semaine ?",
    "hard": "Qu'est-ce qui a été le plus difficile, ou reste flou ?",
    "message": "Un mot pour votre formateur ?",
}
TEXT_LIMIT = 1200

_MOOD = {value: {"emoji": emoji, "fr": fr, "label": en} for value, emoji, fr, en in MOODS}
_PACE = {key: {"fr": fr, "label": en} for key, fr, en in PACES}
_CONFIDENCE = {value: {"fr": fr, "label": en} for value, fr, en in CONFIDENCES}


def choices(kind: str) -> list[tuple[str, str]]:
    """(value, words shown to the candidate) for one of the three choices."""
    if kind == "mood":
        return [(str(value), f"{emoji} {fr}") for value, emoji, fr, _ in MOODS]
    if kind == "pace":
        return [(key, fr) for key, fr, _ in PACES]
    return [(str(value), fr) for value, fr, _ in CONFIDENCES]


def from_words(kind: str, words) -> str | None:
    """The value behind the words a Google Form sends back."""
    wanted = " ".join(str(words or "").split()).lower()
    for value, shown in choices(kind):
        if shown.lower() == wanted or shown.split(" ", 1)[-1].lower() == wanted:
            return value
    return None


def clean_feedback(data) -> dict:
    """What a candidate said about the week, from a form. Unknown values are dropped."""
    def number(name: str, allowed) -> int | None:
        try:
            value = int(str(data.get(name) or "").strip())
        except ValueError:
            return None
        return value if value in allowed else None

    pace = str(data.get("pace") or "").strip()
    return {
        "mood": number("mood", _MOOD), "pace": pace if pace in _PACE else None,
        "confidence": number("confidence", _CONFIDENCE),
        "hard": str(data.get("hard") or "").strip()[:TEXT_LIMIT],
        "message": str(data.get("message") or "").strip()[:TEXT_LIMIT],
    }


def has_feedback(feedback: dict | None) -> bool:
    return bool(feedback) and any(feedback.get(name) for name in ("mood", "pace", "confidence", "hard", "message"))


def _json(text) -> dict:
    try:
        loaded = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def feedback_of(quiz: Quiz) -> dict:
    return _json(quiz.feedback)


def insight_of(quiz: Quiz) -> dict:
    return _json(quiz.insight)


# ------------------------------------------------------------------
# Reading their words
# ------------------------------------------------------------------

def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").replace("’", "'").lower())
    return "".join(char for char in text if not unicodedata.combining(char))


# Whole expressions come first: "pas de problème" is good news, "pas compris" is not.
_GOOD_PHRASES = [
    "pas de probleme", "aucun probleme", "sans probleme", "no problem", "pas difficile", "pas complique", "not hard",
    "not difficult", "tout est clair", "tres clair", "j'ai tout compris", "bien compris", "j'ai compris", "i understood",
    "rien a signaler", "nothing to report", "ca va bien", "fhemt kol chay", "kol chay behi", "bien passe", "j'ai aime",
    "j'ai adore", "j'aime", "i liked", "i loved", "i like", "i enjoy",
    # What people write in a published feedback.
    "a l'ecoute", "je recommande", "highly recommend", "gagner du temps", "gagner un temps",
]
_BAD_PHRASES = [
    "pas compris", "rien compris", "pas clair", "pas claire", "pas assez", "trop rapide", "trop vite", "trop de",
    "pas le temps", "pas eu le temps", "je n'arrive pas", "j'arrive pas", "n'arrive pas", "en retard", "pas bien", "pas facile",
    "pas a l'aise", "du mal a", "mal compris", "didn't understand", "did not understand", "don't understand",
    "do not understand", "not clear", "too fast", "too much", "can't follow", "cannot follow", "not easy", "not good",
    "mafhemtch", "ma fhemtch", "mafhemtech", "ma fhemtech", "mafhemt chay", "ma3jebnich", "mouch behi", "mch behi",
]
_GOOD_WORDS = {
    "bien", "super", "genial", "excellent", "excellente", "parfait", "parfaite", "clair", "claire", "interessant",
    "interessante", "facile", "content", "contente", "satisfait", "satisfaite", "merci", "bravo", "top", "agreable",
    "motive", "motivee", "utile", "enrichissant", "progres", "progresse", "passionnant", "good", "great", "clear",
    "interesting", "easy", "happy", "thanks", "thank", "helpful", "useful", "amazing", "awesome", "perfect", "fun",
    "motivated", "enjoyed", "nice", "behi", "bahi", "mli7", "mlih", "mezyen", "mezyan", "tayara", "y3aychek", "3jebni",
    "behy", "behya", "behia", "bahia", "tayyara", "competent", "competente", "competents", "competant", "enrichissante",
    "recommande", "recommend", "recommended", "disponible", "disponibles", "professionnel", "professionnelle",
    "pedagogue", "efficace", "formidable", "magnifique", "meilleur", "meilleure", "best", "satisfaisant",
    "satisfaisante", "ravi", "ravie", "reactivite", "reactif", "reactive", "facilite", "attractives", "structurees",
}
_BAD_WORDS = {
    "difficile", "difficiles", "dur", "dure", "complique", "compliquee", "compliques", "flou", "floue", "perdu", "perdue",
    "incomprehensible", "fatigue", "fatiguee", "stresse", "stressee", "stress", "decourage", "decouragee", "demotive",
    "demotivee", "ennuyeux", "ennuyant", "nul", "nulle", "mauvais", "mauvaise", "probleme", "problemes", "bloque",
    "bloquee", "confus", "confuse", "frustre", "frustrant", "galere", "inquiet", "inquiete", "peur", "surcharge",
    "difficulte", "difficultes", "lourd", "lourde", "hard", "difficult", "confusing", "confused", "lost", "unclear",
    "boring", "bored", "tired", "stressed", "frustrated", "frustrating", "problem", "problems", "stuck", "struggling",
    "struggle", "behind", "overwhelmed", "bad", "worried", "afraid", "s3ib", "sa3ib", "s3iba", "t3ebt", "ta3ben",
    "mochkla", "mochkel", "mchekel", "dhay3", "dhaya3",
}
_NEGATORS = {"pas", "not", "no", "aucun", "aucune", "jamais", "never", "sans", "mouch", "mch", "mich", "plus", "peu"}
_ASKS_HELP = re.compile(
    r"besoin d'aide|besoin d'un|aidez|m'aider|nous aider|help me|need help|reexpliquer|expliquer encore|"
    r"expliquer a nouveau|pouvez[- ]vous|est[- ]ce qu'on peut|refaire|revoir|reprendre|explain again|go over|"
    r"more examples|plus d'exemples|plus d'exercices|more exercises|more practice|plus de pratique|3awnouni|3awenni"
)
_GIVING_UP = re.compile(
    r"abandonn|arreter la formation|arreter le cours|j'arrete|decourag|demotiv|n'y arrive plus|j'en peux plus|"
    r"give up|giving up|quit|drop out|nsayeb|nwa9ef"
)

# What a difficulty is about, and what the professor can do about it.
THEMES = {
    "pace": {"label": "The pace", "icon": "bi-speedometer", "do": "Slow down on the hard parts, or start with a ten-minute recap.",
             "words": r"rapide|vite|rythme|lent|pace|fast|slow|temps de (comprendre|suivre|noter)|suivre"},
    "practice": {"label": "Not enough practice", "icon": "bi-tools", "do": "Add an exercise done together before they try alone.",
                 "words": r"exercice|pratique|\btp\b|projet|exemple|practice|exercise|example|hands[- ]on|atelier|manip"},
    "explanation": {"label": "The explanations", "icon": "bi-chat-square-text", "do": "Explain the idea again another way, with a drawing or a real case.",
                    "words": r"explication|expliqu|flou|theori|concept|notion|comprendre|compris|unclear|theory|understand|logique"},
    "tools": {"label": "Tools and installation", "icon": "bi-pc-display", "do": "Check everyone's setup at the start of the next session.",
              "words": r"install|configur|erreur|\bbug|ordinateur|\bpc\b|logiciel|setup|error|environnement|environment|version|machine|terminal"},
    "time": {"label": "Time and workload", "icon": "bi-hourglass-split", "do": "Say what is essential this week and what can wait.",
             "words": r"\btemps\b|charge|devoir|travail a faire|emploi|\btime\b|workload|busy|homework|deadline|boulot|examens?|revision"},
    "online": {"label": "Following online", "icon": "bi-wifi", "do": "Share the recording and check the sound at the start.",
               "words": r"connexion|internet|en ligne|micro|\bson\b|camera|\bmeet\b|zoom|connection|audio|enregistrement|recording|coupure"},
    "basics": {"label": "Missing basics", "icon": "bi-layers", "do": "Give a short reminder of the basics, or a resource to catch up.",
               "words": r"\bbases?\b|prerequis|niveau|debutant|basics|background|prerequisite|jamais fait|never done|nouveau pour moi"},
    "motivation": {"label": "Motivation", "icon": "bi-battery-half", "do": "Talk with them one to one before the next session.",
                   "words": r"motivation|motiv|abandon|decourag|give up|quit|envie|moral"},
}
_THEME_PATTERNS = {key: re.compile(theme["words"]) for key, theme in THEMES.items()}
_STOP = {
    "avec", "pour", "dans", "cette", "sont", "mais", "plus", "tout", "tous", "très", "tres", "bien", "comme", "nous", "vous",
    "elle", "être", "etre", "avoir", "fait", "faire", "that", "this", "with", "from", "have", "what", "which", "when", "were",
    "they", "their", "about", "quel", "quelle", "quels", "quelles", "entre", "leur", "leurs", "aussi", "semaine", "cours",
}


def tone_of(text: str) -> dict:
    """The tone of a candidate's words by rules: {"tone", "positive", "negative"} with what was found."""
    plain = _plain(text)
    if not plain.strip():
        return {"tone": "none", "positive": [], "negative": []}
    good, bad = [], []
    for phrases, found in ((_BAD_PHRASES, bad), (_GOOD_PHRASES, good)):
        for phrase in sorted(phrases, key=len, reverse=True):
            if phrase in plain:
                found.append(phrase)
                plain = plain.replace(phrase, " . ")
    words = re.findall(r"[a-z0-9']+", plain)
    for index, word in enumerate(words):
        word = word.strip("'")
        word = word[2:] if word[:2] in ("l'", "d'", "j'", "c'", "s'", "n'", "m'", "t'") else word
        if word not in _GOOD_WORDS and word not in _BAD_WORDS:
            continue
        denied = any(previous in _NEGATORS for previous in words[max(0, index - 2):index])
        is_good = (word in _GOOD_WORDS) != denied
        (good if is_good else bad).append(("pas " if denied else "") + word)
    if not good and not bad:
        tone = "neutral"
    elif bad and good:
        tone = "negative" if len(bad) >= 2 * len(good) else "positive" if len(good) >= 2 * len(bad) else "mixed"
    else:
        tone = "negative" if bad else "positive"
    return {"tone": tone, "positive": good[:6], "negative": bad[:6]}


def themes_of(text: str) -> list[str]:
    plain = _plain(text)
    return [key for key, pattern in _THEME_PATTERNS.items() if pattern.search(plain)]


def mentions_of(text: str, vocabulary: set[str]) -> list[str]:
    """Words of the course the candidate names when saying what was hard."""
    found = []
    for word in re.findall(r"[^\W\d_][\w\-+#.]{2,}", text or ""):
        key = _plain(word).strip(".")
        if len(key) >= 4 and key not in _STOP and key in vocabulary and word.strip(".") not in found:
            found.append(word.strip("."))
    return found[:6]


def vocabulary_of(quiz: Quiz, topic: str = "") -> set[str]:
    """The words of the week: its topic and its questions."""
    try:
        questions = json.loads(quiz.questions or "[]")
    except (TypeError, ValueError):
        questions = []
    text = topic + " " + " ".join(
        f"{item.get('question', '')} {' '.join(str(option) for option in item.get('options', []))}"
        for item in questions if isinstance(item, dict)
    )
    return {word for word in (_plain(found).strip(".") for found in re.findall(r"[^\W\d_][\w\-+#.]{2,}", text)) if len(word) >= 4 and word not in _STOP}


# ------------------------------------------------------------------
# Putting it together
# ------------------------------------------------------------------

SENTIMENTS = {
    "positive": {"label": "Feels good", "tone": "ok", "emoji": "🙂"},
    "neutral": {"label": "Neutral", "tone": "muted", "emoji": "😐"},
    "negative": {"label": "Having a hard time", "tone": "bad", "emoji": "😕"},
    "mixed": {"label": "Mixed signals", "tone": "warn", "emoji": "🤔"},
}
PROFILES = {
    "on_track": {"label": "On track", "tone": "ok", "icon": "bi-check2-circle",
                 "text": "Understood the week and feels able to redo it."},
    "overconfident": {"label": "Sure of themselves, but the answers say otherwise", "tone": "warn", "icon": "bi-exclamation-diamond",
                      "text": "Feels able to redo it, yet the answers show gaps they do not see. Go over the missed questions with them."},
    "underconfident": {"label": "Knows more than they think", "tone": "info", "icon": "bi-emoji-smile",
                       "text": "The answers are good but they do not feel ready: they need encouragement and practice, not more theory."},
    "struggling": {"label": "Struggling, and knows it", "tone": "bad", "icon": "bi-life-preserver",
                   "text": "Low result and low confidence: they need help now, before the next session."},
}
ATTENTION = {
    "act": {"label": "Needs you now", "tone": "bad"},
    "watch": {"label": "Keep an eye", "tone": "warn"},
    "ok": {"label": "Doing fine", "tone": "ok"},
}


def _sentiment(mood: int | None, tone: str) -> str | None:
    if mood is None:
        return {"positive": "positive", "negative": "negative", "mixed": "mixed", "neutral": "neutral"}.get(tone)
    if mood >= 4:
        return "mixed" if tone == "negative" else "positive"
    if mood <= 2:
        return "mixed" if tone == "positive" else "negative"
    return "negative" if tone == "negative" else "positive" if tone == "positive" else "neutral"


def _profile(score: float | None, confidence: int | None, mood: int | None) -> str | None:
    sure = confidence >= 3 if confidence else (mood >= 4 if mood else None)
    if sure is None or score is None:
        return None
    knows = score >= 60
    return ("on_track" if knows else "overconfident") if sure else ("underconfident" if knows else "struggling")


def analyse(quiz: Quiz, previous: dict | None = None, topic: str = "") -> dict:
    """Everything that can be said about one candidate's week. `previous` is
    the insight of their week before, for the trend."""
    feedback = feedback_of(quiz)
    done = quiz.completed_at is not None
    score = float(quiz.score or 0) if done else None
    mood, pace, confidence = feedback.get("mood"), feedback.get("pace"), feedback.get("confidence")
    words = " ".join(part for part in (feedback.get("hard"), feedback.get("message")) if part).strip()
    text = tone_of(words)
    themes = themes_of(words)
    if pace == "fast" and "pace" not in themes:
        themes.insert(0, "pace")
    plain = _plain(words)
    asks_help, giving_up = bool(_ASKS_HELP.search(plain)), bool(_GIVING_UP.search(plain))
    if giving_up and "motivation" not in themes:
        themes.append("motivation")
    profile = _profile(score, confidence, mood)
    sentiment = _sentiment(mood, text["tone"])

    trend = {"mood": None, "score": None}
    if previous:
        if mood and previous.get("mood"):
            trend["mood"] = mood - previous["mood"]
        if score is not None and previous.get("score") is not None:
            trend["score"] = round(score - previous["score"])

    points, reasons = 0, []
    def add(amount: int, reason: str) -> None:
        nonlocal points
        points += amount
        reasons.append(reason)
    if giving_up:
        add(40, "talks about giving up")
    if score is not None and score < 50:
        add(30, f"understood little of the week ({score:.0f}%)")
    if mood and mood <= 2:
        add(25, f"found the week {_MOOD[mood]['label'].lower()}")
    if asks_help:
        add(20, "asks for help")
    if confidence == 1:
        add(15, "does not feel able to redo it at all")
    if profile == "overconfident":
        add(15, "thinks they understood, the answers say otherwise")
    if pace == "fast":
        add(10, "found the pace too fast")
    if trend["mood"] is not None and trend["mood"] <= -2:
        add(15, "feels clearly worse than the week before")
    if trend["score"] is not None and trend["score"] <= -25:
        add(15, f"result down {abs(trend['score'])} points on the week before")
    if text["tone"] == "negative" and not (mood and mood <= 2):
        add(10, "their words are negative")
    level = "act" if points >= 50 else "watch" if points >= 25 else "ok"

    insight = {
        "version": 1, "at": datetime.utcnow().isoformat(timespec="seconds"),
        "score": score, "mood": mood, "pace": pace, "confidence": confidence,
        "words": words, "text": text, "themes": themes, "mentions": mentions_of(words, vocabulary_of(quiz, topic)),
        "asks_help": asks_help, "giving_up": giving_up, "sentiment": sentiment, "profile": profile,
        "attention": {"level": level, "points": min(points, 100), "reasons": reasons}, "trend": trend,
        "reading": None, "reading_state": "pending" if len(words) >= 12 else "none",
    }
    insight["summary"] = summary_of(insight)
    return insight


def summary_of(insight: dict) -> str:
    """The week of one candidate in two sentences, from the facts."""
    parts = []
    mood, score, pace, confidence = insight.get("mood"), insight.get("score"), insight.get("pace"), insight.get("confidence")
    if mood:
        feeling = f"Found the week {_MOOD[mood]['label'].lower()}"
        if pace and pace != "right":
            feeling += f" and the pace {_PACE[pace]['label'].lower()}"
        parts.append(feeling + ".")
    elif pace and pace != "right":
        parts.append(f"Found the pace {_PACE[pace]['label'].lower()}.")
    if score is not None:
        result = f"Got {score:.0f}% on the questions"
        if confidence:
            result += f" and answers “{_CONFIDENCE[confidence]['label'].lower()}” to redoing it alone"
        parts.append(result + ".")
    if insight.get("profile") in ("overconfident", "underconfident"):
        parts.append(PROFILES[insight["profile"]]["label"] + ".")
    about = [THEMES[key]["label"].lower() for key in insight.get("themes", [])[:2]]
    if about and insight.get("words"):
        parts.append("Their words are about " + " and ".join(about) + ".")
    if insight.get("giving_up"):
        parts.append("Talks about giving up.")
    elif insight.get("asks_help"):
        parts.append("Asks for help.")
    return " ".join(parts) or "Answered the questions without saying how the week went."


def store(db: Session, quiz: Quiz, topic: str = "") -> dict:
    """Work out and keep the insight of an answered weekly quiz."""
    earlier = (
        db.query(Quiz)
        .filter(
            Quiz.kind == "weekly", Quiz.candidate_id == quiz.candidate_id, Quiz.class_group_id == quiz.class_group_id,
            Quiz.week_number < (quiz.week_number or 0), Quiz.completed_at.isnot(None),
        )
        .order_by(Quiz.week_number.desc())
        .first()
    )
    insight = analyse(quiz, previous=insight_of(earlier) if earlier is not None else None, topic=topic)
    quiz.insight = json.dumps(insight, ensure_ascii=False)
    quiz.sentiment = insight["sentiment"]
    quiz.sentiment_summary = insight["summary"]
    db.flush()
    return insight


# ------------------------------------------------------------------
# The model's second opinion on their words
# ------------------------------------------------------------------

_SYSTEM = (
    "You read a short message a learner wrote to their trainer after a week of course. "
    "You only report what the message says. You answer with one JSON object and nothing else."
)
_TONES = {"positive", "neutral", "negative", "mixed"}


def read_with_model(words: str) -> dict | None:
    """What the local model reads in a candidate's words, kept only where it can be checked."""
    prompt = (
        "The learner wrote (in French, English, or Tunisian Arabic written in Latin letters):\n"
        f'"""{words[:900]}"""\n\n'
        "Answer with this JSON object:\n"
        '{"tone": "positive" or "neutral" or "negative" or "mixed", '
        '"difficulty": "in English, at most 10 words: what they find hard, or an empty text", '
        '"summary": "in English, one sentence of at most 22 words, only what the message says", '
        '"quote": "the few words of the message that show its tone best, copied exactly"}'
    )
    answer = email_assistant.llm_json(_SYSTEM, prompt, max_tokens=220)
    if not isinstance(answer, dict):
        return None
    tone = str(answer.get("tone") or "").strip().lower()
    summary = " ".join(str(answer.get("summary") or "").split())[:220]
    if tone not in _TONES or len(summary) < 8:
        return None
    quote = " ".join(str(answer.get("quote") or "").split()).strip("\"'« »")[:200]
    # A quote that is not in the message was invented: it is dropped.
    if quote and fuzz.partial_ratio(_plain(quote), _plain(words)) < 90:
        quote = ""
    return {"tone": tone, "summary": summary, "quote": quote, "difficulty": " ".join(str(answer.get("difficulty") or "").split())[:90]}


def _agree(rules: str, model: str) -> bool:
    return not ({rules, model} == {"positive", "negative"})


def enrich_one(db: Session) -> bool:
    """Give one waiting answer its second reading by the model. True when one was done."""
    waiting = (
        db.query(Quiz)
        .filter(Quiz.kind == "weekly", Quiz.completed_at.isnot(None), Quiz.insight.like('%"reading_state": "pending"%'))
        .order_by(Quiz.completed_at.asc())
        .first()
    )
    if waiting is None:
        return False
    insight = insight_of(waiting)
    reading = read_with_model(insight.get("words") or "")
    if reading is None:
        tries = int(insight.get("reading_tries") or 0) + 1
        insight["reading_tries"] = tries
        insight["reading_state"] = "failed" if tries >= 2 else "pending"
    else:
        reading["agrees"] = _agree(insight.get("text", {}).get("tone", "neutral"), reading["tone"])
        insight["reading"], insight["reading_state"] = reading, "done"
        if not reading["agrees"]:
            # Rules and model read opposite tones: a person should read the words.
            insight["sentiment"] = "mixed"
            waiting.sentiment = "mixed"
            reasons = insight["attention"]["reasons"]
            if "their words can be read two ways: read them" not in reasons:
                reasons.append("their words can be read two ways: read them")
    waiting.insight = json.dumps(insight, ensure_ascii=False)
    db.commit()
    return True


# ------------------------------------------------------------------
# For the pages
# ------------------------------------------------------------------

def view(insight: dict) -> dict:
    """An insight with the words and colours a page shows."""
    if not insight:
        return {}
    mood, pace, confidence = insight.get("mood"), insight.get("pace"), insight.get("confidence")
    reading = insight.get("reading") or None
    return {
        **insight,
        "mood_view": _MOOD.get(mood), "pace_view": _PACE.get(pace), "confidence_view": _CONFIDENCE.get(confidence),
        "sentiment_view": SENTIMENTS.get(insight.get("sentiment")),
        "profile_view": PROFILES.get(insight.get("profile")),
        "attention_view": ATTENTION[insight.get("attention", {}).get("level", "ok")],
        "theme_views": [{"key": key, **THEMES[key]} for key in insight.get("themes", []) if key in THEMES],
        "reading": reading,
        "has_feeling": bool(mood or pace or confidence or insight.get("words")),
    }


def _answered(db: Session, class_id: str, week_number: int | None = None) -> list[Quiz]:
    query = db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.class_group_id == class_id, Quiz.completed_at.isnot(None))
    if week_number is not None:
        query = query.filter(Quiz.week_number == week_number)
    return query.order_by(Quiz.week_number.asc(), Quiz.completed_at.asc()).all()


def week_feelings(db: Session, class_group: ClassGroup, week_number: int, hardest: list[dict] | None = None) -> dict:
    """How the class lived one week: faces, pace, what was hard, who needs the professor."""
    quizzes = _answered(db, class_group.id, week_number)
    names = {
        candidate.id: candidate for candidate in db.query(Candidate).filter(
            Candidate.id.in_([quiz.candidate_id for quiz in quizzes] or [""])
        ).all()
    }
    people, moods, paces, confidences = [], [0] * 5, {"slow": 0, "right": 0, "fast": 0}, []
    sentiments = {key: 0 for key in SENTIMENTS}
    themes: dict[str, dict] = {}
    profiles = {key: 0 for key in PROFILES}
    for quiz in quizzes:
        insight = insight_of(quiz)
        if not insight:
            continue
        candidate = names.get(quiz.candidate_id)
        name = (candidate.full_name or candidate.email) if candidate else "Candidate"
        shown = view(insight)
        people.append({"name": name, "candidate": candidate, "quiz": quiz, "insight": shown})
        if insight.get("mood"):
            moods[insight["mood"] - 1] += 1
        if insight.get("pace"):
            paces[insight["pace"]] += 1
        if insight.get("confidence"):
            confidences.append(insight["confidence"])
        if insight.get("sentiment") in sentiments:
            sentiments[insight["sentiment"]] += 1
        if insight.get("profile") in profiles:
            profiles[insight["profile"]] += 1
        for key in insight.get("themes", []):
            if key in THEMES:
                entry = themes.setdefault(key, {"key": key, **THEMES[key], "count": 0, "quotes": []})
                entry["count"] += 1
                if insight.get("words") and len(entry["quotes"]) < 3:
                    entry["quotes"].append({"name": name.split()[0], "words": insight["words"][:220]})
    felt = sum(moods)
    average = round(sum((index + 1) * count for index, count in enumerate(moods)) / felt, 1) if felt else None
    order = {"act": 0, "watch": 1, "ok": 2}
    people.sort(key=lambda row: (order[row["insight"]["attention"]["level"]], -row["insight"]["attention"]["points"], row["name"]))

    said_pace = sum(paces.values())
    advice = []
    if said_pace >= 2 and paces["fast"] / said_pace >= 0.4:
        advice.append({"icon": "bi-speedometer", "text": f"Slow down or start with a recap: {paces['fast']} of {said_pace} found the pace too fast."})
    if said_pace >= 2 and paces["slow"] / said_pace >= 0.4:
        advice.append({"icon": "bi-fast-forward", "text": f"The pace can go up: {paces['slow']} of {said_pace} found it too slow."})
    if average is not None and felt >= 2 and average < 2.8:
        advice.append({"icon": "bi-chat-heart", "text": f"Open the next session by asking what went wrong: the week felt hard to most (average {average} out of 5)."})
    if profiles["overconfident"] >= 2:
        advice.append({"icon": "bi-exclamation-diamond", "text": f"Correct the questions together: {profiles['overconfident']} candidates feel sure but answered wrong."})
    for theme in sorted(themes.values(), key=lambda entry: -entry["count"]):
        if theme["count"] >= 2 and theme["key"] != "pace":
            advice.append({"icon": theme["icon"], "text": f"{theme['label']} comes up {theme['count']} times. {theme['do']}"})
    for entry in (hardest or [])[:2]:
        if entry["asked"] >= 2 and entry["wrong"] / entry["asked"] >= 0.5:
            advice.append({"icon": "bi-arrow-repeat", "text": f"Go over again: “{entry['question']}” ({entry['wrong']} of {entry['asked']} got it wrong)."})
    return {
        "answers": len(quizzes), "felt": felt, "people": people,
        "mood": {"average": average, "counts": moods, "faces": [{"emoji": emoji, "label": en, "count": moods[value - 1]} for value, emoji, _, en in MOODS],
                 "top": max(moods) if felt else 0},
        "pace": {**paces, "said": said_pace},
        "confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "sentiments": [{"key": key, **SENTIMENTS[key], "count": count} for key, count in sentiments.items() if count],
        "profiles": [{"key": key, **PROFILES[key], "count": count} for key, count in profiles.items() if count],
        "themes": sorted(themes.values(), key=lambda entry: -entry["count"]),
        "to_act": sum(1 for row in people if row["insight"]["attention"]["level"] == "act"),
        "to_watch": sum(1 for row in people if row["insight"]["attention"]["level"] == "watch"),
        "advice": advice[:5],
    }


def candidate_feelings(db: Session, candidate_id: str) -> dict:
    """A candidate's weeks side by side: mood, result, and where they stand now."""
    quizzes = (
        db.query(Quiz)
        .filter(Quiz.kind == "weekly", Quiz.candidate_id == candidate_id, Quiz.completed_at.isnot(None))
        .order_by(Quiz.completed_at.asc())
        .all()
    )
    weeks = []
    for quiz in quizzes:
        insight = insight_of(quiz)
        if insight:
            weeks.append({"quiz": quiz, "week": quiz.week_number, "course": quiz.course_name, "insight": view(insight)})
    felt = [week for week in weeks if week["insight"].get("has_feeling")]
    latest = weeks[-1] if weeks else None
    moods = [week["insight"]["mood"] for week in weeks if week["insight"].get("mood")]
    direction = None
    if len(moods) >= 2:
        change = moods[-1] - moods[-2]
        direction = "up" if change > 0 else "down" if change < 0 else "steady"
    return {
        "weeks": weeks, "latest": latest, "felt": len(felt),
        "mood_average": round(sum(moods) / len(moods), 1) if moods else None, "mood_direction": direction,
    }


def latest_week(db: Session, class_id: str) -> int | None:
    """The most recent week of a class that has answers."""
    quiz = (
        db.query(Quiz)
        .filter(Quiz.kind == "weekly", Quiz.class_group_id == class_id, Quiz.completed_at.isnot(None))
        .order_by(Quiz.week_number.desc())
        .first()
    )
    return quiz.week_number if quiz is not None else None
