"""Payment proofs: a candidate sends a photo or a PDF of a receipt (by email, on WhatsApp, or the admin adds it).

It is read and checked (services/proof_reader.py), matched with what the candidate owes, and waits for the admin on
the Payments page and in the candidate's file. The admin confirms (the payment is recorded: paid in full means the
registration is paid, with everything that follows) or refuses. Nothing is recorded by itself.

When the candidate owes for several programs (or none) and nothing says which one this pays, they are asked, with a
fixed text: sent at once when the assistant answers alone, otherwise prepared for the admin to send.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.payment_proof import PaymentProof
from app.services import proof_reader, tunisian_banks

logger = logging.getLogger(__name__)

PROOF_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif", "image/bmp", "image/tiff", "application/pdf")
_PAY_WORDS = re.compile(r"(virement|versement|vers[ée]|paiement|pay[ée]|payment|paid|re[çc]u|facture|invoice|receipt|transfer|"
                        r"khallast|5allast|khalast|7walt|hawalt|flous|d17|flouci|rib|banque|bank)", re.I)


def is_proof_file(content_type: str | None) -> bool:
    return (content_type or "").split(";")[0].strip().lower() in PROOF_TYPES


def talks_of_payment(text: str | None) -> bool:
    return bool(_PAY_WORDS.search(text or ""))


def centre_ribs(db: Session) -> list[str]:
    from app.services.settings_service import get_settings

    return [line.strip() for line in (getattr(get_settings(db), "centre_ribs", None) or "").splitlines() if line.strip()]


def owing(candidate) -> list:
    from app.models.registration import RegistrationStatus

    return [item for item in (candidate.registrations if candidate else [])
            if item.status != RegistrationStatus.CANCELLED and (item.remaining_amount or 0) > 0]


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def guess_registration(candidate, found: dict, words: str = ""):
    """The registration this proof pays: the only one owed, the one named in the message or on the receipt, or the one
    whose remaining amount is exactly the amount paid. None when it cannot be said (then the candidate is asked)."""
    owed = owing(candidate)
    if len(owed) == 1:
        return owed[0]
    if not owed:
        return None
    said = _plain((words or "") + " " + (found.get("text") or ""))
    named = [item for item in owed if item.program_name and _plain(item.program_name) in said]
    if len(named) == 1:
        return named[0]
    amount = found.get("amount")
    if amount is not None:
        exact = [item for item in owed if abs((item.remaining_amount or 0) - amount) < 0.5]
        if len(exact) == 1:
            return exact[0]
    return None


def create(db: Session, *, file, candidate, source: str, message=None) -> PaymentProof:
    proof = PaymentProof(candidate_id=getattr(candidate, "id", None), file_id=file.id, message_id=getattr(message, "id", None),
                         source=source, status="reading")
    db.add(proof)
    db.flush()
    return proof


def read_now(db: Session, proof: PaymentProof, *, words: str = "") -> PaymentProof:
    """Read the file, match it with what the candidate owes, run the checks. The proof then waits for the admin."""
    from app.models import Candidate
    from app.models.email_file import EmailFile
    from app.services import email_files

    file = db.get(EmailFile, proof.file_id)
    data = email_files.read_bytes(file) if file is not None else None
    found = proof_reader.read(data, file.content_type) if data else {"text": "", "amount": None, "ribs": [], "banks": [], "apps": [],
                                                                      "date": None, "reference": None, "editor": None, "fingerprint": None}
    candidate = db.get(Candidate, proof.candidate_id) if proof.candidate_id else None
    registration = guess_registration(candidate, found, words) if candidate else None
    owed = registration.remaining_amount if registration else (sum(item.remaining_amount for item in owing(candidate)) if candidate and owing(candidate) else None)
    seen = bool(found.get("fingerprint")) and db.query(PaymentProof.id).filter(
        PaymentProof.fingerprint == found["fingerprint"], PaymentProof.id != proof.id).first() is not None
    same_reference = bool(found.get("reference")) and db.query(PaymentProof.id).filter(
        PaymentProof.reference == found["reference"], PaymentProof.id != proof.id).first() is not None
    registered_on = registration.registration_date.date() if registration and registration.registration_date else None
    results = proof_reader.checks(found, owed=owed, centre_ribs=centre_ribs(db), registered_on=registered_on,
                                  seen_before=seen, same_reference=same_reference)
    bank = found.get("bank")
    proof.registration_id = registration.id if registration else None
    proof.amount = found.get("amount")
    proof.bank = f"{bank['code']} · {bank['short']}" if bank else (found["apps"][0] if found.get("apps") else None)
    proof.reference = found.get("reference")
    proof.proof_date = found.get("date")
    proof.ribs = json.dumps(found.get("ribs") or [])
    proof.checks = json.dumps(results, ensure_ascii=False)
    proof.text = (found.get("text") or "")[:6000]
    proof.fingerprint = found.get("fingerprint")
    proof.editor = found.get("editor")
    proof.verdict = proof_reader.verdict(results)
    proof.status = "waiting"
    db.flush()
    _tell_admin(db, proof, candidate)
    return proof


def _tell_admin(db: Session, proof: PaymentProof, candidate) -> None:
    from app.services.notification_service import create_notification

    label = proof_reader.VERDICTS.get(proof.verdict, ("To check",))[0]
    who = (candidate.full_name or candidate.email) if candidate else "Someone"
    amount = f"{proof.amount:g} DT" if proof.amount else "no amount read"
    create_notification(db, title=f"Payment proof from {who}: {label}", message=f"{amount}{' · ' + proof.bank if proof.bank else ''}",
                        category="PAYMENT_PROOF", action_url=f"/payments?proof={proof.id}#proofs", candidate_id=proof.candidate_id)


def program_question(candidate, language: str = "fr") -> str:
    """The fixed question: which program does this payment pay?"""
    first = ((candidate.full_name or "").split() or [""])[0].capitalize()
    owed = owing(candidate)
    if language == "en":
        lines = [f"Hello {first}," if first else "Hello,", "", "Thank you for your proof of payment."]
        if owed:
            lines += ["Which program is it for? You are registered for:"] + [f"- {item.program_name}" for item in owed]
        else:
            lines += ["Which program would you like to register for?"]
        lines += ["", "Just answer with the program's name and we will record your payment."]
    else:
        lines = [f"Bonjour {first}," if first else "Bonjour,", "", "Merci pour votre preuve de paiement."]
        if owed:
            lines += ["Pour quelle formation est ce paiement ? Vous êtes inscrit(e) à :"] + [f"- {item.program_name}" for item in owed]
        else:
            lines += ["Pour quelle formation souhaitez-vous vous inscrire ?"]
        lines += ["", "Répondez simplement avec le nom de la formation et nous enregistrerons votre paiement."]
    return "\n".join(lines)


def needs_program(db: Session, proof: PaymentProof) -> bool:
    return proof.status == "waiting" and not proof.registration_id


def program_answer(db: Session, candidate, text: str | None):
    """The candidate answers which program their payment is for: the registration it names, put on the waiting proof
    they were asked about. None when there is no such question waiting, or the answer names no program they owe."""
    if candidate is None:
        return None
    asked = (db.query(PaymentProof).filter(PaymentProof.candidate_id == candidate.id, PaymentProof.status == "waiting",
                                           PaymentProof.registration_id.is_(None), PaymentProof.asked_program_at.isnot(None))
             .order_by(PaymentProof.created_at.desc()).first())
    if asked is None:
        return None
    said = _plain(text or "")
    named = [item for item in owing(candidate) if item.program_name and (_plain(item.program_name) in said
             or any(len(word) >= 4 and word in said for word in _plain(item.program_name).replace("(", " ").replace(")", " ").split()))]
    if len(named) != 1:
        return None
    asked.registration_id = named[0].id
    db.flush()
    return named[0]


def from_message(db: Session, message, candidate, files) -> list[PaymentProof]:
    """The proofs among the files of a message the candidate sent (pictures and PDFs), read at once."""
    words = f"{message.subject or ''} {message.body or ''}"
    proofs = []
    for file in files:
        if not is_proof_file(file.content_type):
            continue
        if db.query(PaymentProof.id).filter(PaymentProof.file_id == file.id).first() is not None:
            continue
        proof = create(db, file=file, candidate=candidate, source="whatsapp" if message.channel == "whatsapp" else "email", message=message)
        read_now(db, proof, words=words)
        if not talks_of_payment(words) and not proof.amount and not proof.bank:
            # A photo that is not a payment at all (a CV, a picture): not kept as a proof.
            db.delete(proof)
            db.flush()
            continue
        proofs.append(proof)
    return proofs


def summary(proof: PaymentProof) -> str:
    label = proof_reader.VERDICTS.get(proof.verdict, ("To check",))[0]
    bits = [f"{proof.amount:g} DT" if proof.amount else "no amount read"]
    if proof.bank:
        bits.append(proof.bank)
    if proof.proof_date:
        bits.append(f"{proof.proof_date:%d/%m/%Y}")
    return f"Payment proof received ({', '.join(bits)}): {label}."


# ------------------------------------------------------------------
# The admin decides
# ------------------------------------------------------------------

def confirm(db: Session, proof: PaymentProof, *, registration_id: str, amount: float, by: str) -> None:
    """Record the payment the proof shows (raises ValueError with a message for the admin)."""
    from app.models import Registration
    from app.services import payment_service

    registration = db.get(Registration, registration_id)
    if registration is None or registration.candidate_id != proof.candidate_id:
        raise ValueError("Choose one of this candidate's registrations.")
    if amount <= 0:
        raise ValueError("Write the amount received.")
    if amount > (registration.remaining_amount or 0) + 0.5:
        raise ValueError(f"{amount:g} DT is more than the {registration.remaining_amount:g} DT still owed for {registration.program_name}.")
    payment_service.confirm_payment(db, registration.id, min(amount, registration.remaining_amount), method="proof")
    proof.status, proof.registration_id, proof.recorded_amount = "confirmed", registration.id, amount
    proof.decided_at, proof.decided_by = datetime.utcnow(), by
    db.commit()


def refuse(db: Session, proof: PaymentProof, *, note: str, by: str) -> None:
    proof.status, proof.decision_note = "refused", (note or "").strip()[:500] or None
    proof.decided_at, proof.decided_by = datetime.utcnow(), by
    db.commit()


def view(db: Session, proof: PaymentProof) -> dict:
    from app.models import Candidate, Registration

    candidate = db.get(Candidate, proof.candidate_id) if proof.candidate_id else None
    registration = db.get(Registration, proof.registration_id) if proof.registration_id else None
    label, tone, icon = proof_reader.VERDICTS.get(proof.verdict or "", ("Being read", "warn", "bi-hourglass-split"))
    try:
        checks = json.loads(proof.checks or "[]")
    except ValueError:
        checks = []
    try:
        ribs = [tunisian_banks.pretty_rib(rib) for rib in json.loads(proof.ribs or "[]")]
    except ValueError:
        ribs = []
    return {"proof": proof, "candidate": candidate, "registration": registration, "owed": owing(candidate) if candidate else [],
            "label": label, "tone": tone, "icon": icon, "checks": checks, "ribs": ribs,
            "file_url": f"/payments/proofs/{proof.id}/file"}


def waiting(db: Session, candidate_id: str | None = None, limit: int = 50) -> list[dict]:
    query = db.query(PaymentProof).filter(PaymentProof.status.in_(("waiting", "reading")))
    if candidate_id:
        query = query.filter(PaymentProof.candidate_id == candidate_id)
    return [view(db, proof) for proof in query.order_by(PaymentProof.created_at.desc()).limit(limit).all()]


def recent(db: Session, candidate_id: str | None = None, limit: int = 20) -> list[dict]:
    query = db.query(PaymentProof).filter(PaymentProof.status.in_(("confirmed", "refused")))
    if candidate_id:
        query = query.filter(PaymentProof.candidate_id == candidate_id)
    return [view(db, proof) for proof in query.order_by(PaymentProof.decided_at.desc()).limit(limit).all()]
