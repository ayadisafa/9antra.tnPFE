from typing import Optional
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.template_context import notification_context
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.registration import Registration
from app.services.payment_service import confirm_payment as record_payment

router = APIRouter(prefix="/payment", tags=["Payments"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


# Dependency for database session management
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/confirm/{registration_id}")
def confirm_payment(registration_id: str, amount: Optional[float] = Form(None), method: str = Form("manual"), db: Session = Depends(get_db)):
    registration = db.get(Registration, registration_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found")
    if amount is None:
        amount = registration.remaining_amount
    profile_url = f"/candidate/{registration.candidate_id}"
    try:
        # Records the payment, notifies the admin and, once fully paid,
        # enrolls the candidate in a class.
        record_payment(db, registration_id, amount, method)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(url=f"{profile_url}?{urlencode({'error': str(exc)})}", status_code=303)

    db.refresh(registration)
    if registration.class_group_id:
        notice = f"Payment recorded. Enrolled in {registration.class_group.name}."
    else:
        notice = "Payment recorded."
    return RedirectResponse(url=f"{profile_url}?{urlencode({'notice': notice})}", status_code=303)