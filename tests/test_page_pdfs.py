"""Every page as a PDF, the analysis reports, the class dynamics, the candidate's file grouped by payment,
the settings in plain words and the report by email. Runs on a COPY of the database; nothing leaves this machine."""
import io
import os
from datetime import date, datetime, timedelta

import testenv

testenv.prepare("page_pdfs")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.classsession import ClassSession
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import access, analysis, auth, class_dynamics, email_assistant as ea, email_threads, program_pdf, report_email, settings_guide
from app.services.settings_service import get_settings

ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None
db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Admin", password="pdfs-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "pdfs-test-1"})


def text_of(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


# ---- A candidate with one program of each payment state, in a class with marks and questions ----
program = db.query(Program).order_by(Program.name).first()
others = db.query(Program).filter(Program.id != program.id).order_by(Program.name).limit(2).all()
person = Candidate(full_name="Testa Folders", email="testa.folders@example.com", phone="+21620000001")
db.add(person)
db.flush()
group = ClassGroup(name="PDF-TEST-CLASS", program_id=program.id, professor_name="Prof Test", status=ClassStatus.ONGOING, total_weeks=8, current_week=3,
                   start_date=datetime.now() - timedelta(days=28))
db.add(group)
db.flush()
paid = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=400, total_price=400, paid_amount=400,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)
partial = Registration(candidate_id=person.id, program_id=others[0].id, program_name=others[0].name, total_amount=300, total_price=300, paid_amount=120,
                       payment_status=PaymentStatus.PARTIAL, status=RegistrationStatus.PENDING)
unpaid = Registration(candidate_id=person.id, program_id=others[1].id, program_name=others[1].name, total_amount=250, total_price=250, paid_amount=0,
                      payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING)
db.add_all([paid, partial, unpaid])
mate = Candidate(full_name="Mate Quiet", email="mate.quiet@example.com")
db.add(mate)
db.flush()
mate_reg = Registration(candidate_id=mate.id, program_id=program.id, program_name=program.name, total_amount=400, total_price=400, paid_amount=400,
                        payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)
db.add(mate_reg)
db.flush()
today = date.today()
sessions = [ClassSession(class_group_id=group.id, week_number=index + 1, session_date=today - timedelta(days=7 * (4 - index)), topic=f"Topic {index + 1}")
            for index in range(4)]
db.add_all(sessions)
db.flush()
# Testa comes every time and is very active; Mate came twice, then stopped, and stays quiet.
for index, session in enumerate(sessions):
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=paid.id, candidate_id=person.id, status="present", participation=3, questions=2))
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=mate_reg.id, candidate_id=mate.id,
                      status="present" if index < 2 else "absent", participation=1 if index < 2 else None, questions=0))
for week, (score_a, score_b) in enumerate(((90, 40), (80, 30)), start=1):
    db.add(Quiz(candidate_id=person.id, registration_id=paid.id, kind="weekly", class_group_id=group.id, week_number=week, sent_at=datetime.utcnow() - timedelta(days=10 - week),
                completed_at=datetime.utcnow() - timedelta(days=9 - week), score=score_a, course_name=program.name))
    db.add(Quiz(candidate_id=mate.id, registration_id=mate_reg.id, kind="weekly", class_group_id=group.id, week_number=week, sent_at=datetime.utcnow() - timedelta(days=10 - week),
                completed_at=datetime.utcnow() - timedelta(days=9 - week) if week == 1 else None, score=score_b if week == 1 else 0, course_name=program.name))
db.commit()

# ---- A. Every page as a PDF ----
expected = {
    "/dashboard/pdf": "This week, day by day", "/dashboard/analysis/pdf": "What to improve", "/candidate/all/pdf": "Every candidate",
    "/candidate/all/analysis/pdf": "Are they learning", f"/candidate/{person.id}/pdf": "Testa Folders", "/programs/pdf": "Bienvenue chez 9antra",
    "/programs/overview/pdf": "Every program", "/programs/market/pdf": "Job market", "/classes/pdf": "Every class",
    f"/classes/{group.id}/pdf": "How this class is doing", f"/classes/{group.id}/attendance/pdf": "Presence sheet", "/payments/pdf": "Who still has to pay",
    "/quiz/pdf": "Levels found", "/inbox/pdf": "Emails waiting", "/reminders/pdf": "Planned", "/website/pdf": "Formations",
    "/system/pdf": "The work it does by itself", "/settings/pdf": "What is chosen now", "/data/candidates.pdf": "Candidates", "/data/payments.pdf": "Payments",
}
texts = {}
for path, words in expected.items():
    response = admin.get(path)
    ok = response.status_code == 200 and response.headers.get("content-type", "").startswith("application/pdf") and response.content[:4] == b"%PDF"
    texts[path] = text_of(response.content) if ok else ""
    check(f"pdf: {path} opens as a PDF with “{words}”", ok and words in texts[path], response.status_code if not ok else texts[path][:200])
response = admin.get("/dashboard/pdf?download=1")
check("pdf: ?download=1 saves the file instead of showing it", response.headers.get("content-disposition", "").startswith("attachment"), response.headers.get("content-disposition"))
check("pdf: shown in the browser otherwise", admin.get("/dashboard/pdf").headers.get("content-disposition", "").startswith("inline"))
check("pdf: an unknown kind of record is 404", admin.get("/data/nothing_here.pdf").status_code == 404)
check("pdf: an unknown candidate is 404", admin.get("/candidate/no-such-person/pdf").status_code == 404)
check("pdf: an unknown class is 404", admin.get("/classes/no-such-class/pdf").status_code == 404)
file_text = texts[f"/candidate/{person.id}/pdf"]
check("candidate pdf: the three payment groups, each with its programs", "Paid in full (1)" in file_text and "Paid in part (1)" in file_text
      and "Just registered, not paid yet (1)" in file_text and others[0].name.split()[0] in file_text, file_text[:600])
check("candidate pdf: their analysis and behaviour score", "Their analysis" in file_text and "Results of the weekly questions" in file_text)
check("candidate pdf: private notes never go in it", "Your notes" not in file_text)
class_text = texts[f"/classes/{group.id}/pdf"]
check("class pdf: presence, interactivity, answers, understanding", all(word in class_text for word in ("PRESENCE", "INTERACTIVITY", "WEEKLY ANSWERS", "UNDERSTANDING")), class_text[:500])
check("class pdf: who leads and who needs a word", "Testa Folders" in class_text and "Mate Quiet" in class_text and "absent from the last 2 sessions" in class_text, class_text[-900:])
sheet = texts[f"/classes/{group.id}/attendance/pdf"]
check("presence sheet: every candidate on it, and the presence so far", "Testa Folders" in sheet and "Mate Quiet" in sheet and "Presence so far" in sheet)
welcome = texts["/programs/pdf"]
check("programs pdf: a welcome page about 9antra, how a program goes, the website", "Comment se passe une formation" in welcome and "https://9antra.tn" in welcome
      and "En savoir plus sur 9antra.tn" in welcome, welcome[:400])
links = [annotation.get_object().get("/A", {}).get("/URI") for page in PdfReader(io.BytesIO(admin.get("/programs/pdf").content)).pages
         for annotation in (page.get("/Annots") or [])]
check("programs pdf: clickable links to 9antra.tn on its pages", sum(1 for link in links if link and "9antra.tn" in link) >= 3, links[:6])
links = [annotation.get_object().get("/A", {}).get("/URI") for page in PdfReader(io.BytesIO(admin.get("/dashboard/pdf").content)).pages
         for annotation in (page.get("/Annots") or [])]
check("reports: the footer of every page links to 9antra.tn", links and all(link and "9antra.tn" in link for link in links), links[:3])
check("programs pdf: without the welcome page when asked", "Bienvenue chez 9antra" not in text_of(program_pdf.build_summary(db, None, "fr", welcome=False)))
check("catalogue: the welcome page too, and a link under each program", "Bienvenue chez 9antra" in text_of(program_pdf.build_catalogue(db, [program, others[0]], "fr")))
check("programs pdf: in English as well", "Welcome to 9antra" in text_of(admin.get("/programs/pdf?lang=en").content))

# ---- B. The PDF button of every page ----
for page, link in (("/", "/dashboard/pdf"), ("/candidate/all", "/candidate/all/pdf"), (f"/candidate/{person.id}", f"/candidate/{person.id}/pdf"),
                   ("/classes/", "/classes/pdf"), (f"/classes/{group.id}", f"/classes/{group.id}/pdf"), ("/payments", "/payments/pdf"),
                   ("/settings", "/settings/pdf"), ("/data/candidates", "/data/candidates.pdf"), ("/programs/all", "/programs/pdf")):
    html = admin.get(page).text
    check(f"button: {page} offers its PDF ({link})", "topbar-pdf" in html and f'href="{link}"' in html and f'href="{link}?download=1"' in html)
html = admin.get("/").text
check("button: the dashboard also offers the analysis and the candidates' progress", "/dashboard/analysis/pdf" in html and "/candidate/all/analysis/pdf" in html)
check("dashboard: three report cards to download", html.count('class="db-report"') == 3)
check("candidates page: a progress report button", "/candidate/all/analysis/pdf" in admin.get("/candidate/all").text)
check("button: a page without a PDF has none", 'class="topbar-pdf"' not in admin.get("/account").text)

# ---- C. Who may open which PDF ----
auth.create_user(db, email="lina.pdf@test.local", name="Lina", password="lina-pdf-1", role=auth.STAFF)
lina = db.query(User).filter(User.email == "lina.pdf@test.local").one()
lina.permissions = access.dump({"candidates": "view"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "lina.pdf@test.local", "password": "lina-pdf-1"})
check("access: a team member opens the PDFs of the parts they may see", staff.get("/candidate/all/pdf").status_code == 200 and staff.get(f"/candidate/{person.id}/pdf").status_code == 200)
for path in ("/payments/pdf", "/dashboard/pdf", "/classes/pdf", "/settings/pdf", "/dashboard/analysis/pdf", "/data/candidates.pdf"):
    check(f"access: not the others ({path})", staff.get(path, headers={"accept": "text/html"}).status_code in (303, 403))
check("access: their candidates page shows the PDF button", "topbar-pdf" in staff.get("/candidate/all").text)
prof_row = db.query(ClassGroup).filter(ClassGroup.id != group.id, ClassGroup.professor_id.isnot(None)).first()
if prof_row is not None:
    auth.create_user(db, email="prof.pdf@test.local", name="Prof", password="prof-pdf-1", role=auth.PROFESSOR, professor_id=prof_row.professor_id)
    professor = TestClient(main.app, follow_redirects=False)
    professor.post("/login", data={"email": "prof.pdf@test.local", "password": "prof-pdf-1"})
    check("access: a professor prints the presence sheet of their class", professor.get(f"/classes/{prof_row.id}/attendance/pdf").status_code == 200)
    check("access: not of another's class", professor.get(f"/classes/{group.id}/attendance/pdf", headers={"accept": "text/html"}).status_code in (303, 403))
    check("access: and no other PDF", professor.get("/dashboard/pdf", headers={"accept": "text/html"}).status_code in (303, 403))

# ---- D. The candidate's file: grouped by where the payment stands ----
from app.api.candidate import profile_data

data = profile_data(db, person.id)
groups = {item["key"]: item for item in data["payment_groups"]}
check("file: three groups, each program in its own", [len(groups[key]["cards"]) for key in ("paid", "partial", "registered")] == [1, 1, 1])
check("file: the amounts of each group", groups["partial"]["paid"] == 120 and groups["partial"]["due"] == 180 and groups["registered"]["due"] == 250)
check("file: how long a registration waits", groups["registered"]["cards"][0]["days_waiting"] is not None)
html = admin.get(f"/candidate/{person.id}").text
check("file page: paid in full shown first, then two folders", html.index('id="folder-paid"') < html.index('id="folder-partial"') < html.index('id="folder-registered"'))
check("file page: the folders are folded until opened", '<details class="cp-folder tone-warn" id="folder-partial" data-folder="partial">' in html
      and '<details class="cp-folder tone-bad" id="folder-registered" data-folder="registered">' in html)
check("file page: three tiles say where the money stands", html.count('class="cp-money-tile') == 3 and "180 DT still due" in html and "250 DT still due" in html)
check("file page: the words of the folders on each program", "Paid in part</span>" in html and "Just registered</span>" in html and "Partly paid" not in html)
check("file page: the forms still work from inside a folder", f'action="/payment/confirm/{partial.id}"' in html and f'action="/payment/confirm/{unpaid.id}"' in html)

# ---- E. How a class is doing ----
dynamics = class_dynamics.of_class(db, group)
figures = dynamics["figures"]
check("dynamics: presence over the sessions (6 of 8)", figures["attendance"] == 75, figures["attendance"])
check("dynamics: the last sessions against the first", figures["recent_attendance"] == 67 and figures["earlier_attendance"] == 100, (figures["recent_attendance"], figures["earlier_attendance"]))
check("dynamics: interactivity from what was noted (70%) and questions (30%)", figures["interactivity"] == round(0.7 * ((4 * 100 + 2 * 30) / 6) + 0.3 * min(100, 8 / 6 * 50)), figures["interactivity"])
check("dynamics: weekly answers and understanding", figures["answer_rate"] == 75 and figures["understanding"] == round((90 + 80 + 40) / 3, 1), (figures["answer_rate"], figures["understanding"]))
check("dynamics: Testa leads", [person_row["name"] for person_row in dynamics["leaders"]] == ["Testa Folders"])
check("dynamics: Mate needs a word, with why", any(item["name"] == "Mate Quiet" and "absent from the last 2" in item["why"] for item in dynamics["to_watch"]))
html = admin.get(f"/classes/{group.id}").text
check("class page: how the class is doing, at its top", "How this class is doing" in html and html.index('id="dynamics"') < html.index("live-class-focus") if "live-class-focus" in html else "How this class is doing" in html)
check("class page: the class report and the presence sheet to print", f"/classes/{group.id}/pdf" in html and f"/classes/{group.id}/attendance/pdf" in html)
empty = ClassGroup(name="PDF-EMPTY-CLASS", program_id=program.id, status=ClassStatus.NOT_STARTED)
db.add(empty)
db.commit()
check("class page: a class with nothing recorded says how the analysis starts", "The analysis starts with the first presence sheet" in admin.get(f"/classes/{empty.id}").text)

# ---- F. Analysis ----
centre = analysis.centre(db)
check("analysis: four measures compared, last 4 weeks against the 4 before", [row["label"] for row in centre["progress"]] ==
      ["Registrations", "Money received", "Presence in sessions", "Results of the weekly questions"])
check("analysis: what to improve, the most pressing first", centre["improve"] and centre["improve"][0]["tone"] in ("bad", "warn", "info", "ok"))
check("analysis: the class's own advice is in it", any("PDF-TEST-CLASS" in item["text"] for item in centre["improve"]), [item["text"][:60] for item in centre["improve"]][:8])
learners = analysis.learners(db)
states = {row["name"]: row["state"][0] for row in learners["rows"]}
check("progress: each candidate's state", states.get("Testa Folders") == "Doing well" and states.get("Mate Quiet") == "Stopped coming", states)

# ---- G. Settings in plain words ----
html = admin.get("/settings").text
check("settings: what is set, in plain words, at the top", "Your settings in plain words" in html and html.index('id="in-words"') < html.index('id="classes"'))
settings = get_settings(db)
settings.default_class_capacity = 17
db.commit()
words = " ".join(line["text"] for item in settings_guide.plain_words(db) for line in item["lines"])
check("settings: the sentences follow the values", "holds 17 candidates" in words, words[:300])
check("settings: every part says where to change it", all(item["change"].startswith("/settings") for item in settings_guide.plain_words(db)))

# ---- H. The report by email ----
sent = []
email_threads.smtp_configured = lambda: True
import app.services.email_service as email_service

email_service.send_email = lambda **kw: sent.append(kw) or True
settings.report_email_on, settings.report_since, settings.report_sent_at, settings.report_every_days, settings.report_recipients = True, None, None, 7, None
db.commit()
now = datetime.utcnow()
check("report: the first minute only starts the clock", report_email.send_if_due(db, now) is None and get_settings(db).report_since is not None and not sent)
check("report: nothing before a full period", report_email.send_if_due(db, now + timedelta(days=6, hours=23)) is None and not sent)
outcome = report_email.send_if_due(db, now + timedelta(days=7, minutes=1))
check("report: sent once the period has passed, to the admin", outcome and outcome["ok"] and [item["to_email"] for item in sent] == ["admin@test.local"], (outcome, [item.get("to_email") for item in sent]))
check("report: two PDFs attached, the analysis and the candidates' progress", sent and len(sent[0]["attachments"]) == 2
      and all(item["data"][:4] == b"%PDF" for item in sent[0]["attachments"]), [item["filename"] for item in sent[0]["attachments"]] if sent else None)
check("report: never written into a candidate's discussion", sent and sent[0]["journal"] is False)
check("report: what to improve, in the email itself", sent and "What to improve first" in sent[0]["body"])
count = len(sent)
check("report: not again right after", report_email.send_if_due(db, now + timedelta(days=7, minutes=5)) is None and len(sent) == count)
settings = get_settings(db)
check("report: days up to 60 only", report_email.choose(settings, on=True, rhythm="days", days="61", weeks="", addresses="") is not None)
check("report: weeks up to 8 only", report_email.choose(settings, on=True, rhythm="weeks", days="", weeks="9", addresses="") is not None)
check("report: 8 weeks is 56 days", report_email.choose(settings, on=True, rhythm="weeks", days="", weeks="8", addresses="") is None and settings.report_every_days == 56)
check("report: every 10 days", report_email.choose(settings, on=True, rhythm="ten", days="", weeks="", addresses="") is None and report_email.rhythm_label(settings.report_every_days) == "every 10 days")
check("report: addresses must be addresses", report_email.choose(settings, on=True, rhythm="week", days="", weeks="", addresses="not an address") is not None)
db.rollback()
response = admin.post("/settings/report", data={"report_email_on": "true", "rhythm": "days", "days": "12", "weeks": "", "recipients": "boss@example.com, team@example.com"})
settings = db.query(type(get_settings(db))).first()
db.refresh(settings)
check("report page: the choice is saved", response.status_code == 303 and settings.report_every_days == 12 and settings.report_recipients == "boss@example.com, team@example.com",
      (settings.report_every_days, settings.report_recipients))
check("report page: the recipients written are used", report_email.recipients(db) == ["boss@example.com", "team@example.com"])
response = admin.post("/settings/report", data={"report_email_on": "true", "rhythm": "days", "days": "90", "weeks": "", "recipients": ""})
check("report page: a wrong number is refused with a reason", response.status_code == 303 and "60" in response.headers.get("location", ""), response.headers.get("location"))
response = admin.post("/settings/report", data={"rhythm": "week", "days": "", "weeks": "", "recipients": ""})
db.refresh(settings)
check("report page: it can be switched off", settings.report_email_on is False and report_email.send_if_due(db, datetime.utcnow() + timedelta(days=90)) is None)
html = admin.get("/settings").text
check("settings: the report card, with its rhythms", 'id="report"' in html and "Every 10 days" in html and 'max="60"' in html and 'max="8"' in html)

db.close()
check.done()
