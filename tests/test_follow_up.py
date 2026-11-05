"""The follow-up batch of 3 November: what did not reach the candidates (one list, tried again once), the presence
filled in from the candidates' own links and confirmed in one tap, one weekly letter and a limit on automatic messages,
the reminders that catch up, and notifications that close themselves. Runs on a COPY; no mail leaves this machine."""
import json
from datetime import datetime, timedelta, time as dtime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("follow_up")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.email_thread import EmailThread
from app.models.candidate_reminder import CandidateReminder
from app.models.session_join import SessionJoin
from app.models.team_message import TeamMessage
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import (advisor, auth, email_templates as tp, email_threads as et, enrollment_service as es, letters, meetings,
                          notification_rules as rules, reach, whats_new)
from app.services.email_service import EmailDeliveryError
from app.services.schedule_utils import local_now

sent = []
failing = {"on": False}


def fake_send(**kw):
    if failing["on"]:
        raise EmailDeliveryError("The mailbox of the recipient is full.")
    sent.append(kw)
    return True


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
from app.services import class_emails  # noqa: E402
from app.services.settings_service import get_settings  # noqa: E402

class_emails.smtp_configured = lambda: True

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="follow-up-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "follow-up-1"})


def where(response):
    return unquote_plus(response.headers.get("location", ""))


program = db.query(Program).first()
teacher = Professor(name="Hela Formatrice", specialties=program.name, email="hela.follow@example.com", phone="50 222 333")
db.add(teacher)
db.flush()
now = local_now()
group = ClassGroup(name="FOLLOW-UP-1", program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.IN_PROGRESS,
                   start_date=(now - timedelta(days=14)).replace(hour=18, minute=0, second=0, microsecond=0), total_weeks=8,
                   start_time=dtime(18, 0), end_time=dtime(21, 0), days_of_week="mon", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group)
db.flush()


def member(name, email, class_group=group, paid=True):
    person = Candidate(full_name=name, email=email, phone="50 111 222")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390,
                                total_price=390, paid_amount=390 if paid else 0,
                                payment_status=PaymentStatus.PAID if paid else PaymentStatus.UNPAID,
                                status=RegistrationStatus.ASSIGNED if class_group else RegistrationStatus.PENDING,
                                class_group_id=class_group.id if class_group else None)
    db.add(registration)
    db.commit()
    return person, registration



amel, amel_reg = member("Amel Ben Salem", "amel.follow@example.com")
bilel, bilel_reg = member("Bilel Jaziri", "bilel.follow@example.com")
chiraz, chiraz_reg = member("Chiraz Ayari", "chiraz.follow@example.com")

# ======================================================================
# A. What did not reach them: one list, tried again once
# ======================================================================
failing["on"] = True
failed = et.deliver(db, amel, "Inscription confirmée — DevOps", "Bonjour Amel", "<p>Bonjour Amel</p>", kind="enrollment", registration=amel_reg)
db.commit()
check("a failed email is kept with why", failed.status == "failed" and "mailbox" in (failed.analysis or ""))
mine = [item for item in reach.not_reached(db) if item["candidate_id"] == amel.id]
check("it is on the list, in plain words, still worth sending", len(mine) == 1 and "mailbox" in mine[0]["why"] and not mine[0]["stale"]
      and mine[0]["kind"] == "Registration confirmed" and mine[0]["call"] == "tel:+21650111222", mine)
page = admin.get("/inbox/not-reached")
check("the page lists it with what to do", page.status_code == 200 and "Amel Ben Salem" in page.text and "Send again" in page.text
      and "Not needed any more" in page.text and "Call" in page.text)
check("the inbox shows the list beside the candidates and the team", "Didn't reach them" in admin.get("/inbox").text)
check("what needs you today says it", any(item.key.startswith("reach:all") for item in advisor.for_dashboard(db)))

# Tried again once, by itself, after ten minutes.
failed.received_at = datetime.utcnow() - timedelta(minutes=20)
db.commit()
failing["on"] = True
check("tried again once, and not a second time", reach.retry_due(db) == 0 and reach.retry_due(db) == 0
      and reach.state_of(db.get(CandidateEmail, failed.id)).get("retried"))
failing["on"] = False
second = et.deliver(db, bilel, "Votre classe a changé", "Bonjour Bilel", "<p>Bonjour</p>", kind="class_moved", registration=bilel_reg)
second.status, second.analysis, second.received_at = "failed", "Not delivered: The email could not be delivered.", datetime.utcnow() - timedelta(minutes=30)
db.commit()
before = len(sent)
check("a failed email that can still help arrives on the second try", reach.retry_due(db) == 1 and db.get(CandidateEmail, second.id).status == "sent"
      and len(sent) == before + 1)
old = et.deliver(db, chiraz, "J-1 · C'est demain !", "Bonjour", "<p>Bonjour</p>", kind="training_reminder", registration=chiraz_reg)
old.status, old.analysis, old.intent = "failed", "Not delivered: The email could not be delivered.", "training_reminder"
old.received_at = datetime.utcnow() - timedelta(days=2)
db.commit()
item = next(row for row in reach.not_reached(db) if row["id"] == old.id)
check("a reminder whose moment passed is said too late, not sent again", item["stale"] and reach.retry_due(db) == 0
      and "Probably too late now" in admin.get("/inbox/not-reached").text)

# A WhatsApp message waiting for the phone.
waiting = CandidateEmail(candidate_id=chiraz.id, registration_id=chiraz_reg.id, direction="outbound", sender="9antra", recipient="+21650111222",
                         subject="Questions",
                         body="Vos questions de la semaine", intent="weekly_questions", status="to_send", author="system", channel="whatsapp",
                         received_at=datetime.utcnow() - timedelta(hours=3))
db.add(waiting)
db.commit()
row = next(row for row in reach.not_reached(db) if row["id"] == waiting.id)
check("a WhatsApp message waiting for the phone is there, with WhatsApp to open", row["whatsapp"] and "wa.me" in row["whatsapp"])
answer = admin.post(f"/inbox/not-reached/{waiting.id}/sent")
db.expire_all()
check("“Sent” takes it off", answer.status_code == 303 and db.get(CandidateEmail, waiting.id).status == "sent"
      and not any(row["id"] == waiting.id for row in reach.not_reached(db)))
answer = admin.post(f"/inbox/not-reached/{old.id}/dismiss")
db.expire_all()
check("“Not needed any more” takes it off the list, the message stays", answer.status_code == 303 and db.get(CandidateEmail, old.id).status == "failed"
      and not any(row["id"] == old.id for row in reach.not_reached(db)))
stale_one = et.deliver(db, bilel, "J-1 · C'est demain !", "Bonjour", "<p>Bonjour</p>", kind="training_reminder", registration=bilel_reg)
stale_one.status, stale_one.analysis, stale_one.received_at = "failed", "Not delivered: refused.", datetime.utcnow() - timedelta(days=3)
db.commit()
check("the page offers to take off at once what is too late", "Take off what is too late" in admin.get("/inbox/not-reached").text)
answer = admin.post("/inbox/not-reached/dismiss-stale")
db.expire_all()
check("…and does it, keeping what can still help", answer.status_code == 303 and not any(row["stale"] for row in reach.not_reached(db))
      and any(row["id"] == failed.id for row in reach.not_reached(db)))
answer = admin.post(f"/inbox/not-reached/{failed.id}/retry")
db.expire_all()
check("“Send again” by hand", "Delivered" in where(answer) and db.get(CandidateEmail, failed.id).status == "sent")

# ======================================================================
# B. Notifications that close themselves; only what needs you rings
# ======================================================================
newcomer, newcomer_reg = member("Dorra Mansour", "dorra.follow@example.com", class_group=None, paid=False)
for _ in range(5):
    db.add(Notification(recipient_role="admin", category="NEW_REGISTRATION", title="New candidate registration: Dorra Mansour",
                        message="Dorra registered", action_url=f"/candidate/{newcomer.id}", candidate_id=newcomer.id,
                        registration_id=newcomer_reg.id))
started = Notification(recipient_role="admin", category="CLASS", title="Class started", message="FOLLOW-UP-1 started",
                       action_url=f"/classes/{group.id}", created_at=datetime.utcnow() - timedelta(days=5))
told = Notification(recipient_role="admin", category="REMINDER", title="Session reminders sent", message="3 reminded",
                    action_url=f"/classes/{group.id}")
db.add_all([started, told])
db.commit()
rules.tidy(db, force=True)
db.expire_all()
same = db.query(Notification).filter(Notification.registration_id == newcomer_reg.id, Notification.is_read.is_(False)).count()
check("the same notification five times is kept once", same == 1, same)
check("what only told something closes by itself after three days", db.get(Notification, started.id).is_read
      and db.get(Notification, started.id).status == "told")
check("a recent one that only tells stays, without ringing", not db.get(Notification, told.id).is_read and not rules.needs_you(db, db.get(Notification, told.id)))
ringing = rules.ringing(db)
page = admin.get("/inbox/team")
check("the bell counts only what needs you", f">\n                            {ringing}\n" in page.text or f">{ringing}<" in page.text.replace(" ", "").replace("\n", ""))
check("the bell's list says what needs you and what only tells", "Needs you · closes when it is done" in page.text
      and "For your information · closes by itself" in page.text)
check("the live count is the same", admin.get("/admin/api/notifications?unread_only=true").json()["unread_count"] == rules.ringing(db))
newcomer_reg.reminder_count = 1
db.commit()
rules.tidy(db, force=True)
db.expire_all()
check("its payment reminder went out: the new registration closes by itself (the dashboard follows it)", db.query(Notification).filter(Notification.registration_id == newcomer_reg.id,
                                                                                    Notification.is_read.is_(False)).count() == 0)
thread = EmailThread(candidate_id=amel.id, subject="Question", needs_admin=True, status="open", channel="email")
db.add(thread)
db.flush()
asked = Notification(recipient_role="admin", category="EMAIL_QUESTION", title="Your answer is needed", message="Amel asks",
                     action_url=f"/inbox?thread={thread.id}", candidate_id=amel.id)
db.add(asked)
db.commit()
rules.tidy(db, force=True)
check("an email to answer rings until it is answered", rules.needs_you(db, asked) and not db.get(Notification, asked.id).is_read)
thread.needs_admin = False
db.commit()
rules.tidy(db, force=True)
db.expire_all()
check("answered anywhere: it closes by itself", db.get(Notification, asked.id).is_read and db.get(Notification, asked.id).status == "settled")
proof_note = Notification(recipient_role="admin", category="PAYMENT_PROOF", title="Payment proof from Amel", message="390 DT",
                          action_url="/payments?proof=no-such-proof#proofs", candidate_id=amel.id)
db.add(proof_note)
db.commit()
rules.seen_at(db, "/payments", {"proof": "other"})
check("another page does not close it", not db.get(Notification, proof_note.id).is_read)
admin.get("/payments?proof=no-such-proof")
db.expire_all()
check("opening the page it leads to closes it", db.get(Notification, proof_note.id).is_read)
check("an email that failed closes once nothing is left for that candidate", rules.settled(db, Notification(
    recipient_role="admin", category="EMAIL_FAILED", title="Email not delivered", candidate_id=bilel.id)))

# ======================================================================
# C. Reminders that catch up
# ======================================================================
late = ClassGroup(name="FOLLOW-UP-LATE", program_id=program.id, status=ClassStatus.NOT_STARTED, total_weeks=8, start_mode="fixed_date",
                  start_date=(now + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0), start_time=dtime(18, 0),
                  end_time=dtime(21, 0), days_of_week="sat", reminder_days="7,3,1", capacity=12, max_capacity=12)
db.add(late)
db.flush()
eya, eya_reg = member("Eya Hamdi", "eya.follow@example.com", class_group=late)
get_settings(db).enable_automated_reminders = True
tp.set_active(db, "training_reminder", True)
es.plan_reminders(db, eya_reg, late, now=now)
db.commit()
rows = {row.reminder_key: row for row in db.query(CandidateReminder).filter(CandidateReminder.registration_id == eya_reg.id).all()}
check("a class formed after its J-7: that reminder goes now, once", rows["training_7d"].status == "planned" and rows["training_7d"].planned_for <= now
      and rows["training_7d"].strategy.startswith("Catch-up") and rows["training_3d"].status == "planned", {k: (v.status, v.planned_for) for k, v in rows.items()})
before = len(sent)
es.dispatch_due_reminders(db, now=now)
to_eya = [item for item in sent[before:] if item["to_email"] == eya.email]
check("it goes out, saying how many days are really left", len(to_eya) == 1 and to_eya[0]["subject"].startswith("J-5"),
      [item["subject"] for item in to_eya])
es.plan_reminders(db, eya_reg, late, now=now)
db.commit()
check("never a second catch-up", db.query(CandidateReminder).filter(CandidateReminder.registration_id == eya_reg.id,
                                                                      CandidateReminder.status == "planned",
                                                                      CandidateReminder.planned_for <= now).count() == 0)
soon = ClassGroup(name="FOLLOW-UP-SOON", program_id=program.id, status=ClassStatus.NOT_STARTED, total_weeks=8, start_mode="fixed_date",
                  start_date=(now + timedelta(days=3, hours=12)).replace(minute=0, second=0, microsecond=0), start_time=None,
                  days_of_week="sat", reminder_days="7,3,1", capacity=12, max_capacity=12)
db.add(soon)
db.flush()
farah, farah_reg = member("Farah Zouari", "farah.follow@example.com", class_group=soon)
es.plan_reminders(db, farah_reg, soon, now=now)
db.commit()
rows = {row.reminder_key: row for row in db.query(CandidateReminder).filter(CandidateReminder.registration_id == farah_reg.id).all()}
check("no catch-up when the next reminder comes within a day", rows["training_7d"].status == "skipped" and rows["training_3d"].status == "planned"
      and rows["training_3d"].planned_for <= now + timedelta(hours=24), {k: (v.status, v.planned_for) for k, v in rows.items()})

# ======================================================================
# D. Presence filled in from their own links, confirmed in one tap
# ======================================================================
yesterday = ClassSession(class_group_id=group.id, week_number=2, session_date=(now - timedelta(days=1)).date(), start_time=dtime(18, 0),
                         end_time=dtime(21, 0), topic="Docker", meeting_url="https://meet.jit.si/follow-up-room")
db.add(yesterday)
db.flush()
starts = datetime.combine(yesterday.session_date, dtime(18, 0))
for registration, came, minutes in ((amel_reg, True, 2), (bilel_reg, True, 25), (chiraz_reg, False, None)):
    db.add(SessionJoin(session_id=yesterday.id, registration_id=registration.id, candidate_id=registration.candidate_id, token=f"tok-{registration.id}",
                       sent_at=datetime.utcnow() - timedelta(days=1), first_joined_at=starts + timedelta(minutes=minutes) if came else None,
                       joins=1 if came else 0, minutes_late=minutes))
db.commit()
check("before anyone confirms, the dashboard asks for the sheet", any(item.key.startswith("sheets:") for item in advisor.for_dashboard(db)))
done = meetings.after_sessions(db, "https://9antra.example", now=now)
marks = {row.registration_id: row for row in db.query(Attendance).filter(Attendance.session_id == yesterday.id).all()}
check("after the session, who never came in through their link is absent", done["absent"] == 1 and marks[chiraz_reg.id].status == "absent"
      and marks[chiraz_reg.id].marked_by == "their link", done)
asked_prof = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{teacher.id}", TeamMessage.direction == "out").all()
check("its professor is asked once to confirm, with the sheet's link", done["asked"] == 1 and asked_prof
      and f"/classes/{group.id}/attendance?session={yesterday.id}" in asked_prof[0].body and "2 entré(s), dont 1 en retard" in asked_prof[0].body,
      [row.body for row in asked_prof])
check("never asked twice", meetings.after_sessions(db, "https://9antra.example", now=now)["asked"] == 0)
page = admin.get(f"/classes/{group.id}/attendance?session={yesterday.id}").text
check("the sheet is filled in from their links", "Filled in from their own links" in page and "Confirm as shown" in page)
check("came on time: present; late: late; never came: absent",
      f'name="status_{amel_reg.id}" value="present" checked' in page and f'name="status_{bilel_reg.id}" value="late" checked' in page
      and f'name="status_{chiraz_reg.id}" value="absent" checked' in page)
answer = admin.post(f"/classes/{group.id}/attendance/{yesterday.id}", data={
    f"status_{amel_reg.id}": "present", f"status_{bilel_reg.id}": "late", f"status_{chiraz_reg.id}": "absent",
    f"participation_{amel_reg.id}": "3", f"participation_{bilel_reg.id}": "1"})
db.expire_all()
marks = {row.registration_id: row for row in db.query(Attendance).filter(Attendance.session_id == yesterday.id).all()}
check("one tap confirms it, with who took part", answer.status_code == 303 and marks[amel_reg.id].participation == 3
      and meetings.confirmed(marks) and "Confirmed." in admin.get(f"/classes/{group.id}/attendance?session={yesterday.id}").text)
check("confirmed: the dashboard no longer asks for it", not any(item.key.startswith("sheets:") and yesterday.id in item.key for item in advisor.for_dashboard(db)))

# ======================================================================
# E. One weekly letter, and a limit
# ======================================================================
for day in range(letters.WEEKLY_LIMIT):
    db.add(CandidateEmail(candidate_id=amel.id, registration_id=amel_reg.id, direction="outbound", sender="9antra", recipient=amel.email,
                          subject=f"Auto {day}",
                          body="…", intent="session_reminder", status="sent", author="system", channel="email",
                          received_at=datetime.utcnow() - timedelta(days=1, hours=day)))
db.commit()
before = len(sent)
held = tp.send(db, "weekly_questions", registration=amel_reg, candidate=amel, values={
    "week_number": "2", "week_topic": "Docker", "questions_count": "5", "questions_link": "https://9antra.example/learn/abc", "program_name": program.name})
db.commit()
check("past the limit, the week's questions wait for the letter", held is not None and held.status == "in_letter" and len(sent) == before
      and tp.delivered(held))
check("what must arrive on time is never kept", tp.send(db, "payment_reminder", registration=amel_reg, candidate=amel) is None
      or len(sent) > before)
before = len(sent)
by_hand = tp.send(db, "weekly_questions", registration=amel_reg, candidate=amel, manual=True, values={
    "week_number": "2", "week_topic": "Docker", "questions_count": "5", "questions_link": "https://9antra.example/learn/abc", "program_name": program.name})
check("what the admin sends by hand is never kept", by_hand.status == "sent" and len(sent) == before + 1)
under = tp.send(db, "weekly_questions", registration=bilel_reg, candidate=bilel, values={
    "week_number": "2", "week_topic": "Docker", "questions_count": "5", "questions_link": "https://9antra.example/learn/def", "program_name": program.name})
check("under the limit it goes as before", under.status == "sent")
db.add(ClassSession(class_group_id=group.id, week_number=3, session_date=(now + timedelta(days=1)).date(), start_time=dtime(18, 0),
                    end_time=dtime(21, 0), topic="Kubernetes"))
db.commit()
before = len(sent)
letter = letters.send_one(db, amel, "https://9antra.example", now=now)
db.commit()
body = sent[-1]["text_body"] if len(sent) > before else ""
check("the letter: the week's sessions and what waits, in one message", letter is not None and letter.status == "sent"
      and "Kubernetes" in body and "https://9antra.example/learn/abc" in body and "18:00" in body, body[:600])
check("what was kept went with it", db.get(CandidateEmail, held.id).status == "sent")
monday = (now - timedelta(days=now.weekday())).replace(hour=10, minute=0)
first = letters.send_due(db, "https://9antra.example", now=monday)
check("Monday: the others get theirs, once a week", first >= 1 and letters.send_due(db, "https://9antra.example", now=monday) == 0)
check("not on another day", letters.send_due(db, "https://9antra.example", now=monday + timedelta(days=2)) == 0)
tp.set_active(db, "weekly_letter", False)
db.commit()
check("switched off in Email templates: nothing is kept", not letters.should_hold(db, "weekly_questions", amel, False))
tp.set_active(db, "weekly_letter", True)
db.commit()
check("the letter is one of the automatic emails", "The weekly letter" in admin.get("/inbox/templates").text)

# ======================================================================
# F. What's new
# ======================================================================
check("what's new: this batch is told", any(release["key"] == "2026-11-03b" for release in whats_new.RELEASES))

db.close()
check.done()
