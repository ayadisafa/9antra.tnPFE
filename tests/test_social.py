"""Social media and the dashboard's feedback (1 November): every feedback in one place, posts and stories made from the
records, videos with composed music, “we listen”, and the engagement of posts. Runs on a COPY; nothing is sent."""
import io
import json
import os
import subprocess
from datetime import datetime, timedelta, time as dtime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("social")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Quiz, Registration, User, UserSession
from app.models.feedback import CandidateFeedback
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.social import FeedbackAnswer, SocialAsset, SocialPost
from app.models.website import WebsiteItem
from app.services import (access, auth, feedback_hub, program_feedback, social_content, social_engagement, social_images,
                          social_music, social_video, whats_new)

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="social-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "social-test-1"})


def where(response):
    return unquote_plus(response.headers.get("location", ""))


program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()

# ---- What the copy holds, plus a public feedback, two reviews and a weekly check-in ----
db.add(WebsiteItem(kind="feedback", external_id="social-test-1", title="Amira Ben Ali", summary="",
                   reading=json.dumps({"version": 2, "text": "Une formation très pratique avec un formateur disponible, je recommande vivement à tous.",
                                       "sentiment": "positive", "stars": 5, "themes": ["trainer", "practice"], "positive": ["pratique"], "name": "Amira Ben Ali"})))
person = Candidate(full_name="Sami Social", email="sami.social@example.com", phone="50 300 400")
db.add(person)
db.flush()
registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390,
                            total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.COMPLETED)
db.add(registration)
db.flush()
db.add(CandidateFeedback(candidate_id=person.id, registration_id=registration.id, source=program_feedback.SOURCE, author_name="Sami Social",
                         program_name=program.name, rating=5, comment="Très bonne ambiance et un contenu riche, merci à toute l'équipe de 9antra !",
                         sentiment="positive", status="received", submitted_at=datetime.utcnow(),
                         raw_payload=json.dumps({"may_publish": True, "themes": ["atmosphere", "content"], "recommend": "yes"})))
other = Candidate(full_name="Rim Private", email="rim.private@example.com")
db.add(other)
db.flush()
db.add(CandidateFeedback(candidate_id=other.id, source=program_feedback.SOURCE, author_name="Rim Private", program_name=program.name, rating=3,
                         comment="Les séances du samedi sont vraiment trop longues, il faudrait des pauses et plus de pratique.",
                         sentiment="mixed", status="received", submitted_at=datetime.utcnow(),
                         raw_payload=json.dumps({"may_publish": False, "improve": "Des pauses pendant les séances du samedi",
                                                 "improve_themes": ["organisation"], "themes": ["organisation"]})))
db.add(Quiz(candidate_id=person.id, course_name=program.name, kind="weekly", week_number=2, questions="[]", completed_at=datetime.utcnow(),
            feedback=json.dumps({"mood": 2, "pace": "fast", "hard": "Le rythme est trop rapide pour moi", "message": ""}),
            insight=json.dumps({"sentiment": "negative"})))
start = datetime.utcnow() + timedelta(days=9)
db.add(ClassGroup(name="SOCIAL-NEXT-1", program_id=program.id, status=ClassStatus.NOT_STARTED, start_date=start, total_weeks=8,
                  start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date", capacity=12, max_capacity=12))
db.commit()

# ======================================================================
# A. Every feedback in one place
# ======================================================================
voices = feedback_hub.overview(db)
sources = {row["source"] for row in voices["rows"]}
check("feedback: the website's, the reviews and the weekly check-ins together", {"website", "review", "weekly"} <= sources, sources)
check("feedback: the unhappy ones first", voices["rows"][0]["sentiment"] in ("negative", "mixed"), voices["rows"][0]["sentiment"])
check("feedback: what to improve comes from what they said", any(item["key"] == "organisation" for item in voices["improve"]), voices["improve"])
check("feedback: one source alone", all(row["source"] == "review" for row in feedback_hub.overview(db, "review")["rows"]))
check("feedback: those waiting for an answer", all(row["sentiment"] in ("negative", "mixed") for row in feedback_hub.overview(db, "answer")["rows"]))
check("feedback: recommendations counted", voices["recommend_pct"] is not None)
home = admin.get("/").text
check("dashboard: one section for every feedback", "What people say" in home and 'data-said-filter="review"' in home and 'data-said-filter="weekly"' in home
      and 'data-said-filter="website"' in home and home.count('class="db-said-card') == voices["total"])
check("dashboard: each card says where it was given", "End-of-program reviews" in home and "Weekly check-ins" in home)

# ======================================================================
# B. Ideas made from the records
# ======================================================================
ideas = social_content.ideas(db, "fr")
topics = {item["topic"] for item in ideas}
check("ideas: success, testimonials, a program that starts, how to join", {"success", "testimonial", "program", "join"} <= topics, topics)
quotes = [item["quote"] for item in ideas if item["topic"] == "testimonial"] + [item["quote"] for item in social_content.testimonials(db, 20)]
check("ideas: a review whose author agreed can be quoted", any("Très bonne ambiance" in quote for quote in quotes))
check("ideas: never a review whose author did not agree, nor a weekly check-in", not any("trop longues" in quote or "trop rapide" in quote for quote in quotes))
names = [item["name"] for item in social_content.testimonials(db, 20)]
check("ideas: first names only", "Sami" in names and not any(" " in name for name in names), names)
program_ideas = [item for item in ideas if item["topic"] == "program"]
check("ideas: the next session, with its program and date", any(item["headline"] == program.name and str(start.day) in item["caption"] for item in program_ideas)
      or (len(program_ideas) == 4 and all(item["headline"] != program.name for item in program_ideas)), [item["headline"] for item in program_ideas])
link = social_content.contact(db)["link"]
check("ideas: every caption leads to the registration form", all(link in item["caption"] for item in ideas if item["topic"] in ("success", "program", "join")))
check("ideas: hashtags", all(item["hashtags"].startswith("#") for item in ideas))
arabic = social_content.ideas(db, "ar")
check("ideas: in Arabic too", any("سجّل" in item["cta"] for item in arabic) and any("قلتم" in item["you_said"] for item in arabic))
check("ideas: no “we listen” before an answer", "listen" not in topics)

# ======================================================================
# C. We listen
# ======================================================================
proposals = social_content.listen_proposals(db)
check("we listen: what people asked to improve is proposed", any(item["theme"] == "organisation" for item in proposals), proposals)
response = admin.post("/social/listen", data={"you_said": "Les séances du samedi sont trop longues", "we_did": "Une pause de 20 minutes à chaque séance",
                                             "theme": "organisation"})
check("we listen: kept", "Kept" in where(response) and db.query(FeedbackAnswer).count() == 1, where(response))
check("we listen: an answered request is no longer proposed", not any(item["theme"] == "organisation" for item in social_content.listen_proposals(db)))
listen = [item for item in social_content.ideas(db, "fr") if item["topic"] == "listen"]
check("we listen: it becomes a post", listen and listen[0]["did"] == "Une pause de 20 minutes à chaque séance" and "Vous avez dit" in listen[0]["caption"])
check("we listen: too short is refused", "error" in admin.post("/social/listen", data={"you_said": "a"}).headers["location"])

# ======================================================================
# D. Pictures
# ======================================================================
for item in social_content.ideas(db, "fr") + social_content.ideas(db, "ar"):
    for fmt in ("post", "story"):
        picture = Image.open(io.BytesIO(social_images.render_png(item, fmt, "light" if item["lang"] == "ar" else "berry")))
        if picture.size != social_images.SIZES[fmt]:
            break
else:
    picture = None
check("pictures: every idea, post 1:1 and story 9:16, in French and Arabic", picture is None)
shot = social_images.picture(listen[0], "post")
footer = max(piece.y for piece in shot.pieces)
check("pictures: the content stays above the footer", all(piece.y + piece.image.height <= footer + 2 for piece in shot.pieces if piece.step < 9))
answer = admin.get(f"/social/image?idea={listen[0]['key']}&fmt=story&look=night&lang=fr&download=1")
check("pictures: downloaded as PNG", answer.status_code == 200 and answer.headers["content-type"] == "image/png"
      and "attachment" in answer.headers.get("content-disposition", ""))
check("pictures: an idea that is gone says so", admin.get("/social/image?idea=nothing:here").status_code == 404)

# ======================================================================
# E. Music
# ======================================================================
for mood in social_music.MOODS:
    track = social_music.compose(6, mood)
    if track.shape != (6 * social_music.RATE, 2) or np.isnan(track).any() or np.max(np.abs(track)) > 0.9:
        break
else:
    mood = None
check("music: each mood, as long as asked, without clipping", mood is None, mood)
track = social_music.compose(8, "inspiring")[:, 0]
segment = track[int(0.5 * social_music.RATE):int(60 / 92 * 4 * social_music.RATE)]
spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
frequencies = np.fft.rfftfreq(len(segment), 1 / social_music.RATE)
energy = {}
for frequency, amount in zip(frequencies, spectrum):
    if 60 < frequency < 2000:
        energy[int(round(69 + 12 * np.log2(frequency / 440))) % 12] = energy.get(int(round(69 + 12 * np.log2(frequency / 440))) % 12, 0) + amount ** 2
check("music: the first bar plays C major (C, E, G)", set(sorted(energy, key=lambda key: -energy[key])[:3]) == {0, 4, 7})

# ======================================================================
# F. Videos
# ======================================================================
check("video: ffmpeg is there", social_video.available())
asset = social_video.make(db, kind="listen", fmt="post", lang="fr", look="night", mood="calm")
social_video.render(db, asset)
path = social_video.folder() / (asset.file or "none")
probe = subprocess.run([social_video.ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr if path.exists() else ""
check("video: made, with its file", asset.status == "ready" and path.exists() and asset.size > 50_000, (asset.status, asset.error))
check("video: H.264 picture and AAC sound, square", "h264" in probe and "aac" in probe and "1080x1080" in probe, probe[-400:])
check("video: as long as its scenes", 8 <= asset.seconds <= 25, asset.seconds)
silent = social_video.make(db, kind="program", fmt="story", lang="ar", mood="none")
social_video.render(db, silent)
probe = subprocess.run([social_video.ffmpeg(), "-i", str(social_video.folder() / silent.file)], capture_output=True, text=True).stderr if silent.file else ""
check("video: without music when asked, vertical, in Arabic", silent.status == "ready" and "aac" not in probe and "1080x1920" in probe, (silent.status, silent.error))
db.query(FeedbackAnswer).delete()
db.commit()
empty = social_video.make(db, kind="listen", fmt="post")
social_video.render(db, empty)
check("video: nothing to tell yet is said plainly", empty.status == "failed" and "We listen" in (empty.error or ""), empty.error)
music = social_video.save_music(open(os.devnull, "rb").read() or social_music.wav_bytes(social_music.compose(3)), "theme.wav")
own = social_video.make(db, kind="success", fmt="post", music_file=music)
social_video.render(db, own)
check("video: with the admin's own music", own.status == "ready", own.error)
try:
    social_video.save_music(b"x" * 10, "virus.exe")
    refused = False
except ValueError:
    refused = True
check("video: only music files are taken", refused)
served = admin.get(f"/social/video/{asset.id}?download=1")
check("video: served and downloaded", served.status_code == 200 and served.headers["content-type"] == "video/mp4" and len(served.content) == asset.size)
response = admin.post("/social/video", data={"kind": "success", "fmt": "post", "lang": "en", "look": "light", "mood": "upbeat"})
made = db.query(SocialAsset).order_by(SocialAsset.created_at.desc()).first()
db.refresh(made)
check("video: made from the page (in the background)", "being made" in where(response) and made.status == "ready", (where(response), made.status, made.error))
admin.post(f"/social/video/{made.id}/delete")
check("video: removed with its file", db.query(SocialAsset).filter(SocialAsset.id == made.id).first() is None
      and not (social_video.folder() / (made.file or "x")).exists())

# ======================================================================
# G. Engagement
# ======================================================================
rows = [("facebook", "video", "success", "2026-10-27T18:30", 120, 14, 9, "2.4k"), ("facebook", "video", "listen", "2026-10-28T19:00", 90, 20, 6, "1800"),
        ("instagram", "post", "testimonial", "2026-10-29T12:00", 45, 2, 0, "1500"), ("facebook", "post", "program", "2026-10-30T12:30", 30, 1, 2, "2000")]
for platform, kind, topic, when, reactions, comments, shares, views in rows:
    admin.post("/social/posts", data={"platform": platform, "kind": kind, "topic": topic, "published_at": when, "reactions": reactions,
                                      "comments": comments, "shares": shares, "views": views})
posts = db.query(SocialPost).all()
check("engagement: posts kept with their numbers (“2.4k” read as 2400)", len(posts) == 4 and any(post.views == 2400 for post in posts))
first = next(post for post in posts if post.views == 2400)
check("engagement: its rate", social_engagement.rate(first) == round(100 * (120 + 14 + 9) / 2400, 2))
view = social_engagement.overview(db)
check("engagement: compared by kind, the best first", view["by_kind"][0]["key"] == "video" and view["by_kind"][0]["average"] > view["by_kind"][-1]["average"])
check("engagement: advice counted from the numbers", any("engagement of" in item["text"] for item in view["advice"]), [item["text"] for item in view["advice"]])
check("engagement: a wrong number is refused", "not a number" in where(admin.post("/social/posts", data={"reactions": "lots"})))
check("engagement: a wrong link is refused", "http" in where(admin.post("/social/posts", data={"link": "javascript:alert(1)"})))
try:
    social_engagement.facebook_import(db)
    refused = False
except ValueError:
    refused = True
check("engagement: Facebook's numbers need the page connected", refused)
admin.post(f"/social/posts/{first.id}/delete")
check("engagement: a post removed", db.query(SocialPost).count() == 3)

# ======================================================================
# H. The page, who may open it, What's new
# ======================================================================
page = admin.get("/social")
check("page: opens with its four parts", page.status_code == 200 and all(f'id="{part}"' in page.text for part in ("create", "videos", "listen", "engagement")))
check("page: in the menu", 'href="/social"' in admin.get("/").text)
check("page: in Arabic and in the light look", admin.get("/social?lang=ar&look=light").status_code == 200)
auth.create_user(db, email="sam.social@example.com", name="Sam", password="sam-social-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.social@example.com").one()
sam.permissions = access.dump({"candidates": "view"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.social@example.com", "password": "sam-social-12"})
check("access: not given, not opened", staff.get("/social").status_code in (303, 403))
sam.permissions = access.dump({"social": "view"})
db.commit()
check("access: given to look, opened, nothing changed", staff.get("/social").status_code == 200
      and staff.post("/social/listen", data={"you_said": "Un essai de l'équipe"}).status_code in (303, 403) and db.query(FeedbackAnswer).count() == 0)
check("what's new: this release is told", any(release["title"].startswith("Social media") for release in whats_new.RELEASES[:8]))

db.close()
check.done()
