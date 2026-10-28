"""Tests for program proposals and pictures, course files, weekly questions, the
recommendation engine and the redesigned pages. Runs on a COPY of the database;
the model, the icon catalogue and SMTP are replaced by fakes: nothing leaves this machine."""
import os, sys, io, json, re, shutil, sqlite3, zipfile
import testenv
DB = testenv.prepare("smart")
SCRATCH = testenv.WORK
os.environ.pop("PUBLIC_BASE_URL", None)

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network

from datetime import datetime, timedelta, timezone, time as dtime
from PIL import Image

import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import (Activity, AdviceSnooze, Candidate, CandidateEmail, ClassGroup, ClassMaterial, ClassStatus, ClassWeek,
                        EmailThread, Notification, Program, ProgramProposal, Quiz, Registration, WeekQuestion)
from app.models.classsession import ClassSession
from app.models.program import ProgramLevel, WeeklyCurriculum
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (advisor, course_materials as cm, email_assistant as ea, email_templates as tp, email_threads as et,
                          class_emails, overview, payment_service, program_assistant as pa, program_icons as pi,
                          settings_service, weekly_questions as wq)
from app.services.email_service import EmailDeliveryError
from app.scheduler import jobs
import httpx

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

# ---------------- fakes ----------------
outbox = []
send_mode = {"fail": False}
def fake_send(**kw):
    if send_mode["fail"]: raise EmailDeliveryError("mailbox unavailable")
    outbox.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True; class_emails.smtp_configured = lambda: True
jobs.generate_quiz_set = lambda **kw: []; payment_service.generate_quiz_set = lambda **kw: []
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
httpx.Client = _no_web

model = {"on": True, "calls": []}
def fake_llm(system, prompt, max_tokens=400):
    model["calls"].append(prompt)
    if not model["on"]:
        return None
    if "Write the catalogue entry" in prompt:
        levels, weeks = map(int, re.search(r"It has (\d+) level\(s\), each with (\d+) weeks", prompt).groups())
        name = re.search(r'training program "(.+?)"', prompt).group(1)
        return {"description": f"Learn to build real projects with {name} from the first week",
                "levels": [{"title": f"Level {l + 1}: Part {l + 1}" if l else "Foundations", "description": f"What level {l + 1} covers",
                            "weeks": [f"Week {l * weeks + w + 1}: Topic {l + 1}.{w + 1} of {name}" for w in range(weeks)]}
                           for l in range(levels)]}
    if "For each week, write what the learner does" in prompt:
        count = len(re.findall(r"^week_\d+:", prompt, re.M))
        return {f"week_{i}": ("" if i == 2 else ["Doing step one", "Learning step two."]) for i in range(1, count + 1)}
    if "Write what the learner does and learns that week" in prompt:
        return {"points": "Asked on its own; Still written"}
    passage = re.search(r"<<<\n(.*?)\n>>>", prompt, re.S)
    passage = passage.group(1) if passage else ""
    if "each with its answer" in prompt:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", passage) if len(s.strip()) > 30][:2]
        items = []
        for index, sentence in enumerate(sentences):
            words = sentence.rstrip(".").split()
            items.append({"question": f"{' '.join(words[:6])}: what follows",
                          "answer": " ".join(words[-3:]),
                          "quote": "A sentence that is nowhere in the course at all." if "NOQUOTE" in passage and index == 0 else sentence})
        if "ABOUTTEXT" in passage:
            items[0]["question"] = "What does the passage say on page 2?"
        return {"items": items}
    if "Write 3 wrong answers" in prompt:
        answer = re.search(r"Correct answer: (.+)", prompt).group(1).strip()
        return {"wrong": ["Alpha wrong", "b) Beta wrong", f"It is {answer} indeed", "why"]}
    if "give the number of the choice the passage supports" in prompt:
        answers = []
        for number, block in enumerate(re.split(r"Question \d+: ", prompt)[1:]):
            options = re.findall(r"^\s+(\d+)\. (.+)$", block, re.M)
            right = [int(i) for i, text in options if not text.lower().endswith("wrong")]
            wrong = [int(i) for i, text in options if text.lower().endswith("wrong")]
            answers.append(wrong[0] if ("TWICE" in passage and number == 0) else (right[0] if right else 0))
        return {"answers": answers}
    return None
ea.llm_json = fake_llm
ea.model_available = lambda: True

SQUARE = b'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 32 32"><path fill="#02569B" d="M3 3h26v26H3z"/></svg>'
WIDE = b'<svg xmlns="http://www.w3.org/2000/svg" width="340" height="96" viewBox="0 0 512 145"><path d="M0 0h512v145H0z"/></svg>'
web = {"online": True, "searches": [], "downloads": []}
CATALOGUE = {"flutter": ["logos:flutter", "devicon:flutter", "logos:flutter-icon"], "python": ["devicon:python", "logos:python"],
             "robot": ["fluent-emoji-flat:robot"], "rocket": ["fluent-emoji-flat:rocket"], "docker": ["logos:docker-icon"]}
def fake_search(query, sets=None, limit=48):
    web["searches"].append((query, tuple(sets or ())))
    if not web["online"]: return None
    found = CATALOGUE.get(query.lower(), [])
    return [ref for ref in found if not sets or ref.split(":")[0] in sets][:limit]
def fake_download(ref):
    web["downloads"].append(ref)
    if not web["online"]: return None
    return WIDE if ref == "logos:flutter" else SQUARE
pi.search = fake_search; pi.download = fake_download

def png(width, height):
    out = io.BytesIO(); Image.new("RGB", (width, height), (164, 44, 103)).save(out, "PNG"); return out.getvalue()

def make_pdf(lines):
    esc = lambda text: text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = ("BT /F1 11 Tf 40 760 Td 14 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET").encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    data, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(data)); data += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return data + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"

def make_zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in files.items(): archive.writestr(name, text)
    return out.getvalue()
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
DOCX = make_zip({"word/document.xml": f'<w:document {W}><w:body><w:p><w:r><w:t>Docker volumes keep data </w:t></w:r><w:r><w:t>after a container is removed.</w:t></w:r></w:p><w:p><w:r><w:t>A bind mount links a folder of the host into the container.</w:t></w:r></w:p></w:body></w:document>'})
PPTX = make_zip({
    "ppt/slides/slide2.xml": f'<p:sld xmlns:p="x" {A}><a:p><a:r><a:t>Second slide: networks connect containers by name.</a:t></a:r></a:p></p:sld>',
    "ppt/slides/slide1.xml": f'<p:sld xmlns:p="x" {A}><a:p><a:r><a:t>First slide: an image is a read-only template.</a:t></a:r></a:p><a:p><a:r><a:t>7</a:t></a:r></a:p></p:sld>',
    "ppt/notesSlides/notesSlide1.xml": f'<p:notes xmlns:p="x" {A}><a:p><a:r><a:t>Speaker note: a container is a running image.</a:t></a:r></a:p></p:notes>',
})

class Upload:
    def __init__(self, filename, data): self.filename, self.file = filename, io.BytesIO(data)

db = SessionLocal()
now = datetime.utcnow()

# ---------------- fixtures ----------------
devops = db.query(Program).filter(Program.name == "DevOps").first()
python = db.query(Program).filter(Program.name == "Python").first()
flutter = db.query(Program).filter(Program.name == "Mobile Flutter").first()
bootcamp = db.query(Program).filter(Program.name == "Bootcamp 6 months").first()
# The copy comes from the live database, where proposals, pictures and course work go on:
# start from a known state (no proposal, no picture, the three name-only programs empty).
db.query(ProgramProposal).delete(); db.query(WeekQuestion).delete(); db.query(ClassWeek).delete(); db.query(ClassMaterial).delete()
db.query(AdviceSnooze).delete(); db.query(Quiz).filter(Quiz.kind == "weekly").delete()
db.query(EmailThread).update({"needs_admin": False}); db.query(CandidateEmail).update({"requires_admin": False})   # real emails may be waiting in the live inbox
db.query(Notification).filter(Notification.category.in_(("PROGRAM_PROPOSAL", "WEEK_QUESTIONS"))).delete(synchronize_session=False)
for program in db.query(Program).all():
    program.icon_file = None; program.icon_ref = None; program.icon_checked_at = None
for program in (python, flutter, bootcamp):
    program.description = None; program.total_duration_days = None; program.class_session_time = None
    program.hours_per_week = 0; program.training_hours = 0; program.supervision_hours = 0
    for level in list(program.levels): db.delete(level)
for group in db.query(ClassGroup).filter(ClassGroup.program_id == python.id).all():
    for session in list(group.sessions): db.delete(session)
db.commit()
def person(name, email, phone="20 111 222"):
    row = Candidate(full_name=name, email=email, phone=phone, university="ISET", city="Tunis"); db.add(row); db.flush(); return row
def register(candidate, program, *, paid=0.0, total=390.0, days_ago=10, class_group=None):
    row = Registration(candidate_id=candidate.id, program_id=program.id, program_name=program.name, delivery_mode="online",
                       total_amount=total, total_price=total, paid_amount=paid,
                       payment_status=PaymentStatus.PAID if paid >= total else (PaymentStatus.PARTIAL if paid else PaymentStatus.UNPAID),
                       status=RegistrationStatus.ASSIGNED if class_group else RegistrationStatus.PENDING,
                       registration_date=now - timedelta(days=days_ago), class_group_id=class_group.id if class_group else None)
    db.add(row); db.flush(); return row
start = (datetime.now() - timedelta(days=9)).replace(hour=9, minute=0, second=0, microsecond=0)
klass = ClassGroup(name="T-DEVOPS-1", program_id=devops.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=2,
                   professor_name="Rami", start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date")
db.add(klass); db.flush()
for week in (1, 2, 3):
    db.add(ClassSession(class_group_id=klass.id, week_number=week, session_date=(start + timedelta(weeks=week - 1)).date(),
                        start_time=dtime(9, 0), end_time=dtime(13, 0), topic=f"Docker week {week}"))
amal, bilel, chaima = person("Amal Test", "amal.test@example.com"), person("Bilel Test", "bilel.test@example.com"), person("Chaima Test", "chaima.test@example.com", phone="")
reg_a, reg_b, reg_c = (register(c, devops, paid=390, class_group=klass) for c in (amal, bilel, chaima))
db.commit()

# ======================================================================
# A. Proposals for programs with missing details
# ======================================================================
check("proposal: a complete program misses nothing", pa.missing(devops) == [])
check("proposal: an empty program misses everything", pa.missing(python) == list(pa.FIELDS), pa.missing(python))
house = pa.house_values(db)
check("proposal: practical details come from what most programs use",
      house["total_duration_days"]["value"] == 60 and house["hours_per_week"]["value"] == 6 and house["levels"] == 2 and house["weeks_per_level"] == 4, house)
check("proposal: a duration written in the name is read", pa.duration_in_name("Bootcamp 6 months") == (180, "6 months") and pa.duration_in_name("Python") is None)
close = pa.similar_program(db, flutter)
check("proposal: a near-duplicate is noticed", close is not None and close[0].name.startswith("Mobile Development"), close and close[0].name)
check("proposal: unrelated programs are not called similar", pa.similar_program(db, python) is None)

before = db.query(Notification).filter(Notification.category == "PROGRAM_PROPOSAL").count()
proposal = pa.request(db, python)
check("proposal: asked for, it waits in the queue", proposal.status == "queued" and pa.request(db, python).id == proposal.id)
check("proposal: the queue is worked one at a time", pa.work_one(db) is True)
db.refresh(proposal)
data, basis = proposal.details, proposal.sources
check("proposal: ready, with a notification for the admin",
      proposal.status == "ready" and db.query(Notification).filter(Notification.category == "PROGRAM_PROPOSAL").count() == before + 1, proposal.status)
check("proposal: hours and duration are the house values, and say so",
      data["total_duration_days"] == 60 and data["hours_per_week"] == 6 and re.search(r"\d+ of your \d+ complete programs", basis["hours_per_week"]), (data.get("hours_per_week"), basis.get("hours_per_week")))
check("proposal: prices are never proposed", not any("price" in key for key in data))
check("proposal: description ends as a sentence", data["description"].endswith("."), data["description"])
weeks_flat = [week for level in data["levels"] for week in level["weeks"]]
check("proposal: plan has the house shape, numbered through the levels",
      len(data["levels"]) == 2 and [w["week_number"] for w in weeks_flat] == list(range(1, 9)), [w["week_number"] for w in weeks_flat])
check("proposal: 'Week n:' written by the model is not repeated in topics", all(not w["topic_title"].lower().startswith("week") for w in weeks_flat), weeks_flat[0]["topic_title"])
check("proposal: level titles are normalised", data["levels"][0]["title"] == "Level 1: Foundations", data["levels"][0]["title"])
check("proposal: a week the model skipped is asked for on its own",
      weeks_flat[1]["learning_objectives"] == "Asked on its own; Still written" and weeks_flat[0]["learning_objectives"] == "Doing step one; Learning step two", weeks_flat[:2])
db.refresh(python)
check("proposal: nothing is saved on the program before the admin accepts", python.description is None and not python.levels)

empty_class = db.query(ClassGroup).filter(ClassGroup.name == "P-20261001-1").first()
edited = dict(data); edited["description"] = "Corrected by the admin."; edited["levels"] = [dict(level) for level in data["levels"]]
done = pa.accept(db, proposal, edited)
db.refresh(python)
check("proposal: accepting saves the corrected text", python.description == "Corrected by the admin." and python.total_duration_days == 60 and python.embedding is None)
check("proposal: accepting creates the levels and weeks", len(python.levels) == 2 and sum(len(l.weeks) for l in python.levels) == 8 and pa.missing(python) == [], done)
db.refresh(empty_class)
check("proposal: classes created before the plan get their sessions", len(empty_class.sessions) == 8 and empty_class.name in done["classes"], (len(empty_class.sessions), done["classes"]))
check("proposal: accepted, nothing waits any more", pa.open_proposal(db, python.id) is None and pa.request(db, python) is None)

python.description = "Kept as it is."; python.hours_per_week = 0; db.commit()
second = pa.request(db, python); pa.work_one(db); db.refresh(second)
check("proposal: only what is missing is proposed", set(second.details) == {"hours_per_week"}, second.details)
pa.accept(db, second, {"hours_per_week": 9, "description": "Must not replace"})
db.refresh(python)
check("proposal: accepting never overwrites what is already there", python.description == "Kept as it is." and python.hours_per_week == 9)

big = pa.request(db, bootcamp); pa.work_one(db); db.refresh(big)
check("proposal: a 6-month program gets its duration from the name and more levels",
      big.details["total_duration_days"] == 180 and "From the name" in big.sources["total_duration_days"] and len(big.details["levels"]) == 6, (big.details.get("total_duration_days"), len(big.details.get("levels", []))))
pa.dismiss(db, big); db.refresh(bootcamp)
check("proposal: dismissing leaves the program untouched", big.status == "dismissed" and bootcamp.description is None)

model["on"] = False
offline = pa.request(db, flutter); pa.work_one(db); db.refresh(offline)
check("proposal: without the model, the practical details are still proposed and the gap is said",
      offline.status == "ready" and "description" not in offline.details and "text_error" in offline.sources and offline.sources["similar"]["name"].startswith("Mobile Development"), (offline.status, offline.sources))
model["on"] = True
form = {"description": "  A description   typed by hand ", "total_duration_days": "45", "hours_per_week": "abc", "level_0_title": "Level 1: Start",
        "week_0_0_topic": "First topic", "week_0_0_objectives": "Learn A; Learn B", "week_0_1_topic": "", "week_0_2_topic": "Third topic", "level_3_title": "Empty level"}
read = pa.read_form(form)
check("proposal: the review form is read back cleanly",
      read["description"] == "A description typed by hand" and read["total_duration_days"] == 45 and "hours_per_week" not in read
      and len(read["levels"]) == 1 and [w["topic_title"] for w in read["levels"][0]["weeks"]] == ["First topic", "Third topic"], read)
db.add(Program(name="Fixture Incomplete Program")); db.commit()
known = db.query(ProgramProposal).count()
queued = pa.queue_missing(db)
names = {db.get(Program, row.program_id).name for row in db.query(ProgramProposal).filter(ProgramProposal.status == "queued")}
check("proposal: only programs that never had a proposal are queued automatically",
      queued >= 1 and "Bootcamp 6 months" not in names and "Mobile Flutter" not in names and "Python" not in names, (queued, names))
stuck = db.query(ProgramProposal).filter(ProgramProposal.status == "queued").first(); stuck.status = "writing"; db.commit()
check("proposal: writing interrupted by a restart goes back in the queue", pa.release_interrupted(db) == 1 and db.get(ProgramProposal, stuck.id).status == "queued")
for row in db.query(ProgramProposal).filter(ProgramProposal.status == "queued"): row.status = "dismissed"
db.commit()

# ======================================================================
# B. Pictures of programs
# ======================================================================
plan = pi.search_plan("Mobile Development (Flutter + Node/Spring Boot)")
check("icons: the technology named first is looked for first", plan[0] == ("flutter", pi.TECH_SETS) and plan[1][0] == "nodejs", plan[:3])
check("icons: subjects are looked for among pictures", pi.search_plan("Artificial Intelligence (AI Chatbot)")[0] == ("robot", pi.COLOUR_SETS))
check("icons: an unknown name falls back to its own words", any(words == "pottery" for words, _ in pi.search_plan("Pottery workshop")))
check("icons: a program without picture shows its initials", pi.view(flutter)["url"] is None and pi.tile(flutter)["initials"] == "MF" and pi.needs_icon(flutter))
check("icons: found on the web and stored here", pi.assign_from_web(db, flutter) == "ok" and pi.has_icon(flutter))
check("icons: a square mark is preferred to a wide logo", flutter.icon_ref == "logos:flutter-icon", flutter.icon_ref)
first_url = pi.url_of(flutter)
first_file = pi.path_of(flutter)
check("icons: served from this application", first_url.startswith(f"/programs/{flutter.id}/icon?v=") and pi.path_of(flutter).read_bytes() == SQUARE)
check("icons: the admin can choose another one", pi.assign_from_web(db, flutter, ref="devicon:flutter") == "ok" and flutter.icon_ref == "devicon:flutter" and pi.url_of(flutter) != first_url)
check("icons: the previous file is not left behind", not first_file.exists() and pi.path_of(flutter).exists())
odd = Program(name="Zzzz Qqqq"); db.add(odd); db.commit()
check("icons: a name nothing matches", pi.assign_from_web(db, odd) == "nothing" and not pi.needs_icon(odd) and odd.icon_checked_at is not None)
web["online"] = False
check("icons: offline is reported as such, and a picture is drawn meanwhile", pi.assign_from_web(db, python) == "offline"
      and python.icon_ref == pi.DRAWN and pi.has_icon(python))
web["online"] = True
python.icon_checked_at = None; db.commit()
got = pi.assign_missing(db, limit=50)
check("icons: programs without picture get one in the background", got >= 1 and pi.has_icon(python) and python.icon_ref == "logos:python", (got, python.icon_ref))
pi.save_upload(db, python, "My Logo.PNG", png(900, 600))
with Image.open(pi.path_of(python)) as stored:
    check("icons: an uploaded picture is kept small", python.icon_ref == "upload" and max(stored.size) <= 256 and pi.media_type(python) == "image/png", stored.size)
try: pi.save_upload(db, python, "bad.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'); refused = False
except ValueError: refused = True
check("icons: an SVG with a script is refused", refused and python.icon_ref == "upload")
try: pi.save_upload(db, python, "notes.pdf", b"%PDF"); refused = False
except ValueError: refused = True
check("icons: a file that is not a picture is refused", refused)
pi.remove(db, python)
check("icons: removed on request, and not looked up again", not pi.has_icon(python) and python.icon_ref == "none" and not pi.needs_icon(python))

# ======================================================================
# C. Course files
# ======================================================================
PDF_LINES = ["Docker volumes keep data after a container is removed.", "A named volume is managed by Docker itself.",
             "A bind mount links a folder of the host into the container.", "Use docker volume ls to list the volumes of a machine."]
pdf = cm.save_upload(db, klass, 2, Upload("C:\\cours\\Semaine 2 - volumes.pdf", make_pdf(PDF_LINES)))
check("files: text is read from a PDF", pdf.learned_status == "read" and "named volume is managed" in pdf.learned_text and pdf.filename == "Semaine 2 - volumes.pdf", (pdf.learned_status, pdf.learned_text))
check("files: stored on disk under a random name", cm.path_of(pdf).is_file() and cm.path_of(pdf).name != pdf.filename and pdf.kind == "document")
docx = cm.save_upload(db, klass, 2, Upload("notes.docx", DOCX))
check("files: text is read from Word", docx.learned_status == "read" and "Docker volumes keep data after a container is removed." in docx.learned_text, docx.learned_text)
pptx = cm.save_upload(db, klass, 2, Upload("slides.pptx", PPTX))
check("files: slides are read in order, with the speaker's notes, without slide numbers",
      pptx.learned_text.index("First slide") < pptx.learned_text.index("Speaker note") < pptx.learned_text.index("Second slide") and "\n7" not in pptx.learned_text, pptx.learned_text)
video = cm.save_upload(db, klass, 2, Upload("session.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 500))
label = cm.learned_label(video)
check("files: a video is kept, and the system says it cannot watch it", video.kind == "video" and video.learned_status == "notes_only" and label["tone"] == "todo" and "watch a video" in label["text"], label)
cm.set_note(db, video, "The recording explains why containers lose their data and how volumes fix that.")
check("files: its note becomes what the system learns", cm.learned_label(video)["tone"] == "ok" and any(s["key"] == video.id for s in cm.sources(db, klass.id, 2)))
photo = cm.save_upload(db, klass, 2, Upload("board.jpg", png(40, 40)))
scan = cm.save_upload(db, klass, 2, Upload("scan.pdf", make_pdf([""])))
check("files: a photo and a scanned PDF ask for a note", photo.learned_status == "notes_only" and scan.learned_status == "nothing" and "scanned" in cm.learned_label(scan)["text"])
old = cm.save_upload(db, klass, 2, Upload("old.doc", b"\xd0\xcf\x11\xe0 word"))
check("files: an old Word file is kept but said unreadable", old.learned_status == "unreadable" and "PDF" in cm.learned_label(old)["text"])
def refused_with(filename, data, week=2):
    try: cm.save_upload(db, klass, week, Upload(filename, data)); return None
    except cm.UploadRefused as exc: db.rollback(); return str(exc)
check("files: a program file is refused", "not accepted" in (refused_with("virus.exe", b"MZ") or ""))
check("files: an empty file is refused", "empty" in (refused_with("empty.pdf", b"") or ""))
check("files: a week outside the class is refused", "not part of the class" in (refused_with("a.txt", b"hello", week=99) or ""))
limit = cm._LIMITS["document"]; cm._LIMITS["document"] = 100
message = refused_with("huge.txt", b"x" * 500); cm._LIMITS["document"] = limit
check("files: a file over the limit is refused and not left on disk", "larger than" in (message or "") and len(list(cm.storage_dir().iterdir())) == 7, (message, len(list(cm.storage_dir().iterdir()))))
path = cm.path_of(old); cm.delete(db, old)
check("files: deleting removes the row and the file", not path.exists() and db.get(ClassMaterial, old.id) is None)
check("files: the week's subject comes from the class plan", cm.week_topic(db, klass.id, 2) == "Docker week 2" and cm.week_count(klass) == 8)

# ======================================================================
# D. Weekly questions
# ======================================================================
long_text = "\n\n".join(f"Paragraph {i} explains one idea of the course in a full sentence that is long enough to matter. " * 4 for i in range(12))
passages = wq.split_passages(long_text)
check("questions: a long course is cut into readable passages", len(passages) > 3 and all(len(p) <= wq.PASSAGE_CHARS + 2 for p in passages), [len(p) for p in passages])
check("questions: a cover page or a table of numbers is not a passage", wq.split_passages("COURS 2026\n\n1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 " * 20) == [])
check("questions: French and English courses are told apart", wq.language_of("Le site doit se charger vite et les pages sont lisibles sur mobile.") == "French" and wq.language_of(PDF_LINES[0]) == "English")
check("questions: a quote must really be in the course", wq.supported_by("A named volume is managed by Docker itself.", " ".join(PDF_LINES)) and not wq.supported_by("Volumes are deleted every night by Docker.", " ".join(PDF_LINES)))
check("questions: a wrong choice that restates the answer is caught",
      wq._says_the_same("Les piliers sont la popularité, la technique et le contenu", "technique, contenu, popularité")
      and not wq._says_the_same("b = a creates a new list", "it does not create a new list"))
check("questions: levels of understanding", [wq.level_of(s)["key"] for s in (None, 100, 80, 79, 50, 49)] == ["none", "strong", "strong", "partial", "partial", "weak"])

model["calls"].clear()
week = wq.queue(db, klass, 2)
check("questions: asked for, the week waits", week.questions_status == "queued")
wq.work_one(db); db.refresh(week)
bank = wq.questions_of(db, klass.id, 2)
check("questions: written from every source of the week", week.questions_status == "ready" and {q.material_id for q in bank} >= {pdf.id, docx.id, pptx.id, video.id}, (week.questions_status, week.questions_note, len(bank)))
check("questions: all are ready when the checks pass", bank and all(q.status == "ready" for q in bank), [(q.status, q.review_reason) for q in bank])
sample = next(q for q in bank if q.material_id == pdf.id)
check("questions: three choices kept, the restated answer and junk dropped", len(sample.choices) == 3 and "Why" not in sample.choices and not any("indeed" in c for c in sample.choices), sample.choices)
check("questions: the right answer is where the system says", not sample.choices[sample.correct_answer].lower().endswith("wrong") and sample.choices[sample.correct_answer][0].isupper(), sample.choices)
check("questions: letters in front of choices are removed", "Beta wrong" in sample.choices, sample.choices)
check("questions: the course's own sentence is kept as the explanation", sample.explanation and sample.explanation in " ".join(PDF_LINES) and sample.source_name == "Semaine 2 - volumes.pdf", sample.explanation)
check("questions: the question ends with a question mark", sample.question.endswith("?"), sample.question)
check("questions: the admin is told", db.query(Notification).filter(Notification.category == "WEEK_QUESTIONS").count() == 1)
positions = {q.correct_answer for q in bank}
check("questions: the right answer is not always in the same place", len(positions) > 1, positions)

count_before = len(bank)
wq.queue(db, klass, 2); wq.work_one(db)
added = [q for q in wq.questions_of(db, klass.id, 2)][count_before:]
check("questions: asking again writes nothing new from the files; only from the course's own plan and the website",
      all((q.material_id or "").startswith(("plan:", "site:")) for q in added), [q.material_id for q in added])
count_before = len(wq.questions_of(db, klass.id, 2))
wq.queue(db, klass, 2); wq.work_one(db)
check("questions: and then nothing new, everything being covered", len(wq.questions_of(db, klass.id, 2)) == count_before)
cm.set_week_notes(db, klass, 3, "NOQUOTE The teacher showed how images are built layer by layer in Docker. Each instruction of a Dockerfile adds one layer to the image.")
wq.queue(db, klass, 3); wq.work_one(db)
week3 = wq.questions_of(db, klass.id, 3)
reasons = {q.review_reason for q in week3 if q.status == "review"}
check("questions: an answer the course does not support waits for the professor", any("not found in the course" in (r or "") for r in reasons) and any(q.status == "ready" for q in week3), [(q.status, q.review_reason) for q in week3])
cm.set_week_notes(db, klass, 4, "TWICE The teacher explained that a registry stores images for a whole team. Pushing an image sends its layers to the registry server.")
wq.queue(db, klass, 4); wq.work_one(db)
week4 = wq.questions_of(db, klass.id, 4)
check("questions: a question the system answers differently on a second reading waits too",
      any("chose another answer" in (q.review_reason or "") for q in week4), [(q.status, q.review_reason) for q in week4])
cm.set_week_notes(db, klass, 5, "ABOUTTEXT The teacher presented continuous integration with a pipeline that runs the tests. A pipeline fails when one of its steps returns an error code.")
wq.queue(db, klass, 5); wq.work_one(db)
check("questions: a question about the document itself is not sent", any("instead of the subject" in (q.review_reason or "") for q in wq.questions_of(db, klass.id, 5)))
cm.set_week_notes(db, klass, 6, "Too short.")
wq.queue(db, klass, 6); wq.work_one(db)
week6 = db.query(ClassWeek).filter(ClassWeek.class_group_id == klass.id, ClassWeek.week_number == 6).first()
check("questions: too little course: only the course's own plan is used, nothing invented from the notes",
      (week6.questions_status == "idle" and not wq.questions_of(db, klass.id, 6))
      or all((q.material_id or "").startswith(("plan:", "site:", "bank:")) for q in wq.questions_of(db, klass.id, 6)),
      [q.material_id for q in wq.questions_of(db, klass.id, 6)])
from app.models import ClassGroup as _Group, Program as _Program

bare_program = _Program(name="Bare Course Without Plan", description="", is_approved=True)
db.add(bare_program); db.flush()
bare = _Group(name="T-BARE-1", program_id=bare_program.id, status=klass.status, start_date=klass.start_date, total_weeks=4,
              start_time=klass.start_time, end_time=klass.end_time, days_of_week=klass.days_of_week)
db.add(bare); db.commit()
cm.set_week_notes(db, bare, 2, "Too short.")
wq.queue(db, bare, 2); wq.work_one(db)
bare_week = db.query(ClassWeek).filter(ClassWeek.class_group_id == bare.id, ClassWeek.week_number == 2).first()
check("questions: a course with no plan and too little course says so instead of inventing",
      bare_week.questions_status == "idle" and "not enough to learn from" in bare_week.questions_note and not wq.questions_of(db, bare.id, 2))
model["on"] = False
cm.set_week_notes(db, klass, 7, "The teacher explained monitoring with dashboards that show the load of every server. An alert is sent when a threshold is passed for five minutes.")
wq.queue(db, klass, 7); wq.work_one(db)
week7 = db.query(ClassWeek).filter(ClassWeek.class_group_id == klass.id, ClassWeek.week_number == 7).first()
check("questions: when the model is down the week still gets questions, from the course plan, and says so",
      week7.questions_status == "ready" and "course plan" in week7.questions_note and any(q.author == "plan" for q in wq.questions_of(db, klass.id, 7)),
      (week7.questions_status, week7.questions_note))
model["on"] = True
own = wq.add_question(db, klass, 2, wq.read_question_form({"question": "Which command lists the volumes?", "option_0": "docker ps", "option_1": "", "option_2": "docker volume ls", "correct": "2", "explanation": "Seen in class."}))
check("questions: the professor's own question is ready at once, blanks removed", own.status == "ready" and own.author == "admin" and own.choices == ["docker ps", "docker volume ls"] and own.correct_answer == 1, (own.choices, own.correct_answer))
for bad, expected in (({"question": "Short?", "option_0": "a", "option_1": "b", "correct": "0"}, "Write the question"),
                      ({"question": "A real question here?", "option_0": "a", "option_1": "", "correct": "0"}, "at least two"),
                      ({"question": "A real question here?", "option_0": "a", "option_1": "A", "correct": "0"}, "the same"),
                      ({"question": "A real question here?", "option_0": "a", "option_1": "b", "correct": "3"}, "marked as correct is empty"),
                      ({"question": "A real question here?", "option_0": "a", "option_1": "b"}, "which answer")):
    try: wq.read_question_form(bad); said = ""
    except ValueError as exc: said = str(exc)
    check(f"questions: a badly filled question is explained ({expected})", expected in said, said)
review_one = next(q for q in week3 if q.status == "review")
wq.save_question(db, review_one, wq.read_question_form({"question": "How is a Docker image built?", "option_0": "Layer by layer", "option_1": "In one block", "correct": "0"}))
check("questions: corrected by the professor, a question becomes theirs and ready", review_one.status == "ready" and review_one.author == "admin" and review_one.review_reason is None)
wq.set_status(db, own, "off")
check("questions: a question set aside is not sent", own not in wq.ready_questions(db, klass.id, 2))
wq.queue(db, klass, 2, "fresh")
check("questions: starting again keeps the professor's questions", [q.author for q in wq.questions_of(db, klass.id, 2)] == ["admin"])
wq.work_one(db)
wq.queue(db, klass, 2, "more"); wq.work_one(db)
bank = wq.questions_of(db, klass.id, 2)
check("questions: written again after starting afresh", sum(1 for q in bank if q.author == "system") >= 4, len(bank))

# ---- sending
klass.weekly_questions_count = 3; db.commit()
outbox.clear()
BASE = "https://app.9antra.test"
sent = wq.send_week(db, klass, 2, base_url=BASE)
quizzes = wq.quizzes_of(db, klass.id, 2)
check("send: every candidate of the class gets an email", sent["sent"] == 3 and len(outbox) == 3 and {m["to_email"] for m in outbox} == {amal.email, bilel.email, chaima.email}, sent)
check("send: each gets their own private link", len({q.access_token for q in quizzes}) == 3 and all(len(q.access_token) >= 24 for q in quizzes)
      and all(f"{BASE}/learn/{q.access_token}" in next(m for m in outbox if m["to_email"] == db.get(Candidate, q.candidate_id).email)["body"] for q in quizzes))
check("send: the asked number of questions each", all(len(wq.questions_in(q)) == 3 for q in quizzes))
check("send: the email is the editable 'Weekly questions' one, in the candidate's discussion",
      "semaine 2" in outbox[0]["subject"] and "Docker week 2" in outbox[0]["body"] and "3 questions" in outbox[0]["body"]
      and db.query(CandidateEmail).filter(CandidateEmail.intent == "weekly_questions").count() == 3, outbox[0]["subject"])
check("send: the week is marked sent", db.query(ClassWeek).filter(ClassWeek.class_group_id == klass.id, ClassWeek.week_number == 2).first().sent_at is not None)
again = wq.send_week(db, klass, 2, base_url=BASE)
check("send: sending twice does not email twice", again["sent"] == 0 and again["already"] == 3 and len(outbox) == 3, again)
check("send: nothing ready, nothing sent", "No question is ready" in wq.send_week(db, bare, 2, base_url=BASE).get("error", ""))
dora = person("Dora Test", "dora.test@example.com"); reg_d = register(dora, devops, paid=390, class_group=klass); db.commit()
send_mode["fail"] = True
failed = wq.send_week(db, klass, 2, base_url=BASE)
send_mode["fail"] = False
quiz_d = next(q for q in wq.quizzes_of(db, klass.id, 2) if q.candidate_id == dora.id)
check("send: a newcomer is the only one emailed; a failed email is said and kept", failed["failed"] == 1 and failed["already"] == 3 and quiz_d.sent_at is None, failed)
retry = wq.send_week(db, klass, 2, base_url=BASE)
check("send: trying again sends the same questions to the one who missed them", retry["sent"] == 1 and db.get(Quiz, quiz_d.id).sent_at is not None and len(wq.quizzes_of(db, klass.id, 2)) == 4, retry)
check("send: an address on this computer is recognised", wq.is_local_address("http://127.0.0.1:8000") and wq.is_local_address("http://localhost:8000/") and not wq.is_local_address(BASE))

# ---- answering
quiz_a = next(q for q in quizzes if q.candidate_id == amal.id)
quiz_b = next(q for q in quizzes if q.candidate_id == bilel.id)
right_a = [q["correct_answer"] for q in wq.questions_in(quiz_a)]
result = wq.grade(db, quiz_a, right_a)
check("answers: all right is 100% and 'understood'", result["score"] == 100 and result["level"]["key"] == "strong" and result["right"] == 3 and quiz_a.completed_at is not None, result["score"])
changed = wq.grade(db, quiz_a, [9, 9, 9])
check("answers: given once, they are final", changed["score"] == 100 and wq.answers_in(quiz_a) == right_a)
wrong_b = [(q["correct_answer"] + 1) % len(q["options"]) for q in wq.questions_in(quiz_b)]
result_b = wq.grade(db, quiz_b, wrong_b)
check("answers: all wrong is 0% and 'needs support', with what was missed", result_b["score"] == 0 and result_b["level"]["key"] == "weak" and len(quiz_b.improvement_areas.splitlines()) == 3)
check("answers: recorded in the candidate's history", db.query(Activity).filter(Activity.candidate_id == amal.id, Activity.event_type == "WEEKLY_QUESTIONS_ANSWERED").count() == 1)
summary = wq.week_results(db, klass, 2)
check("answers: the class result counts who answered", summary["sent"] == 4 and summary["answered"] == 2 and summary["average"] == 50 and summary["level"]["key"] == "partial", {k: summary[k] for k in ("sent", "answered", "average")})
check("answers: the most missed questions are listed", summary["hardest"] and summary["hardest"][0]["wrong"] >= 1)
outbox.clear()
reminded = wq.remind_week(db, klass, 2, base_url=BASE)
check("answers: a reminder goes only to those who have not answered", reminded["sent"] == 2 and {m["to_email"] for m in outbox} == {chaima.email, dora.email}, reminded)
learning = wq.candidate_weeks(db, amal.id)
check("answers: the candidate's results over the weeks", learning["average"] == 100 and learning["answered"] == 1 and learning["weeks"][0]["class_name"] == "T-DEVOPS-1" and wq.candidate_weeks(db, chaima.id)["waiting"] == 1)
overview_numbers = wq.class_overview(db, klass)
check("answers: the class in four numbers", overview_numbers["files"] == 6 and overview_numbers["sent_weeks"] == 1 and overview_numbers["average"] == 50, overview_numbers)
check("answers: a private link cannot be guessed short", wq.quiz_by_token(db, "abc") is None and wq.quiz_by_token(db, quiz_a.access_token).id == quiz_a.id)

# ---- automatic sending
local_now = datetime.now()
check("auto: a week is over once its session has ended", wq.week_is_over(db, klass, 1, local_now) and wq.week_is_over(db, klass, 2, local_now) and not wq.week_is_over(db, klass, 3, local_now))
outbox.clear()
check("auto: a class set to 'when I press Send' is left alone", wq.send_due(db, BASE) == 0 and not outbox)
klass.weekly_questions_mode = "auto"; db.commit()
for q in week3: q.status = "ready"
db.commit()
check("auto: weeks not over yet are not sent", wq.send_due(db, BASE) == 0, len(outbox))
for session in db.query(ClassSession).filter(ClassSession.class_group_id == klass.id, ClassSession.week_number == 3): session.session_date = (local_now - timedelta(days=1)).date()
db.commit()
check("auto: a finished week with ready questions is sent by itself", wq.send_due(db, BASE) == 4 and len(outbox) == 4 and "semaine 3" in outbox[0]["subject"], len(outbox))
check("auto: and only once", wq.send_due(db, BASE) == 0)
tp.save(db, "weekly_questions", subject="S {week_number}", body="B {questions_link}", is_active=False)
check("auto: the weekly questions cannot be switched off (always on)", tp.is_on(db, "weekly_questions"))
tp.reset(db, "weekly_questions")
klass.weekly_questions_mode = "manual"; db.commit()

# ======================================================================
# E. Recommendations
# ======================================================================
def owing(name, email, *, days_ago, reminders=0, last_days_ago=None, phone="20 333 444", programs=1, paid=0.0, wrote=False):
    candidate = person(name, email, phone=phone)
    regs = [register(candidate, [python, devops, flutter][i], paid=paid if i == 0 else 0, days_ago=days_ago) for i in range(programs)]
    for index in range(reminders):
        when = now - timedelta(days=(last_days_ago or 0) + (reminders - 1 - index) * 4)
        db.add(CandidateEmail(candidate_id=candidate.id, registration_id=regs[0].id, direction="outbound", sender="x", recipient=email,
                              subject="Rappel", body="...", intent="payment_reminder", status="sent", received_at=when, author="system"))
    if wrote:
        db.add(CandidateEmail(candidate_id=candidate.id, direction="inbound", sender=email, recipient="x", subject="Re", body="Je paie demain", status="received", received_at=now, author="candidate"))
    db.commit(); db.refresh(candidate)
    return candidate
def pay_advice(candidate):
    history = advisor._payment_history(db, [candidate.id])
    return advisor.payment_advice(candidate, history[candidate.id], datetime.utcnow(), datetime.now())
fresh = owing("Farah New", "farah@example.com", days_ago=0)
first = owing("Ghassen First", "ghassen@example.com", days_ago=5)
waitc = owing("Hela Wait", "hela@example.com", days_ago=6, reminders=1, last_days_ago=0)
againc = owing("Ines Again", "ines@example.com", days_ago=12, reminders=1, last_days_ago=5)
callc = owing("Jamel Call", "jamel@example.com", days_ago=30, reminders=3, last_days_ago=4, programs=3)
nophone = owing("Karim NoPhone", "karim@example.com", days_ago=30, reminders=3, last_days_ago=4, phone="")
wrotec = owing("Lina Wrote", "lina@example.com", days_ago=9, reminders=2, last_days_ago=1, wrote=True)
partial = owing("Mehdi Partial", "mehdi@example.com", days_ago=8, paid=150.0)

a = pay_advice(fresh)
check("advice: registered today -> nothing to do yet, and it says why", a.tone == "info" and "Nothing to do yet" in a.title and "24 hours" in a.why and a.action is None, a.title)
a = pay_advice(first)
check("advice: never reminded -> read and send a reminder", a.tone == "important" and "payment reminder" in a.title and a.action["method"] == "get" and a.action["url"].endswith("/payment-reminder") and "390 DT" in a.why, (a.title, a.why))
a = pay_advice(waitc)
check("advice: reminded today -> wait, do not add noise", "Give Hela Wait time" in a.title and a.action is None and a.tone == "info", a.title)
a = pay_advice(againc)
check("advice: reminded 5 days ago, nothing paid -> remind again", "Ines Again has not answered for 5 days" in a.title and "no payment and no answer" in a.why and a.action["label"] == "Read and send", (a.title, a.why))
a = pay_advice(callc)
check("advice: three reminders unanswered -> call, with the number", "Call Jamel Call on +21620333444" in a.title and a.action["url"].startswith("tel:") and "3 reminders" in a.why, (a.title, a.action))
check("advice: several unpaid programs are ONE recommendation with the total", "1170 DT" in a.why and "on 3 programs" in a.why and "3 programs" in a.facts, (a.why, a.facts))
a = pay_advice(nophone)
check("advice: no phone -> write personally instead", "Call Karim NoPhone:" in a.title and a.action["url"].endswith("/payment-reminder"), (a.title, a.action))
a = pay_advice(wrotec)
check("advice: the candidate answered -> read it before reminding", "Read Lina Wrote's message" in a.title and a.action["url"] == f"/inbox?candidate={wrotec.id}", a.title)
a = pay_advice(partial)
check("advice: a part payment is acknowledged", "240 DT due" in a.facts and "150 DT already paid" in a.facts, a.facts)
check("advice: someone who paid everything has no payment advice", pay_advice(amal) is None)
check("advice: the key changes when the situation does", pay_advice(againc).key != pay_advice(first).key and ":1:" in pay_advice(againc).key)

thread = EmailThread(candidate_id=first.id, subject="Question sur le prix", needs_admin=True, status="needs_admin", last_message_at=now - timedelta(hours=30))
db.add(thread); db.commit()
board = advisor.for_dashboard(db)
titles = [item.title for item in board]
check("advice: the dashboard lists what needs the admin, most pressing first", board and board == sorted(board, key=lambda i: -i.priority) and any("Answer" in t for t in titles[:3]), titles[:6])
inbox_item = next(item for item in board if item.icon == "bi-envelope-exclamation")
check("advice: an email waiting over a day is urgent and cannot be set aside", inbox_item.tone == "urgent" and not inbox_item.can_snooze, (inbox_item.tone, inbox_item.title))
check("advice: 'wait' and 'nothing yet' stay off the dashboard", not any("Give Hela Wait time" in t or "Nothing to do yet" in t for t in titles))
check("advice: one line per candidate, even with three unpaid programs", sum(1 for t in titles if "Jamel" in t) == 1)
check("advice: a week without course is noticed only when it is the current one", not any("Add week 2's course to T-DEVOPS-1" in t for t in titles), titles)
low = next((item for item in board if "Go over week 2 again with T-DEVOPS-1" in item.title), None)
check("advice: an average of exactly 50% is not flagged as not understood", low is None, low and low.why)
quiz_c = next(q for q in wq.quizzes_of(db, klass.id, 2) if q.candidate_id == chaima.id)
wq.grade(db, quiz_c, [(q["correct_answer"] + 1) % len(q["options"]) for q in wq.questions_in(quiz_c)])
board = advisor.for_dashboard(db)
low = next((item for item in board if "Go over week 2 again with T-DEVOPS-1" in item.title), None)
check("advice: below 50% on average, the class is told to go over the week", low is not None and "33%" in low.why and "Most missed" in low.why, low and low.why)
klass.professor_name = None; db.commit()
check("advice: a running class without professor is flagged", any("Give T-DEVOPS-1 a professor" in item.title for item in advisor.for_dashboard(db)))
klass.professor_name = "Rami"; db.commit()
check("advice: a running class with no candidate is flagged", any("no candidate" in item.title for item in board), [i.title for i in board if "candidate" in i.title])
for q in wq.questions_of(db, klass.id, 5): q.status = "review"
db.commit()
board = advisor.for_dashboard(db)
check("advice: a week whose questions all wait for a check is flagged", any("Review week 5's questions for T-DEVOPS-1" in item.title for item in board), [i.title for i in board if "week" in i.title])
grouped = [item for item in board if "this week's course" in item.title]
check("advice: classes without this week's course are one line, naming them", len(grouped) == 1 and "classes" in grouped[0].title and "week" in grouped[0].why and not any("Add week" in i.title for i in board), [i.title for i in board if "course" in i.title])
unplaced = register(amal, flutter, paid=390); db.commit()
check("advice: a paid candidate without class must be placed", any("Place 1 paid candidate" in item.title for item in advisor.for_dashboard(db)))
check("advice: proposals waiting are grouped", any("proposal" in item.title.lower() for item in advisor.for_dashboard(db)))

target = next(item for item in advisor.for_dashboard(db) if "Call Jamel" in item.title)
advisor.snooze(db, target.key)
check("advice: 'not now' hides it", not any("Call Jamel" in item.title for item in advisor.for_dashboard(db)) and db.get(AdviceSnooze, target.key) is not None)
db.add(CandidateEmail(candidate_id=callc.id, direction="outbound", sender="x", recipient=callc.email, subject="Rappel", body=".", intent="payment_reminder", status="sent", received_at=now, author="system")); db.commit()
check("advice: ...until the situation changes", "Give Jamel Call time" in advisor.for_candidate(db, callc)[0].title and advisor.for_candidate(db, callc)[0].key != target.key)
advisor.snooze(db, advisor.for_candidate(db, callc)[0].key)
mine = advisor.for_candidate(db, callc)
check("advice: set aside on the dashboard, a step still shows on the candidate's own page, and says so",
      "Jamel" in mine[0].title and "set this aside" in mine[0].why and not mine[0].can_snooze and not any("Jamel" in i.title for i in advisor.for_dashboard(db)), (mine[0].title, mine[0].why[-60:]))

mine = advisor.for_candidate(db, bilel)
check("advice: on a profile, a bad week is pointed out with the missed question", any("Help Bilel Test with week 2" in item.title and "missed" in item.why for item in mine), [i.title for i in mine])
calm = person("Olfa Calm", "olfa@example.com"); register(calm, devops, paid=390, class_group=klass); db.commit(); db.refresh(calm)
mine = advisor.for_candidate(db, calm)
check("advice: a profile with nothing to do says so", len(mine) == 1 and mine[0].tone == "good" and "Nothing needs you" in mine[0].title, [i.title for i in mine])
mine = advisor.for_candidate(db, first)
check("advice: a profile lists its steps, the waiting email first", mine[0].icon == "bi-envelope-exclamation" and any("payment reminder" in i.title for i in mine), [i.title for i in mine])
people = db.query(Candidate).all()
steps = advisor.for_candidates(db, people)
check("advice: the list gives each candidate one step", set(steps) == {c.id for c in people} and "Answer Ghassen" in steps[first.id].title and steps[calm.id] is None and "Call" in steps[nophone.id].title, steps[first.id].title)

figures = overview.figures(db, datetime.utcnow())
check("overview: money, people and understanding are counted", figures["candidates"] == len(people) and figures["collected"] > 0 and figures["due"] > 0 and 0 < figures["collection_rate"] < 100 and figures["understanding"] is not None, figures)
sessions = overview.coming_sessions(db, datetime.now())
check("overview: sessions of the next 7 days, soonest first", sessions == sorted(sessions, key=lambda s: (s["date"], str(s["start_time"] or ""))) and all(s["date"] >= datetime.now().date() for s in sessions))
feed = overview.activity_feed(db)
check("overview: the same event recorded many times shows once", len(feed) <= 8 and any(item["times"] > 1 for item in feed), [(i["label"], i["times"]) for i in feed])

# ======================================================================
# F. Pages
# ======================================================================
from app.services import auth
from app.models import User, UserSession
db.query(UserSession).delete(); db.query(User).delete(); db.commit(); auth._admin_exists = False
auth.create_user(db, email="tester@example.com", name="Tester", password="tester-password", role="admin")
client = TestClient(main.app, base_url="http://127.0.0.1:8000")
signed = client.post("/login", data={"email": "tester@example.com", "password": "tester-password"}, follow_redirects=False)
check("page: the tests sign in like anyone else", signed.status_code == 303 and signed.headers["location"] == "/")
def page(url, **kw):
    response = client.get(url, **kw)
    return response.status_code, response.text

status, html = page("/")
check("page: dashboard opens", status == 200 and "What needs you" in html and "Classes and weekly timetable" in html and "Newest registrations and most chosen programs" in html, status)
check("page: dashboard shows the recommendations with their reason and button", "Answer Ghassen" in html and "ui-advice" in html and "Not now" in html)
check("page: dashboard has no invented figures left", "4.9" not in html and "AVG. RATING" not in html and "ui-avatars.com" not in html)
check("page: dashboard shows real money and classes", "DT received" in html and "T-DEVOPS-1" in html)
status, html = page("/candidate/all")
check("page: candidates list opens", status == 200 and "Next step" in html and "Jamel Call" in html and "ui-avatars.com" not in html, status)
check("page: one row per candidate with one next step", html.count('class="cl-row') == len(people), html.count('class="cl-row'))
check("page: the old broken links are gone", "/candidate/new" not in html and "/candidate/delete/" not in html)
status, html = page("/candidate/all?search=jamel&add=1")
check("page: the search box is prefilled from the top bar, the add form opens", 'value="jamel"' in html and 'data-open="1"' in html)
status, html = page(f"/candidate/{callc.id}")
check("page: profile opens with its next step", status == 200 and "Next step" in html and "Jamel" in html and "tel:" in html, status)
check("page: the old list of recommendations is gone", "Smart suggestions for next reminders" not in html and "recommendations/dismiss" not in html and "recommendations/create-schedule" not in html)
check("page: profile shows one card per program with its payment", html.count('id="registration-') == 3 and "Paid in full (390 DT)" in html, html.count('id="registration-'))
status, html = page(f"/candidate/{bilel.id}")
check("page: profile shows what was understood, week by week", "What was understood" in html and "Needs support" in html and "Missed:" in html)
for gone in ("create-schedule", "schedule", "dismiss"):
    check(f"page: the dead route recommendations/{gone} is removed", client.post(f"/candidate/{bilel.id}/recommendations/{gone}", data={"registration_id": reg_b.id, "planned_for": "2026-11-01T10:00"}).status_code in (404, 405))

response = client.post("/candidate/create", data={"full_name": "  Nour   Added ", "email": "Nour.Added@Example.com", "phone": "22 000 111", "program_id": devops.id, "delivery_mode": "onsite"}, follow_redirects=False)
nour = db.query(Candidate).filter(Candidate.email == "nour.added@example.com").first()
check("page: a candidate can be added by hand, with a first program", response.status_code == 303 and nour is not None and nour.full_name == "Nour Added" and len(nour.registrations) == 1 and nour.registrations[0].total_amount == devops.price_onsite, response.status_code)
response = client.post("/candidate/create", data={"full_name": "Twin", "email": "nour.added@example.com"}, follow_redirects=False)
check("page: adding the same email again opens the existing file", response.status_code == 303 and f"/candidate/{nour.id}" in response.headers["location"] and db.query(Candidate).filter(Candidate.email == "nour.added@example.com").count() == 1)
response = client.post("/candidate/create", data={"full_name": "No Mail", "email": "not-an-address"}, follow_redirects=False)
check("page: a wrong email is refused with a message", "error=" in response.headers["location"] and "add=1" in response.headers["location"])
response = client.post(f"/candidate/{nour.id}/edit", data={"full_name": "Nour Edited", "email": "nour.added@example.com", "phone": "", "city": "Sfax"}, follow_redirects=False)
db.refresh(nour)
check("page: details can be edited (the phone is optional now)", response.status_code == 303 and nour.full_name == "Nour Edited" and nour.phone is None and nour.city == "Sfax")
response = client.post(f"/candidate/{nour.id}/edit", data={"full_name": "Nour", "email": amal.email}, follow_redirects=False)
check("page: an email already used by someone else is refused", "error=" in response.headers["location"])
client.post(f"/candidate/{nour.id}/notes", data={"admin_notes": "  Call after 6 pm  "}); db.refresh(nour)
check("page: private notes are saved", nour.admin_notes == "Call after 6 pm")
response = client.post(f"/candidate/{nour.id}/registrations", data={"program_id": devops.id}, follow_redirects=False)
check("page: registering twice to a program gives a message, not an error page", response.status_code == 303 and "error=" in response.headers["location"])
outbox.clear()
response = client.post(f"/candidate/{againc.id}/recommendations/send-now", data={"registration_id": againc.registrations[0].id}, follow_redirects=False)
check("page: the older button opens the page where the reminder is read, and sends nothing", response.status_code == 303 and response.headers["location"].endswith("/payment-reminder") and len(outbox) == 0)
response = client.post(f"/candidate/{againc.id}/payment-reminder", data={"subject": "Rappel", "body": "Bonjour, il reste 390 DT."}, follow_redirects=False)
check("page: sending from that page delivers one email", response.status_code == 303 and len(outbox) == 1 and outbox[0]["to_email"] == againc.email)
db.expire_all()
check("page: ...and the advice then says to wait", "Give Ines Again time" in pay_advice(db.get(Candidate, againc.id)).title)
some = next(item for item in advisor.for_dashboard(db) if item.can_snooze)
response = client.post("/advice/snooze", data={"key": some.key, "back": "//evil.example"}, follow_redirects=False)
check("page: 'not now' works from the page and only returns to this application", response.status_code == 303 and response.headers["location"] == "/" and db.get(AdviceSnooze, some.key) is not None)

status, html = page("/programs/all")
check("page: programs list opens", status == 200 and "Training programs" in html and "missing details" in html, status)
check("page: programs show their picture or their initials", f"/programs/{flutter.id}/icon?v=" in html and "pg-cover-tile" in html)
check("page: an incomplete program offers a proposal", "Propose them" in html or "Read and confirm" in html)
response = client.get(pi.url_of(flutter))
check("page: a picture is served safely", response.status_code == 200 and response.headers["content-type"].startswith("image/svg+xml") and "sandbox" in response.headers["content-security-policy"] and response.headers["x-content-type-options"] == "nosniff")
pi.remove(db, odd)
check("page: no picture, no file", client.get(f"/programs/{odd.id}/icon").status_code == 404)
response = client.get("/programs/icons/search", params={"q": "python"})
check("page: the picture search lists the catalogue", response.status_code == 200 and response.json()["icons"][0]["ref"] == "devicon:python" and response.json()["icons"][0]["url"].startswith(pi.API + "/devicon/python.svg"))
web["online"] = False
check("page: the search says when the web is unreachable", client.get("/programs/icons/search", params={"q": "python"}).status_code == 502)
web["online"] = True
response = client.post(f"/programs/{odd.id}/icon", data={"action": "choose", "ref": "fluent-emoji-flat:rocket"}, follow_redirects=False); db.refresh(odd)
check("page: choosing a picture from the catalogue", response.status_code == 303 and odd.icon_ref == "fluent-emoji-flat:rocket")
response = client.post(f"/programs/{odd.id}/icon", data={"action": "upload"}, files={"file": ("logo.png", png(64, 64), "image/png")}, follow_redirects=False); db.refresh(odd)
check("page: uploading one's own picture", response.status_code == 303 and odd.icon_ref == "upload")
response = client.post(f"/programs/{odd.id}/icon", data={"action": "upload"}, files={"file": ("x.exe", b"MZ", "application/octet-stream")}, follow_redirects=False)
check("page: a refused upload comes back with the reason", "error=" in response.headers["location"])
response = client.post(f"/programs/{odd.id}/icon", data={"action": "remove"}, follow_redirects=False); db.refresh(odd)
check("page: removing the picture", odd.icon_file is None and odd.icon_ref == "none")

response = client.post(f"/programs/{odd.id}/proposal/request", follow_redirects=False)
check("page: asking for a proposal", response.status_code == 303 and pa.open_proposal(db, odd.id).status == "queued")
status, html = page(f"/programs/all?review={odd.id}")
check("page: while it is written the review says so", "The proposal is being prepared" in html)
pa.work_one(db)
status, html = page(f"/programs/all?review={odd.id}")
check("page: the review shows every proposed field and where it comes from", 'id="reviewModal"' in html and 'name="description"' in html and "Used by" in html and 'name="week_1_3_topic"' in html and "Accept and save" in html)
proposed = pa.open_proposal(db, odd.id).details
form = {"description": "Hand corrected description.", "total_duration_days": "60", "hours_per_week": "6", "training_hours": "4", "supervision_hours": "2", "class_session_time": "Saturday 9:00"}
for li, level in enumerate(proposed["levels"]):
    form[f"level_{li}_title"] = level["title"]; form[f"level_{li}_description"] = level["description"]
    for wi, wk in enumerate(level["weeks"]):
        form[f"week_{li}_{wi}_topic"] = "" if (li, wi) == (0, 1) else wk["topic_title"]
        form[f"week_{li}_{wi}_objectives"] = wk["learning_objectives"]
response = client.post(f"/programs/{odd.id}/proposal/accept", data=form, follow_redirects=False); db.refresh(odd)
check("page: accepting from the form saves the corrected proposal", response.status_code == 303 and odd.description == "Hand corrected description." and odd.class_session_time == "Saturday 9:00", response.headers.get("location"))
check("page: a week emptied on the form is left out, the rest renumbered", sum(len(l.weeks) for l in odd.levels) == 7 and [w.week_number for l in odd.levels for w in l.weeks] == list(range(1, 8)))
status, html = page(f"/programs/{odd.id}")
check("page: program detail opens, complete", status == 200 and "This program is missing details" not in html and "Hand corrected description." in html)
status, html = page(f"/programs/{bootcamp.id}")
check("page: an incomplete program says what is missing on its own page", status == 200 and "This program is missing details" in html and "Propose them" in html)
response = client.post(f"/programs/{bootcamp.id}/proposal/request", follow_redirects=False); pa.work_one(db)
response = client.post(f"/programs/{bootcamp.id}/proposal/dismiss", follow_redirects=False); db.refresh(bootcamp)
check("page: dismissing from the page", pa.open_proposal(db, bootcamp.id) is None and bootcamp.description is None)
response = client.post(f"/programs/{odd.id}/delete", follow_redirects=False)
check("page: deleting a program", response.status_code == 303 and db.query(Program).filter(Program.name == "Zzzz Qqqq").count() == 0)

status, html = page(f"/classes/{klass.id}")
check("page: the class page links to its course", status == 200 and "Course and weekly questions" in html and f"/classes/{klass.id}/course" in html, status)
status, html = page(f"/classes/{klass.id}/course?week=2")
check("page: the course page opens on the asked week", status == 200 and "Semaine 2 - volumes.pdf" in html and "Candidates' answers" in html, status)
check("page: it says candidates cannot reach this computer, so they answer by replying", "cannot be reached from outside" in html and "answer by replying" in html)
check("page: it shows each file's reading and each question's source", "Read: about" in html and "The system learns from your note" in html and "From Semaine 2 - volumes.pdf" in html)
check("page: it shows who answered and what to go over", "Needs support" in html and "What to go over again" in html and "Remind" in html)
status, html = page(f"/classes/{klass.id}/course")
check("page: without a week it opens on the current one", status == 200 and 'aria-current="page"' in html and "Docker week 2" in html)
check("page: an unknown class is a 404", client.get("/classes/nope/course").status_code == 404)

response = client.post(f"/classes/{klass.id}/course/8/files", data={"note": "Slides of the last week"},
                       files=[("files", ("final.txt", ("The final week covers deployment to a server with one command. " * 6).encode(), "text/plain")),
                              ("files", ("app.exe", b"MZ", "application/octet-stream"))],
                       headers={"X-Requested-With": "fetch"})
body = response.json()
week8 = db.query(ClassWeek).filter(ClassWeek.class_group_id == klass.id, ClassWeek.week_number == 8).first()
check("page: uploading files answers the page's script, says what was refused, and asks for questions",
      response.status_code == 200 and body["ok"] and "1 file added" in body["notice"] and "not accepted" in body["error"] and week8.questions_status == "queued", body)
response = client.post(f"/classes/{klass.id}/course/8/files", files=[("files", ("pic.png", png(20, 20), "image/png"))], follow_redirects=False)
db.expire_all()
check("page: a photo alone asks for a note instead of questions", response.status_code == 303 and "Write+a+few+lines" in response.headers["location"])
picture = db.query(ClassMaterial).filter(ClassMaterial.class_group_id == klass.id, ClassMaterial.filename == "pic.png").first()
response = client.get(f"/classes/files/{picture.id}")
check("page: a course file opens from its link", response.status_code == 200 and response.headers["content-type"] == "image/png" and response.headers["content-disposition"].startswith("inline"))
response = client.get(f"/classes/files/{pdf.id}")
check("page: a PDF opens in the browser, safely", response.headers["content-disposition"].startswith("inline") and response.headers["x-content-type-options"] == "nosniff")
client.post(f"/classes/{klass.id}/course/files/{picture.id}/note", data={"note": "Photo of the board: the deployment steps in order."})
db.expire_all()
check("page: a note on a photo is saved", db.get(ClassMaterial, picture.id).note.startswith("Photo of the board"))
picture_id = picture.id
response = client.post(f"/classes/{klass.id}/course/files/{picture_id}/delete", follow_redirects=False)
db.expire_all()
check("page: a course file can be removed", response.status_code == 303 and db.get(ClassMaterial, picture_id) is None)
response = client.post(f"/classes/{klass.id}/course/8/notes", data={"notes": "The week ends with a demo of the whole pipeline, from commit to production, in front of the class."}, follow_redirects=False)
while wq.work_one(db):                      # (the weeks always on asked for are written too)
    pass
db.expire_all()
check("page: notes are saved and questions are written from them", any(q.material_id == "notes" for q in wq.questions_of(db, klass.id, 8)), [q.material_id for q in wq.questions_of(db, klass.id, 8)])
response = client.post(f"/classes/{klass.id}/course/8/questions/add", data={"question": "What does the demo show?", "option_0": "The whole pipeline", "option_1": "Only the tests", "correct": "0"}, follow_redirects=False)
added = db.query(WeekQuestion).filter(WeekQuestion.question == "What does the demo show?").first()
check("page: adding one's own question", response.status_code == 303 and added is not None and added.status == "ready")
added_id = added.id
response = client.post(f"/classes/{klass.id}/course/8/questions/add", data={"question": "Bad", "option_0": "a", "option_1": "b", "correct": "0"}, follow_redirects=False)
check("page: a badly filled question comes back with the reason", "error=" in response.headers["location"])
client.post(f"/classes/{klass.id}/course/questions/{added_id}/save", data={"question": "What does the final demo show?", "option_0": "The whole pipeline", "option_1": "Only the tests", "option_2": "Nothing", "correct": "0"})
db.expire_all()
check("page: correcting a question", db.get(WeekQuestion, added_id).question == "What does the final demo show?" and len(db.get(WeekQuestion, added_id).choices) == 3)
client.post(f"/classes/{klass.id}/course/questions/{added_id}/status", data={"status": "off"}); db.expire_all()
check("page: setting a question aside", db.get(WeekQuestion, added_id).status == "off")
check("page: a question of another class cannot be touched", client.post(f"/classes/{empty_class.id}/course/questions/{added_id}/delete").status_code == 404)
waiting_review = [q.id for q in wq.questions_of(db, klass.id, 4) if q.status == "review"]
client.post(f"/classes/{klass.id}/course/4/questions/approve-all"); db.expire_all()
check("page: 'I checked them' marks the waiting questions ready", waiting_review and all(db.get(WeekQuestion, qid).status == "ready" for qid in waiting_review))
client.post(f"/classes/{klass.id}/course/questions/{added_id}/delete"); db.expire_all()
check("page: deleting a question", db.get(WeekQuestion, added_id) is None)
response = client.post(f"/classes/{klass.id}/course/settings", data={"week": "2", "weekly_questions_mode": "auto", "weekly_questions_count": "4"}, follow_redirects=False)
db.expire_all()
check("page: how many questions and how they are sent can be set per class", db.get(ClassGroup, klass.id).weekly_questions_mode == "auto" and db.get(ClassGroup, klass.id).weekly_questions_count == 4)
client.post(f"/classes/{klass.id}/course/settings", data={"week": "2", "weekly_questions_mode": "manual", "weekly_questions_count": "3"})
outbox.clear()
response = client.post(f"/classes/{klass.id}/course/4/send", follow_redirects=False)
check("page: sending from the page emails the class and says the questions are in the emails",
      response.status_code == 303 and len(outbox) == 5 and "received+their+questions" in response.headers["location"] and "answer+by+replying" in response.headers["location"], response.headers["location"][:200])
check("page: with no address candidates can reach, the questions are in the email, not a link", "127.0.0.1:8000/learn/" not in outbox[0]["body"]
      and "répondez simplement à cet email" in outbox[0]["body"])
response = client.post("/settings/address", data={"public_base_url": "app.9antra.tn/"}, follow_redirects=False)
check("page: the address for candidates can be set", settings_service.public_base_url(SessionLocal()) == "https://app.9antra.tn")
check("page: a wrong address is refused", "error=" in client.post("/settings/address", data={"public_base_url": "ftp://x y"}, follow_redirects=False).headers["location"])
outbox.clear()
response = client.post(f"/classes/{klass.id}/course/4/send", data={"remind": "1"}, follow_redirects=False)
check("page: reminders then carry the public address", len(outbox) == 5 and "https://app.9antra.tn/learn/" in outbox[0]["body"] and "only+opens" not in response.headers["location"])
status, html = page("/settings")
check("page: settings shows the address for candidates", status == 200 and "Address for candidates" in html and "https://app.9antra.tn" in html)
status, html = page("/inbox/templates")
check("page: 'Weekly questions' is an email the admin can edit", status == 200 and "Weekly questions" in html)
status, html = page("/inbox/templates?key=weekly_questions")
check("page: its editor opens with its own variables", status == 200 and "questions_link" in html and "week_topic" in html, status)

# ---- the candidate's page
db.expire_all()
quiz4 = next(q for q in wq.quizzes_of(db, klass.id, 4) if q.candidate_id == amal.id)
status, html = page(f"/learn/{quiz4.access_token}")
check("learn: the candidate's page opens with the questions", status == 200 and "Envoyer mes réponses" in html and 'name="q0"' in html and html.count("<fieldset>") == len(wq.questions_in(quiz4)) + 5, status)
check("learn: it shows nothing of the admin application", "sidebar" not in html and "/inbox" not in html and "Dashboard" not in html and "notif" not in html.lower())
check("learn: it does not give the answers away", "correct_answer" not in html and "Bonne réponse" not in html)
response = client.get(f"/learn/{quiz4.access_token}")
check("learn: the page is private to search engines and caches", response.headers["cache-control"] == "no-store" and "noindex" in response.headers["x-robots-tag"])
status, html = page("/learn/this-token-does-not-exist-at-all")
check("learn: a wrong link is a clear 404 page", status == 404 and "n'existe pas" in html)
questions4 = wq.questions_in(quiz4)
response = client.post(f"/learn/{quiz4.access_token}", data={"q0": str(questions4[0]["correct_answer"])})
check("learn: an incomplete answer is asked to be completed, nothing recorded", response.status_code == 200 and "toutes les questions" in response.text and db.get(Quiz, quiz4.id).completed_at is None)
answers = {f"q{i}": str(q["correct_answer"] if i else (q["correct_answer"] + 1) % len(q["options"])) for i, q in enumerate(questions4)}
response = client.post(f"/learn/{quiz4.access_token}", data=answers, follow_redirects=False)
db.expire_all()
done4 = db.get(Quiz, quiz4.id)
check("learn: answers are graded and the candidate is sent to the correction", response.status_code == 303 and done4.completed_at is not None and 0 < done4.score < 100, done4.score)
status, html = page(f"/learn/{quiz4.access_token}")
check("learn: the correction shows the right answers and the candidate's own", "Voici la correction" in html and "Bonne réponse" in html and "Votre réponse" in html and "Envoyer mes réponses" not in html)
response = client.post(f"/learn/{quiz4.access_token}", data={f"q{i}": "0" for i in range(len(questions4))}, follow_redirects=False)
db.expire_all()
check("learn: answering a second time changes nothing", response.status_code == 303 and db.get(Quiz, quiz4.id).score == done4.score)
check("learn: the old quiz page does not open weekly questions", client.get(f"/quiz/{quiz4.id}/take").status_code == 404)
status, html = page("/quiz/all")
check("page: the quiz center keeps to level quizzes", status == 200 and "semaine 4" not in html)

# ---- deleting a class removes its course
files_before = len(list(cm.storage_dir().iterdir()))
solo = ClassGroup(name="T-SOLO", program_id=devops.id, status=ClassStatus.NOT_STARTED, start_mode="min_enrollment", total_weeks=4)
db.add(solo); db.commit()
cm.save_upload(db, solo, 1, Upload("solo.txt", b"Some course text for the lonely class, long enough to be read by the system."))
response = client.post(f"/classes/{solo.id}/delete", follow_redirects=False)
check("page: deleting a class removes its course files", response.status_code == 303 and len(list(cm.storage_dir().iterdir())) == files_before
      and db.query(ClassMaterial).filter(ClassMaterial.class_group_id == solo.id).count() == 0, response.headers.get("location"))

status, html = page("/classes/")
check("page: classes list still opens", status == 200, status)
status, html = page("/inbox")
check("page: inbox still opens", status == 200, status)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
