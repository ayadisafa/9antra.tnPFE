"""Frequent questions: the Summer Camp FAQ of the centre's carousel is in the database, every assistant answers with it,
but only when the question is about the Summer Camp; the questions candidates ask most are counted by themselves; a
carousel's pictures are read; FAQ posts are drawn in the centre's design, with a theme per season.
Runs on a COPY; nothing leaves this machine."""
import io
import zipfile
from datetime import date, datetime, timedelta

import testenv

testenv.prepare("faq_posts")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from PIL import Image

from app.database import SessionLocal
from app.models import Candidate, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.faq_item import FaqItem
from app.models.knowledge import KnowledgeEntry
from app.services import auth, faq, faq_posts, knowledge_service, proof_reader

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.query(FaqItem).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="faq-posts-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "faq-posts-1"})

# ---- A. The Summer Camp FAQ of the carousel, in the database ----
added = faq.ensure_seed(db)
rows = db.query(FaqItem).filter(FaqItem.theme == "summer_camp").all()
check("the seven questions of the carousel are kept, each with its answer for the assistants", added == 7 and len(rows) == 7
      and all(row.entry_id and row.post_question and row.post_answer for row in rows))
check("…once (a second pass adds nothing)", faq.ensure_seed(db) == 0)
entries = [db.get(KnowledgeEntry, row.entry_id) for row in rows]
check("their answers belong to the Summer Camp", all((entry.source_key or "").startswith("faq:summer_camp:") for entry in entries))
schedule = next(row for row in rows if row.post_question == "Kifesh elwa9t?")
check("the post's words are kept as the post says them", "chaque samedi 4h" in schedule.post_answer
      and "Chaque samedi, 4 heures de formation" in db.get(KnowledgeEntry, schedule.entry_id).content)

# ---- B. Every assistant answers with them, only about the Summer Camp ----
def found(question):
    return [hit for hit in knowledge_service.search(db, question, questions=[question], limit=5)]


def ids(hits):
    return {hit["id"] for hit in hits}


hits = found("Quels sont les horaires du Summer Camp ?")
check("asked about the Summer Camp's schedule: its answer is found", schedule.entry_id in ids(hits), [hit.get("title") for hit in hits])
hits = found("Quels sont les horaires de la formation ?")
check("asked about a schedule without the Summer Camp: never its Saturdays", schedule.entry_id not in ids(hits), [hit.get("title") for hit in hits])
hits = found("kifech el wa9t fel summer camp")
check("asked in Tunisian about the Summer Camp: found", schedule.entry_id in ids(hits))
from app.services import email_assistant

check("the email assistant's search by meaning keeps the same rule", (email_assistant.taught_by_meaning(db, "Quel est l'emploi du temps ?", set()) or {}).get("id")
      != schedule.entry_id)

# ---- C. What candidates ask most, counted by itself ----
person = Candidate(full_name="Faq Asker", email="faq.asker@test.local")
db.add(person)
db.commit()
for text in ("Combien coûte la formation Python ?", "Combien coute la formation Python svp ?", "combien coûte la formation python",
             "Quand commence la formation DevOps ?", "Quand commence la formation DevOps svp ?", "Est-ce que vous avez un parking ?"):
    db.add(CandidateEmail(candidate_id=person.id, direction="inbound", sender=person.email, recipient="centre@test.local", subject="q", body=text,
                          question=text, status="received", received_at=datetime.utcnow() - timedelta(days=1)))
db.commit()
result = faq.refresh(db)
counted = {row.question.lower(): row.asked for row in db.query(FaqItem).filter(FaqItem.source == "messages").all()}
check("the questions asked most are kept, with how many times", any("python" in question and asked == 3 for question, asked in counted.items())
      and any("devops" in question and asked == 2 for question, asked in counted.items()), counted)
check("a question asked once is not a frequent one", not any("parking" in question for question in counted))
page = admin.get("/inbox/knowledge").text
check("the knowledge page shows the frequent questions, the most asked first, and which have no answer yet", 'id="faq"' in page
      and page.index("Combien") < page.index("emploi du temps du Summer Camp") and "No answer yet" in page,
      ("Combien" in page, "No answer yet" in page, [(row["item"].question[:40], row["item"].asked, bool(row["answer"])) for row in faq.listing(db)][:6]))
taught_part = page[page.index('id="live-knowledge-written"'):]
check("each answer is said once (not also among the taught answers)", "Chaque samedi, 4 heures de formation" not in taught_part)

# ---- D. Teaching, changing, removing ----
item = db.query(FaqItem).filter(FaqItem.source == "messages", FaqItem.question.ilike("%python%")).first()
answer = admin.post("/inbox/knowledge/faq/save", data={"item_id": item.id, "question": item.question, "answer": "La formation Python coûte 390 DT.", "theme": ""})
db.expire_all()
item = db.get(FaqItem, item.id)
check("taught from the frequent questions: every assistant answers with it", answer.status_code == 303 and item.entry_id
      and db.get(KnowledgeEntry, item.entry_id).content == "La formation Python coûte 390 DT.")
admin.post(f"/inbox/knowledge/faq/{item.id}/delete")
check("removed from the frequent questions", db.query(FaqItem).filter(FaqItem.id == item.id).count() == 0)

# ---- E. The posts, in the centre's design, a theme per season ----
check("the season follows the date", [faq_posts.season_of(day) for day in (date(2026, 7, 1), date(2026, 10, 1), date(2027, 1, 10), date(2027, 2, 20), date(2027, 4, 10))]
      == ["summer", "rentree", "winter", "ramadan", "spring"])
png = faq_posts.slide(schedule.post_question, schedule.post_answer, "summer")
check("a slide: a square picture", Image.open(io.BytesIO(png)).size == (1080, 1080))
if proof_reader.ocr_available():
    read = faq.read_slide(png)
    check("a slide read back by the picture reader: its question and its answer", read is not None and "elwa9t" in read["question"].lower()
          and "samedi" in read["answer"].lower(), read)
    answer = admin.post("/inbox/knowledge/faq/read", files=[("pictures", ("slide.png", png, "image/png"))])
    check("“Read a carousel's pictures”: what was read is shown to check before keeping it", answer.status_code == 200 and "Read from the pictures" in answer.text
          and "elwa9t" in answer.text.lower())
else:
    check("the picture reader is not installed here (the read is skipped)", True)
page = admin.get("/social").text
check("Social media › FAQ posts: the slides of this season, the contact slide, the caption, the download", 'data-tab="faq"' in page
      and "/social/faq/slide.png?item=" in page and "/social/faq/contact.png" in page and "/social/faq/carousel.zip" in page)
slide = admin.get(f"/social/faq/slide.png?item={schedule.id}&theme=winter")
check("a slide from the page, in the season chosen", slide.status_code == 200 and slide.headers["content-type"] == "image/png")
archive = zipfile.ZipFile(io.BytesIO(admin.get(f"/social/faq/carousel.zip?theme=summer&items={schedule.id}").content))
names = archive.namelist()
check("the carousel: its slides in order, the contact slide, and the caption in Tunisian", names == ["01.png", "02-contact.png", "caption.txt"]
      and "شنوا تحب تعرف على" in archive.read("caption.txt").decode("utf-8"), names)
from app.services.translation_seed import MORE

check("in French and Arabic, by hand", tuple(MORE["Frequent questions"]) == ("Questions fréquentes", "الأسئلة المتكررة") and "FAQ posts, in your design" in MORE)

check.done()
