"""Payment proofs: see the file, confirm (the payment is recorded) or refuse, ask which program it pays, add one by hand.
See services/payment_proofs.py."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Candidate
from app.models.email_file import EmailFile
from app.models.payment_proof import PaymentProof
from app.services import auth, channels, email_files, payment_proofs

router = APIRouter(tags=["Payment proofs"])


def _back(back: str, **params) -> RedirectResponse:
    target = back if back.startswith("/") and not back.startswith("//") else "/payments"
    path, _, anchor = target.partition("#")
    query = urlencode({key: value for key, value in params.items() if value})
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(path + (joiner + query if query else "") + "#" + (anchor or "proofs"), status_code=303)


def _proof(db: Session, proof_id: str) -> PaymentProof:
    proof = db.get(PaymentProof, proof_id)
    if proof is None:
        raise HTTPException(status_code=404, detail="Proof not found")
    return proof


def _who(request: Request) -> str:
    user = auth.current(request)
    return user.name if user else "admin"


@router.get("/payments/proofs/{proof_id}/file")
def proof_file(proof_id: str, db: Session = Depends(get_db)):
    proof = _proof(db, proof_id)
    file = db.get(EmailFile, proof.file_id)
    data = email_files.read_bytes(file) if file is not None else None
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(data, media_type=file.content_type if payment_proofs.is_proof_file(file.content_type) else "application/octet-stream",
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
                             "Content-Disposition": f'inline; filename="{file.filename}"'})


@router.post("/payments/proofs/{proof_id}/confirm")
def proof_confirm(proof_id: str, request: Request, registration_id: str = Form(""), amount: str = Form(""), back: str = Form("/payments"),
                  db: Session = Depends(get_db)):
    proof = _proof(db, proof_id)
    try:
        value = float((amount or "0").replace(",", ".").replace(" ", ""))
    except ValueError:
        value = 0
    try:
        payment_proofs.confirm(db, proof, registration_id=registration_id, amount=value, by=_who(request))
    except ValueError as error:
        db.rollback()
        return _back(back, error=str(error))
    return _back(back, notice=f"Payment of {value:g} DT recorded.")


@router.post("/payments/proofs/{proof_id}/refuse")
def proof_refuse(proof_id: str, request: Request, note: str = Form(""), back: str = Form("/payments"), db: Session = Depends(get_db)):
    payment_proofs.refuse(db, _proof(db, proof_id), note=note, by=_who(request))
    return _back(back, notice="Proof refused: nothing was recorded. Write to the candidate if they should send another one.")


@router.post("/payments/proofs/{proof_id}/ask")
def proof_ask(proof_id: str, back: str = Form("/payments"), db: Session = Depends(get_db)):
    """Ask the candidate which program the payment is for (the fixed question), by the way chosen above the inbox."""
    from app.services import email_assistant, email_threads
    from app.models.candidate_email import CandidateEmail

    proof = _proof(db, proof_id)
    candidate = db.get(Candidate, proof.candidate_id) if proof.candidate_id else None
    if candidate is None:
        return _back(back, error="This proof is not linked to a candidate.")
    question = payment_proofs.program_question(candidate)
    message = db.get(CandidateEmail, proof.message_id) if proof.message_id else None
    text, html_body = email_assistant.compose_reply(db, candidate, question)
    if message is not None:
        from app.models.email_thread import EmailThread

        thread = db.get(EmailThread, message.thread_id)
        sent = email_threads.deliver(db, candidate, f"Re: {thread.subject}" if thread else "Votre paiement", text, html_body,
                                     kind="reply", author="admin", thread=thread, note="Asked which program the payment is for.")
    else:
        sent = channels.deliver(db, candidate, {"subject": "Votre paiement", "text": text, "html": html_body, "short": question}, kind="reply")
    if sent is None or sent.status not in ("sent", "to_send"):
        db.rollback()
        return _back(back, error="The question could not be sent: check the email settings, or write to them from the inbox.")
    proof.asked_program_at = email_threads.utcnow()
    db.commit()
    return _back(back, notice="Asked which program the payment is for." + (" It waits for your phone (WhatsApp or SMS)." if sent.status == "to_send" else ""))


@router.post("/payments/proofs/{proof_id}/reread")
def proof_reread(proof_id: str, back: str = Form("/payments"), db: Session = Depends(get_db)):
    proof = _proof(db, proof_id)
    payment_proofs.read_now(db, proof)
    db.commit()
    return _back(back, notice="Read again.")


@router.post("/candidate/{candidate_id}/proof")
async def proof_upload(candidate_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """The admin adds a proof a candidate gave another way (a photo on a phone, a paper scanned)."""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    data = await file.read()
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    back = f"/candidate/{candidate_id}#proofs"
    if not data or not payment_proofs.is_proof_file(content_type):
        return _back(back, error="Add a photo (JPG, PNG) or a PDF of the receipt.")
    kept = email_files.store_inbound(db, [(file.filename or "preuve", content_type, data)])
    if not kept:
        return _back(back, error="This file is too big.")
    proof = payment_proofs.create(db, file=kept[0], candidate=candidate, source="upload")
    payment_proofs.read_now(db, proof)
    db.commit()
    return _back(back, notice=payment_proofs.summary(proof))
