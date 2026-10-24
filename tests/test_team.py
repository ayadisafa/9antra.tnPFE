"""The team chat (professors and team members: in the application, email, WhatsApp, SMS; their answers come back;
the assistant prepares or, when allowed, answers with what the admin taught) and when the level check goes
(N days before the first session, or as soon as they paid). Runs on a COPY; email and WhatsApp are fakes."""
from datetime import datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("team")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.knowledge import KnowledgeEntry
from app.models.notification import Notification
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.services import (access, auth, email_assistant as ea, email_threads as et, friend, knowledge_service, level_check,
                          settings_guide, team_messages as tm, whatsapp, whatsapp_inbox)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

emails = []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True
ea.model_available = lambda: False
whatsapp.configured = lambda: False


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="team-test-1", role=auth.ADMIN)
nour = Professor(name="Nour Teamtest", specialties="DevOps", email="nour.teamtest@example.com", phone="22 333 444")
karim = Professor(name="Karim Noaccount", specialties="Python", email="karim.noaccount@example.com")
db.add_all([nour, karim])
db.commit()
auth.create_user(db, email="nour.login@example.com", name="Nour Teamtest", password="nour-team-12", role=auth.PROFESSOR, professor_id=nour.id)
auth.create_user(db, email="sam.team@test.local", name="Sam Team", password="sam-team-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.team@test.local").one()
sam.permissions = access.dump({"candidates": "view"})
db.commit()


def client_for(email, password):
    one = TestClient(main.app, follow_redirects=False)
    one.post("/login", data={"email": email, "password": password})
    return one


admin = client_for("admin@test.local", "team-test-1")
prof = client_for("nour.login@example.com", "nour-team-12")
staff = client_for("sam.team@test.local", "sam-team-12")
nour_key, karim_key, sam_key = f"professor:{nour.id}", f"professor:{karim.id}", f"user:{sam.id}"

# ---- A. The team ----
everyone = {item["key"]: item for item in tm.people(db, me_id=db.query(User).filter(User.email == "admin@test.local").one().id)}
check("team: professors and team members are listed", nour_key in everyone and karim_key in everyone and sam_key in everyone)
check("team: the admin is not in their own list", not any(item["name"] == "Safa Admin" for item in everyone.values()))
check("team: a professor's account talks as that professor (one conversation)", f"user:{db.query(User).filter(User.email == 'nour.login@example.com').one().id}" not in everyone)
check("team: how each can be reached", everyone[nour_key]["ways"] == ["app", "email", "whatsapp", "sms"] and everyone[karim_key]["ways"] == ["email"]
      and everyone[sam_key]["ways"] == ["app", "email"], (everyone[nour_key]["ways"], everyone[karim_key]["ways"], everyone[sam_key]["ways"]))

# ---- B. The admin writes, several ways at once ----
response = admin.post("/inbox/team/send", data={"person": nour_key, "body": "Bonjour Nour, la salle change samedi.", "channels": ["app", "email", "whatsapp", "sms"]})
rows = db.query(TeamMessage).filter(TeamMessage.person == nour_key).all()
check("send: one copy per way, sharing one message", response.status_code == 303 and len(rows) == 4 and len({row.batch for row in rows}) == 1, [(r.channel, r.status) for r in rows])
by = {row.channel: row for row in rows}
ids = {channel: row.id for channel, row in by.items()}
check("send: in the application, sent", by["app"].status == "sent")
check("send: by email, sent from the centre's address", by["email"].status == "sent" and emails and emails[-1][0] == "nour.teamtest@example.com"
      and "Safa Admin" in emails[-1][2], emails[-1:])
check("send: WhatsApp without a Business number waits for the admin's phone", by["whatsapp"].status == "to_send")
check("send: SMS waits for the admin's phone", by["sms"].status == "to_send")
check("send: the notice says what went and what to press", "Sent: In the application, Email." in where(response) and "Open" in where(response), where(response))
bubbles = tm.conversation(db, nour_key)
check("conversation: one bubble with four ways", len(bubbles) == 1 and len(bubbles[0]["ways"]) == 4)
links = {way["channel"]: way["link"] for way in bubbles[0]["ways"]}
check("conversation: WhatsApp opens on the number, written", links["whatsapp"].startswith("https://wa.me/21622333444?text="), links["whatsapp"])
check("conversation: SMS opens the phone's messages, written", links["sms"].startswith("sms:+21622333444?body=Bonjour"), links["sms"])
page = admin.get(f"/inbox/team?person={nour_key}").text
check("page: the conversation shows, with the buttons", "la salle change samedi" in page and "wa.me/21622333444" in page and "sms:+21622333444" in page)
admin.post(f"/inbox/team/message/{ids['whatsapp']}/sent", data={"person": nour_key})
admin.post(f"/inbox/team/message/{ids['sms']}/sent", data={"person": nour_key, "remove": "1"})
db.expire_all()
check("phone: “Sent” marks it sent", db.get(TeamMessage, ids["whatsapp"]).status == "sent")
check("phone: “Remove” takes it away", db.get(TeamMessage, ids["sms"]) is None)
emails.clear()
et._attempt = lambda recipient, subject, *rest, **kw: ("the mail server refused", False)
response = admin.post("/inbox/team/send", data={"person": karim_key, "body": "Test", "channels": ["email"]})
check("send: a failed email says why", "Email: the mail server refused" in where(response), where(response))
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
response = admin.post("/inbox/team/send", data={"person": karim_key, "body": "Bonjour", "channels": ["app"]})
db.expire_all()
check("send: a way they cannot have falls back to one they can", db.query(TeamMessage).filter(TeamMessage.person == karim_key, TeamMessage.status == "sent").one().channel == "email")

# ---- C. The professor's own side ----
page = prof.get("/account/messages")
check("member: the professor reads the centre's message", page.status_code == 200 and "la salle change samedi" in page.text, page.status_code)
check("member: the menu and the phone bar have Messages", 'href="/account/messages"' in prof.get("/teacher").text)
db.expire_all()
check("member: once read, the admin sees it read", db.get(TeamMessage, ids["app"]).read_at is not None)
check("member: nothing new to read", prof.get("/account/messages/count").json()["count"] == 0)
admin.post("/inbox/team/send", data={"person": nour_key, "body": "Merci de confirmer.", "channels": ["app"]})
check("member: a new message is counted", prof.get("/account/messages/count").json()["count"] == 1)
said = prof.get("/account/friend").json()
check("member: Yasmine tells them", any(line["url"] == "/account/messages" for line in said["lines"]), said["lines"])
response = prof.post("/account/messages", data={"body": "Bien reçu, je confirme pour samedi."})
check("member: they answer in the application", response.status_code == 303 and "Sent to the centre" in where(response))
db.expire_all()
note = db.query(Notification).filter(Notification.category == "TEAM_MESSAGE").order_by(Notification.created_at.desc()).first()
check("admin: told, with the way to the conversation", note is not None and "Nour Teamtest wrote to you" in note.title and note.action_url == f"/inbox/team?person={nour_key}",
      note and (note.title, note.action_url))
check("admin: counted as new", tm.unread_count(db) == 1 and admin.get("/inbox/team/count").json()["count"] == 1)
said = admin.get("/account/friend").json()
check("admin: Yasmine tells about the team's message", any(line["url"] == "/inbox/team" for line in said["lines"]), said["lines"][:3])
page = admin.get(f"/inbox/team?person={nour_key}").text
check("admin: the answer is in the conversation", "je confirme pour samedi" in page)
db.expire_all()
check("admin: opening it marks it read", tm.unread_count(db) == 0)
check("admin: the inbox has both sides", 'href="/inbox/team"' in admin.get("/inbox").text and "Team chat" in admin.get("/").text)
check("staff: Messages for a team member too", staff.get("/account/messages").status_code == 200)
check("staff: the team chat is the admin's (or the inbox's)", staff.get("/inbox/team").status_code in (303, 403))

# ---- D. Their emails and WhatsApp come back into the conversation ----
result = ea.ingest_inbound(db, sender="Nour.Teamtest@example.com", subject="Re: Message — The Bridge Desk", body="Je serai en retard de 10 minutes.",
                           message_id="<team-1@example.com>")
db.commit()
check("email: a professor's email goes into their conversation", result["status"] == "stored" and result.get("team_message"), result)
check("email: not taken for a registration request", not db.query(Notification).filter(Notification.message.like("%en retard de 10 minutes%"),
                                                                                        Notification.category != "TEAM_MESSAGE").count())
again = ea.ingest_inbound(db, sender="nour.teamtest@example.com", subject="Re: Message", body="Je serai en retard de 10 minutes.", message_id="<team-1@example.com>")
check("email: the same email is kept once", again["status"] == "duplicate" and db.query(TeamMessage).filter(TeamMessage.message_id == "<team-1@example.com>").count() == 1)
ea.ingest_inbound(db, sender="sam.team@test.local", subject="Absence lundi", body="Je ne serai pas là lundi.", message_id="<team-2@example.com>")
row = db.query(TeamMessage).filter(TeamMessage.message_id == "<team-2@example.com>").one()
check("email: a team member's subject is kept with the text", row.person == sam_key and row.body.startswith("Absence lundi\n"), row.body)
kept = whatsapp_inbox.receive(db, phone="21622333444", text="Je suis arrivée.", wa_id="TEAMWA1", name="Nour")
db.commit()
check("whatsapp: a professor's WhatsApp goes into their conversation, not a candidate's", kept is None
      and db.query(TeamMessage).filter(TeamMessage.message_id == "wa:TEAMWA1", TeamMessage.person == nour_key, TeamMessage.channel == "whatsapp").count() == 1)

# ---- E. The assistant prepares the answer; alone only when allowed, and only what was taught ----
knowledge_service.save_entry(db, title="À quelle heure ouvre le centre le samedi ?", content="Le centre ouvre à 8h30 le samedi.")
db.commit()
staff.post("/account/messages", data={"body": "À quelle heure ouvre le centre le samedi ?"})
db.expire_all()
asked = db.query(TeamMessage).filter(TeamMessage.person == sam_key, TeamMessage.direction == "in").order_by(TeamMessage.created_at.desc()).first()
check("assistant: it found the taught answer", asked.suggestion == "Le centre ouvre à 8h30 le samedi." and asked.suggestion_source == "taught",
      (asked.suggestion, asked.suggestion_source))
check("assistant: by default nothing goes alone", not db.query(TeamMessage).filter(TeamMessage.person == sam_key, TeamMessage.author == tm.ASSISTANT).count())
page = admin.get(f"/inbox/team?person={sam_key}").text
check("assistant: the admin sees it, one click to use it", "The assistant found an answer you taught" in page and "Use this answer" in page)
response = admin.post("/inbox/team/assistant", data={"on": "1", "person": sam_key})
db.expire_all()
check("assistant: the admin lets it answer alone", get_settings(db).team_auto_reply is True and "answers the team alone" in where(response))
staff.post("/account/messages", data={"body": "À quelle heure ouvre le centre le samedi ?"})
db.expire_all()
alone = db.query(TeamMessage).filter(TeamMessage.person == sam_key, TeamMessage.author == tm.ASSISTANT).all()
check("assistant: then it answers alone, in the application", len(alone) == 1 and alone[0].channel == "app" and alone[0].body == "Le centre ouvre à 8h30 le samedi.")
staff.post("/account/messages", data={"body": "Peux-tu me prêter ton ordinateur demain pour la démo ?"})
db.expire_all()
check("assistant: what it was not taught still waits for the admin", len(db.query(TeamMessage).filter(TeamMessage.person == sam_key, TeamMessage.author == tm.ASSISTANT).all()) == 1)
words = " ".join(line["text"] for group in settings_guide.plain_words(db) for line in group["lines"])
check("plain words: the team chat's assistant", "In the team chat, the assistant answers" in words)
admin.post("/inbox/team/assistant", data={"on": "0"})
response = admin.post("/inbox/team/send", data={"person": sam_key, "body": "Oui, je te le prête à 9h.", "channels": ["app"], "learn": "true",
                                                 "question": "Peux-tu me prêter ton ordinateur demain pour la démo ?"})
check("learn: the admin's answer is taught", "The assistant learned this answer" in where(response)
      and db.query(KnowledgeEntry).filter(KnowledgeEntry.content == "Oui, je te le prête à 9h.").count() == 1, where(response))

# ---- F. Their details ----
response = admin.post("/inbox/team/contact", data={"person": karim_key, "email": "karim.new@example.com", "phone": "abc"})
check("details: a number that cannot be read is refused", "cannot be read" in where(response))
admin.post("/inbox/team/contact", data={"person": karim_key, "email": "karim.new@example.com", "phone": "98765432"})
db.expire_all()
check("details: email and number saved, written +216", db.get(Professor, karim.id).email == "karim.new@example.com" and db.get(Professor, karim.id).phone == "+216 98 765 432",
      (db.get(Professor, karim.id).email, db.get(Professor, karim.id).phone))
admin.post("/inbox/team/contact", data={"person": sam_key, "email": "other@example.com", "phone": "+216 50 111 222"})
db.expire_all()
check("details: a team member's number saved, their sign-in email untouched", db.get(User, sam.id).phone == "+216 50 111 222" and db.get(User, sam.id).email == "sam.team@test.local")
check("details: now reachable on WhatsApp", "whatsapp" in tm.person(db, sam_key)["ways"])

# ---- G. When the level check goes ----
check("timing: by default, 7 days before the first session", level_check.timing(db) == {"when": "before", "days": 7})
settings = get_settings(db)
settings.public_base_url = "https://centre.example.com"
db.commit()
program = db.query(Program).order_by(Program.name).first()


def candidate_in(days_ahead, name):
    group = ClassGroup(name=f"LV-TIMING-{name}", program_id=program.id, professor_name="Prof", status=ClassStatus.NOT_STARTED, total_weeks=8,
                       start_date=(local_now() + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0),
                       start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
    db.add(group)
    db.flush()
    person = Candidate(full_name=f"Lv {name}", email=f"lv.{name.lower()}@example.com")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=300, total_price=300, paid_amount=300,
                                payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)
    db.add(registration)
    db.commit()
    return registration


soon, later = candidate_in(5, "Soon"), candidate_in(20, "Later")
emails.clear()
level_check.after_payment(db, later)
check("timing: a class in 20 days: prepared, not sent yet", level_check.current(db, later.id) is not None and level_check.current(db, later.id).sent_at is None)
check("timing: its day is 7 days before", level_check.goes_on(later, level_check.timing(db)).date() == (later.class_group.start_date - timedelta(days=7)).date())
level_check.after_payment(db, soon)
check("timing: a class in 5 days: sent at once", level_check.current(db, soon.id).sent_at is not None and any(item[0] == "lv.soon@example.com" for item in emails))
level_check.current(db, soon.id).sent_at = None
db.commit()
level_check._TRIED.clear()
sent = level_check.send_due(db)
check("timing: the regular round sends those whose day came", sent >= 1 and level_check.current(db, soon.id).sent_at is not None)
check("timing: and not the others", level_check.current(db, later.id).sent_at is None)
check("timing: in 13 days the later one goes", level_check.send_due(db, now_local=local_now() + timedelta(days=13, hours=1)) >= 1
      and level_check.current(db, later.id).sent_at is not None)
page = admin.get("/quiz/all").text
check("page: the level checks page says when", "7 days before the first session" in page and 'action="/quiz/timing"' in page)
response = admin.post("/quiz/timing", data={"when": "before", "days": "3"})
db.expire_all()
check("page: the admin changes it", level_check.timing(db) == {"when": "before", "days": 3} and "3 days before the first session" in where(response), where(response))
admin.post("/quiz/timing", data={"when": "before", "days": "400"})
db.expire_all()
check("page: never more than 30 days", level_check.timing(db)["days"] == 30)
admin.post("/quiz/timing", data={"when": "paid", "days": "7"})
db.expire_all()
check("page: or as soon as they pay", level_check.timing(db)["when"] == "paid")
words = " ".join(line["text"] for group in settings_guide.plain_words(db) for line in group["lines"])
check("plain words: when the level check goes", "as soon as they have paid in full" in words)

# ---- H. The centre's numbers ----
from app.services import email_layout

response = admin.post("/settings/numbers", data={"phone": "abc", "chat": ""})
check("numbers: a number that cannot be read is refused", "cannot be read" in where(response), where(response))
response = admin.post("/settings/numbers", data={"phone": "71 123 456", "chat": "98123456"})
db.expire_all()
settings = get_settings(db)
check("numbers: saved, written +216", settings.centre_phone == "+216 71 123 456" and settings.centre_whatsapp == "+216 98 123 456",
      (settings.centre_phone, settings.centre_whatsapp))
footer = email_layout.branding(settings, "fr")["footer_note"]
check("numbers: under every email", "Tél. +216 71 123 456 · WhatsApp +216 98 123 456" in footer, footer)
check("numbers: in English too", "Phone +216 71 123 456" in email_layout.branding(settings, "en")["footer_note"])
check("numbers: shown in Settings", "+216 98 123 456" in admin.get("/settings").text and 'action="/settings/numbers"' in admin.get("/settings").text)

db.close()
check.done()
