"""The batch of 2 November (2): words with mistakes understood, talking with the email assistant about its answers (why,
correct, send), professors' answers reworded and learnt (also by email), one agent per program (born with the program)
and Yasmine asking the other agents, Yasmine changed by asking her, every word translated, and what needs you alive.
Runs on a COPY; no mail leaves this machine."""
import json
import os
from datetime import datetime, timedelta, time as dtime

import testenv

testenv.prepare("yasmine_agents")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.knowledge import KnowledgeEntry
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.services import (auth, course_questions, email_assistant as ea, email_templates as tp, email_threads as et, friend, orchestrator,
                          program_agents, spelling, team_messages, translations, whats_new)

sent = []


def fake_send(**kw):
    sent.append(kw)
    return True


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="yasmine-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "yasmine-test-1"})


def ask(text):
    return orchestrator.dispatch_chat(db, text)


def fresh(message_id):
    db.expire_all()
    return db.query(CandidateEmail).filter(CandidateEmail.message_id == message_id).one()


program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()

# ======================================================================
# A. Words written with mistakes
# ======================================================================
fixed, fixes = spelling.correct("Bonjour, je veux faire une inscripton à la formtion pyhton, combein le paimant ?")
check("spelling: each word put right", fixed == "Bonjour, je veux faire une inscription à la formation python, combien le paiement ?", fixed)
check("spelling: real words, names and emails are left alone", spelling.correct("merci pour la pression du lien, amira.inscripton@gmail.com")[1] == [])
analysis = ea.analyse("", "combein coute la formtion devops ?")
check("email: misspelt words are understood (the subject is the price)", analysis["intent"] == "pricing" and analysis["fixes"], (analysis["intent"], analysis["fixes"]))
answer = ask("combein coute la formtion pyhton ?")
check("Yasmine: says how she read it", answer.get("understood") and answer["understood"][0]["read"] in ("combien", "formation", "python"), answer.get("understood"))

# ======================================================================
# B. Talking with the email assistant about its answer
# ======================================================================
person = Candidate(full_name="Rania Review", email="rania.review@example.com", phone="50 777 888")
db.add(person)
db.flush()
thread = et.open_thread(db, person.id, "Prix")
asked = et.record_message(db, thread, direction="inbound", author="candidate", message_id="<review-1@example.com>", sender=person.email,
                          recipient="9antra", subject="Prix", body="Combien coûte la formation DevOps ?", status="replied")
asked.question = "Combien coûte la formation DevOps ?"
asked.sources = json.dumps({"threshold": 0.7, "method": "search", "hits": [{"title": "Prix des formations", "kind": "program", "origin": "system", "score": 0.9}]})
reply = et.record_message(db, thread, direction="outbound", author="system", message_id="<review-2@example.com>", sender="9antra",
                          recipient=person.email, subject="Re: Prix", body="Bonjour Rania, la formation DevOps coûte 300 DT.", status="sent")
reply.intent, reply.confidence = "reply", 0.82
reply.received_at = asked.received_at + timedelta(minutes=1)
db.commit()
page = admin.get(f"/inbox?thread={thread.id}").text
check("inbox: under the assistant's answer, “ask why, or correct it”", f'id="review-{reply.id}"' in page and f'/inbox/message/{reply.id}/review' in page)
said = admin.post(f"/inbox/message/{reply.id}/review", json={"text": "Pourquoi ?"}).json()
check("why: where the answer came from, how sure it was, and why it went alone", "82" in said["reply"] and "70" in said["reply"] and "Prix des formations" in said["reply"],
      said["reply"])
said = admin.post(f"/inbox/message/{reply.id}/review", json={"text": "La bonne réponse est : la formation DevOps coûte 390 DT en ligne."}).json()
db.expire_all()
learnt = db.query(KnowledgeEntry).filter(KnowledgeEntry.title == "Combien coûte la formation DevOps ?").first()
check("correct: the right answer is learnt for next time, the answer marked wrong", learnt is not None and "390" in learnt.content
      and db.get(CandidateEmail, reply.id).admin_score == -1 and said["changed"], said["reply"])
before = len(sent)
said = admin.post(f"/inbox/message/{reply.id}/review", json={"text": "Envoyer"}).json()
check("send it: the corrected answer goes to the candidate", len(sent) == before + 1 and "390" in json.dumps(sent[-1], ensure_ascii=False), said["reply"])
said = admin.post(f"/inbox/message/{reply.id}/review", json={"text": "C'était juste"}).json()
db.expire_all()
check("it was right: marked right", db.get(CandidateEmail, reply.id).admin_score == 1, said["reply"])
answer = ask("pourquoi tu as répondu ça à Rania ?")
check("Yasmine: “why did you answer Rania that way?”", answer["agent"]["key"] == "inbox" and "Rania" in answer["reply"], answer["reply"][:160])

# ======================================================================
# C. A technical question goes to the professor; the answer is reworded and learnt
# ======================================================================
professor = Professor(name="Karim Prof", email="karim.prof@example.com", phone="50 999 111", specialties=program.name)
db.add(professor)
db.flush()
group = ClassGroup(name="YAS-1", program_id=program.id, professor_id=professor.id, status=ClassStatus.ONGOING, start_date=datetime.utcnow() - timedelta(days=20),
                   total_weeks=8, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group)
db.flush()
learner = Candidate(full_name="Sami Learner", email="sami.learner@example.com")
db.add(learner)
db.flush()
db.add(Registration(candidate_id=learner.id, program_id=program.id, program_name=program.name, class_group_id=group.id, delivery_mode="online",
                    total_amount=390, total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED))
db.commit()
ea.ingest_inbound(db, sender=learner.email, subject="Erreur", body="Bonjour, j'ai une erreur quand je lance docker compose, le conteneur ne démarre pas.",
                  message_id="<tech-1@example.com>")
question = fresh("<tech-1@example.com>")
waiting = course_questions.waiting_for(db, f"professor:{professor.id}")
check("a technical question goes to the professor", question.status == "no_reply" and len(waiting) == 1, (question.status, question.analysis))
check("…by email too", any("karim.prof@example.com" in str(item.get("to") or item.get("recipient") or item) for item in sent))
told = db.query(CandidateEmail).filter(CandidateEmail.thread_id == question.thread_id, CandidateEmail.direction == "outbound").order_by(CandidateEmail.received_at.desc()).first()
check("the candidate is given the professor's email", told is not None and "karim.prof@example.com" in told.body, told.body if told else None)
before = len(sent)
reply_text = "Il faut lancer docker compose down puis up -d, et vérifier le port 5432."
team_messages.from_email(db, sender=professor.email, subject="Re: Message — 9antra", body=f"{reply_text}\n\nLe lun. 2 nov. 2026, 9antra a écrit :\n> Question de Sami")
db.expire_all()
answered = db.query(CandidateEmail).filter(CandidateEmail.thread_id == question.thread_id, CandidateEmail.author == "professor:Karim Prof").first()
check("the professor answers by email: it goes to the candidate, without the quoted part", answered is not None and "5432" in answered.body
      and "a écrit" not in answered.body, answered.body if answered else None)
check("…and it is learnt for next time", db.query(KnowledgeEntry).filter(KnowledgeEntry.origin == "learned", KnowledgeEntry.content.contains("5432")).count() == 1)
ea.ingest_inbound(db, sender=learner.email, subject="Question", body="J'ai une erreur dans le pipeline yaml, il ne passe pas.", message_id="<tech-2@example.com>")
ea.ingest_inbound(db, sender=learner.email, subject="Question 2", body="J'ai une erreur dans le Dockerfile, ligne 3.", message_id="<tech-3@example.com>")
before = db.query(CandidateEmail).filter(CandidateEmail.author == "professor:Karim Prof").count()
team_messages.from_email(db, sender=professor.email, subject="Re: Message", body="Je regarde ça demain.")
check("an email that does not reply to the question is not taken as its answer", db.query(CandidateEmail).filter(CandidateEmail.author == "professor:Karim Prof").count() == before)
team_messages.from_email(db, sender=professor.email, subject="Re: Message", body="Relance le pipeline.\n\nLe lun. 2 nov. 2026, 9antra a écrit :\n> Question de Sami Learner")
check("with two questions waiting, a reply is not taken as an answer to either", db.query(CandidateEmail).filter(CandidateEmail.author == "professor:Karim Prof").count() == before
      and len(course_questions.waiting_for(db, f"professor:{professor.id}")) == 2)
page_rows = course_questions.waiting_for(db, f"professor:{professor.id}")
course_questions.answer(db, page_rows[0], "regarde la doc yaml, section stages", professor_name="Karim Prof", reword=True)
check("answering in the application, reworded when the model is there (else the professor's words)", db.query(CandidateEmail).filter(CandidateEmail.author == "professor:Karim Prof").count() == before + 1)

# ======================================================================
# D. One agent per program, born with the program; Yasmine asks the others
# ======================================================================
agents = program_agents.agents(db)
check("one agent per program", len(agents) == db.query(Program).count() and all(agent["name"].endswith("agent") for agent in agents))
born = Program(name="Rust Systems", description="Systems programming with Rust", price_online=450, price_onsite=550)
db.add(born)
db.commit()
check("a new program: its agent is born", any(agent["program"].id == born.id for agent in program_agents.agents(db)))
answer = ask(f"quand commence {program.name} ?")
check("a question about one program goes to its agent", answer["agent"]["key"] == program_agents.key_of(program), answer["agent"])
answer = ask("combien coûte Rust Systems ?")
check("…the new program's agent answers its price", answer["agent"]["key"] == program_agents.key_of(born) and "450" in answer["reply"], (answer["agent"], answer["reply"][:120]))
page = admin.get("/assistant/agents").text
check("the agents page: every program's agent, and what was learnt", 'id="program-agents"' in page and page.count('class="ag-program"') == db.query(Program).count()
      and "Rust Systems" in page)

# ======================================================================
# E. Yasmine changed by asking her
# ======================================================================
ask("call yourself Salma")
check("“call yourself Salma”", friend.look(db)["name"] == "Salma")
ask("mets des lunettes")
check("“mets des lunettes”", friend.look(db)["glasses"] is True)
ask("enlève tes lunettes")
check("“enlève tes lunettes”", friend.look(db)["glasses"] is False)
ask("prends une voix d'homme")
check("“prends une voix d'homme”", friend.look(db)["voice"] == "male")
ask("porte un blazer")
check("“porte un blazer”", friend.look(db)["outfit"] == "blazer")
ask("annule")
check("“annule” puts the last one back", friend.look(db)["outfit"] != "blazer")
home = admin.get("/").text
check("her window: her settings one click away, and what she can do", 'href="/settings#character"' in home and 'class="assistant-can"' in home and 'id="aw-asked"' in home)

# ======================================================================
# F. Every word translated; G. what needs you, alive
# ======================================================================
check("translations: pieces of code are not words", not translations._worth("a, button, input, select, textarea") and not translations._worth("text-muted small")
      and not translations._worth("0px 0px -6% 0px") and translations._worth("Set-aside messages") and translations._worth("a free one-hour workshop"))
check("translations: the local model translates more at a time", translations.BATCH >= 25)
translations.ensure(db)
french = translations.dictionary(db, "fr")["t"]
check("translations: this batch's words are in French", french.get("Ask why, or correct it in a few words") and french.get("One agent per program"))
motion = open(os.path.join(testenv.PROJECT, "app", "templates", "_motion.html"), encoding="utf-8").read()
check("alive: what needs you breathes, figures count up, the main button catches the light", "moNeed" in motion and "data-count" in motion and "moShine" in motion)
check("what's new: this batch first", whats_new.latest()["key"] == "2026-11-02b")

db.close()
check.done()
