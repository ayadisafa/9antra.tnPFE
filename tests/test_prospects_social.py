"""Questions about the programs answered from the facts, in the language asked (French, English, Arabic, Tunisian): to
people not registered yet (email, WhatsApp) and to candidates, never alone when something else is asked too; posts of
the Facebook and LinkedIn pages read, analysed and learnt; the assistant keeping itself up to date (its knowledge every
half hour, the admin's own answers kept as “learnt by itself” until confirmed).
Runs on a COPY; email, WhatsApp and SMS are fakes; LinkedIn is faked."""
import inspect
import json
import os
from datetime import datetime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("prospects_social")
check = testenv.Checks()

import requests

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, Notification, Program, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.knowledge import KnowledgeEntry
from app.models.website import WebsiteItem
from app.scheduler import jobs
from app.services import (auth, chatbot, email_assistant as ea, email_templates as tp, email_threads as et, knowledge_service, linkedin, prospects,
                          registration_form, social_analysis, website_sync, whatsapp, whatsapp_inbox)
from app.services.settings_service import get_settings

sent, delivered_to = [], []


def fake_send(**kw):
    sent.append(kw)
    return True


def fake_attempt(to, subject, plain, html_body, *rest, **kw):
    delivered_to.append({"to": to, "subject": subject, "text": plain})
    return None, "msgid"


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
et._attempt = fake_attempt
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None
whatsapp_sent = []
whatsapp.configured = lambda: True
whatsapp.send = lambda to, body, **kw: (whatsapp_sent.append({"to": to, "text": body}) or None)


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="prospects-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "prospects-1"})
settings = get_settings(db)
settings.email_auto_reply_mode, settings.whatsapp_auto_reply = "auto", "auto"
settings.centre_phone, settings.centre_whatsapp = "+216 26 925 635", "+216 95 225 638"
db.commit()
devops = db.query(Program).filter(Program.name == "DevOps").first()
link = registration_form.url(db)
price_online = f"{devops.price_online:,.0f}".replace(",", " ")

# ---- A. The facts, in four languages ----
said = prospects.reply_for(db, "Bonjour, combien coûte la formation DevOps et elle dure combien de temps ?")
check("facts in French: price, length, the form, the numbers", said and said["lang"] == "fr" and f"{price_online} DT en ligne" in said["text"]
      and "semaines" in said["text"] and link in said["text"] and "+216 26 925 635" in said["text"], (said or {}).get("text", "")[:300])
said = prospects.reply_for(db, "Hi, how much is DevOps?")
check("facts in English", said and said["lang"] == "en" and f"costs {price_online} DT online" in said["text"] and "Hello, and thank you" in said["text"])
said = prospects.reply_for(db, "slm 9adech soum el formation devops?")
check("facts in Tunisian", said and said["lang"] == "tn" and "soumha" in said["text"] and said["text"].startswith("Aslema"), (said or {}).get("text", "")[:200])
said = prospects.reply_for(db, "السلام عليكم، بقداش سعر تكوين DevOps؟")
check("facts in Arabic", said and said["lang"] == "ar" and "دينار" in said["text"] and said["text"].startswith("مرحبا"), (said or {}).get("text", "")[:200])
said = prospects.reply_for(db, "What programs do you offer?")
check("which programs: the list, and an invitation to say which", said and devops.name in said["text"] and "Tell us which one" in said["text"])
check("nothing about the programs: no answer", prospects.reply_for(db, "Merci beaucoup !") is None
      and prospects.reply_for(db, "Bonjour je voudrais me faire rembourser") is None)
check("what the facts do not answer is found", prospects.uncovered("combien coûte DevOps et y a-t-il un certificat ?") == ["_certificate"])

# ---- B. People not registered yet, by email ----
delivered_to.clear()
ea.ingest_inbound(db, sender="nouveau.visiteur@example.com", subject="Formation DevOps", body="Bonjour, combien coûte la formation DevOps ? Merci",
                  message_id=f"<stranger-1-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
check("not registered yet: answered at once by email, with the facts and the form", delivered_to and delivered_to[-1]["to"] == "nouveau.visiteur@example.com"
      and f"{price_online} DT" in delivered_to[-1]["text"] and link in delivered_to[-1]["text"], delivered_to[-1:] if delivered_to else None)
check("not registered yet: the admin is told what was sent", db.query(Notification).filter(Notification.message.like("%nouveau.visiteur%")).count()
      + db.query(Notification).filter(Notification.title.like("%nouveau.visiteur%")).count() >= 1)
delivered_to.clear()
ea.ingest_inbound(db, sender="autre.visiteur@example.com", subject="Question", body="Bonjour, combien coûte DevOps et est-ce qu'il y a un certificat à la fin ?",
                  message_id=f"<stranger-2-{datetime.utcnow().timestamp()}@example.com>")
check("not registered yet: something else asked too, nothing goes alone", not delivered_to, delivered_to)
settings.assistant_never = "réduction"
db.commit()
ea.ingest_inbound(db, sender="troisieme@example.com", subject="Prix", body="Bonjour, combien coûte DevOps ? Vous faites une réduction ?",
                  message_id=f"<stranger-3-{datetime.utcnow().timestamp()}@example.com>")
check("not registered yet: the admin's rules apply (“never answer this alone”)", not delivered_to)
settings.assistant_never, settings.prospect_answers_on = None, False
db.commit()
ea.ingest_inbound(db, sender="quatrieme@example.com", subject="Prix", body="Bonjour, combien coûte DevOps ?", message_id=f"<stranger-4-{datetime.utcnow().timestamp()}@example.com>")
check("not registered yet: switched off, no facts sent", not delivered_to)
settings.prospect_answers_on = True
db.commit()

# ---- C. People not registered yet, on WhatsApp, in Tunisian ----
whatsapp_sent.clear()
whatsapp_inbox.receive(db, phone="21655111222", text="slm 9adech soum el formation devops w waqtech tebda?", wa_id="wamid.prospect1", name="Sami")
check("WhatsApp in Tunisian: answered in Tunisian, at once", whatsapp_sent and "soumha" in whatsapp_sent[-1]["text"] and link in whatsapp_sent[-1]["text"],
      whatsapp_sent[-1:] if whatsapp_sent else None)

# ---- D. Candidates asking about a program ----
nour = Candidate(full_name="Nour Faits", email="nour.faits@example.com", phone="98 777 111")
db.add(nour)
db.commit()


def ask(body, key):
    out = ea.ingest_inbound(db, sender=nour.email, subject="Question", body=body, message_id=f"<facts-{key}-{datetime.utcnow().timestamp()}@example.com>")
    db.expire_all()
    inbound = db.get(CandidateEmail, out["email_id"])
    reply = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == inbound.thread_id, CandidateEmail.direction == "outbound")
             .order_by(CandidateEmail.received_at.desc()).first())
    return inbound, reply


inbound, reply = ask("Bonjour, combien coûte la formation DevOps ?", "fr")
check("candidate, French: the facts, with where they come from", inbound.status == "replied" and reply and f"{price_online} DT" in reply.body
      and "Source : les informations de 9antra sur ses formations." in reply.body, (inbound.status, inbound.escalation_reason))
inbound, reply = ask("9adech soum el formation devops?", "tn")
check("candidate, Tunisian: answered in Tunisian (not in French)", inbound.status == "replied" and reply and "soumha" in reply.body and reply.body.startswith("Aslema"),
      (inbound.status, inbound.escalation_reason, reply.body[:160] if reply else None))
inbound, reply = ask("بقداش سعر تكوين DevOps؟", "ar")
check("candidate, Arabic: answered in Arabic", reply is not None and "دينار" in (reply.body if inbound.status == "replied" else inbound.suggested_reply or "")
      and "إليك الإجابة بالفرنسية" not in (reply.body or ""), (inbound.status, inbound.escalation_reason))
inbound, reply = ask("Bonjour, combien coûte DevOps et est-ce qu'il y a un certificat ?", "mixed")
check("candidate: a question the facts only partly answer is a draft for the admin", inbound.status != "replied" and "certificates" in (inbound.escalation_reason or ""),
      (inbound.status, inbound.escalation_reason))

# ---- E. The posts of the Facebook and LinkedIn pages ----
post = "Nouvelle session DevOps ! Inscriptions ouvertes, démarrage le 15/11, 390 DT. #DevOps #9antra https://9antra.tn"
found = social_analysis.analyze(db, post)
check("posts: what one announces, the programs, dates and prices it gives", "session" in found["announces"] and "DevOps" in found["programs"]
      and "15/11" in found["dates"] and "390 DT" in found["prices"] and "#DevOps" in found["hashtags"], found)
raw = {"id": "urn:li:share:7100000000000000001", "commentary": "Atelier gratuit Python samedi 08/11 au centre des Berges du Lac !", "publishedAt": 1761300000000, "_linkedin": True}
item = website_sync.normalise("linkedin", raw)
check("LinkedIn: a post kept like the website's things", item and item["external_id"].startswith("li:") and "Atelier gratuit Python" in item["summary"]
      and item["url"].endswith("urn:li:share:7100000000000000001"), item)
check("LinkedIn: nothing read without its keys", not linkedin.configured() and linkedin.fetch_posts() == [])
response = admin.post("/settings/linkedin", data={"token": "x" * 64, "organization": "urn:li:organization:98765432"})
check("LinkedIn: an admin gives its keys (kept in .env)", "Connected" in where(response) and linkedin.configured()
      and os.environ.get("LINKEDIN_ORGANIZATION_ID") == "98765432", where(response))


class Answer:
    status_code = 200

    def json(self):
        return {"elements": [raw]}


asked = []
requests.get = lambda url, **kw: (asked.append((url, kw.get("params"), kw.get("headers"))) or Answer())
posts = linkedin.fetch_posts()
check("LinkedIn: read with LinkedIn's own interface, as the page", posts and asked and asked[0][0] == linkedin.API
      and asked[0][1]["author"] == "urn:li:organization:98765432" and asked[0][2]["Authorization"].startswith("Bearer "), asked[:1])
db.add(WebsiteItem(kind="linkedin", external_id=item["external_id"], title=item["title"], summary=item["summary"], url=item["url"], starts_at=item["starts_at"],
                   fingerprint="x", data=json.dumps(item["data"]), first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow()))
db.add(WebsiteItem(kind="post", external_id="fb:42_1", title="Nouvelle session DevOps !", summary=post, url="https://facebook.com/9antra.tn/posts/1",
                   starts_at=datetime.utcnow(), fingerprint="y", data=json.dumps({"message": post}), first_seen_at=datetime.utcnow(), last_seen_at=datetime.utcnow()))
db.commit()
knowledge_service.sync_knowledge(db)
learnt = db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("website:social:%")).all()
check("posts: each one learnt by the assistant, with what it announces", len(learnt) == 2 and any("Annonce : a new session" in row.content for row in learnt)
      and any("Publication LinkedIn" in row.title for row in learnt), [row.title for row in learnt])
page = admin.get("/website").text
check("posts: on the Website page, with what they announce", 'id="linkedin"' in page and "bi-linkedin" in page and "a workshop or an event" in page and "a new session" in page)

# ---- F. Keeping itself up to date ----
check("knowledge: rebuilt every half hour by itself", "knowledge_tick" in inspect.getsource(jobs.start) and "minutes=30" in inspect.getsource(jobs.start))
lina = Candidate(full_name="Lina Apprend", email="lina.apprend@example.com", phone="98 777 222")
db.add(lina)
db.commit()


def lina_asks(body, key):
    out = ea.ingest_inbound(db, sender=lina.email, subject="Question", body=body, message_id=f"<auto-{key}-{datetime.utcnow().timestamp()}@example.com>")
    db.expire_all()
    return db.get(CandidateEmail, out["email_id"])


question = "Bonjour, est-ce que vous acceptez les paiements par chèque bancaire ?"
first = lina_asks(question, "1")
check("learning: a new question goes to the admin", first.status != "replied", first.status)
ea.admin_reply(db, db.get(EmailThread, first.thread_id), "Oui, nous acceptons les chèques au centre, à l'ordre de 9antra.")
db.commit()
entry = (db.query(KnowledgeEntry).filter(KnowledgeEntry.origin == "learned", KnowledgeEntry.title.like("%chèque%")).first())
check("learning: the admin's own answer is learnt at once", entry is not None and "à l'ordre de 9antra" in entry.content, entry.title if entry else None)
second = lina_asks(question, "2")
db.expire_all()
answer = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == second.thread_id, CandidateEmail.direction == "outbound")
          .order_by(CandidateEmail.received_at.desc()).first())
check("learning: the same question is then answered alone, with the admin's words", second.status == "replied" and answer is not None
      and "chèques au centre" in (answer.body or ""), (second.status, second.escalation_reason))
# A draft the admin changes before sending: the changed answer is what is learnt.
other = lina_asks("Bonjour, est-ce que je peux payer en plusieurs fois ?", "5")
ea.admin_reply(db, db.get(EmailThread, other.thread_id), "Oui, en deux fois : la moitié à l'inscription, le reste à la troisième semaine.")
db.commit()
again = lina_asks("Bonjour, est-ce que je peux payer en plusieurs fois ?", "6")
ea.admin_reply(db, db.get(EmailThread, again.thread_id), "Oui, en trois fois maximum : à l'inscription, à la 3e et à la 6e semaine.")
db.commit()
db.expire_all()
kept = db.query(KnowledgeEntry).filter(KnowledgeEntry.title.like("%plusieurs fois%"), KnowledgeEntry.origin != "system").all()
check("learning: a later edit replaces the answer, not a second one", len(kept) == 1 and "trois fois" in kept[0].content and kept[0].origin == "learned",
      [(item.origin, item.content[:40]) for item in kept])
personal = lina_asks("Bonjour, quand commence ma classe ?", "4")
before = db.query(KnowledgeEntry).filter(KnowledgeEntry.origin.in_(("auto", "learned"))).count()
ea.admin_reply(db, db.get(EmailThread, personal.thread_id), "Votre classe commence samedi à 9h.")
db.commit()
check("learning: an answer about the candidate's own class is never kept",
      db.query(KnowledgeEntry).filter(KnowledgeEntry.origin.in_(("auto", "learned"))).count() == before)
from app.services import knowledge_service as _ks

_ks.sync_knowledge(db)
check("knowledge: the automatic messages, the professors and the settings are known",
      db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("system:message:%")).count() >= 10
      and db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key == "system:professors").count() == 1
      and db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key == "system:settings").count() == 1)

# ---- G. The admin's assistant in Tunisian, and the settings ----
reply = chatbot.answer(db, "9adech soum el formation devops?", [], user=auth.CurrentUser(id="a", name="A", email="a", role="admin"))
check("admin's assistant: a program question in Tunisian, answered in Tunisian", "soumha" in reply["reply"], reply["reply"][:120])
page = admin.get("/settings").text
check("settings: answer people not registered yet (on), the centre's address", 'id="prospects"' in page and 'name="address"' in page)

db.close()
check.done()
