"""The batch of 1 November (2): messages set aside without an answer (why, "it needs an answer", the rule changed by
talking, undo), ideas to keep candidates engaged (from what they said, the classes, every industry), what is smart to
remind each candidate (SMART suggestions), and what could not be seen (menus cut, Yasmine over windows, pale labels).
Runs on a COPY; no mail leaves this machine."""
import json
import os
from datetime import datetime, timedelta, time as dtime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("suggestions")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Quiz, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.feedback import CandidateFeedback
from app.models.news_item import NewsItem
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import (access, auth, dropout, email_assistant as ea, email_templates as tp, email_threads as et, engagement_ideas,
                          orchestrator, program_feedback, reply_rules, smart_reminders, whats_new)
from app.services.schedule_utils import local_now

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
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="suggest-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "suggest-test-1"})


def where(response):
    return unquote_plus(response.headers.get("location", ""))


def fresh(message_id):
    db.expire_all()
    return db.query(CandidateEmail).filter(CandidateEmail.message_id == message_id).one()


program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
group = ClassGroup(name="SUGGEST-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=datetime.utcnow() - timedelta(days=40),
                   total_weeks=8, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group)
db.flush()


def person(name, email, phone=None, owed=0):
    candidate = Candidate(full_name=name, email=email, phone=phone)
    db.add(candidate)
    db.flush()
    registration = Registration(candidate_id=candidate.id, program_id=program.id, program_name=program.name, class_group_id=group.id,
                                delivery_mode="online", total_amount=390, total_price=390, paid_amount=390 - owed,
                                payment_status=PaymentStatus.PAID if not owed else PaymentStatus.PARTIAL, status=RegistrationStatus.ASSIGNED,
                                registration_date=datetime.utcnow() - timedelta(days=40))
    db.add(registration)
    db.flush()
    return candidate, registration


amira, amira_reg = person("Amira Suggest", "amira.suggest@example.com", "50 111 222")
nour, nour_reg = person("Nour Suggest", "nour.suggest@example.com", "50 333 444", owed=190)
yassine, yassine_reg = person("Yassine Suggest", "yassine.suggest@example.com")
db.commit()

# ======================================================================
# A. Messages set aside without an answer
# ======================================================================
ea.ingest_inbound(db, sender=amira.email, subject="Samedi", body="Ok merci, je viendrai samedi avec mon ordinateur.", message_id="<aside-1@example.com>")
first = fresh("<aside-1@example.com>")
check("set aside: a short message that starts with a thank-you and asks nothing", first.status == "no_reply", first.status)
check("set aside: why is kept (the rule and the word)", reply_rules.reason_of(first) == {"rule": "thanks", "word": "ok"}, first.set_aside)
check("set aside: said plainly", "“ok”" in reply_rules.explain(first))
page = admin.get(f"/inbox?thread={first.thread_id}").text
check("inbox: the message says why, with “It needs an answer” and “Talk about it”", "ix-aside-why" in page and f"/inbox/message/{first.id}/needs-answer" in page
      and f'data-aside-talk="{first.id}"' in page and f'id="aside-{first.id}"' in page)
check("inbox: the set-aside filter", 'href="/inbox?filter=aside' in page and admin.get("/inbox?filter=aside").status_code == 200)
proposal = reply_rules.proposal(first, reply_rules.rules(db))
check("set aside: the narrowest change is proposed (only thanks alone)", proposal == {"key": "only_thanks", "value": True}, proposal)
answer = admin.post(f"/inbox/message/{first.id}/talk", json={"text": "Pourquoi ?"}).json()
check("talk: “why?” explains, and says what would change", "“ok”" in answer["reply"] and answer["changed"] is None and not answer["reload"], answer)
check("talk: nothing changed by a question", reply_rules.rules(db)["only_thanks"] is False)
response = admin.post(f"/inbox/message/{first.id}/needs-answer")
first = fresh("<aside-1@example.com>")
rules = reply_rules.rules(db)
check("it needs an answer: the rule changes", rules["only_thanks"] is True and rules["changes"][0]["message_id"] == first.id, rules["changes"][:1])
check("it needs an answer: it comes back to the admin with an answer to read", first.status == "needs_admin" and first.requires_admin
      and "You said this message needs an answer" in (first.escalation_reason or ""), (first.status, first.escalation_reason))
check("it needs an answer: the notice says what changed", "only a message made of thanks alone" in where(response), where(response))
page = admin.get(f"/inbox?thread={first.thread_id}").text
check("inbox: the change is shown under the message, with “Undo”", "ix-aside-changed" in page and "/inbox/rules/undo" in page)
ea.ingest_inbound(db, sender=yassine.email, subject="Jeudi", body="Ok merci, je serai là jeudi.", message_id="<aside-2@example.com>")
check("the rule holds: the next one like it is not set aside", fresh("<aside-2@example.com>").status != "no_reply", fresh("<aside-2@example.com>").status)
ea.ingest_inbound(db, sender=yassine.email, subject="Merci", body="Merci beaucoup pour votre retour, Yassine", message_id="<aside-3@example.com>")
third = fresh("<aside-3@example.com>")
check("the rule holds: thanks alone (and a name) is still set aside", third.status == "no_reply", third.status)
answer = admin.post(f"/inbox/message/{third.id}/talk", json={"text": "Toujours répondre à cette personne"}).json()
rules = reply_rules.rules(db)
check("talk: “always answer this person” adds them and brings the message back", yassine.email in rules["always_answer"] and answer["reload"]
      and fresh("<aside-3@example.com>").status != "no_reply", (answer, rules["always_answer"]))
ea.ingest_inbound(db, sender=yassine.email, subject="Merci", body="Merci !", message_id="<aside-4@example.com>")
check("the rule holds: every message from that person comes in", fresh("<aside-4@example.com>").status != "no_reply")
answer = admin.post(f"/inbox/message/{third.id}/talk", json={"text": "annule"}).json()
check("talk: “undo” puts the rule back", yassine.email not in reply_rules.rules(db)["always_answer"] and answer["changed"], answer)
answer = reply_rules.talk(db, "“super” needs an answer")
check("talk: a word of thanks taken off the list", "super" not in reply_rules.rules(db)["thanks_words"] and "“super”" in answer["reply"], answer)
answer = reply_rules.talk(db, "'top' ne demande pas de réponse")
check("talk: a word added (never twice)", reply_rules.rules(db)["thanks_words"].count("top") == 1, answer)
answer = reply_rules.talk(db, "150 caractères")
check("talk: the length", reply_rules.rules(db)["max_length"] == 150, answer)
answer = reply_rules.talk(db, "montre les règles")
check("talk: the rules shown", "150" in answer["reply"] and "“merci”" in answer["reply"], answer["reply"][:200])
answer = reply_rules.talk(db, "bonjour")
check("talk: what it can do, when not understood", "undo" in answer["reply"] and answer["changed"] is None)
out = ea.ingest_inbound(db, sender=nour.email, subject="Absent", body="Je suis absent du bureau jusqu'au 10.", message_id="<aside-auto@example.com>",
                        automatic=True)
auto = fresh("<aside-auto@example.com>")
check("automatic replies: set aside, and why", auto.status == "no_reply" and reply_rules.reason_of(auto)["rule"] == "automatic", auto.set_aside)
answer = admin.post(f"/inbox/message/{auto.id}/talk", json={"text": "Ce n'est pas une réponse automatique, il faut répondre"}).json()
check("talk: “not an automatic reply”: this sender is read from now on", nour.email in reply_rules.rules(db)["read_automatic"] and answer["reload"], answer)
check("…and the message is read again", fresh("<aside-auto@example.com>").status != "no_reply", fresh("<aside-auto@example.com>").status)
chat = admin.post("/assistant/chat", json={"message": "Pourquoi le message de Yassine est mis de côté ?"})
data = chat.json()
check("Yasmine: the inbox rules agent answers, in French", chat.status_code == 200 and data["agent"]["key"] == "inbox"
      and ("mis de côté" in data["reply"] or "remerciement" in data["reply"] or "réponse" in data["reply"]), str(data)[:300])
check("Yasmine: the agent is on the agents page", "Inbox rules agent" in admin.get("/assistant/agents").text)
check("the router: about set-aside messages", reply_rules.wants("why was this email set aside?") and reply_rules.wants("ce message ne nécessite pas de réponse ?")
      and not reply_rules.wants("quels emails attendent une réponse aujourd'hui ?"))
auth.create_user(db, email="sam.suggest@example.com", name="Sam", password="sam-suggest-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.suggest@example.com").one()
sam.permissions = access.dump({"inbox": "view"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.suggest@example.com", "password": "sam-suggest-12"})
check("rights: looking at the inbox does not let anyone change its rules", staff.post(f"/inbox/message/{third.id}/talk", json={"text": "undo"}).status_code in (303, 403)
      and staff.post(f"/inbox/message/{third.id}/needs-answer").status_code in (303, 403))

# ======================================================================
# B. Ideas to keep them engaged
# ======================================================================
db.add(CandidateFeedback(candidate_id=nour.id, registration_id=nour_reg.id, source=program_feedback.SOURCE, author_name="Nour Suggest", program_name=program.name,
                         rating=3, comment="Les séances sont longues, il faudrait des pauses.", sentiment="mixed", status="received",
                         submitted_at=datetime.utcnow(), raw_payload=json.dumps({"may_publish": False, "improve": "Des pauses", "improve_themes": ["organisation"]})))
for candidate, registration in ((amira, amira_reg), (nour, nour_reg), (yassine, yassine_reg)):
    db.add(Quiz(candidate_id=candidate.id, registration_id=registration.id, course_name=program.name, kind="weekly", week_number=3, questions="[]",
                class_group_id=group.id, sent_at=datetime.utcnow() - timedelta(days=5), completed_at=None))
for candidate, registration in ((amira, amira_reg), (nour, nour_reg)):
    db.add(Quiz(candidate_id=candidate.id, registration_id=registration.id, course_name=program.name, kind="weekly", week_number=2, questions="[]",
                class_group_id=group.id, sent_at=datetime.utcnow() - timedelta(days=12), completed_at=datetime.utcnow() - timedelta(days=11), score=35,
                feedback=json.dumps({"mood": 2, "pace": "fast", "hard": "trop rapide", "message": ""})))
db.add(Quiz(candidate_id=amira.id, registration_id=amira_reg.id, course_name=program.name, kind="weekly", week_number=1, questions="[]",
            class_group_id=group.id, sent_at=datetime.utcnow() - timedelta(days=19), completed_at=datetime.utcnow() - timedelta(days=18), score=40))
for week in range(1, 4):
    db.add(Quiz(candidate_id=yassine.id, registration_id=yassine_reg.id, course_name=program.name, kind="weekly", week_number=10 + week, questions="[]",
                class_group_id=group.id, sent_at=datetime.utcnow() - timedelta(days=30 - week), completed_at=None))
sessions = []
for days in (24, 17, 10, 3):
    session = ClassSession(class_group_id=group.id, week_number=1, session_date=(local_now() - timedelta(days=days)).date(), start_time=dtime(9, 0),
                           end_time=dtime(12, 0), topic="Session")
    db.add(session)
    db.flush()
    sessions.append(session)
upcoming = ClassSession(class_group_id=group.id, week_number=5, session_date=(local_now() + timedelta(days=4)).date(), start_time=dtime(9, 0),
                        end_time=dtime(12, 0), topic="Next")
db.add(upcoming)
for session in sessions:
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=amira_reg.id, candidate_id=amira.id, status="absent"))
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=nour_reg.id, candidate_id=nour.id, status="absent"))
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=yassine_reg.id, candidate_id=yassine.id, status="present"))
for index in range(4):
    db.add(NewsItem(domain="ai", source="Test feed", title=f"Agents everywhere {index}", url=f"https://example.com/agents-{index}",
                    terms=json.dumps(["agentic", "model"]), published_at=datetime.utcnow() - timedelta(days=2)))
db.commit()
view = engagement_ideas.overview(db)
keys = {idea["key"] for idea in view["plans"]}
check("ideas: from what they asked to improve, with a plan and a dated target", "improve:organisation" in keys
      and all(idea["plan"] and idea["target"] and idea["why"] for idea in view["plans"]), keys)
check("ideas: say what you changed, when nothing was answered yet", "we-listen" in keys)
check("ideas: a pace too fast in a class", f"pace:{group.id}" in keys)
check("ideas: presence falling in a class", f"presence:{group.id}" in keys)
check("ideas: weekly questions left unanswered", "weekly-game" in keys)
ai = next((field for field in view["industries"] if field["domain"] == "ai"), None)
check("ideas: every industry, with what it means (a word no program teaches → a workshop)", ai is not None and ai["articles"] >= 4
      and "agentic" in (ai["idea"] or "") and "workshop" in ai["idea"], ai)
check("ideas: everyday words make no workshop", "model" not in (ai["idea"] or ""))
home = admin.get("/").text
check("dashboard: the ideas, folded under what people say", 'id="engagement-ideas"' in home and home.count('class="db-idea"') == len(view["plans"])
      and 'class="db-field"' in home)

# ======================================================================
# C. What is smart to remind each candidate
# ======================================================================
for hours in (2, 50, 98):                                   # Amira answers WhatsApp, at about 20 h; never the emails
    moment = datetime.utcnow() - timedelta(days=20) + timedelta(hours=hours)
    db.add(CandidateEmail(candidate_id=amira.id, direction="outbound", sender="9antra", recipient=amira.email, subject="Info", body="…", status="sent",
                          channel="email", received_at=moment, author="system"))
    db.add(CandidateEmail(candidate_id=amira.id, direction="outbound", sender="9antra", recipient=amira.phone, subject="", body="…", status="sent",
                          channel="whatsapp", received_at=moment + timedelta(hours=1), author="system"))
    db.add(CandidateEmail(candidate_id=amira.id, direction="inbound", sender=amira.phone, recipient="9antra", subject="", body="ok",
                          status="replied", channel="whatsapp", received_at=moment.replace(hour=19, minute=10) + timedelta(days=0 if moment.hour < 19 else 1)))
db.commit()
row = smart_reminders.for_candidate(db, amira)
found = {item["key"]: item for item in row["suggestions"]}
check("smart: absent twice in a row → “we missed you”, measured at the next session", "missed" in found and "next session" in found["missed"]["measure"],
      list(found))
check("smart: questions waiting → a reminder, measured in two days", "questions" in found and "two days" in found["questions"]["measure"])
check("smart: low scores → a moment to catch up", "catch_up" in found)
check("smart: a hard week → ask what would help", "feeling" in found)
check("smart: the way they answer (WhatsApp)", row["channel"]["key"] == "whatsapp" and "WhatsApp" in row["channel"]["why"], row["channel"])
check("smart: the hour they write", row["hour"] in (20, 21) and "h" in row["hour_why"], (row["hour"], row["hour_why"]))
check("smart: sent the day before the session, before it begins", all(item["when"] < datetime.combine(upcoming.session_date, dtime(9, 0))
                                                                       for item in row["suggestions"] if item["key"] == "missed"))
check("smart: written SMART (what, measure, why, when)", all(item["specific"] and item["measure"] and item["why"] and "Send" in item["when_text"]
                                                             for item in row["suggestions"]))
check("smart: WhatsApp opens with the text", found["missed"]["whatsapp"] and found["missed"]["whatsapp"].startswith("https://wa.me/216"))
nour_row = smart_reminders.for_candidate(db, nour)
check("smart: money is never reminded from here (the payment reminder goes once, by itself)", "owes" not in {item["key"] for item in nour_row["suggestions"]}
      and any(metric["key"] == "owes" and "once" in metric["note"] for metric in nour_row["metrics"]))
nour_reg.reminder_sent_at = "2026-10-13T20:40:48.335412+00:00"
nour_reg.registration_date = datetime.utcnow() - timedelta(days=40)
db.commit()
risk = dropout.of_registration(db, nour_reg)
check("drop-out: a payment reminder date kept as text is read (it failed before)", "owes" in risk["keys"] and risk["score"] > 0, risk)
check("smart: the date of the payment reminder is said", any(metric["key"] == "owes" and "13/10" in metric["note"]
                                                                for metric in smart_reminders.for_candidate(db, nour)["metrics"]))
yassine_row = smart_reminders.for_candidate(db, yassine)
check("smart: present but silent → their questions", [item["key"] for item in yassine_row["suggestions"]] == ["questions"], yassine_row["suggestions"])
board = smart_reminders.board(db)
check("smart: the list, those who need it most first", [row["name"] for row in board[:2]] == ["Amira Suggest", "Nour Suggest"]
      or board[0]["score"] >= board[-1]["score"], [(row["name"], row["score"]) for row in board])
page = admin.get("/reminders").text
check("reminders page: what is smart for each candidate", "What is smart for each candidate" in page and page.count('class="rm-person"') == len(board))
check("reminders page: the class suggestions no longer repeat each candidate's absences", "missed their last two sessions" not in page)
filled = admin.get(f"/reminders?smart={amira.id}:missed")
check("reminders page: “Prepare it” fills the form (who, words, day and hour)", filled.status_code == 200 and "Filled from a smart suggestion" in filled.text
      and f'value="{amira.id}" selected' in filled.text and found["missed"]["when"].strftime("%H:%M") in filled.text)
piece = admin.get(f"/reminders/smart/{amira.id}")
check("reminders page: the person chosen in the form gets their suggestions", piece.status_code == 200 and "rm-for-person" in piece.text
      and "Specific" in piece.text and "Time-bound" in piece.text)
check("reminders page: an unknown candidate", admin.get("/reminders/smart/nobody").status_code == 404)
for lang in ("fr", "ar"):
    admin.cookies.set("lang", lang)
    check(f"pages in {lang}", all(admin.get(path).status_code == 200 for path in ("/", "/reminders", f"/inbox?thread={first.thread_id}")))
admin.cookies.set("lang", "en")

# ======================================================================
# D. What could not be seen
# ======================================================================
look = open(os.path.join(testenv.PROJECT, "app", "templates", "_base_look.html"), encoding="utf-8").read()
head = look[look.index(".ui-head { position:relative;"):]
head = head[:head.index("}")]
check("menus in a page's head are not cut (no overflow:hidden on the head)", "overflow:hidden" not in head and ".ui-head:has(.dropdown-menu.show)" in look)
friend_css = open(os.path.join(testenv.PROJECT, "app", "templates", "_friend.html"), encoding="utf-8").read()
check("Yasmine stays under an open window, and steps aside while a menu is open", "body.modal-open .fr" in friend_css
      and "body:has(.dropdown-menu.show:not(.fr *)) .fr" in friend_css)
social = open(os.path.join(testenv.PROJECT, "app", "templates", "social.html"), encoding="utf-8").read()
check("social page: a chosen chip's words are white on red, not grey", ".so-chip > span {" in social and ".so-chip span {" not in social)
check("what's new: this batch is told", any(release["key"] == "2026-11-01b" for release in whats_new.RELEASES))

db.close()
check.done()
