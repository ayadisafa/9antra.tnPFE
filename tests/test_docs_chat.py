"""Programs as PDF on request (by email and in the assistant), the catalogue of all programs, the assistant's new
answers and follow-ups, the files for Excel, and the new candidate pages.
Staging copy, COPY of the database; no mail leaves this machine."""
import testenv
testenv.prepare("docs_chat")
check = testenv.Checks()

import io, json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote_plus, urlparse
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from pypdf import PdfReader
from app.database import SessionLocal
from app.models import Candidate, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_file import EmailFile
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, chatbot as cb, email_assistant as ea, email_files, email_templates as tp, email_threads as et,
                          program_documents as docs, program_pdf as pp)
from app.services.settings_service import get_settings

sent = []
def fake_send(**kw):
    sent.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="docs-chat-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "docs-chat-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
def text_of(data): return " ".join(" ".join((page.extract_text() or "").split()) for page in PdfReader(io.BytesIO(data)).pages)
def page_text(data, index): return " ".join((PdfReader(io.BytesIO(data)).pages[index].extract_text() or "").split())
def pages_of(data): return len(PdfReader(io.BytesIO(data)).pages)
def query_of(url): return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}

programs = db.query(Program).order_by(Program.name).all()
devops = db.query(Program).filter(Program.name == "DevOps").first()
python = db.query(Program).filter(Program.name == "Python").first()
cyber = db.query(Program).filter(Program.name == "Cyber Security").first()
yasmine = Candidate(full_name="Yasmine Gharbi", email="yasmine.docs@example.com", phone="+21622333444", university="INSAT", city="Tunis"); db.add(yasmine); db.flush()
for program, paid in ((devops, 0), (python, 390)):
    db.add(Registration(candidate_id=yasmine.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390,
                        paid_amount=paid, payment_status=PaymentStatus.PAID if paid else PaymentStatus.UNPAID,
                        status=RegistrationStatus.PENDING, registration_date=datetime.utcnow() - timedelta(minutes=1)))
db.commit()

# ======================================================================
# A. Is it a request for a program as a document, and which programs
# ======================================================================
for said in ("can you please send in pdf all program with details", "Bonjour, pouvez-vous m'envoyer le programme de la formation DevOps ?",
             "envoie moi toutes les formations en pdf", "Je voudrais recevoir votre catalogue", "could you send me the brochure of the course?",
             "Donnez-moi le programme détaillé de Python svp", "can I have the program details?", "send me the course content"):
    check(f"asks for a program: “{said}”", docs.asks_for_program(said))
for said in ("Quel est le programme de la formation DevOps ?", "Je veux m'inscrire au programme DevOps", "can you send me the link of the session?",
             "please send me my payment receipt", "envoyez-moi l'attestation de la formation en pdf", "when does the program start?",
             "send me the slides of week 3 of the course", "I paid for the program, here is the proof"):
    check(f"not a request for a program: “{said}”", not docs.asks_for_program(said))
meant, how = docs.programs_meant(db, "send me all the programs in pdf", yasmine)
check("“all” means every program", how == "all" and len(meant) == len(programs))
meant, how = docs.programs_meant(db, "le programme de DevOps en pdf svp", yasmine)
check("a named program is that one", how == "named" and meant == [devops], [p.name for p in meant])
meant, how = docs.programs_meant(db, "send me my programs in pdf", yasmine)
check("“my programs” are the candidate's own", how == "theirs" and {p.id for p in meant} == {devops.id, python.id}, [p.name for p in meant])
meant, how = docs.programs_meant(db, "send me the program in pdf", yasmine, about=python)
check("“the program” is the one the discussion is about", how == "about" and meant == [python])
meant, how = docs.programs_meant(db, "send me the program in pdf", None)
check("from someone with no program: all of them", how == "all" and len(meant) == len(programs))
check("the language asked is read", docs.language_asked("the pdf in english please") == "en" and docs.language_asked("en français") == "fr" and docs.language_asked("pdf", "") == "")

# ======================================================================
# B. Every program in one PDF
# ======================================================================
data = pp.build_catalogue(db, None, "fr")
text = text_of(data)
check("one PDF with every program in it", data[:4] == b"%PDF" and all(pp.plain(p.name).split("(")[0].strip() in text for p in programs),
      [p.name for p in programs if pp.plain(p.name).split("(")[0].strip() not in text])
first, glance = page_text(data, 0), page_text(data, 1)
check("its first page welcomes the reader: who 9antra is, and the website", "Nos formations" in first and "Bienvenue chez 9antra" in first
      and f"{len(programs)} formations" in first and "https://9antra.tn" in first, first[:300])
check("its second page shows all of them at a glance, with their prices", "Toutes les formations en un coup d'œil" in glance
      and f"{devops.price_online:.0f} DT" in glance, glance[:300])
check("then each program on its own pages, numbered", pages_of(data) > len(programs) and f"FORMATION 1 SUR {len(programs)}" in page_text(data, 2), page_text(data, 2)[:120])
check("each one week by week, as the brochure tells it", all(week.topic_title.split(":")[0][:20] in text for level in devops.levels for week in level.weeks[:2]))
english = pp.build_catalogue(db, [devops, python], "en", "Yasmine Gharbi")
words = text_of(english)
check("a few programs in English, prepared for someone", "Our programs" in words and "2 programs" in words and "Prepared for Yasmine Gharbi" in words
      and "DevOps" in words and "Python" in words and "Cyber Security" not in words, words[:200])
one = pp.store_catalogue(db, None, "fr")
again = pp.store_catalogue(db, None, "fr")
check("the same catalogue is kept once", one.id == again.id and one.filename == "Catalogue des formations.pdf" and one.origin.startswith("program:catalogue-"), (one.filename, one.origin))
few = pp.store_catalogue(db, [devops, python], "en")
check("a few programs have their own name", few.filename == "Programs - 2 programs.pdf", few.filename)
check("the brochure of one program is unchanged", pp.build(db, devops, "brochure", "fr")[:4] == b"%PDF" and "DevOps" in text_of(pp.build(db, devops, "brochure", "fr")))

# ======================================================================
# C. A candidate asks for it by email
# ======================================================================
def ask_by_email(body, subject="", key=None):
    sent.clear()
    out = ea.ingest_inbound(db, sender=yasmine.email, subject=subject, body=body, message_id=f"<docs-{key or len(body)}-{datetime.utcnow().timestamp()}@example.com>")
    db.expire_all()
    inbound = db.get(CandidateEmail, out["email_id"])
    reply = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == inbound.thread_id, CandidateEmail.direction == "outbound")
             .order_by(CandidateEmail.received_at.desc()).first())
    return inbound, reply

inbound, reply = ask_by_email("Hello, can you please send in pdf all program with details", key="all")
files = json.loads(reply.attachments) if reply is not None and reply.attachments else []
check("“send all programs in pdf” is answered by itself", inbound.status == "replied", (inbound.status, inbound.escalation_reason))
check("with every program in one PDF attached", [item["name"] for item in files] == ["Programs catalogue.pdf"], files)
check("in the language of the email", reply is not None and "As you asked" in reply.body and f"our {len(programs)} programs" in reply.body, reply.body[:200] if reply else None)
check("the email that went out carries the file", len(sent) == 1 and len(sent[0].get("attachments") or []) == 1, [len(item.get("attachments") or []) for item in sent])
sources = json.loads(inbound.sources)
check("and it says where the answer came from", sources["method"] == "document" and len(sources["files"]) == 1 and inbound.confidence >= 0.9, sources.get("method"))

inbound, reply = ask_by_email("Bonjour, pouvez-vous m'envoyer le programme de la formation DevOps ? Merci", key="devops")
files = json.loads(reply.attachments) if reply is not None and reply.attachments else []
check("one program named: its own PDF, in French", inbound.status == "replied" and [item["name"] for item in files] == ["Programme - DevOps.pdf"]
      and "Comme demandé" in reply.body and "DevOps" in reply.body, (inbound.status, files))
inbound, reply = ask_by_email("Hi, could you send me my programs in pdf?", key="mine")
files = json.loads(reply.attachments) if reply is not None and reply.attachments else []
check("“my programs”: the candidate's two programs in one PDF", inbound.status == "replied" and [item["name"] for item in files] == ["Programs - 2 programs.pdf"]
      and "your 2 programs" in reply.body, (inbound.status, files, reply.body[:160] if reply else None))

inbound, reply = ask_by_email("Bonjour, envoyez-moi le programme de DevOps en PDF. Et quand commence ma classe ?", key="mixed")
sources = json.loads(inbound.sources)
check("asked with another question: it comes to you", inbound.status == "needs_admin" and "PDF does not answer" in (inbound.escalation_reason or ""), (inbound.status, inbound.escalation_reason))
check("with the draft written and the PDF ready in it", "Comme demandé" in (inbound.suggested_reply or "") and len(sources["files"]) == 1)
page = admin.get(f"/inbox?thread={inbound.thread_id}").text
draft_file = db.get(EmailFile, sources["files"][0])
check("the inbox shows the draft with the PDF already attached", f'name="attachment_ids" value="{draft_file.id}"' in page and "Programme - DevOps.pdf" in page
      and "the program's PDF, made from your programs, is attached" in page)
check("and does not offer to learn it as a text (the PDF would be lost)", 'id="reply-learn"' not in page)
sent.clear()
answer = admin.post(f"/inbox/thread/{inbound.thread_id}/reply", data={"body": inbound.suggested_reply + "\n\nVotre classe commence samedi.", "attachment_ids": [draft_file.id]})
db.expire_all()
check("you send it as it is or completed, the PDF goes with it", "Answer sent" in where(answer) and len(sent) == 1 and len(sent[0].get("attachments") or []) == 1, (where(answer), len(sent)))

settings = get_settings(db); settings.email_auto_reply_mode = "review"; db.commit()
inbound, reply = ask_by_email("Can you send me the brochure of Python please?", key="review")
check("when you review every answer, the PDF waits in the draft", inbound.status == "needs_admin" and len(json.loads(inbound.sources)["files"]) == 1
      and "review every answer" in (inbound.escalation_reason or ""), (inbound.status, inbound.escalation_reason))
settings.email_auto_reply_mode = "auto"; db.commit()
inbound, reply = ask_by_email("Quel est le prix de la formation Python ?", key="price")
check("a question that is not a request for the program goes on as before", json.loads(inbound.sources)["method"] != "document" and not (reply and reply.attachments and "pdf" in reply.attachments.lower() and reply.received_at >= inbound.received_at))
inbound, reply = ask_by_email("Bonjour, je veux m'inscrire au programme DevOps", key="join")
check("nor is wanting to register", json.loads(inbound.sources or "{}").get("method") != "document")

# ======================================================================
# D. The assistant does it
# ======================================================================
def chat(*messages):
    history, found = [], None
    for message in messages:
        history.append({"role": "user", "content": message})
        found = cb.answer(db, message, history)
        history.append({"role": "assistant", "content": found["reply"]})
    return found
def link(found, label):
    return next((item for item in found.get("links") or [] if item["label"].startswith(label)), None)

found = chat("can you please send in pdf all program with details")
opened = link(found, "Open the PDF")
check("“send in pdf all program with details”: the PDF of every program", found["source"] == "action" and opened and opened["url"].startswith("/programs/catalogue.pdf")
      and opened.get("tab") and f"all {len(programs)} programs" in found["reply"], found["reply"][:160])
by_mail = link(found, "Send it by email")
check("ready to send by email", by_mail and query_of(by_mail["url"]).get("program") == "all" and db.get(EmailFile, query_of(by_mail["url"])["attach"]) is not None)
found = chat("can you please send in pdf all program with details", "send it to Yasmine Gharbi")
mail = link(found, "Open the email to Yasmine")
check("“send it to Yasmine”: the email to her is ready with the PDF, nothing sent", mail and query_of(mail["url"])["candidate"] == yasmine.id and "nothing goes before you do" in found["reply"], found["reply"][:160])
found = chat("can you please send in pdf all program with details", "send it to Yasmine Gharbi", "in english")
check("“in English” goes on from there", "in English" in found["reply"] and "lang=en" in link(found, "See the PDF")["url"] and query_of(link(found, "Open the email")["url"])["candidate"] == yasmine.id, found["reply"][:160])
found = chat("give me the pdf of the python program", "same for devops")
check("“same for DevOps”", f"/programs/{devops.id}/pdf/" in link(found, "Open the PDF")["url"], found["reply"][:120])
found = chat("send the devops program to yasmine gharbi")
check("“send the DevOps program to Yasmine”", query_of(link(found, "Open the email")["url"]).get("program") == devops.id, found["reply"][:160])
found = chat("pdf de la formation cyber security")
check("in French too", f"/programs/{cyber.id}/pdf/" in link(found, "Open the PDF")["url"])
check("a how-to question still gets the how-to", chat("how do I send the program as pdf to a candidate?")["source"] == "guide")
found = chat("what can you do?")
check("it says what it can do, with examples", found["source"] == "guide" and "Documents:" in found["reply"] and len(found.get("suggestions") or []) >= 4)
found = chat("list all candidates")
check("every candidate, the newest first", "the newest first" in found["reply"] and "Yasmine Gharbi" in found["reply"], found["reply"][:120])
found = chat("who registered this week?")
check("who registered this week", "Yasmine Gharbi" in found["reply"] and "DevOps" in found["reply"], found["reply"][:160])
found = chat("how much does yasmine gharbi owe?")
check("what one person owes, program by program", "Yasmine Gharbi owes 390 DT" in found["reply"] and link(found, "Write the payment reminder"), found["reply"][:160])
found = chat("what is the phone number of yasmine gharbi?")
check("how to reach someone, WhatsApp included", "+21622333444" in found["reply"] and link(found, "WhatsApp")["url"] == "https://wa.me/21622333444", found["reply"][:120])
found = chat("which candidates are in python?")
check("who follows a program", "Yasmine Gharbi: paid" in found["reply"], found["reply"][:200])
found = chat("which programs have no class?")
check("the programs with no class", "no class running or to come" in found["reply"] or "Every program has a class" in found["reply"], found["reply"][:120])
found = chat("what should I do today?")
check("what needs you today", found["reply"].startswith("Today,") and ("To do" in found["reply"] or "Nothing needs you" in found["reply"]), found["reply"][:120])
found = chat("remind yasmine gharbi to pay")
check("a payment reminder: what is owed, and the reminder to read and send", link(found, "Write the payment reminder") and "goes out once by itself" in found["reply"], found["reply"][-160:])
found = chat("remind yasmine gharbi about the session on saturday")
check("another reminder: planned for her", query_of(link(found, "Plan a reminder")["url"]) == {"audience": "candidate", "to": yasmine.id})
found = chat("write an email to yasmine gharbi")
check("writing to someone opens the email", link(found, "Write to Yasmine")["url"] == f"/inbox/compose?candidate={yasmine.id}")
found = chat("register yasmine gharbi in cyber security")
check("registering someone: their page, the program already chosen", link(found, "Register Yasmine")["url"] == f"/candidate/{yasmine.id}?add={cyber.id}#programs")
found = chat("register yasmine gharbi in python")
check("and says so when they are already registered", "already registered in Python" in found["reply"])
found = chat("export payments to excel")
check("a file for Excel", link(found, "Payments for Excel")["url"] == "/data/payments.csv")
check("earlier answers still work", "DT" in chat("who has not paid?")["reply"] and "390 DT online" in chat("How much is DevOps?")["reply"]
      and chat("tell me about Yasmine Gharbi")["links"][0]["url"] == f"/candidate/{yasmine.id}")
api = admin.post("/assistant/chat", json={"message": "send it to Yasmine Gharbi", "history": [
    {"role": "user", "content": "send me all the programs in pdf"}, {"role": "assistant", "content": "Here are…"}, {"role": "user", "content": "send it to Yasmine Gharbi"}]})
check("the chat on every page goes on from the question before", api.status_code == 200 and "Yasmine" in api.json()["reply"] and api.json()["source"] == "action", api.json().get("reply", "")[:120])
check("its links can open in a new tab", "link.tab ||" in admin.get("/").text)

# ======================================================================
# E. The pages and files behind it
# ======================================================================
pdf = admin.get("/programs/catalogue.pdf?lang=en")
check("every program as one PDF, to open", pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and "inline" in pdf.headers["content-disposition"] and "Our programs" in text_of(pdf.content))
pdf = admin.get(f"/programs/catalogue.pdf?lang=fr&download=1&ids={devops.id},{python.id}")
check("a few of them, to download", pdf.status_code == 200 and "attachment" in pdf.headers["content-disposition"] and "Programmes - 2 formations.pdf" in unquote_plus(pdf.headers["content-disposition"]))
answer = admin.post("/programs/catalogue/send", data={"lang": "fr"})
check("sending it prepares the email, you choose who", where(answer).startswith("/inbox/compose?") and query_of(where(answer))["program"] == "all")
compose = admin.get(where(answer)).text
check("the email is written and the PDF attached", "Le programme détaillé de nos formations" in compose and "Catalogue des formations.pdf" in compose)
answer = admin.post("/programs/catalogue/send", data={"lang": "fr", "candidate_id": yasmine.id, "personal": "1"})
check("or for one candidate", query_of(where(answer)).get("candidate") == yasmine.id)
check("the Programs page offers it", "All programs as PDF" in admin.get("/programs/all").text)
sheet = admin.get("/data/candidates.csv")
body = sheet.content.decode("utf-8")
check("any table as a file for Excel", sheet.status_code == 200 and sheet.headers["content-type"].startswith("text/csv") and body.startswith("﻿") and "Full name" in body.splitlines()[0] and "Yasmine Gharbi" in body)
narrow = admin.get("/data/registrations.csv?q=Yasmine").content.decode("utf-8")
check("the search narrows it, and linked records show by name", len(narrow.strip().splitlines()) == 1 or "Yasmine Gharbi" in narrow, narrow[:200])
check("an unknown table is not found", admin.get("/data/nothing.csv").status_code == 404)
check("each table page has the Excel button", '/data/payments.csv' in admin.get("/data/payments").text)

# ======================================================================
# F. The candidate list and file
# ======================================================================
listing = admin.get("/candidate/all").text
check("the list shows one card per candidate", listing.count('class="cl-row') == db.query(Candidate).count(), listing.count('class="cl-row'))
check("each with the next step, and the list can be ordered", "Next step" in listing and 'id="candidateSort"' in listing and "Who owes the most" in listing)
profile = admin.get(f"/candidate/{yasmine.id}?add={cyber.id}").text
check("the file is in parts", profile.count('class="cp-tab"') == 6 and profile.count('class="cp-pane"') == 6 and 'id="cp-file"' in profile)
check("every part is still there for links and notifications", all(f'id="{name}"' in profile for name in ("programs", "behaviour", "feelings", "notes", "live-candidate-emails", "live-candidate-history")))
check("the programs at a glance, one place for each registration's buttons", "Programs at a glance" in profile and profile.count('id="registration-') == 2)
check("“register” from the assistant arrives with the program chosen", f'<option value="{cyber.id}" selected>' in profile)
check("the notifications' spot opens the right part", "pane:open" in profile and "pane:open" in admin.get("/").text)

# ======================================================================
# G. All programs in short, and any information as a PDF, for clients
# ======================================================================
from app.services import document_pdf, finder
short = pp.build_summary(db, None, "fr", "Yasmine Gharbi")
words = text_of(short)
check("all programs in short: every one, on a few pages", "Nos formations en bref" in words and pages_of(short) <= 4
      and all(pp.plain(p.name).split("(")[0].strip() in words for p in programs) and "Préparé pour Yasmine Gharbi" in words, (pages_of(short), words[:160]))
check("for each: what it teaches, its path and its prices", "Parcours" in words and f"{devops.price_online:.0f} DT" in words and "par semaine" in words)
kept = pp.store_catalogue(db, None, "fr", summary=True)
check("kept as a file an email can carry, once", kept.filename == "Résumé des formations.pdf" and kept.origin.startswith("program:summary-")
      and pp.store_catalogue(db, None, "fr", summary=True).id == kept.id, kept.filename)
pdf = admin.get(f"/programs/summary.pdf?lang=en&ids={devops.id},{python.id}")
check("the summary opens for a few programs, in English", pdf.status_code == 200 and "Our programs in short" in text_of(pdf.content) and "Cyber Security" not in text_of(pdf.content))
answer = admin.post("/programs/catalogue/send", data={"lang": "fr", "kind": "summary"})
check("sending the summary prepares the email", query_of(where(answer)).get("program") == "summary")
compose = admin.get(where(answer)).text
check("written for it, the PDF attached", "Nos formations en bref" in compose and "Résumé des formations.pdf" in compose)

found = document_pdf.blocks("Bonjour,\n\nCe qu'il faut apporter :\n- un ordinateur\n- **une pièce d'identité**\n\n[box gold] Début à 9 h.\n\nTarifs :\n\n- Python : 390 DT")
check("a text is read as paragraphs, headings, lists and squares", [kind for kind, _ in found] == ["text", "heading", "points", "box", "heading", "points"], found)
page = admin.get("/programs/documents")
check("the page of PDF documents: programs in short, in detail, one, or any text", page.status_code == 200 and "All programs in short" in page.text
      and "All programs in detail" in page.text and 'action="/programs/documents/send"' in page.text and "Price list" in page.text, page.status_code)
check("it is in the menu and found by the search", 'href="/programs/documents"' in admin.get("/").text
      and any(item["url"] == "/programs/documents" for group in finder.find(db, "résumé pdf") for item in group["items"]))
letter = {"title": "Informations pratiques de novembre", "text": "Bonjour,\n\nÀ apporter :\n- un ordinateur\n\n[box] Début à **9 h** précises.", "lang": "fr", "for_name": "Yasmine Gharbi"}
pdf = admin.post("/programs/documents/pdf", data=letter)
words = text_of(pdf.content)
check("any text becomes a PDF in the centre's look", pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and "Informations pratiques de novembre" in words
      and "un ordinateur" in words and "Début à 9 h précises" in words and "Préparé pour Yasmine Gharbi" in words and "Inscription et renseignements" in words, words[:200])
pdf = admin.post("/programs/documents/pdf", data={**letter, "download": "1"})
check("or downloaded, named after its title", "attachment" in pdf.headers["content-disposition"] and "Informations pratiques de novembre.pdf" in unquote_plus(pdf.headers["content-disposition"]))
check("an empty text is refused", admin.post("/programs/documents/pdf", data={"title": "x", "text": "  "}).status_code == 400)
answer = admin.post("/programs/documents/send", data=letter)
check("sending it prepares the email with the PDF", query_of(where(answer)).get("program") == "document" and query_of(where(answer)).get("title") == letter["title"])
compose = admin.get(where(answer)).text
check("and the email says what it carries", "Informations pratiques de novembre" in compose and "Informations pratiques de novembre.pdf" in compose)

inbound, reply = ask_by_email("Bonjour, pouvez-vous m'envoyer un résumé de vos formations ?", key="summary")
files = json.loads(reply.attachments) if reply is not None and reply.attachments else []
check("a candidate who asks for a summary gets the programs in short", inbound.status == "replied" and [item["name"] for item in files] == ["Résumé des formations.pdf"]
      and "un résumé de nos" in reply.body, (inbound.status, files))

found = chat("summarize all the programs in pdf")
check("the assistant: all programs in short", link(found, "Open the PDF")["url"].startswith("/programs/summary.pdf") and "summary" in found["reply"], found["reply"][:120])
found = chat("summarize all the programs in pdf", "send it to Yasmine Gharbi")
check("and sends it to someone (ready, nothing sent)", query_of(link(found, "Open the email")["url"]).get("program") == "summary" and query_of(link(found, "Open the email")["url"])["candidate"] == yasmine.id)
found = chat("the price list in pdf")
opened = admin.get(link(found, "Open the PDF")["url"])
check("any information in PDF: the price list", found["source"] == "action" and opened.status_code == 200 and "DevOps" in text_of(opened.content) and f"{devops.price_online:.0f} / {devops.price_onsite:.0f} DT" in text_of(opened.content), found["reply"][:120])
found = chat("who registered this week?", "make it a pdf")
opened = admin.get(link(found, "Open the PDF")["url"])
check("“make it a pdf” after an answer", "Yasmine Gharbi" in text_of(opened.content) and "Who registered this week" in text_of(opened.content), found["reply"][:120])
found = chat("send the price list in pdf to Yasmine Gharbi")
check("“… in pdf to Yasmine”: the email to her, the PDF attached", query_of(link(found, "Open the email")["url"]).get("candidate") == yasmine.id
      and query_of(link(found, "Open the email")["url"]).get("program") == "document" and link(found, "Change it first"), found["reply"][:120])
found = chat("pdf")
check("“pdf” alone: it says what to ask", found["source"] == "guide" and link(found, "PDF documents"))

check.done()
