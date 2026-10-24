"""Meaning: which known question a new one is closest to, in French, Arabic, English or Tunisian.

A multilingual sentence model (paraphrase-multilingual-mpnet-base-v2, on this computer) gives each sentence a vector;
questions with the same meaning get close vectors, whatever their words or their language. Measured here on
2026-10-22: one pair alone does not tell “the same question” from “another one” (paraphrases scored 0.36 in the middle,
different questions 0.30, up to 0.60), so a question is never judged against one sentence. It is put next to many
examples of every known question, and goes to the closest only when that one is clearly closer than the second
(a margin). On 36 guide questions worded unlike any example: 26 closest right; with the margin it answered 23, 22 of
them right (the smaller model, MiniLM-L12: 19 of 22). Tunisian in Latin letters is read through its French words (derja.py): the model does not know it.

The guide uses it when its word rules find nothing; the candidate assistant uses it only to propose a draft to the
admin (an answer they taught for a question that means the same), never to send alone. Without the model (not
downloaded, MEANING_OFF=1) nothing changes: the word rules alone decide, as before.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_MODELS = ("paraphrase-multilingual-mpnet-base-v2", "paraphrase-multilingual-MiniLM-L12-v2")     # the best one on this computer
_model = None
_failed = False
_lock = threading.Lock()
_vectors: dict[str, object] = {}          # sentence -> its vector (the banks are encoded once)
_KEEP = 20000


def _on_this_computer(name: str) -> bool:
    hub = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    return (hub / f"models--sentence-transformers--{name}").is_dir()


def model_name() -> str | None:
    wanted = os.getenv("MEANING_MODEL")
    if wanted:
        return wanted if _on_this_computer(wanted) else None
    return next((name for name in _MODELS if _on_this_computer(name)), None)


def model():
    """The sentence model, loaded once; None when it is switched off or not on this computer."""
    global _model, _failed
    if _model is not None or _failed:
        return _model
    with _lock:
        if _model is None and not _failed:
            name = model_name()
            if os.getenv("MEANING_OFF") == "1" or name is None:
                _failed = True
                return None
            try:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(name)
            except Exception:
                logger.exception("The meaning model could not be loaded; the word rules work alone.")
                _failed = True
    return _model


def available() -> bool:
    return model() is not None


def readable(text: str) -> str:
    """What the model reads: Arabic as written, Tunisian in Latin letters as its French words, the rest as written."""
    from app.services import derja

    text = " ".join((text or "").split())
    if derja.has_arabic_letters(text):
        return text
    if derja.words_in(text):
        return derja.to_french(text)
    return text


def encode(texts: list[str]):
    """Normalised vectors of these sentences (as read by `readable`), or None without the model."""
    found = model()
    if found is None or not texts:
        return None
    import numpy

    forms = [readable(text) for text in texts]
    missing = list(dict.fromkeys(form for form in forms if form not in _vectors))
    if missing:
        if len(_vectors) > _KEEP:
            _vectors.clear()
        for form, vector in zip(missing, found.encode(missing, normalize_embeddings=True, show_progress_bar=False)):
            _vectors[form] = vector
    return numpy.stack([_vectors[form] for form in forms])


def closest(question: str, bank: dict[str, list[str]], *, floor: float = 0.45, margin: float = 0.05) -> dict | None:
    """The key of `bank` whose examples are closest in meaning to the question, when it is clearly the closest:
    {"key", "score", "second", "example"}; None otherwise (or without the model)."""
    examples = [(key, text) for key, texts in bank.items() for text in texts if text and text.strip()]
    if not question or not question.strip() or len(examples) < 2:
        return None
    vectors = encode([text for _, text in examples])
    asked = encode([question])
    if vectors is None or asked is None:
        return None
    scores = vectors @ asked[0]
    best: dict[str, tuple[float, str]] = {}
    for (key, text), score in zip(examples, scores):
        if key not in best or float(score) > best[key][0]:
            best[key] = (float(score), text)
    ranked = sorted(best.items(), key=lambda item: -item[1][0])
    top_key, (top, example) = ranked[0]
    second = ranked[1][1][0] if len(ranked) > 1 else -1.0
    if top < floor or top - second < margin:
        return None
    return {"key": top_key, "score": round(top, 3), "second": round(second, 3), "example": example}


def warm(texts: list[str] | None = None) -> None:
    """Load the model (and encode these sentences) in the background, so the first question does not wait."""
    def run():
        try:
            if texts:
                encode(texts)
            else:
                model()
        except Exception:
            logger.exception("The meaning model could not be warmed.")

    threading.Thread(target=run, name="meaning-warm", daemon=True).start()
