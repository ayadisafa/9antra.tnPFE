"""The admin's own reminders, squares in emails, somebody who asks to register, and the design options.
Staging copy, COPY of the database, no mail leaves this machine."""
import testenv
testenv.prepare("reminders")
check = testenv.Checks()

from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import AdminReminder, Candidate, ClassGroup, ClassStatus, Notification, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.registration import PaymentStatus, RegistrationStatus
from app.scheduler import jobs
from app.services import (auth, chatbot as cb, email_assistant as ea, email_layout as el, email_templates as tp, email_threads as et,
                          notification_fixes as nf, registration_requests as rr, reminders as rm, system_status as ss)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

sent = []
def fake_send(**kw):
    sent.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="reminders-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "reminders-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
devops = db.query(Program).filter(Program.name == "DevOps").first()
python = db.query(Program).filter(Program.name == "Python").first()

start = (local_now() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="DEVOPS-RM-1", program_id=devops.id, professor_name="Nour", status=ClassStatus.NOT_STARTED, start_date=start, total_weeks=8,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group); db.flush()
def member(name, email, program, paid, in_class=True):
    one = Candidate(full_name=name, email=email, phone="50 100 200"); db.add(one); db.flush()
    reg = Registration(candidate_id=one.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390,
                       paid_amount=390 if paid else 0, payment_status=PaymentStatus.PAID if paid else PaymentStatus.UNPAID,
                       status=RegistrationStatus.ASSIGNED if in_class else RegistrationStatus.PENDING, class_group_id=group.id if in_class else None)
    db.add(reg); db.commit()
    return one
ines = member("Ines Ben Salah", "ines.rm@test.local", devops, True)
karim = member("Karim Jlassi", "karim.rm@test.local", devops, True)
omar = member("Omar Sassi", "omar.rm@test.local", python, False, in_class=False)

# ======================================================================
# A. Coloured squares in an email
# ======================================================================
html = el.text_to_html("Bonjour,\n\n[box gold]\n**Quand :** samedi 9h\n- point un\n- point deux\n\nÀ bientôt")
check("a paragraph that starts with [box] becomes a coloured square", "<table" in html and "border-left:4px solid #d97706" in html and "<strong>Quand :</strong>" in html and "<li" in html and "[box" not in html, html[:300])
check("the text around it is unchanged", html.startswith('<p style="margin:0 0 16px;">Bonjour,</p>') and html.rstrip().endswith("À bientôt</p>"))
check("without a colour it takes the centre's own", "border-left:4px solid " + el.DEFAULT_ACCENT in el.text_to_html("[box]\nUn texte"))
check("the mark never shows in the text version", el.strip_marks("Bonjour\n\n[box gold]\nQuand : samedi\n\n[box] Sur la même ligne") == "Bonjour\n\nQuand : samedi\n\nSur la même ligne")
check("text that only looks like it is left alone", "[boxer]" in el.text_to_html("[boxer] n'est pas une marque") and "<table" not in el.text_to_html("Un [box] au milieu"))
check("markup typed in a square is still escaped", "<script>" not in el.text_to_html("[box]\n<script>alert(1)</script>"))

# ======================================================================
# B. Reminders
# ======================================================================
check("a class reminder goes to its candidates, each once", [person.full_name for person, _ in rm.recipients(db, "class", group.id)] == ["Ines Ben Salah", "Karim Jlassi"])
check("“those who still owe” are those who do", any(person.id == omar.id for person, _ in rm.recipients(db, "unpaid", None)) and not any(person.id == ines.id for person, _ in rm.recipients(db, "unpaid", None)))
check("a program, one person, or everyone", any(person.id == omar.id for person, _ in rm.recipients(db, "program", python.id)) and rm.recipients(db, "candidate", ines.id)[0][0].id == ines.id
      and len(rm.recipients(db, "everyone", None)) >= 3 and rm.recipients(db, "candidate", "nobody") == [] and rm.recipients(db, "strange", None) == [] and rm.recipients(db, "class", None) == [] and rm.recipients(db, "program", "") == [])
for label, kwargs in (("without a subject", dict(audience="class", audience_id=group.id, subject=" ", body="x")),
                      ("without anybody", dict(audience="class", audience_id=None, subject="s", body="x")),
                      ("to an empty choice", dict(audience="candidate", audience_id="nobody", subject="s", body="x")),
                      ("to an unknown kind", dict(audience="martians", audience_id=None, subject="s", body="x"))):
    try:
        rm.create(db, **kwargs); refused = False
    except ValueError:
        refused = True
    check(f"a reminder {label} is refused", refused)
now = datetime.utcnow()
idea = rm.idea("bring")
planned = rm.create(db, audience="class", audience_id=group.id, subject=idea["subject"], title=idea["title"], body=idea["body"], style=idea["style"], send_at=now + timedelta(hours=3), by="Test Admin")
check("a reminder is kept with who, what and when", planned.status == "scheduled" and planned.audience_label == "Class DEVOPS-RM-1" and planned.created_by == "Test Admin")
check("nothing goes before its time", rm.send_due(db, now + timedelta(hours=2)) == 0 and sent == [] and db.get(AdminReminder, planned.id).status == "scheduled")
check("at its time it goes to everyone of the class", rm.send_due(db, now + timedelta(hours=3, minutes=1)) == 1 and sorted(mail["to_email"] for mail in sent) == ["ines.rm@test.local", "karim.rm@test.local"], [m["to_email"] for m in sent])
db.expire_all(); planned = db.get(AdminReminder, planned.id)
check("it is marked as gone, with how many received it", planned.status == "sent" and planned.sent_count == 2 and planned.failed_count == 0 and planned.sent_at is not None)
first = next(mail for mail in sent if mail["to_email"] == "ines.rm@test.local")
check("each candidate gets their own name and program", "Bonjour Ines" in first["text_body"] and "DevOps" in first["text_body"] and "{first_name}" not in first["body"])
check("the square is drawn in the email and absent from its text", "border-left:4px solid" in first["body"] and "[box]" not in first["body"] and "[box]" not in first["text_body"] and "À apporter" in first["text_body"])
check("it never goes twice", rm.send_due(db, now + timedelta(days=1)) == 0 and len(sent) == 2)
kept = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == ines.id).order_by(CandidateEmail.received_at.desc()).first()
check("it stays in the candidate's discussion", kept is not None and kept.subject == idea["subject"] and kept.status == "sent")
later = rm.create(db, audience="candidate", audience_id=omar.id, subject="Rappel", body="Bonjour {first_name}", send_at=now + timedelta(hours=1))
check("a planned reminder can be cancelled, and then sends nothing", rm.cancel(db, later) and rm.send_due(db, now + timedelta(hours=2)) == 0 and len(sent) == 2 and not rm.cancel(db, later))
check("ideas to start from are offered, each with a square", len(rm.IDEAS) >= 6 and all("[box" in item["body"] and "{first_name}" in item["body"] and tp.unknown_variables(item["subject"], item["title"], item["body"]) == [] for item in rm.IDEAS))
ideas = rm.suggestions(db)
check("a class about to start is proposed a reminder, not sent one", any("DEVOPS-RM-1 starts" in item["text"] and f"to={group.id}" in item["href"] and "idea=bring" in item["href"] for item in ideas) and len(sent) == 2, ideas)
rm.create(db, audience="class", audience_id=group.id, subject="Déjà prévu", body="Bonjour {first_name}", send_at=now + timedelta(days=1))
check("once one is planned for that class it is no longer proposed", not any("DEVOPS-RM-1 starts" in item["text"] for item in rm.suggestions(db)))

page = admin.get("/reminders")
check("the Reminders page opens and explains itself", page.status_code == 200 and "Your own reminders" in page.text and "Choose who" in page.text and "Start from an idea" in page.text and "Déjà prévu" in page.text and 'data-insert="[box gold]' in page.text, page.status_code)
check("what went and what was cancelled is listed", idea["subject"] in page.text and "delivered to 2" in page.text and "cancelled, nothing sent" in page.text)
page = admin.get(f"/reminders?audience=candidate&to={omar.id}&idea=missed")
check("a suggestion opens the form already filled", "Vous nous avez manqué" in page.text and f'<option value="{omar.id}" selected>' in page.text)
shown = admin.post("/reminders/preview", json={"subject": "Test {first_name}", "title": "", "body": "Bonjour {first_name},\n\n[box green]\nUn carré", "style": "letter", "audience": "candidate", "to": omar.id}).json()
check("the preview shows what the first recipient will read", shown["people"] == 1 and shown["subject"] == "Test Omar" and "Bonjour Omar" in shown["html"] and "border-left:4px solid #16a34a" in shown["html"] and shown["unknown"] == [], shown["subject"])
check("an unknown word in braces is said before anything is planned", admin.post("/reminders/preview", json={"subject": "s", "body": "Bonjour {prenom}"}).json()["unknown"] == ["prenom"]
      and "Unknown variable: {prenom}" in where(admin.post("/reminders/create", data={"audience": "candidate", "to_candidate": omar.id, "subject": "s", "body": "Bonjour {prenom}", "when": "now"})))
tomorrow = local_now() + timedelta(days=1)
answer = admin.post("/reminders/create", data={"audience": "program", "to_program": python.id, "subject": "Une date à retenir", "title": "", "body": "Bonjour {first_name}", "style": "brand",
                                               "when": "later", "date": tomorrow.strftime("%Y-%m-%d"), "time": "18:30"})
made = db.query(AdminReminder).filter(AdminReminder.subject == "Une date à retenir").first()
check("a reminder is planned from the page for a day and an hour", answer.status_code == 303 and "The reminder goes on" in where(answer) and "18:30" in where(answer) and made is not None and made.audience == "program"
      and abs((made.send_at - rm.to_utc(tomorrow.replace(hour=18, minute=30, second=0, microsecond=0))).total_seconds()) < 1, where(answer))
check("a time already past is refused", "already past" in where(admin.post("/reminders/create", data={"audience": "candidate", "to_candidate": omar.id, "subject": "s", "body": "b", "when": "later", "date": "2020-01-01", "time": "09:00"})))
before = len(sent)
answer = admin.post("/reminders/create", data={"audience": "candidate", "to_candidate": omar.id, "subject": "Tout de suite", "body": "Bonjour {first_name}", "when": "now"})
check("“now” goes at the next pass, within a minute", "within a minute" in where(answer) and len(sent) == before)
jobs.reminders_tick()
check("the job sends what is due and nothing else", len(sent) == before + 1 and sent[-1]["to_email"] == "omar.rm@test.local" and db.query(AdminReminder).filter(AdminReminder.subject == "Une date à retenir").first().status == "scheduled")
answer = admin.post(f"/reminders/{made.id}/cancel")
check("cancelling from the page sends nothing", "Nothing was sent" in where(answer) and len(sent) == before + 1)
check("the job is followed on the System page, and cannot be pushed from there", jobs.WATCHED.get("reminders") == "reminders_tick" and "reminders" not in jobs.RUN_NOW and any(item["key"] == "reminders" and not item["run"] for item in ss.WORK))

# ======================================================================
# C. Somebody who is not a candidate asks to register
# ======================================================================
check("the wish to register is read in French, English and Tunisian", rr.wants_to_register("", "Bonjour, je veux m'inscrire à la formation") and rr.wants_to_register("Registration", "I would like to join")
      and rr.wants_to_register("", "n7eb nsajel fi formation") and not rr.wants_to_register("Facture", "Veuillez trouver la facture du mois"))
count = db.query(Notification).filter(Notification.category == rr.CATEGORY).count()
out = ea.ingest_inbound(db, sender="amira.bensalah22@example.com", subject="Inscription", body="Bonjour, je souhaite m'inscrire à la formation DevOps. Quelles sont les dates ?", message_id="<stranger-1@example.com>")
asked = db.query(Notification).filter(Notification.category == rr.CATEGORY).order_by(Notification.created_at.desc()).first()
check("a stranger who asks for a program is told to the admin, and gets no automatic answer", out["status"] == "ignored" and out.get("request") == asked.id and asked.title == "Asks to register: DevOps"
      and "amira.bensalah22@example.com" in asked.message and db.query(Candidate).filter(Candidate.email == "amira.bensalah22@example.com").first() is None and not any(m["to_email"] == "amira.bensalah22@example.com" for m in sent), out)
check("their name is guessed from the address", asked.payload["name"] == "Amira Bensalah")
ea.ingest_inbound(db, sender="amira.bensalah22@example.com", subject="Inscription", body="Je veux m'inscrire en DevOps svp", message_id="<stranger-2@example.com>")
check("asking twice tells the admin once", db.query(Notification).filter(Notification.category == rr.CATEGORY).count() == count + 1)
for label, kw in (("a stranger who asks for nothing", dict(sender="someone@example.com", subject="Bonjour", body="Vos horaires d'ouverture ?")),
                  ("a newsletter", dict(sender="no-reply@shop.example.com", subject="Inscrivez-vous", body="Inscription à notre formation DevOps, -50% aujourd'hui")),
                  ("an automatic answer", dict(sender="x@example.com", subject="Absence", body="Je veux m'inscrire à DevOps", automatic=True)),
                  ("a wish without a program taught here", dict(sender="y@example.com", subject="", body="Je veux m'inscrire à la formation de cuisine"))):
    ea.ingest_inbound(db, **kw)
    check(f"{label} is still ignored", db.query(Notification).filter(Notification.category == rr.CATEGORY).count() == count + 1)
fix = nf.fix_for(db, asked)
check("the notification carries one button that registers them", fix is not None and fix["label"] == "Register them in DevOps" and fix["url"] == f"/candidate/requests/{asked.id}/accept" and fix["method"] == "post" and "payment reminder" in fix["confirm"], fix)
answer = admin.post(fix["url"])
db.expire_all()
amira = db.query(Candidate).filter(Candidate.email == "amira.bensalah22@example.com").first()
reg = db.query(Registration).filter(Registration.candidate_id == amira.id).all() if amira else []
check("pressing it adds the person and registers them, not paid", answer.status_code == 303 and amira is not None and amira.full_name == "Amira Bensalah" and len(reg) == 1 and reg[0].program_id == devops.id
      and reg[0].payment_status == PaymentStatus.UNPAID and reg[0].status == RegistrationStatus.PENDING and where(answer).startswith(f"/candidate/{amira.id}"), where(answer))
check("the button then disappears and pressing again doubles nothing", nf.fix_for(db, db.get(Notification, asked.id)) is None and admin.post(fix["url"]).status_code == 303 and db.query(Registration).filter(Registration.candidate_id == amira.id).count() == 1)
out = ea.ingest_inbound(db, sender="amira.bensalah22@example.com", subject="Question", body="Bonjour, la formation est-elle en ligne ?", message_id="<stranger-3@example.com>")
check("from then on their emails are read like any candidate's", out["status"] in ("received", "stored"), out)
check("a request that is not one cannot be accepted", admin.post("/candidate/requests/nope/accept").status_code == 404)

# ======================================================================
# D. The dashboard and the design
# ======================================================================
home = admin.get("/")
check("the dashboard shows the reminders: planned ones and ideas", home.status_code == 200 and 'id="live-dashboard-reminders"' in home.text and "Déjà prévu" in home.text and "New reminder" in home.text, home.status_code)
if 'id="live-dashboard-feedback"' in home.text:
    check("what candidates say comes after what the admin has to do", home.text.index('id="live-dashboard-feedback"') > home.text.index('id="live-dashboard-programs"') > home.text.index('id="live-dashboard-advice"'))
check("what needs the admin stays first", home.text.index('id="live-dashboard-advice"') < home.text.index('id="live-dashboard-reminders"') < home.text.index('id="live-dashboard-sessions"'))
check("“Programs” in the menu opens the list at once", '<a href="/programs/all" class="nav-link' in home.text and 'href="#programsCollapse"' not in home.text and 'href="/reminders" class="nav-link' in home.text)
check("the background moves softly by default", 'class="it-bg"' in home.text and "bi-code-slash" in home.text and "menu-right" not in home.text.split("<body", 1)[1].split(">", 1)[0])
answer = admin.post("/settings/appearance", data={"ui_color": "#a42c67", "ui_menu_side": "right", "ui_background": "plain"})
db.expire_all()
home = admin.get("/")
check("the menu can go to the right and the background be still", answer.status_code == 303 and get_settings(db).ui_menu_side == "right" and 'class="first-paint menu-right"' in home.text and 'class="it-bg"' not in home.text)
admin.post("/settings/appearance", data={"ui_color": "#0f766e"})
db.expire_all()
check("changing only the colour keeps the rest", get_settings(db).ui_menu_side == "right" and get_settings(db).ui_background == "plain" and get_settings(db).ui_color == "#0f766e")
page = admin.get("/settings")
check("Settings offers the colour, the side of the menu and the background", page.status_code == 200 and 'name="ui_menu_side" value="right" checked' in page.text and 'name="ui_background" value="plain" checked' in page.text)
said = cb.answer(db, "how do I add another reminder for a class?")
check("the assistant says how to plan a reminder", "Reminders" in said["reply"] and "coloured squares" in said["reply"], said["reply"][:160])
said = cb.answer(db, "is my data safe? do we have backups?")
check("and how the data is protected", "copied once a day" in said["reply"], said["reply"][:160])
check.done()
