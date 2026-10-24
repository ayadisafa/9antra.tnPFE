"""“database is locked”: a Firebase record synchronised again without change writes nothing (its times were rewritten
every time: a zone and a text made them look new), a real change is still written, a second writer waits for the first
instead of giving up after 5 s, and a write held more than 5 s is written in the log with where it began.
Runs on a COPY; nothing leaves this machine."""
import logging
import threading
import time

import testenv

testenv.prepare("db_locks")
check = testenv.Checks()

from sqlalchemy import event

from app import database
from app.database import SessionLocal, engine
from app.models import Program, Registration
from app.services import firebase_service

statements = []


@event.listens_for(engine, "before_cursor_execute")
def _seen(conn, cursor, statement, parameters, context, executemany):
    statements.append(statement.lstrip()[:40].upper())


db = SessionLocal()
program = db.query(Program).order_by(Program.name).first()
db.close()
record = {
    "email": "locks.test@example.com", "nom": "Lina Verrou", "phone": "50 222 333", "training": program.name,
    "type": "online", "status": "new", "timestamp": "2026-10-13T14:58:15Z",
    "reminder_sent_at": "2026-10-14T13:05:57.146Z", "payment_reminder_sent": True,
}

# ---- A. Synchronised again without change: nothing is written ----
firebase_service.sync_candidate_from_firebase("fb-locks-1", dict(record))
db = SessionLocal()
row = db.query(Registration).filter(Registration.firebase_id == "fb-locks-1").first()
check("sync: the registration is made, with Firebase's own time", row is not None and row.registration_date.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-13 14:58:15",
      row.registration_date if row else None)
db.close()
statements.clear()
firebase_service.sync_candidate_from_firebase("fb-locks-1", dict(record))
firebase_service.sync_candidate_from_firebase("fb-locks-1", dict(record))
writes = [statement for statement in statements if statement.startswith(("UPDATE", "INSERT", "DELETE"))]
check("sync again, twice, nothing new: nothing is written", not writes, writes[:3])

# A registration stored before this change (its times as they were kept then) is not rewritten either.
db = SessionLocal()
row = db.query(Registration).filter(Registration.firebase_id == "fb-locks-1").first()
row.reminder_sent_at = "2026-10-14 13:05:57.146000+00:00"
db.commit()
db.close()
statements.clear()
firebase_service.sync_candidate_from_firebase("fb-locks-1", dict(record))
writes = [statement for statement in statements if statement.startswith(("UPDATE", "INSERT", "DELETE"))]
check("sync: the text the reminder's time was kept as counts as the same time", not writes, writes[:3])

# ---- B. A real change is still written ----
statements.clear()
firebase_service.sync_candidate_from_firebase("fb-locks-1", dict(record, status="paid", timestamp="2026-10-13T15:10:00Z"))
db = SessionLocal()
row = db.query(Registration).filter(Registration.firebase_id == "fb-locks-1").first()
check("sync: a changed status and time are written", row.firebase_status == "paid" and row.registration_date.strftime("%H:%M") == "15:10"
      and any(statement.startswith("UPDATE REGISTRATIONS") for statement in statements), (row.firebase_status, row.registration_date))
db.close()

# ---- C. A second writer waits for the first, and a long write is written in the log ----
caught = []


class Catch(logging.Handler):
    def emit(self, item):
        caught.append(item.getMessage())


logging.getLogger("app.database").addHandler(Catch())
check("settings: a writer waits up to 30 s, a write held 5 s is noted", database.WAIT_FOR_WRITER_SECONDS == 30 and database.LONG_WRITE_SECONDS == 5)
result = {}


def first_writer():
    session = SessionLocal()
    try:
        held = session.query(Registration).filter(Registration.firebase_id == "fb-locks-1").first()
        held.firebase_status = "held"
        session.flush()                          # the write lock is taken here...
        time.sleep(6.5)                          # ...and kept while something slow happens
        session.commit()
        result["first"] = "ok"
    except Exception as exc:
        result["first"] = repr(exc)
    finally:
        session.close()


def second_writer():
    time.sleep(0.7)
    session = SessionLocal()
    started = time.monotonic()
    try:
        other = session.query(Registration).filter(Registration.firebase_id != "fb-locks-1").first()
        other.firebase_status = "second"
        session.commit()
        result["second"] = "ok"
    except Exception as exc:
        result["second"] = repr(exc)
    finally:
        result["waited"] = time.monotonic() - started
        session.close()


threads = [threading.Thread(target=first_writer), threading.Thread(target=second_writer)]
[thread.start() for thread in threads]
[thread.join() for thread in threads]
check("two writers: the second waits for the first instead of “database is locked”", result.get("first") == "ok" and result.get("second") == "ok"
      and result.get("waited", 0) > 5, result)
check("log: the write held 6 s is noted, with how long", any("held for writing" in line and " s " in line for line in caught), caught[-2:])

check.done()
