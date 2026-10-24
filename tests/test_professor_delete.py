"""Deleting a professor: the dialog says what will happen (each class with who could take it), a class goes only to a
professor who teaches its program and is free at its hours (else nothing changes), the others wait for one; their
account, sessions, absences, team conversation, announcements and reminders go; a question waiting for them comes back
to the inbox; a finished class keeps their name; the data pages delete the same way; a team member who can only look
cannot delete; the guide knows how. Runs on a COPY; email, WhatsApp and SMS are fakes."""
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("professor_delete")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.edit_log import EditLog
from app.models.email_thread import EmailThread
from app.models.professor_absence import ProfessorAbsence
from app.models.resource_reminder import ResourceReminder
from app.models.team_message import TeamMessage
from app.services import access, auth, data_editor, email_threads as et, howto, professor_announcements as pa, professor_removal

et._attempt = lambda *args, **kwargs: (None, False)
et.smtp_configured = lambda: True


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="delete-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "delete-test-1"})

program = db.query(Program).order_by(Program.name).first()
start = datetime.combine(date.today() - timedelta(days=20), dtime(9, 0))


def professor(name, teaches=True):
    row = Professor(name=name, specialties=program.name if teaches else "Cooking", email=f"{name.split()[0].lower()}@example.com")
    db.add(row)
    db.flush()
    return row


def group(name, who, day="sat", hours=(9, 13), status=ClassStatus.ONGOING):
    first = start - timedelta(days=120) if status == ClassStatus.COMPLETED else start
    row = ClassGroup(name=name, program_id=program.id, professor_id=who.id if who else None, professor_name=who.name if who else None,
                     status=status, start_date=first, total_weeks=8, start_time=dtime(hours[0], 0), end_time=dtime(hours[1], 0), days_of_week=day)
    db.add(row)
    db.flush()
    return row


leaving = professor("Leila Partante")
free = professor("Bilel Libre")
busy = professor("Chiraz Occupee")
other_field = professor("Dora Cuisine", teaches=False)
k1 = group("DEL-K1", leaving)
k2 = group("DEL-K2", leaving, day="mon", hours=(9, 12))
k0 = group("DEL-K0", leaving, status=ClassStatus.COMPLETED)
kc = group("DEL-KC", busy)
db.add(ProfessorAbsence(professor_id=leaving.id, absence_date=date.today() + timedelta(days=3), reason="Congé"))
db.add(ResourceReminder(class_group_id=k1.id, week_number=3, professor_id=leaving.id))
candidate = Candidate(full_name="Nour Question", email="nour.question@example.com", phone="50 111 222")
db.add(candidate)
db.flush()
thread = EmailThread(candidate_id=candidate.id, subject="Une question sur le cours")
db.add(thread)
db.flush()
asked = CandidateEmail(candidate_id=candidate.id, thread_id=thread.id, direction="inbound", sender=candidate.email, recipient="centre@example.com",
                       body="Comment marche une boucle for ?", status="forwarded", requires_admin=False)
db.add(asked)
db.flush()
db.add(TeamMessage(person=f"professor:{leaving.id}", direction="out", channel="app", body="Question de Nour Question …", author="The assistant",
                   question_message_id=asked.id, question_candidate_id=candidate.id))
db.add(TeamMessage(person=f"professor:{leaving.id}", direction="in", channel="app", body="Bonjour", status="received"))
db.commit()
leaving_id = leaving.id
auth.create_user(db, email="leila.login@example.com", name=leaving.name, password="leila-prof-12", role=auth.PROFESSOR, professor_id=leaving.id)
prof_client = TestClient(main.app, follow_redirects=False)
prof_client.post("/login", data={"email": "leila.login@example.com", "password": "leila-prof-12"})
account = db.query(User).filter(User.professor_id == leaving.id).one()
account_id = account.id
check("setup: the professor has an account, a session and an announcement", db.query(UserSession).filter(UserSession.user_id == account.id).count() >= 1
      and db.query(pa.ProfessorAnnouncement).filter(pa.ProfessorAnnouncement.professor_id == leaving.id).count() >= 1)

# ---- A. The page and the dialog ----
page = admin.get("/classes/?tab=professors").text
check("page: each professor card has its bin button and the dialog", f'data-delete-professor="{leaving_id}"' in page and 'id="deleteProfessorModal"' in page)
dialog = admin.get(f"/classes/professors/{leaving_id}/delete")
text = dialog.text
k1_part = text[text.index("DEL-K1"):text.index("DEL-K2")]
check("dialog: opens with their classes still running (not the finished one)", dialog.status_code == 200 and "DEL-K1" in text and "DEL-K2" in text and "DEL-K0" not in text)
check("dialog: offers who teaches the program and is free, not the busy one, not another field, not themselves",
      free.name in k1_part and busy.name not in k1_part and other_field.name not in k1_part and f'value="{leaving_id}"' not in text)
check("dialog: says their access goes and a question comes back", "Their access is removed" in text and "come back to your inbox" in text and "Delete for good" in text)

# ---- B. A class goes only to someone who can take it; otherwise nothing changes ----
response = admin.post(f"/classes/professors/{leaving_id}/delete", data={f"give_{k1.id}": other_field.id})
db.expire_all()
check("refused: someone who does not teach the program", "does not teach" in where(response) and db.get(Professor, leaving_id) is not None
      and db.get(ClassGroup, k1.id).professor_id == leaving_id, where(response))
response = admin.post(f"/classes/professors/{leaving_id}/delete", data={f"give_{k1.id}": busy.id})
db.expire_all()
check("refused: someone already teaching at those hours", "already teaches" in where(response) and db.get(Professor, leaving_id) is not None
      and db.get(ClassGroup, k1.id).professor_id == leaving_id, where(response))

# ---- C. A team member who can only look cannot delete ----
auth.create_user(db, email="lea.view@test.local", name="Lea View", password="lea-view-123", role=auth.STAFF)
lea = db.query(User).filter(User.email == "lea.view@test.local").one()
lea.permissions = access.dump({"classes": "view"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "lea.view@test.local", "password": "lea-view-123"})
response = staff.post(f"/classes/professors/{leaving_id}/delete", data={})
db.expire_all()
check("access: looking only cannot delete", response.status_code in (303, 403) and db.get(Professor, leaving_id) is not None, response.status_code)

# ---- D. The removal ----
response = admin.post(f"/classes/professors/{leaving_id}/delete", data={f"give_{k1.id}": free.id, f"give_{k2.id}": ""})
said = where(response)
db.expire_all()
check("deleted: back on the Professors tab, saying what happened", response.status_code == 303 and "tab=professors" in said and "Leila Partante is deleted" in said
      and "DEL-K1 now goes to Bilel Libre" in said and "Waiting for a professor: DEL-K2" in said and "access is removed" in said, said)
check("deleted: the professor is gone", db.get(Professor, leaving_id) is None)
check("classes: the one handed over has its new professor", db.get(ClassGroup, k1.id).professor_id == free.id and db.get(ClassGroup, k1.id).professor_name == free.name)
check("classes: the other waits for one", db.get(ClassGroup, k2.id).professor_id is None and db.get(ClassGroup, k2.id).professor_name is None)
check("classes: the finished one keeps the name of who taught it", db.get(ClassGroup, k0.id).professor_id is None and db.get(ClassGroup, k0.id).professor_name == "Leila Partante")
check("account: gone, and signed out everywhere", db.query(User).filter(User.email == "leila.login@example.com").first() is None
      and db.query(UserSession).filter(UserSession.user_id == account_id).count() == 0 and prof_client.get("/account/messages").status_code in (303, 401))
check("records: absences, team conversation, announcements and reminders gone",
      db.query(ProfessorAbsence).filter(ProfessorAbsence.professor_id == leaving_id).count() == 0
      and db.query(TeamMessage).filter(TeamMessage.person == f"professor:{leaving_id}").count() == 0
      and db.query(pa.ProfessorAnnouncement).filter(pa.ProfessorAnnouncement.professor_id == leaving_id).count() == 0
      and db.query(ResourceReminder).filter(ResourceReminder.professor_id == leaving_id).count() == 0)
check("announcement: the new professor will be told of the class", db.query(pa.ProfessorAnnouncement).filter(
    pa.ProfessorAnnouncement.professor_id == free.id, pa.ProfessorAnnouncement.class_group_id == k1.id).count() == 1)
check("question: back in the inbox for the admin", db.get(CandidateEmail, asked.id).requires_admin and db.get(EmailThread, thread.id).needs_admin
      and db.query(Notification).filter(Notification.title == "Candidate questions are back in your inbox").count() == 1)
log = db.query(EditLog).filter(EditLog.table == "professors", EditLog.row_id == leaving_id, EditLog.action == "delete").first()
check("log: who deleted whom, and where the classes went", log is not None and log.who == "Safa Admin" and "DEL-K1" in (log.changes or ""), log.changes if log else None)
page = admin.get("/classes/?tab=professors").text
check("page: the card is gone, the waiting class says it has no professor", f'data-delete-professor="{leaving_id}"' not in page and "DEL-K2" in page)

# ---- E. Two classes at the same hours to the same professor: the second is refused, nothing changes ----
twice = professor("Emna Double")
taker = professor("Fares Preneur")
x1 = group("DEL-X1", twice, hours=(15, 17))
x2 = group("DEL-X2", twice, hours=(15, 17))
db.commit()
twice_id, x1_id, x2_id = twice.id, x1.id, x2.id
response = admin.post(f"/classes/professors/{twice.id}/delete", data={f"give_{x1.id}": taker.id, f"give_{x2.id}": taker.id})
db.expire_all()
check("refused: the same professor twice at the same hours, and nothing changed", "already teaches" in where(response)
      and db.get(Professor, twice.id) is not None and db.get(ClassGroup, x1.id).professor_id == twice.id, where(response))

# ---- F. The data pages delete the same way ----
problem = data_editor.remove(db, "professors", db.get(Professor, twice_id), "Safa Admin")
db.expire_all()
check("data pages: deleted, their classes wait for a professor", problem is None and db.get(Professor, twice_id) is None
      and db.get(ClassGroup, x1_id).professor_id is None and db.get(ClassGroup, x2_id).professor_name is None, problem)
nobody = professor("Ghada Sans Classe")
db.commit()
nobody_id = nobody.id
done = professor_removal.remove(db, nobody, {}, who="test")
check("a professor with no class: deleted, nothing else touched", done["given"] == [] and done["waiting"] == [] and not done["account"]
      and db.get(Professor, nobody_id) is None)

# ---- G. The guide knows how ----
for question in ("how do I delete a professor", "supprimer un formateur", "nheb nfasa5 el prof"):
    found = howto.task_for(question, None, by_meaning=False)
    check(f"guide: “{question}”", found is not None and found.get("key") == "professor-delete", found.get("key") if found else None)
found = howto.task_for("remove professor access", None, by_meaning=False)
check("guide: removing their access is still the access task", found is not None and found.get("key") == "professor-access", found.get("key") if found else None)

db.close()
check.done()
