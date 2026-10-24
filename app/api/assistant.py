import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup
from app.models.notification import Notification
from app.models.quiz import Quiz
from app.models.registration import Registration
from app.models.candidate_reminder import CandidateReminder
from app.services import chatbot
from datetime import datetime, timedelta


router = APIRouter(prefix="/assistant", tags=["Assistant"])


class AssistantMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list)
    candidate_id: str | None = None
    lang: str | None = None          # chosen in the window: en, fr, ar, tn (none: the language of the question)


@router.post("/chat")
def chat_with_assistant(payload: AssistantMessage, request: Request, db: Session = Depends(get_db)):
    # optional candidate context enables the assistant to return structured recommendations
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    context = {
        "candidates": db.query(Candidate).count(),
        "registrations": db.query(Registration).count(),
        "classes": db.query(ClassGroup).count(),
        "quizzes": db.query(Quiz).count(),
        "prepared_quizzes": db.query(Quiz).filter(Quiz.sent_at.is_(None)).count(),
        "planned_reminders": db.query(CandidateReminder).filter(
            CandidateReminder.status == "planned"
        ).count(),
        "sent_reminders": db.query(CandidateReminder).filter(
            CandidateReminder.status == "sent"
        ).count(),
        "unread_notifications": db.query(Notification).filter(
            Notification.recipient_role == "admin",
            Notification.is_read.is_(False),
        ).count(),
    }
    history = "\n".join(
        f"{item.get('role', 'user').upper()}: {item.get('content', '')}"
        for item in payload.history[-12:]
        if item.get("content")
    )
    prompt = f"""You are the 9antra administration assistant and FAQ guide.
"""

    # If candidate context is provided, compute structured recommendations
    recommendations = []
    if payload.candidate_id:
        candidate = db.query(Candidate).filter(Candidate.id == payload.candidate_id).first()
        if candidate:
            # Prioritize: unpaid registrations older than 24h, upcoming class starts, then planned reminders
            now = datetime.utcnow()
            # Unpaid or partially paid registrations
            unpaid_regs = [r for r in candidate.registrations if getattr(r, 'payment_status', None) and r.payment_status.value in ('UNPAID', 'PARTIAL')]
            for reg in unpaid_regs:
                recommendations.append({
                    "id": f"payment_reg_{reg.id}",
                    "type": "payment",
                    "title": "Payment reminder",
                    "reason": f"Registration {reg.program_name or reg.program_id} has remaining {reg.remaining_amount:.2f} DT.",
                    "suggested_for": (now + timedelta(hours=1)).isoformat(),
                    "registration_id": reg.id,
                })
            # Upcoming class starts
            for reg in candidate.registrations:
                start = getattr(reg, 'training_start_date', None)
                if start:
                    delta = start - now
                    if delta.total_seconds() > 0 and delta.days <= 7:
                        recommendations.append({
                            "id": f"training_reg_{reg.id}",
                            "type": "training",
                            "title": "Upcoming class start",
                            "reason": f"Class for {reg.program_name or reg.program_id} starts on {start.strftime('%d/%m/%Y')}.",
                            "suggested_for": (now + timedelta(minutes=30)).isoformat(),
                            "registration_id": reg.id,
                        })
            # Include planned reminders from CandidateReminder
            planned = db.query(CandidateReminder).filter(CandidateReminder.candidate_id == candidate.id, CandidateReminder.status == 'planned').order_by(CandidateReminder.planned_for.asc()).all()
            for rem in planned:
                recommendations.append({
                    "id": rem.id,
                    "type": rem.reminder_type,
                    "title": f"Planned {rem.reminder_type} reminder",
                    "reason": rem.strategy or "Planned reminder",
                    "suggested_for": rem.planned_for.isoformat() if rem.planned_for else None,
                    "reminder_id": rem.id,
                })

    # The answer comes from the application's own data, its guides and what it knows of the website
    # (see chatbot); the language model is only its last resort.
    from app.services import auth

    found = chatbot.answer(db, message, payload.history, user=auth.current(request), lang=payload.lang)
    return {"reply": found["reply"], "links": found.get("links") or [], "source": found.get("source"),
            "sources": found.get("sources") or [], "resources": found.get("resources") or [],
            "suggestions": found.get("suggestions") or [], "context": context, "recommendations": recommendations,
            "schema": found.get("schema")}


@router.post("/auto-send")
def switch_auto_send(request: Request, on: str = Form("1"), db: Session = Depends(get_db)):
    """“Send at once”: what the admin asks the assistant to send goes at once (on), or is only prepared (off)."""
    from app.services import auth
    from app.services.settings_service import get_settings

    user = auth.current(request)
    if user is None or not (user.is_admin or (user.permissions or {}).get("assistant") == "edit"):
        raise HTTPException(status_code=403, detail="Only someone who may change things decides this.")
    settings = get_settings(db)
    settings.assistant_auto_send = on == "1"
    db.commit()
    return {"on": bool(settings.assistant_auto_send)}


@router.get("/suggestions")
def suggestions():
    """A few questions to start with."""
    return {"suggestions": chatbot.SUGGESTIONS}


def _local_assistant_reply(message: str, context: dict) -> str:
    """Keep FAQ assistance useful when the remote model is unavailable."""
    text = message.lower()
    if any(word in text for word in ("start", "commence", "démarre", "يبدا", "وقتاش")):
        return (
            "Pour connaître le début, ouvre Classes et consulte la date de début de la classe assignée. "
            "Si aucune classe n'est assignée, le système propose généralement un démarrage après le délai "
            "par défaut; l'administrateur peut modifier cette date."
        )
    if any(word in text for word in ("reminder", "rappel", "relance", "تذكير")):
        return (
            f"Il y a {context['planned_reminders']} rappel(s) planifié(s) et "
            f"{context['sent_reminders']} rappel(s) envoyé(s). Les recommandations priorisent d'abord "
            "les paiements incomplets, puis les formations qui commencent bientôt."
        )
    if any(word in text for word in ("quiz", "questionnaire", "quizz")):
        return (
            f"Avant le début d'une formation, chaque candidat qui a payé reçoit un test de niveau : ouvre "
            f"« Level checks », puis clique sur Send. Il y a actuellement {context['prepared_quizzes']} "
            f"questionnaire(s) préparé(s) non envoyé(s). Les questions de la semaine sont dans la page Course de chaque classe."
        )
    if any(word in text for word in ("payment", "paiement", "pay", "خلص")):
        return (
            "Ouvre le profil du candidat, section Payment pending, pour enregistrer un paiement partiel ou "
            "complet. Une inscription partiellement payée reste non assignée à une classe."
        )
    return (
        "Le service IA distant est momentanément indisponible, mais je peux toujours aider avec les FAQ "
        "du système. Demande-moi par exemple: « quand commence la classe ? », « comment envoyer un quiz ? » "
        "ou « quels rappels sont planifiés ? »."
    )
