"""Professors on one page, absences over a period asked by the professor and decided by the admin when nobody can
replace them (refusal answered from a link), professors' emails laid out like the candidates', monthly prices (a month
counted when it starts, one reminder, the access paused and resumed), online / on site changed from a message,
WhatsApp and SMS statuses, the iPhone's SMS coming in, and Settings › Facebook / LinkedIn / Monthly payments.
Runs on a COPY; email, WhatsApp and SMS are fakes."""
import json
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("absence_month")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.email_thread import EmailThread
from app.models.professor_absence import ProfessorAbsence
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.models.team_message import TeamMessage
from app.services import (absences, auth, delivery, email_templates as tp, email_threads as et, mode_change, monthly,
                          phone_sms, replacements, team_messages)
from app.services.settings_service import get_settings

mails = []


def fake_attempt(recipient, subject, text, html_body, *args, **kwargs):
    mails.append({"to": recipient, "subject": subject, "text": text, "html": html_body})
    return None, False


et._attempt = fake_attempt
et.smtp_configured = lambda: True
tp.send_email = lambda **kw: mails.append({"to": kw.get("to_email") or kw.get("to"), "subject": kw.get("subject"), "text": kw.get("body") or "", "html": kw.get("html") or ""}) or True


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="absence-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "absence-test-1"})

program = Program(name="Absences Test Program", total_duration_days=60, price_online=390.0, price_onsite=490.0, is_approved=True)
db.add(program)
db.flush()
settings = get_settings(db)
settings.absence_decision = "nobody"
settings.replacement_fallback = "ask"
db.commit()
today = date.today()


def professor(name, teaches=True):
    row = Professor(name=name, specialties=program.name if teaches else "Cooking", email=f"{name.split()[0].lower()}@example.com", is_available=True)
    db.add(row)
    db.flush()
    return row


# These classes are made without candidates (what is looked at is their professor's sessions): the empty-class rule has
# its own suite (test_empty_classes), so it does not end them here.
from app.services import empty_classes

empty_classes.sweep = lambda *args, **kwargs: {"removed": [], "ended": []}


def group(name, who, first_day, hours=(9, 12), mode=None):
    row = ClassGroup(name=name, program_id=program.id, professor_id=who.id if who else None, professor_name=who.name if who else None,
                     status=ClassStatus.ONGOING, start_date=datetime.combine(first_day, dtime(hours[0], 0)), total_weeks=8,
                     start_time=dtime(hours[0], 0), end_time=dtime(hours[1], 0), days_of_week="sat", delivery_mode=mode)
    db.add(row)
    db.flush()
    return row


def session(group_row, day, week=1):
    row = ClassSession(class_group_id=group_row.id, week_number=week, session_date=day, start_time=group_row.start_time,
                       end_time=group_row.end_time, topic=f"Week {week}")
    db.add(row)
    db.flush()
    return row


# ---- A. One page for the professors ----
amel = professor("Amel Absente")
basma = professor("Basma Libre")
cyrine = professor("Cyrine Ailleurs", teaches=False)
k1 = group("ABS-K1", amel, today - timedelta(days=10))
s1 = session(k1, today + timedelta(days=4), 2)
s2 = session(k1, today + timedelta(days=6), 3)
db.commit()
page = admin.get("/professors")
check("professors page: opens", page.status_code == 200, page.status_code)
check("professors page: the cards, the absences, who teaches what, the resources", all(part in page.text for part in (
    'id="professor-' + amel.id, 'id="absences"', 'id="coverage"', 'id="resources"', "Note an absence", "Add a professor")), "")
check("professors page: the dialogs to add, give access, delete and note an absence", all(part in page.text for part in (
    'id="professorModal"', 'id="accessModal"', 'id="deleteProfessorModal"', 'id="absenceModal"')))
old = admin.get("/classes/?tab=professors")
check("the old Classes › Professors goes to the professors' page", old.status_code == 303 and old.headers["location"] == "/professors", old.status_code)
classes = admin.get("/classes/")
check("the classes page no longer carries the professors' cards", classes.status_code == 200 and "pf-card" not in classes.text and 'href="/professors"' in classes.text)

# ---- B. An absence noted by the admin over a period: accepted, each session asks its replacement ----
response = admin.post(f"/classes/professors/{amel.id}/absences", data={"absence_date": (today + timedelta(days=3)).isoformat(),
                                                                         "end_date": (today + timedelta(days=7)).isoformat(), "reason": "Congé"})
db.expire_all()
noted = db.query(ProfessorAbsence).filter(ProfessorAbsence.professor_id == amel.id).one()
check("admin's absence: back on the professors' page", response.status_code == 303 and where(response).startswith("/professors"), where(response))
check("admin's absence: a period, accepted", noted.end_date == today + timedelta(days=7) and absences.state(noted) == "accepted" and noted.requested_by == "admin")
rows = db.query(replacements.SessionReplacement).filter(replacements.SessionReplacement.absent_professor_id == amel.id).all()
check("admin's absence: both sessions of the period look for a replacement", {row.session_id for row in rows} == {s1.id, s2.id}, len(rows))
check("admin's absence: the free colleague who teaches it is asked", all(row.status == "asked" and row.asked_professor_id == basma.id for row in rows),
      [(row.status, row.asked_professor_id) for row in rows])
ask = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{basma.id}", TeamMessage.channel == "email").order_by(TeamMessage.created_at.desc()).first()
asked_mail = next((mail for mail in reversed(mails) if mail["to"] == basma.email), None)
check("professor's email: a subject, laid out like the candidates' (header, table, button)", asked_mail is not None
      and asked_mail["subject"] == "Pouvez-vous remplacer un collègue ?" and "<table" in asked_mail["html"] and "/replace/" in asked_mail["html"]
      and "Répondre" in asked_mail["html"], (asked_mail or {}).get("subject"))
check("professor's message as kept (and for WhatsApp, SMS): the link written under it", ask is not None and "Répondre : http" in ask.body and "/replace/" in ask.body)
check("absent_on: the whole period counts", replacements.busy_then is not None and __import__("app.services.enrollment_service", fromlist=["absent_on"]).absent_on(
    db, amel.id, today + timedelta(days=5)) and not __import__("app.services.enrollment_service", fromlist=["absent_on"]).absent_on(db, amel.id, today + timedelta(days=9)))
absences.cancel(db, noted, by="test")
db.expire_all()
check("removed: the replacements still asked stop", all(row.status == "cancelled" for row in db.query(replacements.SessionReplacement)
                                                         .filter(replacements.SessionReplacement.absent_professor_id == amel.id).all()))

# ---- C. The professor asks; nobody can replace them: the admin decides ----
auth.create_user(db, email="amel@example.com", name=amel.name, password="amel-test-12", role=auth.PROFESSOR, professor_id=amel.id)
teacher = TestClient(main.app, follow_redirects=False)
teacher.post("/login", data={"email": "amel@example.com", "password": "amel-test-12"})
basma.is_available = False                      # nobody else teaches it now
db.commit()
first, last = (today + timedelta(days=3)).isoformat(), (today + timedelta(days=7)).isoformat()
preview = teacher.get(f"/teacher/absence/check?first={first}&last={last}").json()
check("before asking: the preview says nobody is free, session by session", preview.get("waits") is True and len(preview.get("sessions") or []) == 2
      and all(item["nobody"] for item in preview["sessions"]), preview)
response = teacher.post("/teacher/absence", data={"absence_date": first, "end_date": last, "reason": "Mariage"})
db.expire_all()
asked = db.query(ProfessorAbsence).filter(ProfessorAbsence.professor_id == amel.id, ProfessorAbsence.status == "requested").one_or_none()
check("asked: it waits for the admin", asked is not None and asked.requested_by == "professor" and "admin" in where(response).lower(), where(response))
note = db.query(Notification).filter(Notification.category == "ABSENCE_REQUEST", Notification.action_url.contains(asked.id if asked else "-")).first()
check("asked: the admin has one notification that waits", note is not None and not note.is_read)
page = admin.get(f"/professors?absence={asked.id}")
check("professors page: the request with its sessions and nobody free", "To decide" in page.text and "Nobody free" in page.text and "Mariage" in page.text)
space = teacher.get("/teacher/space")
check("their space: the request, waiting for the admin, can be taken back", space.status_code == 200 and "Waiting for the admin" in space.text
      and f"/teacher/absence/{asked.id}/cancel" in space.text)
mails.clear()
response = admin.post(f"/professors/absences/{asked.id}/refuse", data={"note": "Les examens tombent cette semaine-là."})
db.expire_all()
asked = db.get(ProfessorAbsence, asked.id)
check("refused: kept with the word, a private link to answer", asked.status == "refused" and asked.token and asked.decision_note.startswith("Les examens"))
refusal = next((mail for mail in mails if mail["to"] == amel.email), None)
check("refused: the professor's email is laid out, says why, has the button to answer", refusal is not None and refusal["subject"] == "Votre demande d'absence"
      and "Les examens" in refusal["html"] and f"/absence/{asked.token}" in refusal["html"] and "<table" in refusal["html"])
check("refused: the notification is settled", db.query(Notification).filter(Notification.id == note.id).one().is_read)
public = TestClient(main.app, follow_redirects=False)
answer = public.get(f"/absence/{asked.token}")
check("answer page: opens without signing in, says the period and the word", answer.status_code == 200 and "Je serai là" in answer.text
      and "Les examens" in answer.text)
empty = public.post(f"/absence/{asked.token}", data={"answer": "cannot", "words": ""})
check("answer page: “I still cannot come” needs a reason", empty.status_code == 400 and "pourquoi" in empty.text)
answer = public.post(f"/absence/{asked.token}", data={"answer": "cannot", "words": "Je suis témoin, je dois partir."})
db.expire_all()
asked = db.get(ProfessorAbsence, asked.id)
check("they still cannot come: back before the admin", asked.status == "insists" and "témoin" in asked.response and asked.token is None)
again = db.query(Notification).filter(Notification.category == "ABSENCE_REQUEST", Notification.action_url.contains(asked.id)).first()
check("they still cannot come: the notification rings again", again is not None and not again.is_read)
check("the link works once", public.get(f"/absence/{asked.token or 'x' * 20}").status_code == 404)
response = admin.post(f"/professors/absences/{asked.id}/accept", data={"note": "Bon mariage !"})
db.expire_all()
asked = db.get(ProfessorAbsence, asked.id)
rows = db.query(replacements.SessionReplacement).filter(replacements.SessionReplacement.absent_professor_id == amel.id,
                                                        replacements.SessionReplacement.status.in_(("none", "asked"))).all()
check("accepted: the sessions with nobody free wait for the admin's choice", asked.status == "accepted" and len(rows) == 2
      and all(row.status == "none" for row in rows), [(row.status) for row in rows])
check("accepted: the professor is told", any(mail["subject"] == "Votre absence est acceptée" for mail in mails if mail["to"] == amel.email))

# ---- D. Someone is free for each session: accepted by itself ----
basma.is_available = True
dora = professor("Dora Seconde")
k2 = group("ABS-K2", dora, today - timedelta(days=10), hours=(14, 17))
session(k2, today + timedelta(days=20), 4)
db.commit()
done_absence, done = absences.request(db, dora, today + timedelta(days=19), today + timedelta(days=21), None, None, "", "http://test")
check("someone free: accepted by itself and the colleague asked", done["outcome"] == "accepted" and absences.state(done_absence) == "accepted"
      and done["opened"] and done["opened"][0].status == "asked")
settings.absence_decision = "always"
db.commit()
waits_absence, waits = absences.request(db, dora, today + timedelta(days=30), today + timedelta(days=30), None, None, "", "http://test")
check("setting “I decide every request”: it waits even with someone free", waits["outcome"] == "waiting" and waits_absence.status == "requested")
settings.absence_decision = "nobody"
db.commit()
try:
    absences.read_period((today - timedelta(days=1)).isoformat(), "")
    past = False
except ValueError:
    past = True
check("a day already gone cannot be asked", past)

# ---- E. Monthly prices ----
nada = Candidate(full_name="Nada Mensuelle", email="nada.mensuelle@example.com", phone="50 222 333")
db.add(nada)
db.flush()
k3 = group("ABS-K3", basma, today - timedelta(days=35), mode=None)
registration = Registration(candidate_id=nada.id, program_id=program.id, program_name=program.name, delivery_mode="online",
                            total_amount=390.0, total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID,
                            status=RegistrationStatus.ASSIGNED, class_group_id=k3.id)
db.add(registration)
settings.monthly_since = datetime.utcnow() - timedelta(days=60)
settings.month_grace_days, settings.month_remind_days, settings.month_pause_on = 3, 5, True
db.commit()
check("months: 60 days = 2 months, 180 = 6", monthly.program_months(program) == 2 and monthly.program_months(type("P", (), {"total_duration_days": 180})()) == 6)
check("months: the whole program costs two months", monthly.program_total(registration) == 780.0, monthly.program_total(registration))
mails.clear()
done = monthly.tick(db, today=today)
db.expire_all()
registration = db.get(Registration, registration.id)
check("month 2 started 5 days ago: counted, owed, paused (3 days of grace)", registration.months_counted == 2 and registration.total_amount == 780.0
      and registration.payment_status == PaymentStatus.PARTIAL and registration.paused_at is not None, (registration.months_counted, registration.total_amount, registration.payment_status))
check("paused: they are told once, the admin has one notification", any("pause" in (mail["subject"] or "").lower() for mail in mails)
      and db.query(Notification).filter(Notification.category == "MONTH_UNPAID", Notification.registration_id == registration.id).count() == 1)
monthly.tick(db, today=today)
check("paused: never twice", db.query(Notification).filter(Notification.category == "MONTH_UNPAID", Notification.registration_id == registration.id).count() == 1)
from app.services import meetings

join_session = session(k3, today, 6)
db.commit()
join = meetings.links_for(db, k3, join_session).get(registration.id)
door = public.get(f"/join/{join.token}") if join is not None else None
check("paused: their session link says the access waits for the payment", door is not None and "Votre accès est en pause" in door.text, getattr(door, "status_code", None))
from app.services import payment_service

mails.clear()
payment_service.confirm_payment(db, registration.id, 390.0, method="cash")
db.expire_all()
registration = db.get(Registration, registration.id)
check("paid: they continue (not paused, paid)", registration.paused_at is None and registration.payment_status == PaymentStatus.PAID)
check("paid: “month received” goes, not “class to come”", any("mois 2" in (mail["subject"] or "") for mail in mails)
      and not any("Confirmation de paiement" in (mail["subject"] or "") for mail in mails), [mail["subject"] for mail in mails])
try:
    payment_service.confirm_payment(db, registration.id, 500.0)
    too_much = False
except ValueError:
    too_much = True
check("a payment above the whole program is refused", too_much)
# A month that started before monthly payments were switched on: never counted by itself.
settings.monthly_since = datetime.utcnow()
early = Registration(candidate_id=nada.id, program_id=program.id, program_name=program.name, delivery_mode="onsite", total_amount=490.0,
                     total_price=490.0, paid_amount=490.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                     class_group_id=group("ABS-K4", basma, today - timedelta(days=40)).id)
db.add(early)
db.commit()
monthly.tick(db, today=today)
db.expire_all()
early = db.get(Registration, early.id)
check("a month that began before: not counted, nobody written to", monthly.counted(early) == 1 and early.payment_status == PaymentStatus.PAID)
board = admin.get("/payments")
check("payments page: the month to decide, with “add” and “leave it free”", board.status_code == 200 and 'id="monthly"' in board.text
      and f"/payments/monthly/{early.id}" in board.text)
response = admin.post(f"/payments/monthly/{early.id}", data={"number": 2, "choice": "charge"})
db.expire_all()
early = db.get(Registration, early.id)
check("added by the admin: owed at the on-site price", early.total_amount == 980.0 and early.payment_status == PaymentStatus.PARTIAL, early.total_amount)

# ---- F. Online / on site changed from a message ----
sami = Candidate(full_name="Sami Change", email="sami.change@example.com", phone="50 333 444")
db.add(sami)
db.flush()
k5 = group("ABS-K5", basma, today + timedelta(days=10), mode=None)
switch = Registration(candidate_id=sami.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                      total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=k5.id)
db.add(switch)
db.commit()
said = mode_change.handle(db, sami, "Bonjour, je préfère passer en présentiel pour la suite de la formation.", "fr")
db.commit()
db.expire_all()
switch = db.get(Registration, switch.id)
check("change of way: made", said is not None and said["changed"] and switch.delivery_mode == "onsite", said)
check("change of way: the new price from next month, the month paid unchanged", switch.month_price == 490.0 and switch.total_amount == 390.0
      and "490 DT" in said["text"] and "mois prochain" in said["text"])
check("a question about the ways is not a change", mode_change.handle(db, sami, "C'est en ligne ou en présentiel ?", "fr") is None)
k5.delivery_mode = "online"
db.commit()
switch.delivery_mode = "online"
db.commit()
said = mode_change.handle(db, sami, "Can I switch to on site please?", "en")
db.commit()
conflict = db.query(Notification).filter(Notification.category == "MODE_CHANGE", Notification.registration_id == switch.id).first()
check("class online only: the admin gets a button to make it mixed", said["conflict"] and conflict is not None
      and (__import__("app.services.notification_fixes", fromlist=["fix_for"]).fix_for(db, conflict) or {}).get("url") == f"/classes/{k5.id}/mode")
admin.post(f"/classes/{k5.id}/mode", data={"mode": "mixed"})
db.expire_all()
check("made mixed: the notification is settled", __import__("app.services.notification_rules", fromlist=["settled"]).settled(db, db.get(Notification, conflict.id)))
check("detector: Tunisian and Arabic", mode_change.wanted("nheb nwalli online", "onsite") == "online" and mode_change.wanted("أريد التحويل إلى الحضوري", "online") == "onsite")

# Through the assistant, from an email: changed, answered alone, the admin told.
from app.services import email_assistant as ea

ea.llm_json = lambda *args, **kwargs: None
__import__("app.services.cloud_reply", fromlist=["read"]).read = lambda *args, **kwargs: None
k5.delivery_mode = None
db.commit()
kept = ea.ingest_inbound(db, sender=sami.email, subject="Ma formation", body="Bonjour, est-ce que je peux passer en ligne ? Merci",
                         message_id=f"<mode-{datetime.utcnow().timestamp()}@example.com>")
inbound = kept if isinstance(kept, CandidateEmail) else db.query(CandidateEmail).filter(CandidateEmail.candidate_id == sami.id,
                                                                                         CandidateEmail.direction == "inbound").order_by(CandidateEmail.received_at.desc()).first()
db.expire_all()
inbound = db.get(CandidateEmail, inbound.id)
outcome = ea.process_inbound(db, inbound) if inbound.status == "processing" else inbound.status
db.expire_all()
reply = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == sami.id, CandidateEmail.direction == "outbound")
         .order_by(CandidateEmail.received_at.desc()).first())
check("from an email: the way changes and the answer goes alone", db.get(Registration, switch.id).delivery_mode == "online"
      and outcome == "replied" and reply is not None and "en ligne" in reply.body and "lien personnel" in reply.body, (outcome, getattr(reply, "body", "")[:200]))
space = admin.get(f"/professors/{amel.id}")
check("a professor's space, as the admin sees it: their absences and the form for a period", space.status_code == 200 and 'name="end_date"' in space.text)

# ---- G. What became of a WhatsApp message or an SMS ----
thread = EmailThread(candidate_id=sami.id, subject="WhatsApp", channel="whatsapp")
db.add(thread)
db.flush()
sent = et.record_message(db, thread, channel="whatsapp", direction="outbound", author="system", message_id="wa-out:test-1", sender="WhatsApp",
                         recipient="+21650333444", subject="WhatsApp", body="Bonjour", status="sent")
sent.external_id, sent.delivery = "wamid.TEST1", "sent"
db.commit()
payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.TEST1", "status": "delivered", "timestamp": "1763100000"}]}}]}]}
for item in delivery.from_whatsapp(payload):
    delivery.update(db, item["id"], item["state"], when=item["when"])
payload["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = "read"
for item in delivery.from_whatsapp(payload):
    delivery.update(db, item["id"], item["state"], when=item["when"])
payload["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = "delivered"
for item in delivery.from_whatsapp(payload):
    delivery.update(db, item["id"], item["state"], when=item["when"])
db.commit()
db.expire_all()
check("WhatsApp statuses: delivered then read, never back", db.get(CandidateEmail, sent.id).delivery == "read")
sms_sent = et.record_message(db, thread, channel="sms", direction="outbound", author="system", message_id="sms-out:test-1", sender="SMS",
                             recipient="+21650333444", subject="SMS", body="Bonjour", status="sent")
sms_sent.external_id = "gw-1"
db.commit()
status = delivery.from_sms({"event": "sms:failed", "payload": {"messageId": "gw-1", "reason": "No signal"}})
delivery.update(db, status["id"], status["state"], reason=status["reason"])
db.commit()
db.expire_all()
check("SMS status: a failure is kept, with its reason", db.get(CandidateEmail, sms_sent.id).status == "failed" and "No signal" in db.get(CandidateEmail, sms_sent.id).analysis)

# ---- H. An SMS that came to the iPhone ----
secret = phone_sms.token(db)
queued = []
__import__("app.services.email_assistant", fromlist=["queue_processing"]).queue_processing = lambda message_id: queued.append(message_id)
came = public.post(f"/phone-sms/{secret}/received", json={"number": "+216 50 333 444", "text": "Bonjour, je serai en retard samedi"})
check("iPhone SMS in: kept in the candidate's SMS discussion and given to the assistant", came.status_code == 200 and came.json().get("kept") == 1 and queued)
check("iPhone SMS in: a wrong secret is refused", public.post("/phone-sms/not-the-secret/received", json={"number": "1", "text": "x"}).status_code == 404)

# ---- I. Settings ----
page = admin.get("/settings")
check("settings: every way at a glance, Facebook, LinkedIn, the iPhone's SMS in, monthly payments", page.status_code == 200 and all(part in page.text for part in (
    'id="connections"', 'id="facebook"', 'id="linkedin"', 'id="iphone-sms-in"', 'id="monthly"', 'id="absence-decision"')))
bad = admin.post("/settings/facebook", data={"token": "short", "page_id": "123"})
check("Facebook: a token that is not one is refused", "error=" in bad.headers.get("location", ""))
good = admin.post("/settings/facebook", data={"token": "EAA" + "x" * 80, "page_id": "page 1234567890"})
import os

check("Facebook: kept in the .env file and used at once", "notice=" in good.headers.get("location", "") and os.environ.get("FACEBOOK_PAGE_ID") == "1234567890"
      and "FACEBOOK_PAGE_ACCESS_TOKEN=EAA" in open(os.environ["ENV_FILE"], encoding="utf-8").read())
response = admin.post("/settings/linkedin", data={"token": "", "organization": "123", "back": "/settings#linkedin"})
check("LinkedIn from Settings: back on Settings", response.headers.get("location", "").startswith("/settings"))
response = admin.post("/settings/monthly", data={"remind_days": "7", "grace_days": "2", "sent": "1"})
db.expire_all()
check("monthly payments: saved", get_settings(db).month_remind_days == 7 and get_settings(db).month_grace_days == 2 and get_settings(db).month_pause_on is False)


# ---- J. A new class starts in the first week of the next month; a running class takes candidates only from the admin ----
from app.services import enrollment_service as es

settings = get_settings(db)
settings.class_start_mode = "next_month"
db.commit()
made = es.create_class(db, program, settings=settings, days_of_week="sat", start_time=dtime(9, 0), end_time=dtime(12, 0))
db.commit()
next_first = date(today.year + today.month // 12, today.month % 12 + 1, 1)
check("next-month rule: the first Saturday of the next month", made.start_date.date().month == next_first.month
      and made.start_date.date().day <= 7 and made.start_date.date().weekday() == 5 and made.start_mode == "next_month", made.start_date)
running = group("ABS-RUN", basma, today - timedelta(days=3))
running.status = ClassStatus.ONGOING
lina = Candidate(full_name="Lina Nouvelle", email="lina.nouvelle@example.com", phone="50 444 555")
db.add(lina)
db.flush()
fresh = Registration(candidate_id=lina.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                     total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING)
db.add(fresh)
db.commit()
placed = es.enroll_paid_registration(db, fresh, notify_candidate=False)
db.expire_all()
fresh = db.get(Registration, fresh.id)
check("a paid candidate never joins a class that already runs by themselves", fresh.class_group_id != running.id
      and (fresh.class_group is None or fresh.class_group.status == ClassStatus.NOT_STARTED), (fresh.class_group_id, placed.reason))
es.move_registration(db, fresh.id, running.id, notify_candidate=False)
db.expire_all()
check("...but the admin can add them to it", db.get(Registration, fresh.id).class_group_id == running.id)

# ---- K. "Do you continue with the next month?" ----
check("yes/no replies read", monthly.reply_answer("Oui") is True and monthly.reply_answer("Bonjour, oui je continue. Merci") is True
      and monthly.reply_answer("non merci") is False and monthly.reply_answer("Le sam. 14 nov. 2026 a écrit :\n> question") is None
      and monthly.reply_answer("oui mais c'est combien ?") is None and monthly.reply_answer("نعم") is True
      and monthly.reply_answer("Non\n\nLe sam. 14 nov. 2026 à 10:00, 9antra <a@b.c> a écrit :\n> Vous continuez ?") is False)
settings.monthly_since = datetime.utcnow() - timedelta(days=90)
settings.month_remind_days = 5
settings.month_pause_on = True
db.commit()
omar = Candidate(full_name="Omar Choix", email="omar.choix@example.com", phone="50 555 666")
db.add(omar)
db.flush()
k6 = group("ABS-K6", basma, today - timedelta(days=28))
choosing = Registration(candidate_id=omar.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                        total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=k6.id)
db.add(choosing)
db.commit()
begins = monthly.month_start(choosing, 2)
mails.clear()
monthly.tick(db, today=begins - timedelta(days=3))
db.expire_all()
choosing = db.get(Registration, choosing.id)
asked_mail = next((mail for mail in mails if mail["to"] == omar.email), None)
check("near the end of month 1: one question, with their link", asked_mail is not None and choosing.month_token
      and f"/month/{choosing.month_token}" in (asked_mail["html"] or "") and "continue" in (asked_mail["subject"] or ""), (asked_mail or {}).get("subject"))
page = public.get(f"/month/{choosing.month_token}")
check("their page: both choices, nothing recorded by opening it", page.status_code == 200 and "Oui, je continue" in page.text
      and "Non, je m'arrête après ce mois" in page.text and db.get(Registration, choosing.id).month_answer is None)
page = public.post(f"/month/{choosing.month_token}", data={"answer": "no"})
db.expire_all()
choosing = db.get(Registration, choosing.id)
check("no: they stop after month 1, nothing more owed", page.status_code == 200 and choosing.months == 1 and monthly.answer_for(choosing, 2) == "no"
      and monthly.program_total(choosing) == 390.0 and "rien de plus" in page.text)
check("no: the admin is told", db.query(Notification).filter(Notification.category == "MONTH_CHOICE", Notification.registration_id == choosing.id).count() == 1)
monthly.tick(db, today=begins + timedelta(days=4))
db.expire_all()
choosing = db.get(Registration, choosing.id)
check("month 2 starts: not counted, not paused", choosing.total_amount == 390.0 and choosing.payment_status == PaymentStatus.PAID and choosing.paused_at is None)
later = session(k6, begins + timedelta(days=1), 5)
earlier = session(k6, begins - timedelta(days=2), 4)
db.commit()
from app.services import attendance

check("the sessions after their months are not theirs, the ones before are", choosing.id not in {row.id for row in attendance.members(db, k6.id, on=later.session_date)}
      and choosing.id in {row.id for row in attendance.members(db, k6.id, on=earlier.session_date)})
again = Registration(candidate_id=omar.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                     total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                     class_group_id=group("ABS-K7", basma, today - timedelta(days=27)).id)
db.add(again)
db.commit()
monthly.tick(db, today=monthly.month_start(again, 2) - timedelta(days=2))
db.expire_all()
again = db.get(Registration, again.id)
public.post(f"/month/{again.month_token}", data={"answer": "no"})
public.post(f"/month/{again.month_token}", data={"answer": "yes"})
db.expire_all()
again = db.get(Registration, again.id)
check("they can change their mind before the month starts", monthly.answer_for(again, 2) == "yes" and again.months is None and monthly.program_total(again) == 780.0)

# A "oui" by email, to the question.
sara = Candidate(full_name="Sara Reponse", email="sara.reponse@example.com", phone="50 666 777")
db.add(sara)
db.flush()
replying = Registration(candidate_id=sara.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                        total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                        class_group_id=group("ABS-K8", basma, today - timedelta(days=27)).id, month_reminded=2, month_token="tok-sara-reponse-123456")
db.add(replying)
db.commit()
kept = ea.ingest_inbound(db, sender=sara.email, subject="Re: vous continuez ?", body="Oui merci !",
                         message_id=f"<oui-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
inbound = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == sara.id, CandidateEmail.direction == "inbound").first()
if inbound is not None and inbound.status == "processing":
    ea.process_inbound(db, inbound)
    db.expire_all()
replying = db.get(Registration, replying.id)
answer_mail = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == sara.id, CandidateEmail.direction == "outbound")
               .order_by(CandidateEmail.received_at.desc()).first())
check("a “oui” reply: recorded, and they are told what to pay before when", monthly.answer_for(replying, 2) == "yes" and answer_mail is not None
      and "vous continuez" in answer_mail.body, getattr(answer_mail, "body", "")[:160])

# One month or the whole program, on the candidate's file.
nour = Candidate(full_name="Nour Deux Mois", email="nour.deux@example.com", phone="50 777 888")
db.add(nour)
db.flush()
db.add(Registration(candidate_id=nour.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                    total_price=390.0, paid_amount=0.0, payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING))
db.commit()
file_page = admin.get(f"/candidate/{nour.id}").text
check("their file: one month, or the two months at once", "1 month paid (390 DT)" in file_page and "The 2 months paid (780 DT)" in file_page)


# ---- L. What a professor teaches: only the admin adds it; when allowed, they ask and the admin approves ----
from app.services import teach_requests

dora = db.query(Professor).filter(Professor.name == "Dora Seconde").first()
dora_client = TestClient(main.app, follow_redirects=False)
auth.create_user(db, email="dora.teach@test.local", name=dora.name, password="dora-teach-12", role=auth.PROFESSOR, professor_id=dora.id)
dora_client.post("/login", data={"email": "dora.teach@test.local", "password": "dora-teach-12"})
other = Program(name="Teach Request Program", total_duration_days=60, price_online=390.0, price_onsite=490.0, is_approved=True)
db.add(other)
db.commit()
before = db.get(Professor, dora.id).specialties
dora_client.post("/teacher/profile", data={"name": dora.name, "phone": "", "specialties": "Teach Request Program, Hacking"})
db.expire_all()
check("a professor cannot change what they teach from their profile", db.get(Professor, dora.id).specialties == before, db.get(Professor, dora.id).specialties)
get_settings(db).professor_program_requests = False
db.commit()
answer = dora_client.post("/teacher/teach-request", data={"program_id": other.id})
check("asking is off by default: only the admin adds a program", "error=" in answer.headers.get("location", "") and teach_requests.waiting(db) == [])
get_settings(db).professor_program_requests = True
db.commit()
profile = dora_client.get("/teacher/profile").text
check("allowed: their profile offers to ask, the specialities are no longer typed", "Ask to teach a program" in profile and 'name="specialties"' not in profile)
dora_client.post("/teacher/teach-request", data={"program_id": other.id, "note": "J'ai enseigné ce module deux ans."})
db.expire_all()
waiting_now = teach_requests.waiting(db)
check("their request waits for the admin, who is told", len(waiting_now) == 1 and db.query(Notification).filter(Notification.category == "TEACH_REQUEST").count() == 1)
check("...and it is not added meanwhile", not __import__("app.services.enrollment_service", fromlist=["teaches"]).teaches(db.get(Professor, dora.id), other))
page = admin.get("/professors").text
check("the professors' page shows it, with approve and refuse", 'id="teach-requests"' in page and "Teach Request Program" in page
      and f"/professors/teach-requests/{waiting_now[0]['request'].id}/approve" in page)
mails.clear()
admin.post(f"/professors/teach-requests/{waiting_now[0]['request'].id}/approve")
db.expire_all()
check("approved: the program joins what they teach, they are told", __import__("app.services.enrollment_service", fromlist=["teaches"]).teaches(db.get(Professor, dora.id), other)
      and any("Vous enseignez maintenant" in (mail["subject"] or "") for mail in mails))

# ---- M. The rules applied once to what existed ----
settings = get_settings(db)
settings.rules_applied_at = None
settings.monthly_since = datetime.utcnow()
db.commit()
free_prof = professor("Farah Nouvelle")
old_rule = group("ABS-OLD", free_prof, today + timedelta(days=6), hours=(18, 20))
old_rule.status, old_rule.start_mode, old_rule.created_at = ClassStatus.NOT_STARTED, "days_after_creation", datetime.utcnow() - timedelta(days=4)
chosen = group("ABS-CHOSEN", free_prof, today + timedelta(days=9), hours=(14, 17))
chosen.status, chosen.start_mode = ClassStatus.NOT_STARTED, "fixed_date"
late = Candidate(full_name="Hedi Retard", email="hedi.retard@example.com", phone="50 888 999")
db.add(late)
db.flush()
k9 = group("ABS-K9", basma, today - timedelta(days=45))
behind = Registration(candidate_id=late.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390.0,
                      total_price=390.0, paid_amount=390.0, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=k9.id)
db.add(behind)
db.commit()
done = monthly.apply_everywhere(db)
monthly.tick(db)
db.expire_all()
old_rule, chosen, behind = db.get(ClassGroup, old_rule.id), db.get(ClassGroup, chosen.id), db.get(Registration, behind.id)
check("a class not started moves to the first week of the coming month", "ABS-OLD" in done["moved"] and old_rule.start_mode == "next_month"
      and old_rule.start_date.day <= 7 and old_rule.start_date.date() > today, (old_rule.start_date, done))
check("...a date the admin chose stays", chosen.start_mode == "fixed_date" and "ABS-CHOSEN" not in done["moved"])
check("a month begun long ago and not paid: owed, and their access paused until they pay", behind.total_amount == 780.0
      and behind.payment_status == PaymentStatus.PARTIAL and behind.paused_at is not None)
check("...done once", monthly.apply_everywhere(db) is None)
board = admin.get("/payments").text
from app.services import advisor

check("the dashboard says a month is not paid", any(item.icon == "bi-pause-circle" for item in advisor.monthly_advice(db)))
check("the board on Payments: the paused candidate, with the payment to record", 'id="live-monthly-rows"' in board and "Paused until they pay" in board
      and f'action="/payment/confirm/{behind.id}"' in board)
recorded = admin.post(f"/payment/confirm/{behind.id}", data={"amount": "390", "method": "manual", "back": "/payments#monthly"})
db.expire_all()
check("recorded from the board: back on the board, access starts again", recorded.headers["location"].startswith("/payments")
      and db.get(Registration, behind.id).paused_at is None)
class_page = admin.get(f"/classes/{k9.id}").text
check("the class page: each candidate's month", "This month" in class_page and ("Paid" in class_page or "Continues" in class_page))
settings_page = admin.get("/settings").text
check("Settings: the start rule is fixed, no other choice", "Always in the first week of the coming month" in settings_page
      and "A number of days after it is created" not in settings_page and 'id="teach-rule"' in settings_page)
made = __import__("app.services.enrollment_service", fromlist=["create_class"]).create_class(db, program)
db.commit()
check("a new class follows the rule whatever the old setting", made.start_mode == "next_month" and made.start_date.day <= 7)


# ---- N. A class the application makes always starts in the first week of a month ----
check("first week on or after a day", es.first_week_on_or_after(date(2026, 12, 18), [5]) == date(2027, 1, 2)
      and es.first_week_on_or_after(date(2026, 12, 3), [5]) == date(2026, 12, 5) and es.first_week_on_or_after(date(2026, 12, 6), [5]) == date(2027, 1, 2))
auto_mid = group("ABS-AUTO", free_prof, date(2027, 2, 13), hours=(8, 9))
auto_mid.status, auto_mid.auto_created, auto_mid.start_mode = ClassStatus.NOT_STARTED, True, "fixed_date"
mine_mid = group("ABS-MINE", free_prof, date(2027, 3, 17), hours=(10, 11))
mine_mid.status, mine_mid.auto_created, mine_mid.start_mode = ClassStatus.NOT_STARTED, False, "fixed_date"
db.commit()
moved = es.align_auto_classes(db)
db.expire_all()
check("a class the application made, mid-month: moved to the first week after", "ABS-AUTO" in moved
      and db.get(ClassGroup, auto_mid.id).start_date.date() == date(2027, 3, 6), db.get(ClassGroup, auto_mid.id).start_date)
check("a date the admin chose: never moved", "ABS-MINE" not in moved and db.get(ClassGroup, mine_mid.id).start_date.date() == date(2027, 3, 17))
check("...and done once", es.align_auto_classes(db) == [])

check.done()
