import logging
import os
import threading
import time
import traceback
import weakref

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)
# The database may be named in .env (DATABASE_URL=postgresql://…): read before the engine is made. What is already set
# (the tests' copies) is kept.
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")
# SQLite lets one writer at a time: another one waits up to this long (it used to give up after 5 s: "database is locked").
WAIT_FOR_WRITER_SECONDS = 30
# A write held longer than this keeps the others waiting: it is written in the log with where it began.
LONG_WRITE_SECONDS = 5

if IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": WAIT_FOR_WRITER_SECONDS})
else:
    # PostgreSQL: many requests at once, each with its own connection from a pool; a connection the server dropped is
    # noticed before it is used.
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800,
                           connect_args={"connect_timeout": 10, "options": f"-c lock_timeout={WAIT_FOR_WRITER_SECONDS * 1000}"})


def _where() -> str:
    """The application's own lines that led to this write (innermost last)."""
    here = os.path.join("app", "")
    frames = [frame for frame in traceback.extract_stack()[:-3]
              if here in frame.filename.replace("/", os.sep) and not frame.filename.endswith("database.py")]
    return " < ".join(f"{os.path.relpath(frame.filename).replace(os.sep, '/')}:{frame.lineno} {frame.name}" for frame in reversed(frames[-4:])) or "?"


# The sessions of this thread that hold changes not saved yet (see let_others_write).
_holding = threading.local()


def _held() -> "weakref.WeakSet":
    found = getattr(_holding, "sessions", None)
    if found is None:
        found = _holding.sessions = weakref.WeakSet()
    return found


@event.listens_for(engine, "before_cursor_execute")
def _note_first_write(conn, cursor, statement, parameters, context, executemany):
    # SQLite takes the write lock at the first change of a transaction and keeps it until the commit.
    if "write_started" not in conn.info and statement.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE", "REPLAC"):
        conn.info["write_started"] = (time.monotonic(), _where())
        owner = conn.info.get("session")
        session = owner() if owner else None
        if session is not None:
            _held().add(session)


def _report(info: dict) -> None:
    owner = info.get("session")
    session = owner() if owner else None
    if session is not None:
        _held().discard(session)
    started = info.pop("write_started", None)
    if started is not None and time.monotonic() - started[0] > LONG_WRITE_SECONDS:
        logger.warning("The database was held for writing %.1f s (other writers waited meanwhile); it began at %s",
                       time.monotonic() - started[0], started[1])


# Every database: a change kept open while waiting holds the others back.
event.listen(engine, "commit", lambda conn: _report(conn.info))
event.listen(engine, "rollback", lambda conn: _report(conn.info))
# A connection given back without a commit or a rollback of its own is rolled back by the pool: it ends there.
event.listen(engine, "checkin", lambda dbapi_connection, record: _report(record.info))

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


@event.listens_for(SessionLocal, "after_begin")
def _session_of(session, transaction, connection):
    connection.info["session"] = weakref.ref(session)


def let_others_write() -> int:
    """About to wait for the network (a mail server, Meta, the Android phone, the model, the mailbox): what this thread
    changed and has not saved yet is saved now, so the database is not held for everyone meanwhile (SQLite lets one
    writer at a time). Returns how many sessions were saved."""
    done = 0
    for session in list(_held()):
        if not session.is_active:            # a session that failed is the caller's to roll back
            continue
        session.commit()
        done += 1
    return done


def ensure_sqlite_compatibility():
    """Add columns introduced after the original SQLite schema was created."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    additions = {
        "registrations": {
            "program_name": "VARCHAR", "delivery_mode": "VARCHAR DEFAULT 'online'",
            "firebase_id": "VARCHAR", "total_amount": "FLOAT DEFAULT 0",
            "total_price": "FLOAT DEFAULT 0", "paid_amount": "FLOAT DEFAULT 0",
            "class_group_id": "VARCHAR",
        },
        "class_groups": {
            "professor_name": "VARCHAR", "professor_id": "VARCHAR", "capacity": "INTEGER DEFAULT 30",
            "max_capacity": "INTEGER DEFAULT 30", "status": "VARCHAR DEFAULT 'NOT_STARTED'",
            "start_date": "DATETIME", "end_date": "DATETIME", "total_weeks": "INTEGER DEFAULT 12",
            "current_week": "INTEGER DEFAULT 0", "days_of_week": "VARCHAR",
            "start_time": "TIME", "end_time": "TIME", "room": "VARCHAR",
            "auto_start_on_full": "BOOLEAN DEFAULT 0",
        },
        "professors": {
            "available_from": "DATE", "available_until": "DATE",
        },
        "notifications": {
            "status": "VARCHAR DEFAULT 'unread'",
            "registration_id": "VARCHAR",
        },
        "quizzes": {
            "registration_id": "VARCHAR",
            "analysis": "TEXT",
            "improvement_areas": "TEXT",
            "assessed_level": "VARCHAR",
            "scheduled_for": "DATETIME",
            "prepared_at": "DATETIME",
            "email_subject": "VARCHAR",
            "email_body": "TEXT",
            "sentiment": "VARCHAR",
            "sentiment_summary": "TEXT",
        },
        "weekly_curriculum": {
            "required_skill_id": "VARCHAR",
        },
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspect(connection).get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'))
        connection.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS "ux_class_groups_name" '
            'ON "class_groups" ("name")'
        ))
        # SQLite cannot ALTER a NOT NULL column. Rebuild this one legacy table
        # so admin-created registrations can legitimately omit firebase_id.
        registration_columns = {column["name"]: column for column in inspect(connection).get_columns("registrations")} if "registrations" in inspector.get_table_names() else {}
        if registration_columns.get("firebase_id", {}).get("nullable") is False:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("""
                CREATE TABLE registrations_compat (
                    id VARCHAR PRIMARY KEY, candidate_id VARCHAR NOT NULL, program_id VARCHAR,
                    program_name VARCHAR, delivery_mode VARCHAR DEFAULT 'online', firebase_id VARCHAR,
                    registration_date DATETIME, training_start_date DATETIME, training_end_date DATETIME,
                    status VARCHAR, payment_status VARCHAR, total_amount FLOAT DEFAULT 0,
                    total_price FLOAT DEFAULT 0, paid_amount FLOAT DEFAULT 0, quiz_score FLOAT DEFAULT 0,
                    certificate_generated BOOLEAN DEFAULT 0, payment_reminder_sent BOOLEAN DEFAULT 0,
                    reminder_sent_at VARCHAR, reminder_count INTEGER DEFAULT 0, class_group_id VARCHAR
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO registrations_compat
                SELECT id,candidate_id,program_id,program_name,delivery_mode,firebase_id,
                registration_date,training_start_date,training_end_date,status,payment_status,
                total_amount,total_price,paid_amount,quiz_score,certificate_generated,
                payment_reminder_sent,reminder_sent_at,reminder_count,class_group_id
                FROM registrations
            """)
            connection.exec_driver_sql("DROP TABLE registrations")
            connection.exec_driver_sql("ALTER TABLE registrations_compat RENAME TO registrations")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_registrations_firebase_id ON registrations(firebase_id)")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def get_db():
    """Dependency that creates a database session for a request

    and automatically closes it when finished.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ensure_sqlite_compatibility():
    """Add columns introduced after the original SQLite schema was created."""
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)

    additions = {
        "candidates": {
            "photo_url": "VARCHAR",
            "instagram_url": "VARCHAR",
            "facebook_url": "VARCHAR",
            "preferred_language": "VARCHAR DEFAULT 'fr'",
            "admin_notes": "TEXT",
        },

        "registrations": {
            "program_name": "VARCHAR",
            "delivery_mode": "VARCHAR DEFAULT 'online'",
            "firebase_id": "VARCHAR",
            "firebase_status": "VARCHAR",
            "firebase_timestamp": "DATETIME",
            "total_amount": "FLOAT DEFAULT 0",
            "total_price": "FLOAT DEFAULT 0",
            "paid_amount": "FLOAT DEFAULT 0",
            "class_group_id": "VARCHAR",
            "payment_reminder_sent": "BOOLEAN DEFAULT 0",
            "reminder_sent_at": "DATETIME",
        },

        "registration_biases": {
            "registration_id": "VARCHAR",
            "candidate_id": "VARCHAR",
        },

        # Classes that existed before start rules keep their current date.
        "class_groups": {
            "start_mode": "VARCHAR DEFAULT 'fixed_date'",
            "start_delay_days": "INTEGER DEFAULT 10",
            "min_students_to_start": "INTEGER DEFAULT 5",
            "min_start_notice_days": "INTEGER DEFAULT 3",
            "reminder_days": "VARCHAR DEFAULT '7,3,1'",
            "auto_created": "BOOLEAN DEFAULT 0",
            "weekly_questions_mode": "VARCHAR DEFAULT 'manual'",
            "weekly_questions_count": "INTEGER DEFAULT 5",
            "symbol": "VARCHAR",
            "color": "VARCHAR",
        },

        # Professors can be reached, and can be given a login.
        "professors": {
            "email": "VARCHAR",
            "phone": "VARCHAR",
        },

        # Each browser signed in is a session: which device, and when it was last used.
        "users": {
            "permissions": "TEXT",
            "phone": "VARCHAR",
        },
        "team_messages": {
            "question_message_id": "VARCHAR",
            "question_candidate_id": "VARCHAR",
            "answered_at": "DATETIME",
            "reminded_at": "DATETIME",
            "admin_told_at": "DATETIME",
        },
        "user_sessions": {
            "device": "VARCHAR",
            "last_seen_at": "DATETIME",
        },

        "candidate_reminders": {
            "attempts": "INTEGER DEFAULT 0",
            "last_error": "TEXT",
        },

        "system_settings": {
            "auto_assign_professor": "BOOLEAN DEFAULT 1",
            "class_start_mode": "VARCHAR DEFAULT 'days_after_creation'",
            "min_students_to_start": "INTEGER DEFAULT 5",
            "min_start_notice_days": "INTEGER DEFAULT 3",
            "default_session_days": "VARCHAR DEFAULT 'sat'",
            "default_session_start_time": "VARCHAR DEFAULT '09:00'",
            "default_session_end_time": "VARCHAR DEFAULT '13:00'",
            "email_auto_reply_mode": "VARCHAR DEFAULT 'auto'",
            "email_confidence_threshold": "FLOAT DEFAULT 0.7",
            "email_signature": "TEXT",
            "email_footer_note": "TEXT",
            "email_accent_color": "VARCHAR DEFAULT '#a42c67'",
            "public_base_url": "VARCHAR",
            "ui_color": "VARCHAR",
            "app_name": "VARCHAR",
            "email_rewrite_taught": "BOOLEAN DEFAULT 1",
            "program_pdf_style": "VARCHAR",
            "market_read_at": "DATETIME",
            "backup_second_dir": "VARCHAR",
            "ui_menu_side": "VARCHAR",
            "ui_mode": "VARCHAR",
            "ui_background": "VARCHAR",
            "report_email_on": "BOOLEAN DEFAULT 1",
            "report_every_days": "INTEGER DEFAULT 7",
            "report_recipients": "TEXT",
            "report_sent_at": "DATETIME",
            "report_since": "DATETIME",
            "whatsapp_auto_reply": "VARCHAR DEFAULT 'auto'",
            "assistant_never": "TEXT",
            "assistant_notes": "TEXT",
            "team_auto_reply": "BOOLEAN DEFAULT 0",
            "level_check_when": "VARCHAR DEFAULT 'before'",
            "level_check_days_before": "INTEGER DEFAULT 7",
            "centre_phone": "VARCHAR",
            "centre_whatsapp": "VARCHAR",
            "send_way": "VARCHAR DEFAULT 'auto'",
            "send_fallback": "BOOLEAN DEFAULT 1",
            "payment_reminder_hours": "INTEGER DEFAULT 24",
            "welcome_since": "DATETIME",
            "centre_ribs": "TEXT",
            "reword_and_send": "BOOLEAN DEFAULT 0",
            "assistant_auto_send": "BOOLEAN DEFAULT 1",
            "resource_reminder_on": "BOOLEAN DEFAULT 1",
            "resource_reminder_days": "INTEGER DEFAULT 2",
            "resource_reminder_text": "TEXT",
            "registration_form_url": "VARCHAR",
            "next_class_gap_days": "INTEGER DEFAULT 10",
            "session_check_on": "BOOLEAN DEFAULT 1",
            "session_check_hours": "INTEGER DEFAULT 24",
            "session_check_since": "DATETIME",
            "prospect_answers_on": "BOOLEAN DEFAULT 1",
            "centre_address": "VARCHAR",
        },

        "email_threads": {
            "last_status": "VARCHAR",
            "channel": "VARCHAR DEFAULT 'email'",
        },
        "admin_reminders": {"channel": "VARCHAR DEFAULT 'email'"},

        "candidate_emails": {
            "emotions": "TEXT",
            "author": "VARCHAR",
            "html_body": "TEXT",
            "in_reply_to": "VARCHAR",
            "confidence": "FLOAT",
            "sources": "TEXT",
            "question": "TEXT",
            "suggested_reply": "TEXT",
            "escalation_reason": "TEXT",
            "attachments": "TEXT",
            "channel": "VARCHAR DEFAULT 'email'",
            "admin_score": "INTEGER",
        },

        # Automatic emails are stored next to the admin's own templates.
        "email_templates": {
            "key": "VARCHAR",
            "closing": "TEXT",
            "image_id": "VARCHAR",
            "image_position": "VARCHAR DEFAULT 'top'",
            "attachment_ids": "TEXT",
            "show_details": "BOOLEAN DEFAULT 1",
            "is_active": "BOOLEAN DEFAULT 1",
            "cta_label": "VARCHAR",
            "cta_url": "VARCHAR",
            "channel": "VARCHAR",
        },

        # The picture shown next to a program: found on the web or uploaded.
        "programs": {
            "icon_file": "VARCHAR",
            "icon_ref": "VARCHAR",
            "icon_checked_at": "DATETIME",
            # What was taken from the public website (see website_sync).
            "website_slug": "VARCHAR",
            "website_values": "TEXT",
            "website_synced_at": "DATETIME",
        },

        # Weekly questions are quizzes of a class week, opened with a private link.
        "class_sessions": {"meeting_url": "VARCHAR"},
        "attendance": {"participation": "INTEGER", "questions": "INTEGER"},
        "quizzes": {
            "kind": "VARCHAR DEFAULT 'level'",
            "class_session_id": "VARCHAR",      # the questions after one session (session_check)
            "class_group_id": "VARCHAR",
            "week_number": "INTEGER",
            "access_token": "VARCHAR",
            "reminded_at": "DATETIME",      # level check: the one reminder to answer it
            # The weekly check-in: what the candidate said about the week, what
            # the system reads in it, and where the answer came from.
            "feedback": "TEXT",
            "insight": "TEXT",
            "answer_source": "VARCHAR",
            "external_id": "VARCHAR",
        },

        # The Google Form of a class week, when answers go through Google and Firebase.
        "class_weeks": {
            "form_id": "VARCHAR",
            "form_url": "VARCHAR",
            "form_map": "TEXT",
            "form_checked_at": "DATETIME",
            "form_error": "TEXT",
        },

        # Other wordings of a taught question, confirmed by the admin.
        "knowledge_entries": {
            "variants": "TEXT",
            "wordings_at": "DATETIME",
        },

        # What the application read in a published feedback (its words, its tone).
        "website_items": {
            "reading": "TEXT",
            "removed_at": "DATETIME",
        },
    }

    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue

            existing_columns = {
                column["name"]
                for column in inspect(connection).get_columns(table)
            }

            for column_name, definition in columns.items():
                if column_name in existing_columns:
                    continue

                connection.execute(
                    text(
                        f'ALTER TABLE "{table}" '
                        f'ADD COLUMN "{column_name}" {definition}'
                    )
                )