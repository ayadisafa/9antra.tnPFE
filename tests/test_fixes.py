"""Program names as synonyms, class hours from the program, the email account, website records kept, the market
without the centre's own offers, and an assistant that reads the whole database.
Staging copy, COPY of the database; nothing leaves this machine."""
import os
import testenv
testenv.prepare("fixes")
os.environ["ENV_FILE"] = os.path.join(testenv.WORK, "fixes_env.txt")
open(os.environ["ENV_FILE"], "w", encoding="utf-8").write("# a comment\nGEMINI_API_KEY=keep-me\nSMTP_EMAIL=old@test.local\nSMTP_PASSWORD=oldpass\n")
os.environ["SMTP_EMAIL"], os.environ["SMTP_PASSWORD"] = "old@test.local", "oldpass"
check = testenv.Checks()

import smtplib
from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import (AdminReminder, Candidate, ClassGroup, ClassSession, ClassStatus, MarketOffer, Notification, Payment, Professor, Program,
                        Registration, User, UserSession, WebsiteItem)
from app.models.candidate_email import CandidateEmail
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (advisor, auth, candidate_service as cs, chatbot as cb, email_account as acc, email_assistant as ea, email_service,
                          email_templates as tp, email_threads as et, enrollment_service as es, market as mk, program_names as pn, reminders as rm,
                          website_sync as ws)
from app.services.schedule_utils import local_now

et.send_email = lambda **kw: True; tp.send_email = lambda **kw: True
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="fixes-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "fixes-test-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
programs = db.query(Program).all()
by = {program.name: program for program in programs}
ai, devops, python = by["Artificial Intelligence (AI Chatbot)"], by["DevOps"], by["Python"]

# ======================================================================
# A. Another way of writing a program's name
# ======================================================================
check("a shorter name is the same program", pn.same_program("Artificial Intelligence", programs) is ai and pn.same_program("artificial  intelligence ", programs) is ai)
check("the same words in another order, or glued, too", pn.same_program("Digital Marketing", programs) is by["Marketing Digital"] and pn.same_program("Cybersecurity", programs) is by["Cyber Security"]
      and pn.same_program("Formation DevOps", programs) is devops)
check("a name that fits several programs is nobody's", pn.same_program("Web Full Stack", programs) is None and pn.same_program("Flutter", programs) is None)
check("an exact name wins over a longer one that contains it", pn.same_program("Mobile Flutter", programs) is by["Mobile Flutter"])
check("an unknown name is not forced onto a program", pn.same_program("Comptabilité", programs) is None and pn.same_program("", programs) is None)
found = cs.resolve_program_with_transformers(db, "Artificial Intelligence")
check("a registration by that name gets the program at once, no review", found[0] is not None and found[0].id == ai.id and found[1] is False)
ahmed = Candidate(full_name="Ahmed Naji", email="ahmed.fix@test.local", phone="+21620000002"); db.add(ahmed); db.flush()
orphan = Registration(candidate_id=ahmed.id, program_id=None, program_name="Artificial Intelligence", delivery_mode="online", total_amount=0, total_price=0, paid_amount=0,
                      payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING)
vague = Registration(candidate_id=ahmed.id, program_id=None, program_name="Web Full Stack", delivery_mode="online", total_amount=0, total_price=0, paid_amount=0,
                     payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING)
db.add_all([orphan, vague]); db.flush()
review = Notification(recipient_role="admin", category="PROGRAM_MATCH", title="Program requires admin review", message="x", registration_id=orphan.id); db.add(review); db.commit()
page = admin.get(f"/candidate/{ahmed.id}").text
check("a registration without a program no longer says “Paid, waiting for a class”", "Paid, waiting for a class" not in page and "Which program is “Artificial Intelligence”?" in page)
done = pn.relink(db)
db.expire_all()
orphan, vague, review = db.get(Registration, orphan.id), db.get(Registration, vague.id), db.get(Notification, review.id)
check("it is linked to its program, with the program's price", done >= 1 and orphan.program_id == ai.id and orphan.program_name == ai.name and orphan.total_amount == ai.price_online and orphan.total_price == ai.price_online, (done, orphan.total_amount))
check("it stays not paid, and its review is closed", orphan.payment_status == PaymentStatus.UNPAID and review.is_read)
check("a name that could be two programs is left for the admin", vague.program_id is None)
check("doing it again changes nothing", pn.relink(db) == 0)
answer = admin.post(f"/candidate/registrations/{vague.id}/program", data={"program_id": by["Web Full Stack Js(Node React)"].id})
db.expire_all()
check("the admin says which program it is, on the candidate's page", answer.status_code == 303 and db.get(Registration, vague.id).program_id == by["Web Full Stack Js(Node React)"].id and db.get(Registration, vague.id).total_amount > 0 and "is now for" in where(answer), where(answer))
page = admin.get(f"/candidate/{ahmed.id}").text
check("the card then shows the price to pay", "0 / 390 DT" in page and "Which program is" not in page)

# ======================================================================
# B. A class takes its hours from its program
# ======================================================================
start = (local_now() + timedelta(days=5)).replace(hour=0, minute=0, second=0, microsecond=0)
group = ClassGroup(name="PY-HOURS-1", program_id=python.id, professor_name="Nour", status=ClassStatus.NOT_STARTED, start_date=start, total_weeks=8, capacity=12, max_capacity=12, start_mode="fixed_date")
db.add(group); db.flush()
session = ClassSession(class_group_id=group.id, week_number=1, session_date=start.date(), topic="Intro"); db.add(session)
db.add(Registration(candidate_id=ahmed.id, program_id=python.id, program_name=python.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)); db.commit()
check("the program says when it is taught", "9:00" in (python.class_session_time or ""), python.class_session_time)
before = [item.title for item in advisor.for_dashboard(db)] if hasattr(advisor, "for_dashboard") else []
filled = es.fill_session_times(db)
db.expire_all(); group = db.get(ClassGroup, group.id)
check("a class without hours takes them from its program", filled >= 1 and group.start_time == dtime(9, 0) and group.end_time == dtime(13, 0) and "sat" in (group.days_of_week or "").lower(), (group.start_time, group.end_time, group.days_of_week))
check("and so does its session", db.get(ClassSession, session.id).start_time == dtime(9, 0))
check("nothing is left to fill", es.fill_session_times(db) == 0 and db.query(ClassGroup).filter(ClassGroup.start_time.is_(None), ClassGroup.status != ClassStatus.COMPLETED, ClassGroup.program_id.isnot(None)).count() == 0)
home = admin.get("/").text
check("the dashboard no longer asks to set session times", "Set the session time" not in home)
custom = ClassGroup(name="PY-HOURS-2", program_id=python.id, status=ClassStatus.NOT_STARTED, start_date=start, total_weeks=8, capacity=12, max_capacity=12, start_time=dtime(18, 0), end_time=dtime(20, 0), days_of_week="wed")
db.add(custom); db.commit(); es.fill_session_times(db); db.expire_all()
check("hours the admin chose are never replaced", db.get(ClassGroup, custom.id).start_time == dtime(18, 0) and db.get(ClassGroup, custom.id).days_of_week == "wed")

# ======================================================================
# D. The address emails are sent from
# ======================================================================
asked = []
acc.check = lambda email, password: asked.append((email, password)) or None
check("a wrong address is refused", acc.save("not an address", "abcd") is not None and acc.save("new@test.local", "") is not None and asked == [])
check("the same address with no password changes nothing", acc.save("old@test.local", "") is None and open(os.environ["ENV_FILE"], encoding="utf-8").read().count("oldpass") == 1)
problem = acc.save("New@Test.Local", "abcd efgh ijkl mnop")
kept = open(os.environ["ENV_FILE"], encoding="utf-8").read()
check("a new address is checked with the mail server, spaces of the password removed", problem is None and asked == [("new@test.local", "abcdefghijklmnop")], asked)
check("it is written where the application reads it, the other lines untouched", "SMTP_EMAIL=new@test.local\n" in kept and "SMTP_PASSWORD=abcdefghijklmnop\n" in kept and "GEMINI_API_KEY=keep-me\n" in kept and "# a comment\n" in kept and "oldpass" not in kept)
check("and used at once, without restarting", os.environ["SMTP_EMAIL"] == "new@test.local" and email_service.SMTP_EMAIL == "new@test.local" and email_service.SMTP_PASSWORD == "abcdefghijklmnop" and et.smtp_configured())
acc.check = lambda email, password: "The mail server refused this address and password."
check("when the mail server refuses, nothing changes", acc.save("other@test.local", "wrongwrongwrong1") is not None and "other@test.local" not in open(os.environ["ENV_FILE"], encoding="utf-8").read() and os.environ["SMTP_EMAIL"] == "new@test.local")
answer = admin.post("/settings/email-account", data={"smtp_email": "other@test.local", "smtp_password": "wrongwrongwrong1"})
check("the page says why", "refused" in where(answer) and where(answer).endswith("#email"))
acc.check = lambda email, password: None
answer = admin.post("/settings/email-account", data={"smtp_email": "contact@test.local", "smtp_password": "aaaa bbbb cccc dddd"})
check("the address is changed from Settings", "Emails are now sent from contact@test.local" in where(answer) and os.environ["SMTP_EMAIL"] == "contact@test.local")
page = admin.get("/settings").text
check("Settings shows the address, the steps to get the password, and never the password", 'value="contact@test.local"' in page and "Where to get the app password" in page and "myaccount.google.com/apppasswords" in page
      and "Create an app password" in page and "aaaabbbbccccdddd" not in page and "Enable IMAP" in page)
class Refusing:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, *a): raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
import importlib; importlib.reload(acc)
acc.smtplib.SMTP_SSL = Refusing
check("a password Gmail does not accept is explained", "app password" in (acc.check("x@test.local", "bad") or "") and "steps below" in acc.check("x@test.local", "bad"))

# ======================================================================
# G. What was taken from the website stays
# ======================================================================
items = db.query(WebsiteItem).filter(WebsiteItem.gone_at.is_(None)).all()
total = db.query(WebsiteItem).count()
gone, present = items[0], items[1]
gone.gone_at = datetime.utcnow() - timedelta(days=2); db.commit()
check("something the website no longer shows is still recorded", db.query(WebsiteItem).count() == total and [item.id for item in ws.kept(db)] == [gone.id] and ws.KEEP_CHECKS >= 400)
page = admin.get("/website")
check("it is listed as kept, with a way to remove it", page.status_code == 200 and 'id="kept"' in page.text and gone.title in page.text.split('id="kept"')[1].split('id="post"')[0] and f"/website/items/{gone.id}/remove" in page.text, page.status_code)
answer = admin.post(f"/website/items/{present.id}/remove")
db.expire_all()
check("what is still on the website cannot be removed", "still on the website" in where(answer) and db.get(WebsiteItem, present.id).removed_at is None)
answer = admin.post(f"/website/items/{gone.id}/remove")
db.expire_all()
check("only the admin removes a record, and it then leaves the list", "was removed from your records" in where(answer) and db.get(WebsiteItem, gone.id).removed_at is not None and ws.kept(db) == [])
guest = TestClient(main.app, follow_redirects=False)
check("nobody else can", guest.post(f"/website/items/{present.id}/remove").status_code in (303, 401))

# ======================================================================
# H. The market: other platforms, offer by offer
# ======================================================================
check("the centre's own offers are not counted as the market", len(mk.website_offers(db)) == 9 and mk.all_offers(db) == [] and "jobicy" in mk.BOARDS and "himalayas" in mk.BOARDS)
class Answer:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data
def fake_get(url, **kw):
    if "remotive" in url: return Answer({"jobs": [{"id": 1, "title": "DevOps Engineer", "description": "Docker, Kubernetes, AWS, Terraform. English.", "tags": [], "company_name": "Acme", "candidate_required_location": "Worldwide", "url": "https://remotive.com/1", "publication_date": "2026-10-10T08:00:00"}]})
    if "arbeitnow" in url: return Answer({"data": [{"slug": "devops-berlin", "title": "DevOps Engineer (m/w/d)", "description": "Kubernetes, Helm, Azure, Linux", "tags": [], "company_name": "Beta", "location": "Berlin", "url": "https://arbeitnow.com/x", "created_at": 1791900000}] if kw.get("params", {}).get("page") == 1 else []})
    if "jobicy" in url: return Answer({"jobs": [{"id": "77", "url": "https://jobicy.com/jobs/77", "jobTitle": "Senior DevOps Engineer", "companyName": "Gamma", "jobGeo": "Europe", "jobDescription": "<p>CI/CD, Jenkins, Docker, Python scripting</p>", "pubDate": "2026-10-12T10:00:00+00:00", "jobIndustry": ["DevOps"]}]})
    if "himalayas" in url: return Answer({"jobs": [{"title": "Platform Engineer", "companyName": "Delta", "locationRestrictions": ["Tunisia", "France"], "categories": ["DevOps"], "description": "Kubernetes, ArgoCD, Terraform, GCP", "pubDate": "1791926277", "applicationLink": "https://himalayas.app/jobs/9", "guid": "https://himalayas.app/jobs/9"}] if kw.get("params", {}).get("offset") == 0 else []})
    raise AssertionError("unexpected address " + url)
mk.requests.get = fake_get
import time as _time; _time.sleep = lambda seconds: None
jobs = mk.fetch_public()
check("four platforms are read into one shape", sorted({job["source"] for job in jobs}) == ["arbeitnow", "himalayas", "jobicy", "remotive"] and all(job["id"] and job["title"] for job in jobs), [job["source"] for job in jobs])
out = mk.read_public(db)
check("their offers are kept, each under its platform", out["state"] == "read" and out["new"] == 4 and {row.source for row in db.query(MarketOffer).all()} == {"remotive", "arbeitnow", "jobicy", "himalayas"}, out)
result = mk.analysis(db, devops)
rows = {row["company"]: row for row in result["list"]}
check("each offer of the field is shown with what was read in it", result["offers"] == 4 and len(result["list"]) == 4 and rows["Delta"]["location"] == "Tunisia, France" and rows["Gamma"]["url"] == "https://jobicy.com/jobs/77"
      and "Kubernetes" in rows["Delta"]["taught"] + rows["Delta"]["missing"] and rows["Acme"]["also"] == ["English"], rows.get("Delta"))
check("what an offer asks is split into taught and missing", all(set(row["taught"]).isdisjoint(row["missing"]) for row in result["list"]) and "AWS" in rows["Acme"]["missing"] and "Docker" in rows["Acme"]["taught"], rows["Acme"])
check("the latest offer comes first", [row["company"] for row in result["list"]][0] in ("Delta", "Beta"))
detail = admin.get(f"/programs/{devops.id}").text
check("the program's page lists them one by one", 'id="offers"' in detail and "Platform Engineer" in detail and "Tunisia, France" in detail and "latest offers of this field, one by one" in detail)
market = admin.get("/programs/market").text
check("the market page no longer shows 9antra's own offers", "From 9antra.tn" not in market and "are not counted" in market and "four public job boards" in market)

# ======================================================================
# I. The assistant reads the whole database
# ======================================================================
ines = Candidate(full_name="Ines Trabelsi", email="ines.fix@test.local", phone="+21655111222", university="ESPRIT", city="Sousse", admin_notes="Prefers evening sessions"); db.add(ines); db.flush()
reg = Registration(candidate_id=ines.id, program_id=devops.id, program_name=devops.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                   payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id); db.add(reg); db.flush()
now = datetime.utcnow()
db.add_all([
    CandidateEmail(candidate_id=ines.id, direction="inbound", sender=ines.email, recipient="c@test.local", subject="Attestation de présence", body="Bonjour, pouvez-vous m'envoyer une attestation de présence pour mon université ?", status="received", requires_admin=True, received_at=now - timedelta(seconds=40), author="candidate"),
    CandidateEmail(candidate_id=ines.id, direction="outbound", sender="c@test.local", recipient=ines.email, subject="Bienvenue", body="Bonjour Ines, bienvenue dans la formation DevOps.", status="sent", received_at=now - timedelta(days=3), author="admin"),
    Payment(registration_id=reg.id, amount=390, payment_method="manual", payment_date=now - timedelta(seconds=30)),
])
teacher = Professor(name="Nour Ben Youssef", specialties="DevOps, Python", email="nour@test.local", phone="+21698000111"); db.add(teacher); db.flush()
group.professor_id, group.professor_name = teacher.id, teacher.name
db.commit()
said = cb.answer(db, "what did Ines Trabelsi write to us?")
check("it tells a candidate's emails, the latest first", said["source"] == "system" and "2 emails with Ines Trabelsi" in said["reply"] and "1 waiting for your answer" in said["reply"] and "Attestation de présence" in said["reply"] and said["reply"].index("Attestation") < said["reply"].index("Bienvenue"), said["reply"][:260])
said = cb.answer(db, "which emails are waiting for an answer?")
check("the emails that wait for the admin", "wait for your answer" in said["reply"] and "Ines Trabelsi" in said["reply"] and "attestation de présence pour mon université" in said["reply"], said["reply"][:200])
said = cb.answer(db, "emails received today")
check("the emails of a period", said["reply"].startswith("Emails today: ") and " received, " in said["reply"] and "Attestation" in said["reply"], said["reply"][:120])
said = cb.answer(db, "is there an email about attestation?")
check("the emails that mention some words", "Emails that mention “attestation”" in said["reply"] and "Ines Trabelsi" in said["reply"], said["reply"][:160])
check("and says so when none does", "No email mentions" in cb.answer(db, "any email about parachute?")["reply"])
said = cb.answer(db, "tell me about Ines Trabelsi")
check("a candidate's file: contact, payment, class, presence, last email, your note", all(part in said["reply"] for part in ("ines.fix@test.local", "ESPRIT", "Sousse", "paid in full", "PY-HOURS-1", "Last email (from them): Attestation de présence", "Prefers evening sessions")), said["reply"])
said = cb.answer(db, "who is in PY-HOURS-1 ?")
check("a class by its name: program, professor, hours, who is in it", said["reply"].startswith("PY-HOURS-1: Python, not started") and "Nour Ben Youssef" in said["reply"] and "Ines Trabelsi" in said["reply"] and "Ahmed Naji" in said["reply"] and "09:00" in said["reply"], said["reply"])
said = cb.answer(db, "what does Nour Ben Youssef teach?")
check("a professor by their name: specialities, contact, classes", "nour@test.local" in said["reply"] and "DevOps, Python" in said["reply"] and "PY-HOURS-1" in said["reply"], said["reply"])
said = cb.answer(db, "how much money did we receive today?")
check("the money received in a period, and what is still to come", said["reply"].startswith("Received today: ") and " DT in " in said["reply"] and "Still to come:" in said["reply"] and "390 DT · Ines Trabelsi · DevOps" in said["reply"], said["reply"][:160])
rm.create(db, audience="candidate", audience_id=ines.id, subject="Projet à rendre", body="Bonjour {first_name}", send_at=now + timedelta(days=1))
said = cb.answer(db, "which reminders are planned?")
check("the reminders that are planned", "1 reminder planned" in said["reply"] and "Projet à rendre" in said["reply"] and "Ines Trabelsi" in said["reply"], said["reply"])
said = cb.answer(db, "ESPRIT")
check("a word that is only in the records is searched everywhere", "Here is what the records say" in said["reply"] and "Candidate: Ines Trabelsi" in said["reply"] and "ESPRIT" in said["reply"], said["reply"][:200])
said = cb.answer(db, "55111222")
check("no word to search gives the honest answer, not an invention", said["source"] in ("none", "system") and ("I do not have the answer" in said["reply"] or "Ines" in said["reply"]))
check("earlier answers still work", "DT" in cb.answer(db, "who has not paid?")["reply"] and "DevOps" in cb.answer(db, "what is the price of DevOps?")["reply"] and "coloured squares" in cb.answer(db, "how do I add another reminder for a class?")["reply"])
check("a how-to about emails is still a how-to", cb.answer(db, "how do I answer an email?")["source"] == "guide")
# ---- what the website teaches is learned
chunks = {chunk["source_key"]: chunk for chunk in ws.knowledge_chunks(db)}
courses = [chunk for key, chunk in chunks.items() if key.startswith("website:formation-content:")]
check("each formation of the website is learned with its weeks and its videos", len(courses) >= 8 and all("semaine 1" in chunk["content"] for chunk in courses) and any("Vidéo : «" in chunk["content"] for chunk in courses), len(courses))
flutter = max(courses, key=lambda chunk: len(chunk["content"]))
check("with what each week covers", "Cours, semaine 1" in flutter["content"] and "Encadrement, semaine 1" in flutter["content"] and any("en accès libre" in chunk["content"] for chunk in courses) and len(flutter["content"]) <= 7000, flutter["content"][:200])
check.done()
