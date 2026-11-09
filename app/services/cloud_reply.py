"""The email assistant's reader: a candidate's message understood by the cloud model and answered from their facts.

read() gives what cloud_answer found (or None: no key, no connection, quota spent… and the email assistant does as
before). as_found() turns it into the email assistant's own answer, so its rules decide as for any answer: sent alone
only when every point is answered by facts that were checked, the assistant is on "answers alone", and nothing else
holds it (a frustrated candidate, a language read less reliably…); otherwise a draft for the admin, saying what is
missing. A refund, a cancellation or a complaint read in the message always comes to the admin, with no draft.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

SURE = 0.95                    # every point answered from checked facts
PARTLY = 0.5


def _history(db: Session, message) -> list[dict]:
    """The last messages of the conversation before this one (ours and theirs), so a short reply is understood."""
    from app.models.candidate_email import CandidateEmail

    rows = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == message.thread_id, CandidateEmail.id != message.id)
            .order_by(CandidateEmail.received_at.desc()).limit(3).all())
    return [{"role": "user" if row.direction == "inbound" else "assistant", "content": (row.body or "")[:600]} for row in reversed(rows)]


def read(db: Session, candidate, message, subject: str = "") -> dict | None:
    from app.services import cloud_answer, cloud_model, lookups

    if not cloud_model.available():
        return None
    try:
        return cloud_answer.answer(message.body or "", lookups.for_candidate(db, candidate), "candidate",
                                   history=_history(db, message), subject=subject or "")
    except Exception:                       # the reader never stops an email from being handled
        import logging

        logging.getLogger(__name__).exception("The cloud reader failed on an email")
        return None


def as_found(found: dict) -> dict:
    left = [item["ask"] for item in found["asks"] if not item["verified"]]
    blocker = None
    if not found["ok"]:
        blocker = ("The answer written has something the application's facts do not show (" + ", ".join(found["unsupported"][:3])
                   + "): read it before it goes.")
    elif left or not found["asks"]:
        blocker = ("Part of the message is not in the application's facts: " + "; ".join(f"“{ask}”" for ask in left[:3])
                   + ". The draft answers the rest and says the team will answer that point.") if left else \
                  "The message was read, but no point of it could be checked against the application's facts."
    sure = found["ok"] and found["covered"]
    return {"method": "cloud", "answer": found["reply"], "confidence": SURE if sure else PARTLY, "hits": [], "used": [],
            "blocker": blocker, "evidence": 1.0 if sure else PARTLY, "unsupported": found["unsupported"], "programs": [], "files": [],
            "language": found["language"] if found["language"] in ("ar", "tn") else None,
            "cloud": {"asks": found["asks"], "used": found["used"]}}
