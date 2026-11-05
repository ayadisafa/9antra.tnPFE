"""The batch of 31 October: the orchestrator and its agents, the categories of messages, the trends and knowledge agents,
the professors' assistant, Yasmine as the one assistant (her character), the reminder before each session and the
Reminders page, the design of emails, the questions page's clock, What's new, and the mailbox that tries again.
Runs on a COPY; no mail leaves this machine."""
import json
from datetime import datetime, timedelta, time as dtime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("agents")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import (AdminReminder, AgentRun, Candidate, ClassGroup, ClassStatus, KnowledgeEntry, Notification, Professor, Program,
                        Quiz, Registration, User, UserSession)
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.email_template import EmailTemplate
from app.models.news_item import NewsItem
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import (access, attention, auth, email_assistant as ea, email_files, email_layout as el, email_templates as tp,
                          email_threads as et, friend, knowledge_service, mailbox_service as mb, message_categories as mc,
                          orchestrator, professor_assistant, rag, reminder_board, session_reminders as sr, trends_agent, weekly_questions,
                          whats_new)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

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
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="agents-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "agents-test-1"})


def where(response):
    return unquote_plus(response.headers.get("location", ""))


devops = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
other_program = db.query(Program).filter(Program.id != devops.id).first()

# ======================================================================
# A. What a message is about
# ======================================================================
for text, expected in (("Le lien de la séance ne marche pas", "technical"), ("Combien coûte la formation ?", "payment"),
                       ("Je serai absent samedi", "planning"), ("Qu'est-ce qu'on voit dans le module 3 du programme ?", "programs"),
                       ("Je voudrais m'inscrire à la formation", "registration"), ("C'est inadmissible, personne ne répond", "complaint"),
                       ("الرابط لا يعمل", "technical"), ("9adech el flous ?", "payment")):
    found = mc.categorize(text, use_meaning=False)
    check(f"category: “{text}” is {expected}", found["key"] == expected, found)
check("category: a thank-you is general", mc.categorize("Merci beaucoup, c'est noté !", use_meaning=False)["key"] == "general")
check("category: the words that decided are kept", "lien" in mc.categorize("Le lien ne marche pas", use_meaning=False)["why"])
check("category: trends and figures are the admin's only", mc.categorize("what is trendy in AI?", use_meaning=False)["key"] != "trends"
      and mc.categorize("what is trendy in AI?", for_admin=True, use_meaning=False)["key"] == "trends")
check("category: the email assistant's reading counts", mc.categorize("Bonjour", intent="refund", use_meaning=False)["key"] == "payment")
check("category: each has a name, an icon and a colour", all(mc.label_of(key)["icon"].startswith("bi-") and mc.label_of(key)["colour"].startswith("#")
                                                              for key in mc.CATEGORIES))
check("category: an unknown key is general", mc.label_of("martian")["key"] == "general")

# ======================================================================
# B. The orchestrator and the assistant's window
# ======================================================================
said = admin.post("/assistant/chat", json={"message": "Who has not paid?"}).json()
check("chat: the answer says which agent gave it", said.get("agent", {}).get("key") == "records" and said.get("category", {}).get("key") == "records", said.get("agent"))
check("chat: the figures are still the figures", "have not paid" in said["reply"] or "paid" in said["reply"], said["reply"][:120])
said = admin.post("/assistant/chat", json={"message": "comment ajouter un professeur ?"}).json()
check("chat: a how-to goes to the guide agent", said["agent"]["key"] == "guide" and said["source"] == "guide", (said["agent"], said["source"]))
runs = db.query(AgentRun).filter(AgentRun.channel == "chat").count()
check("chat: every request is written down with its agent", runs >= 2, runs)
view = orchestrator.overview(db, 7)
check("agents page: each agent with its count", {row["key"] for row in view["agents"]} == set(orchestrator.AGENTS)
      and sum(row["total"] for row in view["agents"]) >= 2 and view["latest"])
page = admin.get("/assistant/agents")
check("agents page: opens, with the flow and the try box", page.status_code == 200 and "The orchestrator" in page.text and "Try a sentence" in page.text
      and "Knowledge agent" in page.text, page.status_code)
tried = admin.post("/assistant/agents/route", json={"message": "Le lien de la séance ne marche pas"}).json()
check("agents page: a sentence is sorted without answering", tried["agent"]["key"] == "technical" and "lien" in tried["category"]["why"], tried)
before = db.query(AgentRun).count()
admin.post("/assistant/agents/route", json={"message": "Je serai absent"})
check("agents page: trying writes nothing down", db.query(AgentRun).count() == before)
old = AgentRun(agent="general", channel="chat", outcome="answered", at=datetime.utcnow() - timedelta(days=200))
db.add(old)
db.commit()
gone = orchestrator.forget_old(db)
check("agents: runs older than four months go", gone >= 1 and db.query(AgentRun).filter(AgentRun.at < datetime.utcnow() - timedelta(days=150)).count() == 0)

# ======================================================================
# C. The trends agent
# ======================================================================
check("trends: what it takes", trends_agent.wants("What is trendy in AI?") == "trends" and trends_agent.wants("Quels services pourrions-nous ajouter ?") == "services"
      and trends_agent.wants("le nom 9antra est-il inspirant ?") == "name")
check("trends: not what is for others", trends_agent.wants("envoie un email à Fares: le service est fermé") is None
      and trends_agent.wants("what is trending in the market?") is None and trends_agent.wants("any news from Fares?") is None)
reading = trends_agent.name_reading(db, "9antra")
check("name: 9antra is read as a bridge, and scores high", reading["score"] >= 8 and "bridge" in reading["reply"] and "Internships" in reading["reply"], reading["reply"][:200])
check("name: generic words score low", trends_agent.name_reading(db, "Tech Academy")["score"] <= 5)
check("name: the name is taken from the question", trends_agent._name_in(db, "le nom 9antra est-il inspirant ?") == "9antra"
      and trends_agent._name_in(db, "Que pensez-vous du nom « CodeLab Academy » ?") == "CodeLab Academy")
now = datetime.utcnow()
for index, (title, terms) in enumerate((("Agents everywhere: the year of agentic AI", ["agents", "llm"]), ("RAG in production", ["rag", "llm"]),
                                         ("New LLM release", ["llm"]))):
    db.add(NewsItem(domain="ai", source="Test feed", title=title, url=f"https://example.com/ai-{index}", summary=title + ". More.",
                    terms=json.dumps(terms), published_at=now - timedelta(days=index + 1)))
db.commit()
said = admin.post("/assistant/chat", json={"message": "What is trendy in AI?"}).json()
check("trends: what is new in a field, from the journal, numbered", said["agent"]["key"] == "trends" and "llm" in said["reply"] and "[1]" in said["reply"]
      and said["sources"][0]["label"].startswith("[1]") and said["sources"][0]["url"].startswith("https://example.com"), said["reply"][:300])
said = admin.post("/assistant/chat", json={"message": "quoi de neuf ?"}).json()
check("trends: every field at once", said["agent"]["key"] == "trends" and "Artificial intelligence" in said["reply"] or "Intelligence" in said["reply"], said["reply"][:200])
said = admin.post("/assistant/chat", json={"message": "What services could we add?"}).json()
check("trends: services to add, with what supports them", said["agent"]["key"] == "trends" and "•" in said["reply"] and said["sources"], said["reply"][:200])
said = admin.post("/assistant/chat", json={"message": "what is trending in the market?"}).json()
check("trends: the job market keeps its own answer", "Most asked" in said["reply"] or "market" in said["reply"].lower() or "offer" in said["reply"].lower(), said["reply"][:160])

# ======================================================================
# D. The knowledge agent (RAG)
# ======================================================================
fact = knowledge_service.save_fact(db, text="La salle Carthage du centre est fermée pour travaux jusqu'au 20 novembre.")
db.commit()
knowledge_service.ensure_fresh(db)
answer = rag.answer(db, "la salle Carthage est-elle fermée ?", lang="fr")
check("rag: the closest passage is quoted with its number", answer is not None and "[1]" in answer["reply"] and "Carthage" in answer["reply"]
      and answer["sources"][0]["label"].startswith("[1]"), answer)
check("rag: it says nothing was added", answer is not None and any("no sentence was added" in item["label"] or "aucune phrase" in item["label"] for item in answer["sources"]))
check("rag: nothing found, no answer", rag.answer(db, "zzqx wvbn plok", lang="en") is None)
news = rag.passages(db, "agentic AI agents", for_admin=True)
check("rag: the journal for the admin", any(item["kind"] == "news" for item in news), news)
check("rag: never the journal for a candidate", not any(item["kind"] == "news" for item in rag.passages(db, "agentic AI agents", for_admin=False)))

# ======================================================================
# E. The professors' assistant
# ======================================================================
teacher = Professor(name="Hela Formatrice", specialties=devops.name, email="hela.agents@example.com")
db.add(teacher)
db.flush()
start = (local_now() - timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="DEVOPS-AG-1", program_id=devops.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.IN_PROGRESS,
                   start_date=start, total_weeks=8, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date",
                   capacity=12, max_capacity=12)
other_group = ClassGroup(name="OTHER-AG-1", program_id=other_program.id, status=ClassStatus.IN_PROGRESS, start_date=start, total_weeks=8,
                         start_time=dtime(14, 0), end_time=dtime(17, 0), days_of_week="sun", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add_all([group, other_group])
db.flush()


def member(name, email, class_group, program):
    person = Candidate(full_name=name, email=email, phone="50 111 222")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390,
                                total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                                class_group_id=class_group.id)
    db.add(registration)
    db.commit()
    return person, registration


sami, sami_reg = member("Sami Trabelsi", "sami.agents@example.com", group, devops)
rania, rania_reg = member("Rania Gharbi", "rania.agents@example.com", group, devops)
outsider, outsider_reg = member("Youssef Kefi", "youssef.agents@example.com", other_group, other_program)
past = ClassSession(class_group_id=group.id, week_number=1, session_date=(local_now() - timedelta(days=3)).date(), start_time=dtime(9, 0),
                    end_time=dtime(12, 0), topic="Docker")
upcoming = ClassSession(class_group_id=group.id, week_number=2, session_date=(local_now() + timedelta(days=4)).date(), start_time=dtime(9, 0),
                        end_time=dtime(12, 0), topic="Kubernetes")
db.add_all([past, upcoming])
db.flush()
db.add(Attendance(class_group_id=group.id, session_id=past.id, registration_id=rania_reg.id, candidate_id=rania.id, status="absent"))
db.add(Attendance(class_group_id=group.id, session_id=past.id, registration_id=sami_reg.id, candidate_id=sami.id, status="present", participation=3))
db.commit()
auth.create_user(db, email="hela.agents@example.com", name="Hela Formatrice", password="hela-agents-1", role=auth.PROFESSOR, professor_id=teacher.id)
prof = TestClient(main.app, follow_redirects=False)
prof.post("/login", data={"email": "hela.agents@example.com", "password": "hela-agents-1"})
hela = auth.CurrentUser(id="x", name="Hela", email="hela.agents@example.com", role=auth.PROFESSOR, professor_id=teacher.id)

said = professor_assistant.answer(db, "Qui était absent ?", user=hela, lang="fr")
check("professor: who was absent, in their class", "Rania Gharbi" in said["reply"] and "Sami" not in said["reply"], said["reply"])
said = professor_assistant.answer(db, "When is my next session?", user=hela, lang="en")
check("professor: their next session", "Kubernetes" in said["reply"] and "DEVOPS-AG-1" in said["reply"], said["reply"])
said = professor_assistant.answer(db, "my candidates", user=hela, lang="en")
check("professor: their candidates, not the others", "Sami Trabelsi" in said["reply"] and "Youssef" not in said["reply"], said["reply"])
said = professor_assistant.answer(db, "Sami Trabelsi", user=hela, lang="en")
check("professor: one of their candidates, without contact details", "Sami Trabelsi" in said["reply"] and "absent 0" in said["reply"]
      and "50 111" not in said["reply"] and "@" not in said["reply"], said["reply"])
said = professor_assistant.answer(db, "Youssef Kefi", user=hela, lang="en")
check("professor: a candidate of another class is not read", "Youssef" not in said["reply"] or "not in your assistant" in said["reply"], said["reply"][:200])
said = professor_assistant.answer(db, "Combien Sami a payé ?", user=hela, lang="fr")
check("professor: payments are refused", "paiements" in said["reply"] and "390" not in said["reply"], said["reply"])
said = prof.post("/account/guide", json={"message": "who was absent?"}).json()
check("professor: their window goes to their agent", said["agent"]["key"] == "professor" and "Rania" in said["reply"], said)
check("professor: their suggestions", "When is my next session?" in prof.get("/account/guide/suggestions?lang=en").json()["suggestions"])
check("professor: never the whole assistant", prof.post("/assistant/chat", json={"message": "Who has not paid?"}).status_code in (303, 401, 403))
check("professor: never the agents page", prof.get("/assistant/agents").status_code in (303, 401, 403))
page = prof.get("/teacher")
check("professor: Yasmine's window is on their pages, asking their agent", page.status_code == 200 and 'id="assistant-panel"' in page.text
      and 'data-ask-url="/account/guide"' in page.text and 'id="assistant-auto"' not in page.text,
      (page.status_code, 'id="assistant-panel"' in page.text, 'data-ask-url="/account/guide"' in page.text, 'id="assistant-auto"' in page.text))

# ======================================================================
# F. Every incoming message gets its category
# ======================================================================
out = ea.ingest_inbound(db, sender=sami.email, subject="Lien", body="Bonjour, le lien de la séance ne marche pas chez moi.", message_id="<agents-tech-1@example.com>")
db.expire_all()
message = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<agents-tech-1@example.com>").one()
check("email: its category is kept", message.category == "technical", message.category)
run = db.query(AgentRun).filter(AgentRun.ref == message.id).first()
check("email: the agent that handled it is written down", run is not None and run.agent == "technical" and run.channel == "email", run and run.agent)
ea.ingest_inbound(db, sender=sami.email, subject="Pas content", body="Je suis très déçu, c'est n'importe quoi cette organisation.", message_id="<agents-complaint-1@example.com>")
db.expire_all()
unhappy = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<agents-complaint-1@example.com>").one()
check("email: a complaint always comes to the admin", unhappy.category == "complaint" and unhappy.status == "needs_admin"
      and "complaint" in (unhappy.escalation_reason or "").lower(), (unhappy.category, unhappy.status, unhappy.escalation_reason))
db.add(CandidateEmail(candidate_id=sami.id, direction="inbound", sender=sami.email, recipient="x", subject="Prix", body="Quel est le prix ?",
                      status="received", author="candidate"))
db.commit()
check("email: the older ones get theirs", mc.backfill(db) >= 1 and db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound",
                                                                                                CandidateEmail.category.is_(None)).count() == 0)
page = admin.get(f"/inbox?category=technical&thread={message.thread_id}")
check("inbox: filter by category, and the chip on the message", page.status_code == 200 and "Technical help" in page.text and "ix-catchip" in page.text)
page = admin.get(f"/inbox?message={unhappy.id}")
check("inbox: one message opens its discussion", page.status_code == 200 and "déçu" in page.text)
response = admin.post(f"/inbox/thread/{message.thread_id}/reply", data={"body": "Le lien de la séance change chaque semaine : il arrive une heure avant.",
                                                                         "remember": "true"})
facts = [entry.content for entry in knowledge_service.facts(db)]
check("inbox: “keep this information” teaches every assistant", any("une heure avant" in text for text in facts) and "Kept for the next ones" in where(response),
      where(response))

# ======================================================================
# G. The reminder before each session
# ======================================================================
sent.clear()
tomorrow = ClassSession(class_group_id=group.id, week_number=3, session_date=(local_now() + timedelta(days=1)).date(), start_time=dtime(10, 0),
                        end_time=dtime(12, 0), topic="Helm")
db.add(tomorrow)
db.commit()
at = datetime.combine(tomorrow.session_date, dtime(10, 0)) - timedelta(hours=20)
check("session reminder: due the day before", any(session.id == tomorrow.id for _, session, _ in sr.due(db, at)))
check("session reminder: not two days before", not any(session.id == tomorrow.id for _, session, _ in sr.due(db, at - timedelta(hours=10))))
went = sr.send_due(db, at)
check("session reminder: each candidate of the class, once", went == 2, went)
check("session reminder: with the day, the hours and the topic", any("Helm" in (mail.get("text_body") or mail.get("body") or "") for mail in sent))
check("session reminder: never twice", sr.send_due(db, at) == 0)
sr.set_hours(db, 48)
check("session reminder: the hours are changed", sr.hours_before(db) == 48)
try:
    sr.set_hours(db, 500)
    refused = False
except ValueError:
    refused = True
check("session reminder: too many hours refused", refused)
sr.set_hours(db, 24)

# ======================================================================
# H. Every reminder on the Reminders page
# ======================================================================
items = {item["key"]: item for item in reminder_board.items(db)}
check("reminders page: every reminder is listed", {"payment_reminder", "training_reminder", "session_reminder", "session_link", "level_reminder",
                                                   "resources", "course_questions"} <= set(items))
check("reminders page: the new one is on, 24 h before", items["session_reminder"]["on"] and items["session_reminder"]["timing"]["value"] == 24)
page = admin.get("/reminders")
check("reminders page: opens with every reminder", page.status_code == 200 and 'id="auto"' in page.text and "Reminder before each session" in page.text
      and "rb-switch" in page.text)
response = admin.post("/reminders/auto/session_reminder/switch", data={"on": "0"})
check("reminders page: switched off", not tp.is_on(db, "session_reminder") and "is off" in where(response))
check("reminders page: switching off is not a change of words", not tp.get(db, "session_reminder").customized)
check("reminders page: nothing goes when it is off", sr.send_due(db, at) == 0)
admin.post("/reminders/auto/session_reminder/switch", data={"on": "1"})
check("reminders page: switched on again", tp.is_on(db, "session_reminder"))
response = admin.post("/reminders/auto/session_reminder/timing", data={"value": "36"})
check("reminders page: the timing is changed", sr.hours_before(db) == 36 and "36 h" in where(response), where(response))
response = admin.post("/reminders/auto/training_reminder/timing", data={"value": "5, 2"})
check("reminders page: the countdown days are changed", get_settings(db).training_reminder_days == "5,2", get_settings(db).training_reminder_days)
response = admin.post("/reminders/auto/payment_reminder/timing", data={"value": "abc"})
check("reminders page: a wrong value does not break anything", response.status_code == 303)
tp.save(db, "session_reminder", subject="Rappel {first_name}", body="Votre séance {session_date}.", title="", closing="")
db.commit()
response = admin.post("/reminders/auto/session_reminder/reset")
check("reminders page: the original, its words and its timing", tp.get(db, "session_reminder").subject == tp.AUTO_EMAILS["session_reminder"].subject
      and sr.hours_before(db) == 24 and "back as at first" in where(response))
admin.post("/reminders/auto/training_reminder/reset")
check("reminders page: the countdown back to 7, 3, 1", get_settings(db).training_reminder_days == "7,3,1")
response = admin.post("/reminders/auto/course_questions/switch", data={"on": "0"})
check("reminders page: one that cannot be switched off says so", "cannot be switched off" in where(response), where(response))

# ======================================================================
# I. The design of an email
# ======================================================================
plain = el.render_email(title="T", body_html="<p>x</p>", cta=("Go", "https://example.com"))
check("design: no design, the same email as before", plain == el.render_email(title="T", body_html="<p>x</p>", cta=("Go", "https://example.com"), design={}))
check("design: only known values are kept", el.clean_design({"font": "serif", "button": "pill", "x": 1, "banner_id": "../../etc", "accent": "red"}) == {"font": "serif"})
picture = email_files.save_upload(db, filename="banner.png", data=open(testenv.os.path.join(testenv.PROJECT, "app", "static", "brand", "icon-64.png"), "rb").read(),
                                  purpose="image")
db.commit()
designed = {"font": "serif", "button": "wide", "background": "dark", "accent": "#16a34a", "icon": "bell", "banner_id": picture.id, "gallery_ids": [picture.id]}
built = tp.build(db, subject="S", body="Bonjour\n\n[box]\nQuand", context={}, design=designed)
check("design: the banner and the photos travel inside the email", built["html"].count("cid:") >= 2 and len(built["inline"]) >= 2)
check("design: font, colour, background, icon, button", "Georgia" in built["html"] and "#16a34a" in built["html"] and "#1d1430" in built["html"]
      and "&#128276;" in built["html"] and 'width="100%" style="margin:6px 0 20px;"' not in plain)
response = admin.post("/inbox/templates/auto/session_reminder/save", data={"subject": tp.AUTO_EMAILS["session_reminder"].subject,
                                                                         "body": tp.AUTO_EMAILS["session_reminder"].body, "is_active": "true",
                                                                         "design": json.dumps(designed)})
check("design: saved with the automatic email", tp.get(db, "session_reminder").design.get("font") == "serif" and tp.get(db, "session_reminder").customized)
preview = admin.post("/inbox/templates/preview", json={"key": "session_reminder", "subject": "S", "body": "B", "design": {"background": "dark"}}).json()
check("design: the preview follows it", "#1d1430" in preview["html"])
page = admin.get("/inbox/templates?key=session_reminder")
check("design: its editor is on the page", page.status_code == 200 and "data-design" in page.text and "A banner across the top" in page.text)
response = admin.post("/reminders/create", data={"audience": "class", "to_class": group.id, "subject": "Bonne semaine", "body": "Bonjour {first_name}",
                                                "when": "now", "design": json.dumps({"background": "white", "icon": "party"})})
own = db.query(AdminReminder).filter(AdminReminder.subject == "Bonne semaine").first()
check("design: the admin's own reminders keep theirs", own is not None and json.loads(own.design or "{}").get("icon") == "party", where(response))
preview = admin.post("/reminders/preview", json={"subject": "S", "body": "B", "design": {"icon": "party"}}).json()
check("design: and their preview", "&#127881;" in preview["html"])
admin.post("/reminders/auto/session_reminder/reset")

# ======================================================================
# J. Yasmine is the assistant, with her character
# ======================================================================
check("character: Yasmine at first", friend.look(db)["name"] == "Yasmine" and friend.look(db)["gender"] == "female")
response = admin.post("/settings/friend", data={"name": "Karim", "gender": "male", "hair": "short", "hair_colour": "#1f1512", "skin": "#c68642",
                                                 "outfit": "blazer", "outfit_colour": "#0ea5e9", "glasses": "1", "beard": "1", "voice": "auto"})
look = friend.look(db)
check("character: saved", look["name"] == "Karim" and look["gender"] == "male" and look["glasses"] and look["beard"] and look["outfit"] == "blazer"
      and "new look" in where(response), look)
admin.cookies.set("lang", "fr")
talk = admin.get("/account/friend", params={"path": "/"}).json()
check("character: his name, a man's words, a man's voice", talk["name"] == "Karim" and "votre ami de" in talk["intro"] and talk["voice"] == "male", talk["intro"])
admin.cookies.set("lang", "en")
page = admin.get("/").text
check("character: drawn on every page and in the window", 'data-gender="male"' in page and 'data-outfit="blazer"' in page and "Karim" in page
      and 'id="assistant-launcher"' not in page)
check("character: strange values are not kept", friend.clean_look({"gender": "robot", "hair": "<b>", "skin": "red", "name": "x" * 80})["gender"] == "female"
      and len(friend.clean_look({"name": "x" * 80})["name"]) <= 24)
admin.post("/settings/friend", data={"reset": "1"})
check("character: back as at first", friend.look(db) == friend.LOOK_DEFAULTS)

# ======================================================================
# K. What's new
# ======================================================================
db.query(Notification).filter(Notification.category == whats_new.CATEGORY).delete()
db.commit()
check("what's new: the admin is told once", whats_new.announce(db) and not whats_new.announce(db))
talk = admin.get("/account/friend", params={"path": "/"}).json()
check("what's new: Yasmine says it while it is unread", any(line["url"] == "/whats-new" for line in talk["lines"]))
page = admin.get("/whats-new")
check("what's new: the page lists each novelty", page.status_code == 200 and "A team of agents with an orchestrator" in page.text and "/assistant/agents" in page.text)
check("what's new: opening it marks it read", whats_new.unread(db) is None)

# ======================================================================
# L. The questions page's clock
# ======================================================================
questions = [{"question": "Docker sert à ?", "options": ["Conteneurs", "Tableurs"], "correct_answer": 0, "explanation": ""},
             {"question": "Kubernetes ?", "options": ["Orchestration", "Dessin"], "correct_answer": 0, "explanation": ""}]
quiz = Quiz(candidate_id=sami.id, registration_id=sami_reg.id, course_name="DevOps", kind="weekly", class_group_id=group.id, week_number=1,
            questions=json.dumps(questions), access_token="agents-weekly-token-1234", sent_at=datetime.utcnow())
db.add(quiz)
db.commit()
page = TestClient(main.app).get("/learn/agents-weekly-token-1234")
check("clock: the page says the time is noted", page.status_code == 200 and "lq-timer" in page.text and "sont notés pour votre formateur" in page.text)
measured = {"total_s": 95, "left": 2, "away_s": 40, "blurs": 2,
            "per_question": [{"s": 3, "changes": 0, "away_s": 0, "first_s": 3}, {"s": 50, "changes": 1, "away_s": 40, "first_s": 80}]}
TestClient(main.app).post("/learn/agents-weekly-token-1234", data={"q0": "1", "q1": "0", "attention": json.dumps(measured)})
db.expire_all()
quiz = db.query(Quiz).filter(Quiz.access_token == "agents-weekly-token-1234").one()
kept = attention.of(quiz)
check("clock: kept with the answers", kept and kept["left"] == 2 and kept["per_question"][1]["away_s"] == 40, quiz.attention)
said = attention.reading(quiz, weekly_questions.result_of(quiz)["rows"])
check("clock: read plainly, with what is worth a word", said["tone"] == "watch" and "Left the page 2 time(s)" in " ".join(said["lines"])
      and "question 2" in " ".join(said["lines"]), said)
check("clock: anything strange is not kept", attention.clean("not json", 2) is None and attention.clean(json.dumps({"total_s": "lots"}), 1)["total_s"] == 0)
results = weekly_questions.week_results(db, group, 1)
check("clock: shown with the week's results", any(row.get("attention") for row in results["rows"]))

# ======================================================================
# M. The mailbox tries again when the connection is cut
# ======================================================================
real_once, real_configured = mb._poll_once, mb.configured
calls = []


def dropped_then_fine(session):
    calls.append(1)
    return {"status": "error", "imported": 0, "error": "socket error: EOF", "dropped": True} if len(calls) == 1 else {"status": "ok", "imported": 0}


mb.configured = lambda: True
mb._poll_once = dropped_then_fine
mb._DROPPED = 0
check("mailbox: a cut connection is tried again at once", mb.poll(db)["status"] == "ok" and len(calls) == 2)
mb._poll_once = lambda session: {"status": "error", "imported": 0, "error": "socket error: EOF", "dropped": True}
first = mb.poll(db)["status"]
second = mb.poll(db)["status"]
third = mb.poll(db)["status"]
check("mailbox: shown as a problem only when it keeps happening", (first, second, third) == ("retry", "retry", "error"), (first, second, third))
check("mailbox: EOF is recognised as a cut connection", mb._dropped(Exception("socket error: EOF")) and not mb._dropped(ValueError("bad password")))
mb._poll_once, mb.configured = real_once, real_configured

# ======================================================================
# N. Who may open what
# ======================================================================
auth.create_user(db, email="sam.agents@example.com", name="Sam", password="sam-agents-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.agents@example.com").one()
sam.permissions = access.dump({"candidates": "view"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.agents@example.com", "password": "sam-agents-12"})
check("access: a team member without the assistant cannot open the agents page", staff.get("/assistant/agents").status_code in (303, 401, 403))
said = staff.post("/account/guide", json={"message": "how do I change my password?"}).json()
check("access: they ask the guide agent", said["agent"]["key"] == "guide", said.get("agent"))
check("access: signed out, nothing", TestClient(main.app, follow_redirects=False).get("/assistant/agents").status_code in (303, 401, 403))
page = admin.get("/").text
check("the bell's count is shown whole (the buttons' ripple does not clip it)", 'id="nav-notif-badge"' in page
      and ".btn:has(> .badge) { overflow:visible; }" in page and ':scope > .badge' in page)

# ======================================================================
# O. The orchestration runs on LangGraph; the rules choose every step
# ======================================================================
from app.services import orchestrator

built = orchestrator.graph()
check("graph: the orchestrator is a LangGraph graph, built once", type(built).__module__.startswith("langgraph") and orchestrator.graph() is built)
said = admin.post("/assistant/chat", json={"message": "Quand publier cette semaine ?"}).json()
check("graph: a marketing question goes read, marketing, answer", said.get("path") == ["read", "marketing", "finish"]
      and said["agent"]["key"] == "marketing", said.get("path"))
said = admin.post("/assistant/chat", json={"message": "Who has not paid?"}).json()
check("graph: a question on the figures goes to the subject's agent with its tools", said.get("path", [])[:1] == ["read"]
      and "tools" in said["path"] and said["path"][-1] == "finish", said.get("path"))
said = admin.post("/assistant/chat", json={"message": f"Combien coûte la formation {devops.name} ?"}).json()
check("graph: a question naming a program passes by the program's agent", "program" in said.get("path", []), said.get("path"))
said = admin.post("/assistant/chat", json={"message": "zqxv wplk trnm bvcx"}).json()
check("graph: when nobody knows, the others are asked before answering", said.get("path", [])[-2:] == ["consult", "finish"], said.get("path"))
said = admin.post("/assistant/chat", json={"message": "comment faire une inscripton ?"}).json()
check("Yasmine's window is told the words she read put right", any(item["written"] == "inscripton" for item in said.get("understood", [])), said.get("understood"))
said = prof.post("/account/guide", json={"message": "who was absent?"}).json()
check("graph: a professor's question goes read, professors' agent, answer, nothing more", said["agent"]["key"] == "professor"
      and db.query(AgentRun).order_by(AgentRun.at.desc()).first().agent == "professor", said.get("agent"))
check("graph: the guide window never reaches the whole assistant", orchestrator.first_step(db, "Who has not paid?", sam, "payment", limited=True) == "guide"
      and orchestrator._after({"limited": True, "found": {"source": "none"}, "agent": "guide", "path": ["read", "guide"]}) == "finish")
rows = orchestrator.drawing()
keys = [step["key"] for line in rows for step in line["steps"]]
check("graph: the agents page draws every step once, from the graph itself", sorted(keys) == sorted(orchestrator.STEPS)
      and rows[0]["steps"][0]["key"] == "read" and rows[-1]["steps"][0]["key"] == "finish", keys)
check("graph: each arrow leads to a drawn step", all(after["key"] in keys for line in rows for step in line["steps"] + [{"next": line["next"]}]
                                                     for after in step["next"]))
page = admin.get("/assistant/agents").text
check("graph: the agents page shows how a question travels", 'id="graph"' in page and "LangGraph" in page and 'data-step="consult"' in page)
tried = admin.post("/assistant/agents/route", json={"message": "Quand publier cette semaine ?"}).json()
check("graph: trying a sentence says its first step, nothing answered", tried.get("step") == "marketing")
from app.services import whats_new

check("what's new: the graph is told", any(release["key"] == "2026-11-03" for release in whats_new.RELEASES))

db.close()
check.done()
