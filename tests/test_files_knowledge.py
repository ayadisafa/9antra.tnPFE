"""Course files checked against their program (one that does not fit waits for the admin, who accepts or moves it),
the program's own files (its plan and description taken from them), what the admin tells the assistants in any language,
reminders once, weekly, monthly or at each absence and to professors, the questions after a session written in the email,
the course of another program refused (“register first”), who may drop out, and the journal. Runs on a COPY; nothing
is sent, no site is reached (the feeds are given here)."""
import io
import json
from datetime import date, datetime, time as dtime, timedelta

import testenv

testenv.prepare("files_knowledge")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.admin_reminder import AdminReminder
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.course_material import ClassMaterial, ProgramFile
from app.models.knowledge import KnowledgeEntry
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import (auth, chatbot, course_materials, dropout, email_assistant as ea, email_composer, email_service, email_threads,
                          journal, knowledge_service, program_assistant, program_files, program_match, reminders, session_check, weekly_questions as wq)

delivered = []
email_threads._attempt = lambda to, subject, plain, *rest, **kw: (delivered.append({"to": to, "subject": subject, "text": plain}) or (None, "id"))
email_threads.smtp_configured = lambda: True
mailed = []
email_service.send_email = lambda **fields: (mailed.append(fields) or True)
ea.llm_json = lambda *a, **k: None
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="files-pass-123", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "files-pass-123"})

programs = {program.name: program for program in db.query(Program).all()}
python = next(program for name, program in programs.items() if name.startswith("Python"))
devops = next(program for name, program in programs.items() if name.startswith("DevOps"))
teacher = Professor(name="Pr Rim Fichiers", specialties="Python", email="rim.fichiers@example.com", phone="98 765 432")
db.add(teacher)
db.flush()
group = ClassGroup(name="PY-FILES", program_id=python.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.ONGOING,
                   start_date=datetime.combine(date.today() - timedelta(days=21), dtime(9, 0)), total_weeks=8, current_week=3,
                   start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat")
devops_group = ClassGroup(name="DO-FILES", program_id=devops.id, status=ClassStatus.ONGOING, total_weeks=8, current_week=2,
                          start_date=datetime.combine(date.today() - timedelta(days=10), dtime(9, 0)), start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat")
db.add_all([group, devops_group])
db.flush()
sana = Candidate(full_name="Sana Fichiers", email="sana.fichiers@example.com", phone="50 222 333")
db.add(sana)
db.flush()
registration = Registration(candidate_id=sana.id, program_id=python.id, program_name=python.name, total_amount=390, total_price=390, paid_amount=390,
                            payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow() - timedelta(days=30),
                            class_group_id=group.id)
db.add(registration)
db.commit()
auth.create_user(db, email="rim.fichiers@example.com", name=teacher.name, password="prof-pass-123", role=auth.PROFESSOR, professor_id=teacher.id)
professor = TestClient(main.app, follow_redirects=False)
professor.post("/login", data={"email": "rim.fichiers@example.com", "password": "prof-pass-123"})

DOCKER = ("Semaine 3 : Docker. Une image est un modèle ; un conteneur est une image qui tourne. Le Dockerfile décrit l'image : FROM, RUN, COPY. "
          "docker build, docker run, docker ps. Les volumes gardent les données. Docker Compose lance plusieurs conteneurs avec un fichier YAML.")
LISTES = ("Les listes en Python : append, parcourir avec une boucle for. Les tuples ne changent pas. Les dictionnaires associent une clé à une valeur. "
          "Les fonctions avec def et return. Exercice : compter les mots d'un fichier.")

# ---- A. Course files checked against the program ----
answer = professor.post(f"/classes/{group.id}/course/3/files", files={"files": ("docker.txt", DOCKER.encode("utf-8"), "text/plain")}, data={"note": ""})
db.expire_all()
waiting = db.query(ClassMaterial).filter(ClassMaterial.class_group_id == group.id, ClassMaterial.filename == "docker.txt").first()
check("a Docker course added to a Python class waits for the admin", answer.status_code == 303 and waiting is not None and waiting.review_status == "waiting"
      and waiting.fit == "other" and waiting.fit_program_id == devops.id, (waiting.review_status, waiting.fit) if waiting else None)
check("the admin is told, with where it belongs", db.query(Notification).filter(Notification.category == "COURSE_FILE").count() == 1)
check("until then nothing is learnt from it", not any(source["key"] == waiting.id for source in course_materials.sources(db, group.id, 3)))
answer = professor.post(f"/classes/{group.id}/course/3/files", files={"files": ("listes.txt", LISTES.encode("utf-8"), "text/plain")}, data={"note": ""})
db.expire_all()
fitting = db.query(ClassMaterial).filter(ClassMaterial.filename == "listes.txt").first()
check("a Python course in the Python class is used at once", fitting.review_status == "ok" and fitting.fit == "fits"
      and any(source["key"] == fitting.id for source in course_materials.sources(db, group.id, 3)))
page = professor.get(f"/classes/{group.id}/course?week=3")
check("the professor sees it waits, without the admin's buttons", "Waits for the admin" in page.text and "/accept" not in page.text)
refused = professor.post(f"/classes/{group.id}/course/files/{waiting.id}/accept")
db.expire_all()
check("a professor cannot accept it", db.get(ClassMaterial, waiting.id).review_status == "waiting")
page = admin.get(f"/classes/{group.id}/course?week=3")
check("the admin sees why and what to do", f'id="file-{waiting.id}"' in page.text and "Looks like part of" in page.text
      and f"/course/files/{waiting.id}/accept" in page.text and f"class:{devops_group.id}:" in page.text)
moved = admin.post(f"/classes/{group.id}/course/files/{waiting.id}/move", data={"target": f"class:{devops_group.id}:2"})
db.expire_all()
row = db.get(ClassMaterial, waiting.id)
check("the admin moves it to the DevOps class: used there", moved.status_code == 303 and row.class_group_id == devops_group.id and row.review_status == "ok"
      and row.week_number == 2)
off = admin.post(f"/classes/{group.id}/course/3/files", files={"files": ("tajine.txt", "Recette du tajine : oignons, poulet, œufs, persil, four trente minutes.".encode("utf-8"), "text/plain")})
db.expire_all()
tajine = db.query(ClassMaterial).filter(ClassMaterial.filename == "tajine.txt").first()
check("a file about nothing taught waits too", tajine.review_status == "waiting" and tajine.fit == "off")
admin.post(f"/classes/{group.id}/course/files/{tajine.id}/accept")
db.expire_all()
check("the admin can keep it anyway", db.get(ClassMaterial, tajine.id).review_status == "ok")

# ---- B. A program's own files ----
SYLLABUS = "\n".join([
    "Programme de la formation DevOps",
    "Description : cette formation apprend à automatiser le déploiement des applications avec Docker, Kubernetes et une chaîne CI/CD complète.",
    "Durée : 8 semaines, 6 heures par semaine",
    "Semaine 1 : Linux et Bash", "- les commandes de base", "- écrire des scripts",
    "Semaine 2 : Git et GitHub", "Semaine 3 : Docker", "- images et conteneurs", "Semaine 4 : Docker Compose",
    "Semaine 5 : Kubernetes", "Semaine 6 : CI/CD avec Jenkins", "Semaine 7 : Terraform et Ansible", "Semaine 8 : Monitoring avec Prometheus et Grafana",
])
answer = admin.post(f"/programs/{devops.id}/files", files={"files": ("programme-devops.txt", SYLLABUS.encode("utf-8"), "text/plain")})
db.expire_all()
own = db.query(ProgramFile).filter(ProgramFile.program_id == devops.id).first()
check("a syllabus added to its program is accepted", answer.status_code == 303 and own is not None and own.review_status == "ok")
said = program_files.read(own)
check("its plan is read week by week, with its points", len(said["plan"]) == 8 and said["plan"][0]["title"] == "Linux et Bash"
      and said["plan"][0]["points"] == ["les commandes de base", "écrire des scripts"], said["plan"][:2])
check("and its description, length and hours", said["description"].startswith("cette formation apprend") and said["days"] == 56 and said["hours_per_week"] == 6)
answer = admin.post(f"/programs/{python.id}/files", files={"files": ("programme-devops.txt", SYLLABUS.encode("utf-8"), "text/plain")})
db.expire_all()
astray = db.query(ProgramFile).filter(ProgramFile.program_id == python.id).first()
check("the same syllabus added to Python waits, it belongs to DevOps", astray.review_status == "waiting" and astray.fit_program_id == devops.id)
page = admin.get("/programs/expertise")
check("the page of knowledge shows each program's files and what waits", f'id="file-{astray.id}"' in page.text and "Waits for your approval" in page.text
      and "A plan of" in page.text)
admin.post(f"/programs/files/{astray.id}/move", data={"target": f"program:{devops.id}"})
db.expire_all()
check("moved to DevOps", db.get(ProgramFile, astray.id).program_id == devops.id)
admin.post(f"/programs/files/{astray.id}/delete")
before = [(week.week_number, week.topic_title) for level in devops.levels for week in level.weeks]
answer = admin.post(f"/programs/files/{own.id}/plan")
db.expire_all()
devops = db.get(Program, devops.id)
after = {week.week_number: week.topic_title for level in devops.levels for week in level.weeks}
check("the admin uses its plan: the weeks follow the file", answer.status_code == 303 and after.get(1) == "Linux et Bash" and after.get(5) == "Kubernetes")
admin.post(f"/programs/files/{own.id}/description")
db.expire_all()
check("and its description", db.get(Program, devops.id).description.startswith("cette formation apprend"))
blank = Program(name="Formation Vide Test", is_approved=True)
db.add(blank)
db.commit()
admin.post(f"/programs/{blank.id}/files", files={"files": ("programme-devops.txt", SYLLABUS.encode("utf-8"), "text/plain")})
db.expire_all()
proposal = program_assistant.open_proposal(db, blank.id)
program_files.accept(db, db.query(ProgramFile).filter(ProgramFile.program_id == blank.id).first(), "test")
program_assistant.prepare(db, proposal)
db.expire_all()
data = json.loads(proposal.data or "{}")
basis = json.loads(proposal.basis or "{}")
check("a new program's details are proposed from its file first (no model)", proposal.status == "ready" and data.get("description", "").startswith("cette formation")
      and sum(len(level["weeks"]) for level in data.get("levels", [])) == 8 and "From the file" in basis.get("curriculum", ""), (proposal.status, basis))
knowledge_service.sync_knowledge(db)
check("the assistant learns from the program's files", db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("program-file:%")).count() >= 1)

# ---- C. What the admin tells the assistants ----
fact = knowledge_service.save_fact(db, text="Le centre est fermé le 15 novembre pour la fête du centre.")
db.commit()
fact_id = fact.id
hits = knowledge_service.search(db, "le centre est-il fermé le 15 novembre ?", limit=3)
check("a fact the admin told is found", hits and hits[0]["id"] == fact.id and hits[0]["quality"] >= 0.45, [(hit["title"], hit["quality"]) for hit in hits[:2]])
knowledge_service.sync_knowledge(db)
check("it stays when the knowledge is rebuilt", db.get(KnowledgeEntry, fact.id) is not None)
user = auth.CurrentUser(id="x", name="Safa Admin", email="admin@test.local", role=auth.ADMIN)
reply = chatbot.answer(db, "retiens que la certification est envoyée une semaine après la dernière séance", user=user)
check("“retiens que …” in the assistant: kept for every assistant", "noté" in reply["reply"].lower()
      and db.query(KnowledgeEntry).filter(KnowledgeEntry.kind == "fact", KnowledgeEntry.content.like("%certification%")).count() == 1)
answer = chatbot.answer(db, "Quand est envoyée la certification ?", user=user)
check("and the assistant answers with it", "une semaine après la dernière séance" in answer["reply"], answer["reply"][:120])
page = admin.get("/inbox/knowledge")
check("the knowledge page: tell the assistants something, and what was told", 'id="facts"' in page.text and "fermé le 15 novembre" in page.text)
admin.post("/inbox/knowledge/fact", data={"text": "Les séances pratiques de Python se font sur place.", "program_id": python.id,
                                          "back": f"/programs/expertise?open={python.id}#ex-{python.id}"})
check("taught about one program, shown on its page", "se font sur place" in admin.get("/programs/expertise").text)
admin.post(f"/inbox/knowledge/fact/{fact_id}/delete", data={"back": "/inbox/knowledge#facts"})
db.expire_all()
check("forgotten when asked", db.get(KnowledgeEntry, fact_id) is None)

# ---- D. Reminders of every kind ----
delivered.clear()
weekly = reminders.create(db, audience="candidate", audience_id=sana.id, subject="Rappel de la semaine", body="Bonjour {first_name}, à samedi !",
                          repeat="weekly")
first_time = weekly.send_at
reminders.send(db, weekly)
db.expire_all()
weekly = db.get(AdminReminder, weekly.id)
check("every week: it went, and it is planned again a week later", weekly.status == "scheduled" and weekly.times == 1
      and abs((weekly.send_at - first_time) - timedelta(days=7)) < timedelta(minutes=1) and any(item["to"] == sana.email for item in delivered))
monthly = reminders.create(db, audience="candidate", audience_id=sana.id, subject="Le mois", body="Bonjour", repeat="monthly",
                           send_at=datetime(2026, 1, 31, 8, 0), until=datetime(2026, 2, 15))
reminders.send(db, monthly)
db.expire_all()
check("every month, until a last day: it stops after it", db.get(AdminReminder, monthly.id).status == "sent")
check("the same day of the next month, or its last day", reminders._next_time(datetime(2026, 1, 31, 8, 0), "monthly") == datetime(2026, 2, 28, 8, 0))
absent = reminders.create(db, audience="class", audience_id=group.id, subject="Vous nous avez manqué", body="Bonjour {first_name}, le cours vous attend.",
                          repeat="absence")
check("each time someone is absent: it waits", absent.status == "watching")
session = ClassSession(class_group_id=group.id, session_date=date.today() - timedelta(days=1), week_number=3, topic="Les listes")
db.add(session)
db.flush()
db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=registration.id, candidate_id=sana.id, status="absent",
                  marked_at=datetime.utcnow() + timedelta(seconds=1)))
db.commit()
delivered.clear()
went = reminders.answer_absences(db)
again = reminders.answer_absences(db)
check("marked absent: the candidate receives it, once", went == 1 and again == 0 and any(item["to"] == sana.email and "Vous nous avez manqué" in item["subject"] for item in delivered))
mailed.clear()
to_teachers = reminders.create(db, audience="professor", audience_id=teacher.id, subject="Préparez la semaine, {first_name}", body="Bonjour {first_name}, …")
reminders.send(db, to_teachers)
check("a reminder to a professor, by email, with their name", any(item["to_email"] == teacher.email and "Rim" in item["subject"] for item in mailed),
      [item["subject"] for item in mailed])
page = admin.get("/reminders")
check("the page: once, every week, every month, at each absence; the professors", 'name="repeat"' in page.text and 'value="absence"' in page.text
      and 'name="to_professor"' in page.text and "Waiting for an absence" in page.text)

# ---- E. The questions after a session, written in the email ----
quiz = Quiz(candidate_id=sana.id, registration_id=registration.id, course_name=f"{python.name} — séance du 24/10", kind="session", class_group_id=group.id,
            week_number=3, class_session_id=session.id, access_token="t" * 32, answers="[]", score=0.0, sent_at=datetime.utcnow(),
            questions=json.dumps([{"id": "q1", "question": "Que fait append ?", "options": ["Ajoute à la fin", "Trie", "Supprime"], "correct_answer": 0},
                                  {"id": "q2", "question": "Un tuple change-t-il ?", "options": ["Non", "Oui", "Parfois"], "correct_answer": 0}], ensure_ascii=False))
db.add(quiz)
db.commit()
wq.save_settings(db, every=1, way="auto")
overrides = session_check.questions_written(db, quiz, "http://127.0.0.1:8000")
check("this computer cannot be reached: the questions are written in the email, no button", overrides is not None
      and "Que fait append ?" in overrides["body"] and "A) Ajoute à la fin" in overrides["body"] and overrides["cta_url"] == "")
check("reachable and “automatic”: a button to the page", session_check.questions_written(db, quiz, "https://app.example.com") is None)
handled = wq.handle_reply(db, sana.id, "Re: Sana, qu'avez-vous retenu de la séance du 24/10 ?", "1-A, 2-A")
db.expire_all()
check("the reply to the session's questions is graded", handled is not None and handled["done"] and db.get(Quiz, quiz.id).score == 100.0)

# ---- F. The course of another program: register first ----
delivered.clear()
outcome = ea.ingest_inbound(db, sender=sana.email, subject="Cours DevOps", body="Bonjour, envoyez-moi le cours de DevOps de la semaine 2 svp",
                            message_id=f"<devops-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
reply = next((item for item in delivered if item["to"] == sana.email), None)
check("asked the course of a program they are not in: told to register first, alone", reply is not None and "inscrivez-vous d'abord" in reply["text"]
      and "DevOps" in reply["text"], reply["text"][:200] if reply else outcome)
check("no file of that program goes with it", not any("docker" in (item.get("text") or "").lower() for item in delivered))

# ---- G. Who may drop out ----
session2 = ClassSession(class_group_id=group.id, session_date=date.today() - timedelta(days=8), week_number=2, topic="Les fonctions")
db.add(session2)
db.flush()
db.add(Attendance(class_group_id=group.id, session_id=session2.id, registration_id=registration.id, candidate_id=sana.id, status="absent"))
for number in (1, 2):
    db.add(Quiz(candidate_id=sana.id, registration_id=registration.id, course_name=f"semaine {number}", kind="weekly", class_group_id=group.id,
                week_number=number, answers="[]", score=0.0, sent_at=datetime.utcnow() - timedelta(days=10 - number), questions="[]"))
db.commit()
found = dropout.at_risk(db)
mine = next((item for item in found if item["candidate"].id == sana.id), None)
check("absent twice in a row and questions unanswered: may drop out, with why", mine is not None and mine["score"] >= 50
      and any("Absent from the last 2 sessions" in reason for reason in mine["reasons"]), mine["reasons"] if mine else found)
page = admin.get("/")
check("on the dashboard", "Who may drop out" in page.text and "Sana Fichiers" in page.text)

# ---- H. The journal ----
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Kubernetes 1.40 brings GitOps by default</title><link>https://example.com/k8s</link><description>&lt;p&gt;The new Kubernetes adds GitOps and ArgoCD to every cluster. Helm charts too.&lt;/p&gt;</description><pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>GitOps everywhere</title><link>https://example.com/gitops</link><description>GitOps with ArgoCD and Kubernetes, for every team.</description><pubDate>Tue, 27 Oct 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Agents and RAG in production</title><link href="https://example.com/agents"/><summary>How agents use RAG and embeddings.</summary><updated>2026-10-27T08:00:00Z</updated></entry></feed>"""
check("an RSS feed is read", [item["title"] for item in journal.parse(RSS)] == ["Kubernetes 1.40 brings GitOps by default", "GitOps everywhere"])
check("an Atom feed too", journal.parse(ATOM)[0]["url"] == "https://example.com/agents")
check("a feed that declares entities is not read", journal.parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>') == [])
journal.fetch = lambda url: RSS if "kubernetes" in url or "docker" in url or "devops" in url else (ATOM if "huggingface" in url else None)
outcome = journal.read_all(db)
again = journal.read_all(db)
check("the feeds are read, each article kept once", outcome["new"] == 3 and again["new"] == 0, (outcome, again))
item = db.query(journal.NewsItem).filter(journal.NewsItem.url == "https://example.com/k8s").first()
check("briefly, with the words of its field", "gitops" in item.term_list() and "kubernetes" in item.term_list() and item.summary.startswith("The new Kubernetes"))
ideas = journal.suggestions(db)
check("what it suggests for the programs", any(idea["program"].id == devops.id for idea in ideas), [(idea["term"], idea["program"].name) for idea in ideas])
pasted = admin.post("/journal/paste", data={"text": "Une publication LinkedIn : la cybersécurité, le phishing et le ransomware frappent les PME tunisiennes.", "url": ""})
db.expire_all()
check("a LinkedIn post pasted is kept, its field found", pasted.status_code == 303 and db.query(journal.NewsItem).filter(journal.NewsItem.source == "Pasted",
                                                                                                                       journal.NewsItem.domain == "cyber").count() == 1)
page = admin.get("/journal")
check("the journal's page", page.status_code == 200 and "Kubernetes 1.40 brings GitOps by default" in page.text and "What it suggests" in page.text)
check("and on the dashboard", "The journal" in admin.get("/").text)

db.close()
check.done()
