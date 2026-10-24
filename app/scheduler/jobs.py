from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.activity import Activity
from app.models.candidate_reminder import CandidateReminder
from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.classgroup import ClassGroup
from app.models.classsession import ClassSession
from app.models.programprogress import ProgramProgress  # Added to prevent SQLAlchemy mapper resolution errors
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.models.quiz import Quiz
from app.services.firebase_service import start_firebase_listener
from app.services.enrollment_service import (
    assign_pending_paid_registrations,
    dispatch_due_reminders,
    reconcile_all,
    sync_class_lifecycle,
)
from app.services import (
    email_assistant,
    email_composer,
    email_templates,
    email_threads,
    feedback,
    knowledge_service,
    level_check,
    mailbox_service,
    meetings,
    payment_reminders,
    program_assistant,
    program_feedback,
    program_icons,
    settings_service,
    system_status,
    website_sync,
    whatsapp,
    brand,
    answer_sync,
    google_forms,
    week_insights,
    weekly_questions,
)
import logging
import os

logger = logging.getLogger(__name__)

# ==========================================
# Service Functions
# ==========================================

def assign_and_update_registration(
    db_session: Session, 
    registration_id: str | int, 
    new_status: str | RegistrationStatus = None,
    class_group_id: str | int = None
) -> Registration:
    """
    Updates registration lifecycle status and assigns it to a ClassGroup
    with dynamic capacity enforcement.
    """
    registration = db_session.get(Registration, registration_id)
    if not registration:
        raise ValueError(f"Registration '{registration_id}' not found.")

    if class_group_id:
        class_group = db_session.get(ClassGroup, class_group_id)
        if not class_group:
            raise ValueError(f"Class Group '{class_group_id}' not found.")

        max_capacity = getattr(class_group, "max_capacity", 12) or 12

        current_count = db_session.query(Registration).filter(
            Registration.class_group_id == class_group_id,
            Registration.status != RegistrationStatus.CANCELLED,
            Registration.id != registration_id
        ).count()

        if current_count >= max_capacity:
            raise ValueError(
                f"Class group '{class_group.name}' is full ({current_count}/{max_capacity} occupied)."
            )

        registration.class_group_id = class_group_id
        registration.status = RegistrationStatus.ASSIGNED

    if new_status:
        if isinstance(new_status, str):
            try:
                new_status = RegistrationStatus(new_status.lower().strip())
            except ValueError:
                allowed = [e.value for e in RegistrationStatus]
                raise ValueError(f"Invalid status '{new_status}'. Allowed values: {', '.join(allowed)}")
        registration.status = new_status

    db_session.commit()
    db_session.refresh(registration)
    return registration


def update_registration_status(
    db_session: Session, 
    registration_id: str | int, 
    new_status: str | RegistrationStatus
) -> Registration:
    """Updates registration training lifecycle status using model Enums."""
    return assign_and_update_registration(db_session, registration_id, new_status=new_status)


def settle_registration_payment(
    db_session: Session, 
    registration_id: str | int, 
    payment_status: str | PaymentStatus, 
    amount_paid: float = None
) -> Registration:
    """Settles payment status, updates paid amounts, and auto-assigns if fully paid."""
    if isinstance(payment_status, str):
        try:
            payment_status = PaymentStatus(payment_status.lower().strip())
        except ValueError:
            allowed = [e.value for e in PaymentStatus]
            raise ValueError(f"Invalid payment status '{payment_status}'. Allowed values: {', '.join(allowed)}")

    registration = db_session.get(Registration, registration_id)
    if not registration:
        raise ValueError(f"Registration '{registration_id}' not found.")

    registration.payment_status = payment_status

    if amount_paid is not None:
        registration.paid_amount = float(amount_paid)

    if registration.payment_status == PaymentStatus.PAID and registration.status == RegistrationStatus.PENDING:
        registration.status = RegistrationStatus.ASSIGNED

    db_session.commit()
    db_session.refresh(registration)
    return registration


def purge_candidate_registrations(db_session: Session, candidate_id: str | int) -> int:
    """Deletes all prior registrations for a candidate to free reserved spots."""
    deleted_count = db_session.query(Registration).filter(
        Registration.candidate_id == candidate_id
    ).delete(synchronize_session='fetch')
    
    db_session.commit()
    return deleted_count


def update_candidate_status(db_session: Session, candidate_id: int, new_status: str) -> Candidate:
    """Updates specific candidate status in the database."""
    candidate = db_session.get(Candidate, candidate_id)
    if not candidate:
        raise ValueError(f"Candidate with ID {candidate_id} not found.")
    
    candidate.status = new_status
    db_session.commit()
    db_session.refresh(candidate)
    return candidate


# ==========================================
# Scheduled Background Jobs
# ==========================================

def training_reminders():
    """
    Sends the reminders due before each class's first session (7, 3 and 1 day
    by default; the days are set per class). Each email states the exact date
    and time of the session.
    """
    db: Session = SessionLocal()
    try:
        dispatch_due_reminders(db)
    except Exception:
        db.rollback()
        logger.exception("Error executing training reminders.")
    finally:
        db.close()


@system_status.watched("classes")
def class_lifecycle_tick():
    """
    Runs every minute: automatic class status changes, classes that reached
    their minimum enrollment, paid registrations still without a class, and
    the reminders that are due.
    """
    db: Session = SessionLocal()
    try:
        sync_class_lifecycle(db)
        assign_pending_paid_registrations(db)
        dispatch_due_reminders(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Class lifecycle tick failed.")
        system_status.problem("classes", exc)
    finally:
        db.close()


def reconcile_classes_on_startup():
    """Bring classes, registrations and reminder plans in line once at startup."""
    db: Session = SessionLocal()
    try:
        reconcile_all(db)
    except Exception:
        db.rollback()
        logger.exception("Startup class reconciliation failed.")
    finally:
        db.close()


@system_status.watched("mailbox")
def mailbox_tick():
    """
    Reads the emails candidates sent since the last check and hands them to
    the email assistant; also resumes emails whose handling was interrupted.
    """
    db: Session = SessionLocal()
    try:
        mailbox_service.poll(db)
        email_assistant.requeue_stuck(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Mailbox tick failed.")
        system_status.problem("mailbox", exc)
    finally:
        db.close()


def prepare_email_assistant():
    """Once at startup: discussions for older emails, starter templates, knowledge index."""
    db: Session = SessionLocal()
    try:
        email_threads.backfill_threads(db)
        email_composer.seed_templates(db)
        knowledge_service.sync_knowledge(db)
        knowledge_service.tidy_taught(db)   # a question taught twice becomes one answer
        # Writing that a stopped server left unfinished starts again.
        program_assistant.release_interrupted(db)
        weekly_questions.release_interrupted(db)
        whatsapp.tidy_stored(db)    # phone numbers written the international way (+216…)
        from app.services import enrollment_service, program_names

        program_names.merge_twins(db)               # an empty copy of a program ("Mobile Flutter") merged into the real one
        program_names.relink(db)                    # "Artificial Intelligence" is the AI program: linked, with its price
        from app.services import translations

        translations.ensure(db)                     # a line for every text of the interface, the hand-made translations in
        enrollment_service.fill_session_times(db)   # a class takes its hours from its program when it has none
    except Exception:
        db.rollback()
        logger.exception("Email assistant preparation failed.")
    try:
        # The quizzes written the earlier way go; every paid candidate whose class
        # has not started gets a level check (prepared only: nothing is sent here).
        level_check.retire_old(db)
        level_check.prepare_missing(db)
    except Exception:
        db.rollback()
        logger.exception("Level checks could not be prepared.")
    finally:
        db.close()


@system_status.watched("writing")
def writing_tick():
    """
    One piece of writing for the local model at a time: the questions of a
    class week first (a professor is waiting for them), else the proposal
    for a program whose details are missing.
    """
    db: Session = SessionLocal()
    try:
        if not weekly_questions.work_one(db):
            if not program_assistant.work_one(db):
                # Nothing to write: read what a candidate wrote about their week,
                # else think of other ways a taught question could be asked.
                if not week_insights.enrich_one(db):
                    email_assistant.guess_wordings_one(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Writing tick failed.")
        system_status.problem("writing", exc)
    finally:
        db.close()


@system_status.watched("website")
def catalogue_tick():
    """
    Programs: ask for a proposal when details are missing (once per program)
    and fetch the picture of programs that have none.
    """
    db: Session = SessionLocal()
    try:
        program_assistant.queue_missing(db)
        program_icons.assign_missing(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Catalogue tick failed.")
        system_status.problem("website", exc)
    try:
        # The public website: read again every few hours (see website_sync).
        if website_sync.check_if_due(db) is not None:
            knowledge_service.sync_knowledge(db)
        brand.refresh_from_facebook()
        # Published feedback: a few pictures kept and read each time, until all are.
        if feedback.tick(db, limit=4):
            knowledge_service.sync_knowledge(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Website tick failed.")
        system_status.problem("website", exc)
    finally:
        db.close()


@system_status.watched("weekly")
def weekly_questions_tick():
    """Classes set to automatic: send the questions of the weeks that just ended."""
    db: Session = SessionLocal()
    try:
        if email_threads.smtp_configured():
            weekly_questions.send_due(db, settings_service.public_base_url(db))
            # Each session that ended some hours ago: its candidates are asked what they learnt in it.
            from app.services import session_check

            session_check.send_due(db, settings_service.public_base_url(db))
            # Classes that just finished: their candidates are asked what they thought.
            program_feedback.send_due(db)
            # Level checks whose day has come (7 days before the class starts, unless the admin changed it).
            level_check.send_due(db)
            # A level check left unanswered for two days: one reminder, never a second.
            level_check.remind_due(db, settings_service.public_base_url(db))
            # A session starts within the hour: each candidate gets their own link to its meeting, once.
            meetings.send_due(db, settings_service.public_base_url(db))
    except Exception as exc:
        db.rollback()
        logger.exception("Weekly questions tick failed.")
        system_status.problem("weekly", exc)
    finally:
        db.close()


@system_status.watched("answers")
def answers_tick():
    """
    The candidates' weekly answers: Google Form → Firebase → here (see
    answer_sync), every two minutes. Every tenth time, Google is asked again
    whether Forms is switched on for the project.
    """
    db: Session = SessionLocal()
    try:
        answers_tick.count = getattr(answers_tick, "count", 0) + 1
        if answers_tick.count % 10 == 1:
            google_forms.refresh_status()
        answer_sync.tick(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Answers tick failed.")
        system_status.problem("answers", exc)
    finally:
        db.close()


@system_status.watched("payments")
def check_payments():
    """
    The first payment reminder: one email per candidate for everything they
    owe, 24 hours after registering. It is never repeated by itself; after
    three days of silence the dashboard suggests sending it again
    (see payment_reminders and advisor).
    """
    db: Session = SessionLocal()
    try:
        if not email_templates.is_on(db, "payment_reminder"):
            return
        if not email_threads.smtp_configured():
            # Nothing is marked as sent, so the reminders go out once email works.
            email_threads.flag_smtp_not_configured(db)
            db.commit()
            return

        sent = payment_reminders.send_first(db)
        if sent:
            print(f"✅ {sent} first payment reminder(s) sent.")
    except Exception as e:
        db.rollback()
        logger.exception("check_payments failed.")
        system_status.problem("payments", e)
        print(f"❌ Erreur dans check_payments: {e}")
    finally:
        db.close()


@system_status.watched("website")
def website_now():
    """The website read again at once, when the admin asks for it on the System page."""
    db: Session = SessionLocal()
    try:
        website_sync.check(db, by="admin")
        knowledge_service.sync_knowledge(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Reading the website on request failed.")
        system_status.problem("website", exc)
    finally:
        db.close()


@system_status.watched("translations")
def translations_tick():
    """A few more texts of the interface translated by the local model (see translations.work)."""
    from app.services import translations

    db: Session = SessionLocal()
    try:
        translations.work(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Translating the interface failed.")
        system_status.problem("translations", exc)
    finally:
        db.close()


@system_status.watched("reminders")
def reminders_tick():
    """The reminders the admin planned, when their time has come (see reminders.send_due)."""
    from app.services import reminders

    db: Session = SessionLocal()
    try:
        reminders.send_due(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Sending the planned reminders failed.")
        system_status.problem("reminders", exc)
    try:
        # Professors reminded to add the resources of a week before it is taught (once per class and week).
        from app.services import resource_reminders

        resource_reminders.send_due(db, base_url=settings_service.public_base_url(db))
    except Exception as exc:
        db.rollback()
        logger.exception("Reminding the professors of their resources failed.")
        system_status.problem("reminders", exc)
    try:
        # A professor given a class is told to prepare it (once per class and professor).
        from app.services import resends

        resends.run_due(db, settings_service.public_base_url(db))      # what the admin reset, at the time they chose
        from app.services import professor_announcements

        professor_announcements.send_due(db, base_url=settings_service.public_base_url(db))
    except Exception as exc:
        db.rollback()
        logger.exception("Telling the professors of their new classes failed.")
        system_status.problem("reminders", exc)
    try:
        # Candidates' questions their professor has not answered: the professor after a day, the admin after two.
        from app.services import course_questions

        course_questions.remind_due(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Reminding the professors failed.")
        system_status.problem("reminders", exc)
    try:
        # The welcome message to those who just registered (see welcome).
        from app.services import welcome

        welcome.send_due(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Sending the welcome messages failed.")
        system_status.problem("reminders", exc)
    try:
        # The progress report by email, on the rhythm the admin chose (see report_email).
        from app.services import report_email

        report_email.send_if_due(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Sending the progress report failed.")
        system_status.problem("reminders", exc)
    finally:
        db.close()


@system_status.watched("backup")
def backup_tick():
    """A copy of the data once a day (see backups.make_if_due)."""
    from app.services import backups

    db: Session = SessionLocal()
    try:
        outcome = backups.make_if_due(db)
        if outcome is not None and outcome["problem"]:
            system_status.problem("backup", outcome["problem"])
    except Exception as exc:
        logger.exception("Copying the data failed.")
        system_status.problem("backup", exc)
    finally:
        db.close()


@system_status.watched("backup")
def backup_now():
    """A copy of the data at once, when the admin asks for it."""
    from app.services import backups

    db: Session = SessionLocal()
    try:
        outcome = backups.make(db)
        if outcome["problem"]:
            system_status.problem("backup", outcome["problem"])
    except Exception as exc:
        logger.exception("Copying the data on request failed.")
        system_status.problem("backup", exc)
    finally:
        db.close()


@system_status.watched("market")
def market_tick():
    """The job market, read again once a day (see market.read_if_due). Only public offers are read."""
    from app.services import market

    db: Session = SessionLocal()
    try:
        outcome = market.read_if_due(db)
        if outcome is not None and outcome["state"] == "unreachable":
            system_status.problem("market", "The job boards could not be reached. It will be tried again in an hour.")
        # The programs' recommendations, from the offers just read and the developers' community: once a day.
        from app.services import recommender

        recommender.refresh_if_due(db)
    except Exception as exc:
        db.rollback()
        logger.exception("Reading the job market failed.")
        system_status.problem("market", exc)
    finally:
        db.close()


@system_status.watched("market")
def market_now():
    """The job market read at once, when the admin asks for it on the System page (six hours apart at least)."""
    from app.services import market

    db: Session = SessionLocal()
    try:
        if market.read_public(db)["state"] == "unreachable":
            system_status.problem("market", "The job boards could not be reached.")
    except Exception as exc:
        db.rollback()
        logger.exception("Reading the job market on request failed.")
        system_status.problem("market", exc)
    finally:
        db.close()


def periodic_candidate_status_update():
    """Periodic job routine to batch update candidate statuses."""
    db: Session = SessionLocal()
    try:
        # Custom logic for periodic candidate updates goes here
        pass
    except Exception as e:
        db.rollback()
        print(f"❌ Error in periodic_candidate_status_update: {e}")
    finally:
        db.close()


def assign_paid_registrations():
    """Enroll fully paid registrations that are still without a class."""
    db: Session = SessionLocal()
    try:
        assign_pending_paid_registrations(db)
    finally:
        db.close()


def sync_firebase_candidates():
    """Starts the Firebase real-time listener process."""
    print("🔥 Starting Firebase synchronization...")
    try:
        start_firebase_listener()
        print("✅ Firebase listener is running.")
    except Exception as e:
        print(f"❌ Firebase listener error: {e}")


# ==========================================
# Scheduler Lifecycle
# ==========================================

scheduler = BackgroundScheduler()

# The System page: the scheduler's name of each piece of work it shows, and what
# "Run now" starts for those that offer it.
WATCHED = {
    "mailbox": "mailbox_tick", "classes": "class_lifecycle_tick", "payments": "check_payments",
    "website": "catalogue_tick", "writing": "writing_tick", "weekly": "weekly_questions_tick",
    "answers": "answers_tick", "market": "market_tick", "backup": "backup_tick",
    "reminders": "reminders_tick", "translations": "translations_tick",
}
RUN_NOW = {"mailbox": mailbox_tick, "classes": class_lifecycle_tick, "website": website_now, "answers": answers_tick,
           "market": market_now, "backup": backup_now, "translations": translations_tick}

def knowledge_tick():
    """What the assistant knows, rebuilt from the records (programs, classes, website, posts, course files): only what
    changed is written again. The assistant keeps up to date by itself."""
    db: Session = SessionLocal()
    try:
        knowledge_service.sync_knowledge(db)
    except Exception:
        db.rollback()
        logger.exception("Knowledge refresh failed.")
    finally:
        db.close()


def start():
    scheduler.add_job(
        knowledge_tick,
        "interval",
        minutes=30,
        next_run_time=datetime.now() + timedelta(minutes=3),
        max_instances=1,
        coalesce=True,
        id="knowledge_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        check_payments,
        "interval",
        hours=1,
        id="check_payments",
        replace_existing=True,
    )

    scheduler.add_job(
        periodic_candidate_status_update,
        "interval",
        hours=1
    )

    # Once, shortly after startup, then every minute.
    scheduler.add_job(
        reconcile_classes_on_startup,
        "date",
        run_date=datetime.now() + timedelta(seconds=5),
    )
    scheduler.add_job(
        class_lifecycle_tick,
        "interval",
        minutes=1,
        next_run_time=datetime.now() + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
        id="class_lifecycle_tick",
        replace_existing=True,
    )

    scheduler.add_job(
        prepare_email_assistant,
        "date",
        run_date=datetime.now() + timedelta(seconds=8),
    )
    scheduler.add_job(
        mailbox_tick,
        "interval",
        seconds=max(30, int(os.getenv("IMAP_POLL_SECONDS", "60"))),
        next_run_time=datetime.now() + timedelta(seconds=45),
        max_instances=1,
        coalesce=True,
        id="mailbox_tick",
        replace_existing=True,
    )

    # Programs: proposals for missing details and pictures; classes: weekly questions.
    scheduler.add_job(
        catalogue_tick,
        "interval",
        minutes=3,
        next_run_time=datetime.now() + timedelta(seconds=40),
        max_instances=1,
        coalesce=True,
        id="catalogue_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        writing_tick,
        "interval",
        seconds=45,
        next_run_time=datetime.now() + timedelta(seconds=90),
        max_instances=1,
        coalesce=True,
        id="writing_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        answers_tick,
        "interval",
        minutes=2,
        next_run_time=datetime.now() + timedelta(seconds=75),
        max_instances=1,
        coalesce=True,
        id="answers_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        weekly_questions_tick,
        "interval",
        minutes=10,
        next_run_time=datetime.now() + timedelta(minutes=3),
        max_instances=1,
        coalesce=True,
        id="weekly_questions_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        market_tick,
        "interval",
        hours=1,
        next_run_time=datetime.now() + timedelta(minutes=5),
        max_instances=1,
        coalesce=True,
        id="market_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        backup_tick,
        "interval",
        hours=1,
        next_run_time=datetime.now() + timedelta(minutes=2),
        max_instances=1,
        coalesce=True,
        id="backup_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        reminders_tick,
        "interval",
        minutes=1,
        next_run_time=datetime.now() + timedelta(seconds=55),
        max_instances=1,
        coalesce=True,
        id="reminders_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        translations_tick,
        "interval",
        minutes=1,
        next_run_time=datetime.now() + timedelta(seconds=100),
        max_instances=1,
        coalesce=True,
        id="translations_tick",
        replace_existing=True,
    )

    scheduler.start()
    print("✅ Scheduler started")