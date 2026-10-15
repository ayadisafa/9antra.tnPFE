from contextlib import asynccontextmanager
import os
import signal
import threading

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session

from app.database import Base, engine, get_db, ensure_sqlite_compatibility,SessionLocal
from app.models import Candidate

# Routers
from app.api.dashboard import router as dashboard_router
from app.api.candidate import router as candidate_router
from app.api.payment import router as payment_router
from app.api.database import router as database_router
from app.api.program import router as program_router
from app.api.notification import router as notification_router
from app.api.classgroup import router as classgroup_router
from app.api.quiz import router as quiz_router
from app.api.assistant import router as assistant_router
from app.api.settings import router as settings_router
from app.api.realtime import router as realtime_router
from app.api.inbox import router as inbox_router
from app.api.course import router as course_router
from app.api.auth import router as auth_router
from app.api.teacher import router as teacher_router
from app.api.website import router as website_router
from app.api.opinion import router as opinion_router
from app.api.system import router as system_router
from app.api.meeting import router as meeting_router
from app.api.market import router as market_router
from app.api.program_pdf import router as program_pdf_router
from app.api.reminders import router as reminders_router
from app.api.i18n import router as i18n_router
from app.api.workspace import router as workspace_router
from app.services.auth import AccessGate, SecurityHeaders
from app.services.realtime import broadcaster, install_session_hooks


from app.services.firebase_service import start_firebase_listener
from app.scheduler.jobs import start
from apscheduler.schedulers.background import BackgroundScheduler
import app.models

from app.database import (
    Base,
    engine,
    ensure_sqlite_compatibility,
)





# Create Database Tables
Base.metadata.create_all(bind=engine)
ensure_sqlite_compatibility()

from app.template_context import notification_context

templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[notification_context],
)

scheduler = BackgroundScheduler(
    job_defaults={
        'misfire_grace_time': 3600,  # Allow up to 1 hour grace time before skipping
        'coalesce': True             # Combine missed runs into a single run
    }
)


# Every committed change is pushed to open admin pages (notifications + live refresh).
install_session_hooks(SessionLocal)


_exit_timer = None


def _exit_even_if_a_job_is_running(delay: float = 8.0):
    """Leave no later than `delay` seconds after the server was asked to stop.

    A background job can run for minutes (the local model writing quizzes or
    questions), and Python waits for running jobs before a process ends. A
    reload or Ctrl+C would then leave a server that no longer answers but
    keeps working with the old code. Stopping mid-job is safe for the
    database: what was not saved is simply not kept.
    """
    global _exit_timer
    if _exit_timer is None:
        _exit_timer = threading.Timer(delay, os._exit, args=(0,))
        _exit_timer.daemon = True
        _exit_timer.start()


def _close_event_streams_on_shutdown():
    """End open event streams as soon as the server is asked to stop.

    Browsers keep /events/stream open, and the server waits for open
    connections before exiting, so without this Ctrl+C would hang.
    """
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous = signal.getsignal(signum)

            def handler(received, frame, previous=previous):
                broadcaster.close_all()
                _exit_even_if_a_job_is_running()
                if callable(previous):
                    previous(received, frame)
                else:
                    signal.signal(received, previous or signal.SIG_DFL)
                    signal.raise_signal(received)

            signal.signal(signum, handler)
        except (ValueError, OSError):
            # Not on the main thread; nothing to hook.
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    _close_event_streams_on_shutdown()

    # Start background listener thread
    threading.Thread(
        target=start_firebase_listener,
        daemon=True
    ).start()

    # Start scheduled background tasks
    start()

    yield

    broadcaster.close_all()


app = FastAPI(
    title="Training Management System",
    lifespan=lifespan
)

# Nobody opens a page without signing in, and professors only their own space.
app.add_middleware(AccessGate)
app.add_middleware(SecurityHeaders)

app.mount("/static", StaticFiles(directory="app/static"), name="static")




ensure_sqlite_compatibility()
# Include Application Routers
app.include_router(auth_router)
app.include_router(teacher_router)
app.include_router(realtime_router)
app.include_router(settings_router)
app.include_router(inbox_router)
app.include_router(course_router)
app.include_router(website_router)
app.include_router(opinion_router)
app.include_router(system_router)      # /system, and what a phone needs to keep the application on its home screen
app.include_router(meeting_router)     # a session's meeting: before teacher_router's /classes/{id}/attendance/{session}
app.include_router(market_router)      # /programs/market: before program_router's /programs/{program_id}
app.include_router(program_pdf_router) # /programs/{id}/pdf: a program to download or send
app.include_router(reminders_router)   # /reminders: the admin's own reminders
app.include_router(i18n_router)        # /language, /i18n: the interface in French and Arabic
app.include_router(workspace_router)   # /find, /data, /settings/team: find anything, change any value, the team
app.include_router(quiz_router)
app.include_router(dashboard_router)
app.include_router(candidate_router)
app.include_router(payment_router)
app.include_router(database_router)
app.include_router(program_router)
app.include_router(notification_router)
app.include_router(classgroup_router)
app.include_router(assistant_router)

@app.get("/admin/templates")
def render_admin_templates_page():
    """Email templates now live with the inbox."""
    return RedirectResponse("/inbox/templates", status_code=307)
