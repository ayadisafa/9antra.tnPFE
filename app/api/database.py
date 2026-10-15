from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.notification import Notification
from app.models.program import Program
from app.models.quiz import Quiz
from app.models.registration import Registration

router = APIRouter(prefix="/database", tags=["Database"])

from app.template_context import notification_context

templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


# Dependency for database session management
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/")
def database(request: Request, db: Session = Depends(get_db)):
    candidates = db.query(Candidate).all()
    programs = db.query(Program).all()
    registrations = db.query(Registration).all()
    activities = db.query(Activity).all()
    notifications = db.query(Notification).all()
    quizzes = db.query(Quiz).all()

    return templates.TemplateResponse(
        name="database.html",
        request=request,
        context={
            "candidates": candidates,
            "programs": programs,
            "registrations": registrations,  # Updated key
            "courses": registrations,        # Kept as alias for template backward compatibility
            "activities": activities,
            "notifications": notifications,
            "quizzes": quizzes,
        },
    )