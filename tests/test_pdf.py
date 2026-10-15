"""A program as a PDF: three looks, download, and the email that carries it to a candidate.
Staging copy, COPY of the database, no mail leaves this machine."""
import testenv
testenv.prepare("pdf")
check = testenv.Checks()

import io, json
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from pypdf import PdfReader
from urllib.parse import unquote_plus, urlparse, parse_qs
from app.database import SessionLocal
from app.models import Candidate, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_file import EmailFile
from app.services import auth, chatbot as cb, email_assistant as ea, email_files, email_templates as tp, email_threads as et, program_pdf as pp
from app.services.settings_service import get_settings

sent = []
def fake_send(**kw):
    sent.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="pdf-test-1", role=auth.ADMIN)
auth.create_user(db, email="prof@test.local", name="Test Prof", password="pdf-test-2", role=auth.PROFESSOR)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "pdf-test-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
def text_of(data): return " ".join(" ".join((page.extract_text() or "").split()) for page in PdfReader(io.BytesIO(data)).pages)
def pages_of(data): return len(PdfReader(io.BytesIO(data)).pages)

devops = db.query(Program).filter(Program.name == "DevOps").first()
bootcamp = db.query(Program).filter(Program.name == "Bootcamp 6 months").first()
weeks = sum(len(level.weeks) for level in devops.levels)

# ======================================================================
# A. What the PDF says
# ======================================================================
content = pp.content_of(db, devops, "fr")
check("the content is the program's own: name, description, levels and weeks", content["name"] == "DevOps" and content["about"] == " ".join(devops.description.split()) and len(content["levels"]) == len(devops.levels) and content["weeks"] == weeks, (content["weeks"], weeks))
numbers = [week["number"] for level in content["levels"] for week in level["weeks"]]
check("weeks are numbered from the first to the last across levels", numbers == list(range(1, weeks + 1)), numbers)
first = devops.levels[0].weeks[0]
check("what a week covers is split into points", content["levels"][0]["weeks"][0]["title"] == first.topic_title and len(content["levels"][0]["weeks"][0]["points"]) == len([p for p in first.learning_objectives.split(";") if p.strip()]))
check("a level's title loses its “Level 1:”", not content["levels"][0]["title"].lower().startswith("level") and content["levels"][0]["title"] in devops.levels[0].title)
facts = {key: (value, detail) for key, _, value, detail in content["facts"]}
check("the rhythm is the program's hours", facts["pace"][0] == "6 h par semaine" and "4 h de cours" in facts["pace"][1] and "2 h d'encadrement" in facts["pace"][1], facts.get("pace"))
check("prices are the program's own", [(item["label"], item["value"]) for item in content["prices"]] == [("En ligne", f"{devops.price_online:.0f} DT"), ("En présentiel", f"{devops.price_onsite:.0f} DT")], content["prices"])
english = pp.content_of(db, devops, "en")
check("in English the labels change, the content does not", [item["label"] for item in english["prices"]] == ["Online", "On site"] and english["levels"] == content["levels"] and {key: value for key, _, value, _ in english["facts"]}["pace"] == "6 h a week")
check("what the PDF's fonts cannot write is left out, accents stay", pp.plain("Sécurité  réseau \u2192 œuvre \u4e2d\u6587 😀") == "Sécurité réseau -> œuvre")
empty = Program(name="Programme vide", is_approved=True); db.add(empty); db.commit()
bare = pp.content_of(db, empty, "fr")
check("a program with nothing yet invents nothing", bare["levels"] == [] and bare["facts"] == [] and bare["about"] == "" and bare["tools"] == [] and [item["value"] for item in bare["prices"]] == [f"{empty.price_online:.0f} DT", f"{empty.price_onsite:.0f} DT"], bare)

# ======================================================================
# B. The three looks
# ======================================================================
check("three looks are offered", set(pp.STYLES) == {"brochure", "classic", "one_page"} and pp.DEFAULT_STYLE == "brochure" and pp.available())
made = {style: pp.build(db, devops, style, "fr") for style in pp.STYLES}
check("each look is a real PDF", all(data[:5] == b"%PDF-" and len(data) > 3000 for data in made.values()), {k: len(v) for k, v in made.items()})
check("each one names the program and its weeks", all("DevOps" in text_of(data) and first.topic_title in text_of(data) and devops.levels[-1].weeks[-1].topic_title in text_of(data) for data in made.values()))
check("the brochure and the classic say what each week covers", all(content["levels"][1]["weeks"][0]["points"][0] in text_of(made[style]) for style in ("brochure", "classic")))
check("the prices are written", all(f"{devops.price_online:.0f} DT" in text_of(data) and f"{devops.price_onsite:.0f} DT" in text_of(data) for data in made.values()))
check("the one-page look is one page, even for the longest program", pages_of(made["one_page"]) == 1 and pages_of(pp.build(db, bootcamp, "one_page")) == 1 and "S24" in text_of(pp.build(db, bootcamp, "one_page")))
check("a long program runs over several pages with every week", pages_of(pp.build(db, bootcamp, "brochure")) >= 3 and bootcamp.levels[-1].weeks[-1].topic_title in text_of(pp.build(db, bootcamp, "brochure")))
check("the same program gives the same file", all(pp.build(db, devops, style, "fr") == data for style, data in made.items()))
named = pp.build(db, devops, "brochure", "fr", "Amira Ben Salah")
check("with a candidate's name it says who it was prepared for", "Préparé pour Amira Ben Salah" in text_of(named) and "Préparé pour" not in text_of(made["brochure"]))
check("in English it is written in English", "Prepared for Amira" in text_of(pp.build(db, devops, "classic", "en", "Amira")) and "Week" in text_of(pp.build(db, devops, "classic", "en")))
check("a program with nothing yet still gives a PDF", all(pp.build(db, empty, style)[:5] == b"%PDF-" and "Programme vide" in text_of(pp.build(db, empty, style)) for style in pp.STYLES))
check("an unknown look falls back on the brochure", pp.build(db, devops, "nope") == made["brochure"] and pp.clean_language("xx") == "fr")
check("the file has a name people can read", pp.filename_of(devops) == "Programme - DevOps.pdf" and pp.filename_of(devops, "en") == "Program - DevOps.pdf" and "/" not in pp.filename_of(db.query(Program).filter(Program.name.like("Mobile Development%")).first()))

# ======================================================================
# C. The page, the download
# ======================================================================
page = admin.get(f"/programs/{devops.id}/pdf")
check("the PDF page opens with the three looks", page.status_code == 200 and all(f'id="look-{style}"' in page.text for style in pp.STYLES) and "Used by default" in page.text and "Send it to a candidate" in page.text, page.status_code)
check("the brochure is the look until another is chosen", f'/programs/{devops.id}/pdf/brochure?lang=fr&download=1' in page.text.replace("&amp;", "&"))
shown = admin.get(f"/programs/{devops.id}/pdf/classic")
check("a look opens in the browser", shown.status_code == 200 and shown.headers["content-type"] == "application/pdf" and shown.headers["content-disposition"].startswith("inline") and shown.content == made["classic"])
down = admin.get(f"/programs/{devops.id}/pdf/one_page?download=1&lang=en")
check("or is downloaded under its name", down.headers["content-disposition"].startswith('attachment; filename="Program - DevOps.pdf"') and down.content[:5] == b"%PDF-")
check("an unknown look or program is not found", admin.get(f"/programs/{devops.id}/pdf/fancy").status_code == 404 and admin.get("/programs/nope/pdf").status_code == 404 and admin.get("/programs/nope/pdf/brochure").status_code == 404)
answer = admin.post("/programs/pdf-look", data={"style": "classic", "program_id": devops.id})
db.expire_all()
check("a look is chosen as the default", answer.status_code == 303 and "is now the look used by default" in where(answer) and get_settings(db).program_pdf_style == "classic", where(answer))
page = admin.get(f"/programs/{devops.id}/pdf")
check("the page then opens on it", f'/programs/{devops.id}/pdf/classic?lang=fr&download=1' in page.text.replace("&amp;", "&") and 'class="pf-look is-on" href="/programs/' + devops.id + '/pdf?look=classic' in page.text.replace("&amp;", "&"))
check("a look that does not exist is refused", admin.post("/programs/pdf-look", data={"style": "x"}).status_code == 400)
check("the program's page and the list lead to it", f'href="/programs/{devops.id}/pdf"' in admin.get(f"/programs/{devops.id}").text and f'href="/programs/{devops.id}/pdf"' in admin.get("/programs/all").text)
guest = TestClient(main.app, follow_redirects=False)
check("somebody not signed in gets nothing", guest.get(f"/programs/{devops.id}/pdf/brochure").status_code in (303, 401) and guest.get(f"/programs/{devops.id}/pdf").status_code in (303, 401))
prof = TestClient(main.app, follow_redirects=False)
prof.post("/login", data={"email": "prof@test.local", "password": "pdf-test-2"})
check("a professor cannot prepare emails to candidates from it", prof.post(f"/programs/{devops.id}/pdf/send", data={"candidate_id": "x"}).status_code in (303, 403) and "/inbox/compose" not in where(prof.post(f"/programs/{devops.id}/pdf/send", data={"candidate_id": "x"})))

# ======================================================================
# D. To a candidate
# ======================================================================
person = Candidate(full_name="Amira Ben Salah", email="amira.pdf@test.local", phone="+21620000001"); db.add(person); db.commit()
before = db.query(EmailFile).count()
answer = admin.post(f"/programs/{devops.id}/pdf/send", data={"candidate_id": person.id, "style": "brochure", "lang": "fr", "personal": "1"})
target = urlparse(answer.headers["location"]); query = parse_qs(target.query)
check("sending prepares an email, it does not send", answer.status_code == 303 and target.path == "/inbox/compose" and query["candidate"] == [person.id] and query["program"] == [devops.id] and sent == [], answer.headers.get("location"))
file = db.get(EmailFile, query["attach"][0])
check("the PDF is kept as a file an email can carry", file is not None and file.content_type == "application/pdf" and file.filename == "Programme - DevOps.pdf" and file.origin.startswith("program:") and email_files.read_bytes(file) == named and db.query(EmailFile).count() == before + 1)
again = admin.post(f"/programs/{devops.id}/pdf/send", data={"candidate_id": person.id, "style": "brochure", "lang": "fr", "personal": "1"})
check("the same PDF is kept once", parse_qs(urlparse(again.headers["location"]).query)["attach"] == [file.id] and db.query(EmailFile).count() == before + 1)
other = admin.post(f"/programs/{devops.id}/pdf/send", data={"candidate_id": person.id, "lang": "fr"})
other_file = db.get(EmailFile, parse_qs(urlparse(other.headers["location"]).query)["attach"][0])
check("without a look the default one is used, without the name when not asked", other_file.id != file.id and email_files.read_bytes(other_file) == made["classic"])
check("without a candidate it asks for one", "Choose the candidate" in where(admin.post(f"/programs/{devops.id}/pdf/send", data={"candidate_id": ""})))
compose = admin.get(answer.headers["location"])
check("the email is ready: the file attached, the message written", compose.status_code == 200 and "Programme - DevOps.pdf" in compose.text and file.id in compose.text and "Le programme de la formation DevOps" in compose.text and "Vous trouverez en pièce jointe" in compose.text, compose.status_code)
check("a file that is not a program's PDF cannot be slipped in by the address", "stranger.pdf" not in admin.get(f"/inbox/compose?candidate={person.id}&attach={email_files._store(db, filename='stranger.pdf', content_type='application/pdf', data=b'%PDF-1.4 x', is_image=False, origin='candidate').id}").text)
db.commit()
message = pp.message_for(devops)
answer = admin.post("/inbox/compose/send", data={"candidate_ids": [person.id], "subject": message["subject"], "title": message["title"], "body": message["body"], "style": "brand", "attachment_ids": [file.id]})
check("when the admin presses Send, the email leaves with the PDF", answer.status_code == 303 and "Email sent to Amira Ben Salah" in where(answer) and len(sent) == 1 and sent[0]["to_email"] == "amira.pdf@test.local"
      and [(part["filename"], part["content_type"], part["data"] == named) for part in sent[0]["attachments"]] == [("Programme - DevOps.pdf", "application/pdf", True)], (where(answer), len(sent)))
check("written to them by name", "Bonjour Amira" in sent[0]["text_body"] and "DevOps" in sent[0]["text_body"])
db.expire_all()
kept = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person.id).order_by(CandidateEmail.received_at.desc()).first()
check("and it stays in the candidate's discussion with its file", kept is not None and kept.status == "sent" and file.id in (kept.attachments or "") and "Programme - DevOps.pdf" in (kept.attachments or ""), kept.attachments if kept else None)
registration = Registration(candidate_id=person.id, program_id=devops.id, program_name="DevOps"); db.add(registration); db.commit()
profile = admin.get(f"/candidate/{person.id}")
check("the candidate's page sends the program in one click", profile.status_code == 200 and f'action="/programs/{devops.id}/pdf/send"' in profile.text and "Send the program (PDF)" in profile.text, profile.status_code)
page = admin.get(f"/programs/{devops.id}/pdf")
check("a candidate registered for the program is offered first", 'label="Registered for this program"' in page.text and "Amira Ben Salah" in page.text.split('label="Registered for this program"')[1].split("</optgroup>")[0])
said = cb.answer(db, "how do I send the program as pdf to a candidate?")
check("the assistant says how", "three looks" in said["reply"] and "Send the program (PDF)" in said["reply"], said["reply"][:200])
check.done()
