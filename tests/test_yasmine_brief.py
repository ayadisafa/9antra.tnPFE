"""Yasmine sums things up ("résume…"), answers short first (the whole answer one tap away), and each answer comes with
its moving picture (a journal photo for the news). Runs on a COPY of the database; nothing leaves this machine."""
import testenv
DB = testenv.prepare("yasmine_brief")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

from datetime import datetime, timedelta
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.news_item import NewsItem
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import auth, brief, email_assistant as ea, email_threads as et, orchestrator, pictures, summarize

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None
db = SessionLocal()
now = datetime.utcnow()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.yb@example.com", name="Yb Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.yb@example.com", "password": "correct horse battery"})


def ask(message, history=None, path="/", lang="fr"):
    response = client.post("/assistant/chat", json={"message": message, "history": history or [], "lang": lang, "path": path})
    return response.json() if response.status_code == 200 else {"reply": "", "status": response.status_code}


# ======================================================================
# A. What asks for a summary
# ======================================================================
check("asked in every way", all(summarize.wants(text) for text in ("Résume la journée", "resume moi Fares", "Summarize this page", "sum it up",
                                                                     "a7kili lkhoulasa", "لخص اليوم", "en bref, la semaine ?")))
check("...and only then", not any(summarize.wants(text) for text in ("Qui a payé ?", "Combien de candidats ?", "Quel est le prix ?")))
text = ("La formation DevOps commence lundi à 18h. Elle dure 12 semaines en ligne. Les candidats apprennent Docker et Kubernetes. "
        "Docker sert à emballer une application. Le prix est de 390 DT. Il fait beau aujourd'hui.")
kept = summarize.gist(text)
check("a text: its sentences that say the most, never rewritten, in their order", len(summarize.sentences(kept)) == 3
      and all(sentence in text for sentence in summarize.sentences(kept)) and kept.startswith("La formation DevOps") and "beau" not in kept, kept)

# ======================================================================
# B. Short first
# ======================================================================
listed = ("Voici les candidats qui doivent encore payer cette semaine :\n- Ali Ben Salah : 200 DT, inscrit en DevOps\n- Sami Trabelsi : 150 DT, inscrit en Python\n"
          "- Ines Gharbi : 90 DT, inscrite en IA\n- Nour Ben Youssef : 300 DT, inscrite en Flutter\n- Rami Jaziri : 20 DT, inscrit en marketing")
short = brief.shorten(listed)
check("a list: its first line, the first names, and how many more", short == "Voici les candidats qui doivent encore payer cette semaine : Ali Ben Salah, Sami Trabelsi, Ines Gharbi… +2", short)
long_text = ("Les inscriptions de cette semaine sont nombreuses et la plupart des candidats ont choisi la formation DevOps qui commence lundi "
             "prochain à 18h. Trois candidats ont déjà payé la totalité. Deux autres ont payé une partie et recevront un rappel demain. "
             "Le formateur a confirmé sa présence pour la première séance et le lien de la réunion partira une heure avant.")
short = brief.shorten(long_text)
check("a long text: its first sentences, cut from itself", short and long_text.startswith(short.rstrip("…")) and len(short) <= brief.SHORT + 50, short)
check("a short answer has no short version (nothing shown twice)", brief.shorten("Trois candidats ont payé.") is None)

# ======================================================================
# C. Summing up from the records
# ======================================================================
program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
group = ClassGroup(name="YB-DEVOPS-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=now - timedelta(days=20), total_weeks=8,
                   current_week=3, start_mode="fixed_date", capacity=10, max_capacity=10, professor_name="Hela Prof")
db.add(group); db.flush()
person = Candidate(full_name="Yassine Brief", email="yassine.brief@example.com", phone="+216 50 999 111"); db.add(person); db.flush()
db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390,
                    paid_amount=200, payment_status=PaymentStatus.PARTIAL if hasattr(PaymentStatus, "PARTIAL") else PaymentStatus.UNPAID,
                    status=RegistrationStatus.ASSIGNED, registration_date=now - timedelta(days=25), class_group_id=group.id))
db.commit()
ea.ingest_inbound(db, sender=person.email, subject="Horaires", body="Bonjour, à quelle heure commence la séance de samedi ?", message_id="<yb-1@example.com>")
db.commit()

said = ask("Résume Yassine Brief")
check("a candidate: their programs, what is left to pay, their last message", said.get("agent", {}).get("key") == "summary"
      and "Yassine Brief" in said["reply"] and "190 DT" in said["reply"] and "Dernier message" in said["reply"], said.get("reply"))
check("...with the animated picture of candidates and the place to open", (said.get("picture") or {}).get("scene") == "candidates"
      and "<svg" in (said.get("picture") or {}).get("svg", "") and any(f"/candidate/{person.id}" == link["url"] for link in said.get("links") or []))
said = ask("Résume la discussion avec Yassine Brief")
check("their discussion: how many messages, what they asked, what waits", "messages avec Yassine Brief" in said["reply"]
      and (said.get("picture") or {}).get("scene") == "inbox", said.get("reply"))
said = ask("Résume la classe YB-DEVOPS-1")
check("a class: candidates, week, professor", "YB-DEVOPS-1" in said["reply"] and "semaine 3/8" in said["reply"] and "Hela Prof" in said["reply"]
      and (said.get("picture") or {}).get("scene") == "classes", said.get("reply"))
said = ask(f"Résume la formation {program.name}")
check("a program: its weeks, what it covers, who follows it", program.name in said["reply"] and "inscrit" in said["reply"]
      and (said.get("picture") or {}).get("scene") == "programs", said.get("reply"))
said = ask("Résume la journée")
check("the day: what came in and went out, and what waits", said["reply"].startswith("Aujourd'hui") and "À faire" in said["reply"]
      and (said.get("picture") or {}).get("scene") == "dashboard", said.get("reply"))
said = ask("Summarize the week", lang="en")
check("the week, in English", said["reply"].startswith("These 7 days") and "To do" in said["reply"], said.get("reply"))
said = ask("Résume cette page", path="/inbox")
check("the page that is open: what it is for and its parts", said["reply"].startswith("Cette page") and (said.get("picture") or {}).get("scene") == "look", said.get("reply"))
said = ask("Résume : " + text)
check("a text pasted after the word: in short", said["reply"].startswith("En bref :") and "Docker" in said["reply"] and "beau" not in said["reply"], said.get("reply"))
said = ask("Résume ta réponse", history=[{"role": "user", "content": "Explique"}, {"role": "assistant", "content": long_text}])
check("her own last answer, shorter", said["reply"].startswith("En bref :") and len(said["reply"]) < len(long_text), said.get("reply"))
said = ask("Résume")
check("nothing named: she asks what, with ideas", "Que voulez-vous que je résume" in said["reply"] and said.get("suggestions"), said.get("reply"))

# ======================================================================
# D. Every answer: short first, and its picture
# ======================================================================
said = ask("Résume la discussion avec Yassine Brief")
check("an answer says if it has a short version", "short" in said)
said = ask("Ça va ?")
check("small talk: no picture, she is just talking", said.get("picture") is None, said.get("picture"))
db.query(NewsItem).delete()
db.add(NewsItem(domain="ai", source="Test", title="Agents take over customer support", url="https://news.example/agents", summary="Agents.",
                image_url="https://img.example/agents.jpg", published_at=now, fetched_at=now))
db.commit()
picture = pictures.for_answer(db, {"reply": "Les agents prennent le support client."}, "trends", "trends")
check("the news: a real photo from the journal, with its title and link", picture == {"photo": "https://img.example/agents.jpg", "caption": "Agents take over customer support",
                                                                                     "url": "https://news.example/agents"}, picture)
check("any other subject: its animated picture", pictures.for_answer(db, {"reply": "x"}, "payment", "payment")["scene"] == "payments")
check("the figures agent: the picture of what the answer speaks of", pictures.for_answer(db, {"reply": "4 candidates have not paid in full, 9 000 DT in all."}, "records", "general")["scene"] == "payments"
      and pictures.for_answer(db, {"reply": "La prochaine séance est samedi."}, "records", "general")["scene"] == "sessions"
      and pictures.for_answer(db, {"reply": "Bonjour."}, "records", "general")["scene"] == "assistant")
frame = client.get("/").text
check("the window draws the short version, “More”, and the picture", "assistant-more-btn" in frame and 'id="aw-more"' in frame and "assistant-pic" in frame
      and "awKen" in frame and "path: location.pathname" in frame)
check("Yasmine reads the short version aloud", "window.friendSpeak(data.short || data.reply)" in frame)

# ======================================================================
# E. The graph
# ======================================================================
check("the graph has its summary step, drawn on the agents page", "summary" in orchestrator.STEPS and "summary" in orchestrator.AGENTS
      and any(step["key"] == "summary" for line in orchestrator.drawing() for step in line["steps"]))
check("trying “résume la journée” on the agents page: the summary step", client.post("/assistant/agents/route", json={"message": "Résume la journée"}).json().get("step") == "summary")

db.close()
print(f"{sum(results)} of {len(results)} checks passed")
