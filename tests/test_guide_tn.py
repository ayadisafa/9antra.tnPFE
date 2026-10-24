"""The guide and the assistant in Tunisian, Arabic, French and English (steps drawn as a diagram), what is still missing
in the application, the assistant learning from a link or a pasted text (never LinkedIn, Facebook or Google, never this
computer), and a program's PDF sent at once.
Runs on a COPY; email, WhatsApp and SMS are fakes; no web page is reached (pages are faked)."""
import json
import socket
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("guide_tn")
check = testenv.Checks()

import requests

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.knowledge import KnowledgeEntry
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, chatbot, completeness, derja, email_assistant as ea, email_templates as tp, email_threads as et, howto,
                          howto_tn, knowledge_import, knowledge_service, program_documents)
from app.services.settings_service import get_settings

sent = []


def fake_send(**kw):
    sent.append(kw)
    return True


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="guide-tn-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "guide-tn-1"})
boss = auth.CurrentUser(id="a", name="A", email="a", role="admin")

# ---- A. The guide in four languages ----
check("language: Tunisian in Latin letters", howto.language_of("kifech nsajjel khlas candidat?") == "tn")
check("language: Arabic letters", howto.language_of("كيف أسجل دفع مترشح؟") == "ar")
check("language: French", howto.language_of("comment enregistrer un paiement ?") == "fr")
check("language: English", howto.language_of("how do I record a payment?") == "en")

found = howto.answer(db, "kifech nsajjel khlas candidat?", boss)
check("tn: “nsajjel khlas” is recording a payment (not the registration form)", found["reply"].startswith("Sajjel khlas:") and "docs.google.com" not in found["reply"],
      found["reply"][:120])
check("tn: the steps drawn as a diagram, with the page to open", found["schema"]["title"] == "Sajjel khlas" and len(found["schema"]["steps"]) == 3
      and found["schema"]["url"] == found["links"][0]["url"], found.get("schema"))
found = howto.answer(db, "كيف أسجل دفع مترشح؟", boss)
check("ar: the same task, in Arabic", found["reply"].startswith("تسجيل دفع:") and "المترشحون › افتح المترشح" in found["reply"], found["reply"][:120])
found = howto.answer(db, "comment enregistrer un paiement ?", boss)
check("fr: in French", found["reply"].startswith("Enregistrer un paiement:") and "Candidats › ouvrez le candidat" in found["reply"], found["reply"][:120])
found = howto.answer(db, "how do I record a payment?", boss)
check("en: in English", found["reply"].startswith("Record a payment:") and found["lang"] == "en")
found = howto.answer(db, "kifech na3mel classe jdida?", boss)
check("tn: “na3mel classe jdida” is creating a class", found["reply"].startswith("A3mel classe:"), found["reply"][:80])
found = howto.answer(db, "كيف أضيف برنامج؟", boss)
check("ar: adding a program", found["reply"].startswith("إضافة برنامج:"), found["reply"][:80])
form = howto.answer(db, "kifech yetsajel candidat?", boss)
check("tn: how somebody registers gives the form, in Tunisian", "docs.google.com/forms" in form["reply"] and form["reply"].startswith("Bech yetsajjel"), form["reply"][:120])
form = howto.answer(db, "كيف يسجل مترشح؟", boss)
check("ar: and in Arabic", "docs.google.com/forms" in form["reply"] and form["reply"].startswith("للتسجيل"), form["reply"][:120])

keys = [task[0] for task in howto.TASKS]
check("tn: every task of the guide is written in Tunisian", not [key for key in keys if key not in howto_tn.TN], [key for key in keys if key not in howto_tn.TN])
untranslated = [step for task in howto.TASKS for step in [task[2]] + list(task[3])
                if howto._words(step, "ar") == step or (howto._words(step, "fr") == step and step not in ("Classes",))]
check("fr/ar: every title and step of the guide has its French and Arabic", not untranslated, untranslated[:3])

reply = chatbot.answer(db, "kifech nsajjel khlas candidat?", [], user=boss)
check("assistant: a Tunisian how-to goes to the guide, with its diagram", reply["source"] == "guide" and reply.get("schema") and reply["reply"].startswith("Sajjel khlas"),
      (reply.get("source"), reply["reply"][:80]))
reply = chatbot.answer(db, "كيف أسجل دفع مترشح؟", [], user=boss)
check("assistant: an Arabic one too", reply["source"] == "guide" and reply["reply"].startswith("تسجيل دفع"), reply["reply"][:80])
page = admin.get("/").text
check("diagram: drawn in the assistant's and Yasmine's windows", "window.drawSchema" in page and 'id="fr-schema"' in page)

# ---- B. What is still missing ----
program = db.query(Program).filter(Program.is_approved.is_(True)).order_by(Program.name).first()
bare = Program(name="Zeta Bare Program", description="", price_online=0, price_onsite=0, is_approved=True)
db.add(bare)
lonely = ClassGroup(name="MISS-NOPROF", program_id=program.id, status=ClassStatus.NOT_STARTED, start_date=datetime.now() + timedelta(days=9),
                    total_weeks=8, start_time=dtime(9, 0), end_time=dtime(11, 0), days_of_week="mon")
db.add(lonely)
db.commit()
lines = completeness.items(db)
texts = " | ".join(item["text"] for item in lines)
check("missing: a program without description, with its name", any("programs have no description" in item["text"] and "Zeta Bare Program" in item["names"] for item in lines), texts[:300])
check("missing: a program without price", any(("programs have no price" in item["text"] or "program has no price" in item["text"]) and "Zeta Bare Program" in item["names"] for item in lines))
check("missing: a class without professor", any("classes have no professor" in item["text"] and "MISS-NOPROF" in item["names"] for item in lines))
check("missing: the sending address when no email can leave", any("No email can leave" in item["text"] for item in lines) or et.smtp_configured())
check("missing: each line opens where to complete it", all(item["url"].startswith("/") for item in lines))
check("missing: asked in every language", all(completeness.asks(howto.understood(text)) for text in
                                               ("what is missing?", "qu'est-ce qui manque ?", "chnowa na9es?", "ماذا ينقص؟")))
said = howto.answer(db, "chnowa na9es?", boss)
check("missing: Tunisian head, the lines in French, a checklist", said["reply"].startswith("Chnowa mazel na9es") and "formations n'ont pas de description" in said["reply"]
      and said["schema"]["kind"] == "checklist" and said["schema"]["url"] == "/system#missing", said["reply"][:160])
said = howto.answer(db, "ماذا ينقص؟", boss)
check("missing: in Arabic", "برامج بلا وصف" in said["reply"], said["reply"][:160])
said = chatbot.answer(db, "what is still missing?", [], user=boss)
check("missing: the assistant gives the same list", said["source"] == "system" and "programs have no description" in said["reply"], said["reply"][:120])
page = admin.get("/system").text
check("missing: on the System page", 'id="missing"' in page and "Zeta Bare Program" in page and "MISS-NOPROF" in page)
check("missing: Yasmine points to it", "/system#missing" in json.dumps(admin.get("/account/friend").json()))
teacher = Professor(name="Rim Prof", specialties=program.name, email="rim.prof@example.com", phone="")
db.add(teacher)
db.flush()
ready = ClassGroup(name="MISS-RIM", program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.ONGOING,
                   start_date=datetime.now() - timedelta(days=14), total_weeks=8, start_time=dtime(14, 0), end_time=dtime(16, 0), days_of_week="sun")
db.add(ready)
db.flush()
db.add(ClassSession(class_group_id=ready.id, week_number=3, session_date=date.today() + timedelta(days=2), start_time=dtime(14, 0), end_time=dtime(16, 0), topic="Git"))
db.commit()
rim = auth.CurrentUser(id="r", name="Rim Prof", email="rim.prof@example.com", role="professor", professor_id=teacher.id)
mine = completeness.for_professor(db, rim)
check("missing for a professor: the resources of their coming week, and their phone", any("MISS-RIM · 3" in item["names"] for item in mine)
      and any(item["text"] == "Your phone number is missing" for item in mine), mine)
check("missing for a professor: their list leads to their space", completeness.answer(db, rim, "en")["schema"]["url"] == "/teacher")

# ---- C. Learning from a link or a text ----
PAGES = {
    "partner.example": "93.184.216.34", "redirect.example": "93.184.216.35", "inner.example": "93.184.216.36",
    "wall.example": "93.184.216.37", "pdf.example": "93.184.216.38", "private.example": "192.168.1.10", "loop.example": "127.0.0.1",
}
real_lookup = socket.getaddrinfo


def lookup(host, port, *rest, **kw):
    if host in PAGES:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PAGES[host], port))]
    if host in ("10.0.0.5",):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
    return real_lookup(host, port, *rest, **kw)


class Page:
    def __init__(self, status=200, kind="text/html; charset=utf-8", body="", location=None):
        self.status_code, self.encoding = status, "utf-8"
        self.headers = {"content-type": kind, **({"location": location} if location else {})}
        self.is_redirect = location is not None
        self._body = body.encode("utf-8")

    def iter_content(self, size):
        for start in range(0, len(self._body), size):
            yield self._body[start:start + size]


PARTNER = ("<html><head><title>9antra chez Partner</title><script>var x=1;</script></head><body><h1>9antra et Partner</h1>"
           + "".join(f"<p>Le centre 9antra forme ses candidats au métier numéro {n} avec des projets réels, des formateurs du terrain "
                     f"et un accompagnement vers l'emploi. Paragraphe {n}.</p>" for n in range(12))
           + "<p>Code partenaire unique : ZEBRA-4471.</p></body></html>")
asked = []


def fake_get(address, **kw):
    asked.append(address)
    if "partner.example" in address:
        return Page(body=PARTNER)
    if "redirect.example" in address:
        return Page(status=301, location="https://www.facebook.com/9antra")
    if "inner.example" in address:
        return Page(status=302, location="http://10.0.0.5/admin")
    if "wall.example" in address:
        return Page(body="<html><body><p>Sign in to see this page. Join now to continue.</p>" + "<p>.</p>" * 40 + "</body></html>")
    if "pdf.example" in address:
        return Page(kind="application/pdf", body="%PDF-1.4")
    raise AssertionError(f"the test reached {address}")


socket.getaddrinfo = lookup
requests.get = fake_get


def refused(url):
    try:
        knowledge_import.fetch(url)
    except ValueError as err:
        return str(err)
    return ""


check("import: LinkedIn is never fetched", "LinkedIn, Facebook and Google" in refused("https://www.linkedin.com/company/9antra") and not asked)
check("import: nor Facebook, nor Google", "LinkedIn, Facebook and Google" in refused("https://facebook.com/9antra")
      and "LinkedIn, Facebook and Google" in refused("https://www.google.com/search?q=9antra") and not asked)
check("import: never this computer", "Only public web pages" in refused("http://loop.example/") and not asked)
check("import: never the local network", "Only public web pages" in refused("https://private.example/") and not asked)
check("import: only web addresses", "starts with https://" in refused("file:///C:/Windows/win.ini"))
check("import: a redirect to Facebook is refused", "leads to LinkedIn, Facebook or Google" in refused("https://redirect.example/9antra"))
check("import: a redirect into the local network is refused", "Only public web pages" in refused("https://inner.example/") and not any("10.0.0.5" in a for a in asked))
check("import: a sign-in wall is said", "does not show its text" in refused("https://wall.example/"))
check("import: a PDF is not read as a page", "not a web page" in refused("https://pdf.example/file"))
title, text = knowledge_import.fetch("https://partner.example/9antra")
check("import: a public page gives its title and readable text", title == "9antra chez Partner" and "ZEBRA-4471" in text and "var x" not in text, (title, text[:80]))

response = admin.post("/inbox/knowledge/import", data={"url": "https://partner.example/9antra", "title": ""})
db.expire_all()
rows = db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("import:%")).all()
check("import: learnt from the link, in parts, with its source", "Learnt:" in where(response) and len(rows) >= 2
      and all(row.origin == "system" and row.kind == "imported" and "partner.example" in row.title for row in rows), (where(response), len(rows)))
count = len(rows)
admin.post("/inbox/knowledge/import", data={"url": "https://partner.example/9antra"})
db.expire_all()
check("import: the same page again replaces it", db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("import:%")).count() == count)
pasted = ("9antra sur LinkedIn : 9antra est un centre de formation tunisien. Ses formations durent de 8 à 24 semaines, en ligne ou sur place, "
          "avec un accès au réseau des anciens et le parrainage KESTREL-208 pour chaque nouveau candidat.")
response = admin.post("/inbox/knowledge/import", data={"url": "https://www.linkedin.com/company/9antra", "text": pasted, "title": "9antra sur LinkedIn"})
check("import: LinkedIn's text pasted by the admin is learnt", "Learnt: 1 part" in where(response), where(response))
response = admin.post("/inbox/knowledge/import", data={"url": "https://www.linkedin.com/company/9antra"})
check("import: a LinkedIn link alone asks for its text", "copy its text and paste it below" in where(response), where(response))
response = admin.post("/inbox/knowledge/import", data={})
check("import: nothing given", "Give a link, or paste a text" in where(response))
hits = knowledge_service.search(db, "Quel est le code de parrainage pour un nouveau candidat ?", limit=5)
check("import: what was learnt is found by the assistant's search", any("KESTREL-208" in hit.get("content", "") for hit in hits), [hit.get("title") for hit in hits])
knowledge_service.sync_knowledge(db)
db.expire_all()
check("import: a rebuild of the knowledge keeps it", db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("import:%")).count() == count + 1)
check("knowledge: how sessions work and the path of a candidate are known", {"system:sessions", "system:path"} <= {
    row.source_key for row in db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.in_(["system:sessions", "system:path"])).all()})
page = admin.get("/inbox/knowledge").text
check("import: the card lists what was learnt, with a way to forget it", 'id="learn"' in page and "9antra sur LinkedIn" in page and "/inbox/knowledge/import/forget" in page)
key = next(item["key"] for item in knowledge_import.sources(db) if item["title"].startswith("9antra sur LinkedIn"))
response = admin.post("/inbox/knowledge/import/forget", data={"key": key})
db.expire_all()
check("import: forgotten", "Forgotten" in where(response) and not any(item["key"] == key for item in knowledge_import.sources(db)))
check("import: only imported knowledge can be forgotten this way", knowledge_import.forget(db, "system:") == 0)
socket.getaddrinfo = real_lookup

# ---- D. A program's PDF ----
devops = db.query(Program).filter(Program.name == "DevOps").first()
check("pdf: “what will I learn in DevOps?” asks for its content", program_documents.asks_about_content("Qu'est-ce qu'on apprend dans la formation DevOps ?")
      and program_documents.asks_about_content("chnowa n9raw fi DevOps?") and program_documents.asks_about_content("What will I learn in DevOps?"))
check("pdf: but not the price", not program_documents.asks_about_content("Combien coûte DevOps ?"))
yasmine = Candidate(full_name="Yasmine Content", email="yasmine.content@example.com", phone="98 123 456")
db.add(yasmine)
db.commit()
sent.clear()
out = ea.ingest_inbound(db, sender=yasmine.email, subject="DevOps", body="Bonjour, qu'est-ce qu'on apprend dans la formation DevOps ? Merci",
                        message_id=f"<content-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
inbound = db.get(CandidateEmail, out["email_id"])
reply = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == inbound.thread_id, CandidateEmail.direction == "outbound")
         .order_by(CandidateEmail.received_at.desc()).first())
files = json.loads(reply.attachments) if reply is not None and reply.attachments else []
check("pdf: asked what a program teaches, the candidate gets its PDF", inbound.status == "replied" and [item["name"] for item in files] == ["Programme - DevOps.pdf"],
      (inbound.status, inbound.escalation_reason, files))
settings = get_settings(db)
settings.assistant_auto_send = True
db.commit()
sent.clear()
before = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == yasmine.id, CandidateEmail.direction == "outbound").count()
done = chatbot.answer(db, "envoie le programme DevOps à Yasmine Content", [], user=boss)
db.expire_all()
after = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == yasmine.id, CandidateEmail.direction == "outbound").order_by(CandidateEmail.received_at.desc()).all()
check("pdf: “send it to Yasmine” with send-at-once on: it goes now, with the PDF", done["reply"].startswith("Envoyé à") and len(after) == before + 1
      and after[0].attachments and "DevOps" in after[0].attachments, (done["reply"][:120], len(after) - before))
staff_like = auth.CurrentUser(id="s", name="S", email="s", role="staff", permissions={"assistant": "view"})
done = chatbot.answer(db, "envoie le programme DevOps à Yasmine Content", [], user=staff_like)
check("pdf: somebody who may only look gets it prepared, not sent", "rien ne part avant" in done["reply"] or "nothing goes" in done["reply"], done["reply"][:120])

db.close()
check.done()
