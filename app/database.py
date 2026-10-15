import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


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
        },

        "email_threads": {
            "last_status": "VARCHAR",
        },

        "candidate_emails": {
            "author": "VARCHAR",
            "html_body": "TEXT",
            "in_reply_to": "VARCHAR",
            "confidence": "FLOAT",
            "sources": "TEXT",
            "question": "TEXT",
            "suggested_reply": "TEXT",
            "escalation_reason": "TEXT",
            "attachments": "TEXT",
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