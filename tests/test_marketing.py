"""The batch of 2 November: what is trending in posts, the new looks and this year's music, the best times to post,
this week's marketing plan (Social media page and dashboard), Yasmine's marketing and system agents (she changes the
system when asked, and undoes it), and every functionality explained with a schema whose steps link to their place.
Runs on a COPY; nothing is sent."""
import io
import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import testenv

testenv.prepare("marketing")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.database import SessionLocal
from app.models import Candidate, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.news_item import NewsItem
from app.services import (access, auth, email_templates, guides, marketing, orchestrator, reply_rules, social_content, social_images,
                          social_music, social_video, system_actions, whats_new)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="market-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "market-test-1"})


def ask(text):
    return orchestrator.dispatch_chat(db, text)


# ======================================================================
# A. This year's music
# ======================================================================
for mood in social_music.TRENDING:
    track = social_music.compose(8, mood)
    if track.shape != (8 * social_music.RATE, 2) or np.isnan(track).any() or np.max(np.abs(track)) > 0.9:
        break
else:
    mood = None
check("music: dance-pop, lo-fi and cinematic, as long as asked, without clipping", mood is None, mood)
lofi = social_music.compose(8, "lofi")[:, 0]
segment = lofi[int(0.3 * social_music.RATE):int(60 / 82 * 4 * social_music.RATE)]
spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
frequencies = np.fft.rfftfreq(len(segment), 1 / social_music.RATE)
energy = {}
for frequency, amount in zip(frequencies, spectrum):
    if 80 < frequency < 1500:
        note = int(round(69 + 12 * np.log2(frequency / 440))) % 12
        energy[note] = energy.get(note, 0) + amount ** 2
check("music: the lo-fi starts on F major seventh (F, A, C, E)", set(sorted(energy, key=lambda key: -energy[key])[:4]) == {5, 9, 0, 4},
      sorted(energy, key=lambda key: -energy[key])[:4])
cinematic = social_music.compose(12, "cinematic")[:, 0]
first, last = np.sqrt(np.mean(cinematic[: 3 * social_music.RATE] ** 2)), np.sqrt(np.mean(cinematic[7 * social_music.RATE: 10 * social_music.RATE] ** 2))
check("music: the cinematic one builds", last > first * 1.15, (round(float(first), 3), round(float(last), 3)))
asset = social_video.make(db, kind="success", fmt="post")
check("videos: the trending music that fits the video, by default", json.loads(asset.spec)["mood"] == "pop" and json.loads(asset.spec)["look"] == "bold")
check("videos: “we listen” gets lo-fi", json.loads(social_video.make(db, kind="listen").spec)["mood"] == "lofi")
check("videos: no music when asked", json.loads(social_video.make(db, kind="success", mood="none").spec)["mood"] == "none")

# ======================================================================
# B. Posts redesigned
# ======================================================================
ideas = social_content.ideas(db, "fr") + social_content.ideas(db, "ar")
sizes_ok = all(Image.open(io.BytesIO(social_images.render_png(item, fmt, look))).size == social_images.SIZES[fmt]
               for item in ideas[:4] + ideas[-2:] for fmt in ("post", "story") for look in social_images.TREND_LOOKS)
check("posts: Bold and Bright, post and story, French and Arabic", sizes_ok and social_images.DEFAULT_LOOK == "bold")
bold = Image.open(io.BytesIO(social_images.render_png(ideas[0], "post", "bold"))).convert("RGB")
check("posts: Bold is flat and dark where the text goes (no blur)", bold.getpixel((60, 600)) == (28, 11, 25), bold.getpixel((60, 600)))
picture = admin.get(f"/social/image?idea={ideas[0]['key']}")
check("posts: the picture is drawn in Bold when no look is asked", picture.status_code == 200
      and Image.open(io.BytesIO(picture.content)).convert("RGB").getpixel((60, 600)) == (28, 11, 25))

# ======================================================================
# C. Trends, the best times, this week
# ======================================================================
now = datetime.utcnow()
for index in range(6):
    db.add(NewsItem(domain="marketing", source="Test", title=f"How AI answer engines change SEO {index}", url=f"https://example.com/aeo-{index}",
                    summary="AEO and AI visibility: being cited by ChatGPT.", published_at=now - timedelta(days=3)))
for index in range(3):
    db.add(NewsItem(domain="marketing", source="Test", title=f"Short video wins on TikTok {index}", url=f"https://example.com/video-{index}",
                    summary="Reels and short-form video.", published_at=now - timedelta(days=5)))
db.add(NewsItem(domain="marketing", source="Test", title="Old news about LinkedIn", url="https://example.com/old", published_at=now - timedelta(days=90)))
db.commit()
found = marketing.subjects(db)
keys = [item["key"] for item in found["subjects"]]
check("trends: counted from the marketing articles of the last weeks", found["articles"] >= 9 and keys[:2] == ["ai", "answers"] or ("answers" in keys and "video" in keys),
      (found["articles"], keys))
check("trends: an old article does not count", "linkedin" not in keys)
check("trends: this year's design said as general practice", len(marketing.trends(db)["design"]) == 6)
# When people are active: five people write on Tuesdays around 20 h (local); one person registering ten times counts once.
people = []
for index in range(5):
    person = Candidate(full_name=f"Market Person {index}", email=f"market{index}@example.com")
    db.add(person)
    db.flush()
    people.append(person)
tuesday = local_now() - timedelta(days=(local_now().weekday() - 1) % 7 + 7)
moment = tuesday.replace(hour=20, minute=10) - timedelta(hours=1)          # stored in UTC (Tunis is UTC+1)
for week in range(3):
    for person in people:
        db.add(CandidateEmail(candidate_id=person.id, direction="inbound", sender=person.email, recipient="9antra", subject="?", body="Bonjour",
                              status="replied", channel="email", received_at=moment - timedelta(days=7 * week)))
db.commit()
times = marketing.best_times(db)
check("best times: the moment most of them write comes first", times["best"][0]["weekday"] == 1 and times["best"][0]["band"] == (18, 21),
      [(slot["weekday"], slot["band"], slot["score"]) for slot in times["best"]])
check("best times: said why, counted", "%" in times["best"][0]["why"][0])
check("best times: a week of squares", len(times["grid"]) == 7 and all(len(row["cells"]) == 6 for row in times["grid"]))
plan = marketing.week_plan(db)
check("this week: posts at the best moments, in order, each with where to make it", plan and all(plan[index]["when"] <= plan[index + 1]["when"] for index in range(len(plan) - 1))
      and all(item["href"].startswith("/social") for item in plan), [(item["day"], item["time"], item["format"]) for item in plan])
check("this week: a video gets this year's music", all(item["music"] for item in plan if item["format"] == "video"))
tips = marketing.tips(db)
check("advice: the trending sound is added in the networks' apps", any("trending sound" in item["text"] for item in tips))
check("advice: being found in AI answers, counted", any("ChatGPT" in item["text"] for item in tips))
page = admin.get("/social").text
check("social page: this week, trending, best times", all(f'id="{part}"' in page for part in ("plan", "trends", "timing"))
      and page.count('class="mk-cell') == 42 and page.count('class="mk-slot"') == len(plan))
check("social page: the new looks and music in the forms", 'value="trend" checked' in page and "Dance-pop" in page and "Bright" in page)
check("social page: a planned post lands on its card", any(f'id="{item["href"].split("#")[1]}"' in page for item in plan if "#idea-" in item["href"]) or not any("#idea-" in item["href"] for item in plan))
check("social page: a video kind can be chosen by the link", 'name="kind" value="success" checked' in admin.get("/social?kind=success").text)
home = admin.get("/").text
check("dashboard: marketing this week, with Yasmine", 'id="dashboard-marketing"' in home and home.count("data-ask-yasmine>") == 3 and "db-market-slot" in home)
check("dashboard: each idea says why, the plan and the target", ("db-idea-tag" in home) or 'class="db-idea"' not in home)

# ======================================================================
# D. Yasmine: marketing, and changing the system
# ======================================================================
answer = ask("quand publier sur instagram ?")
check("Yasmine: marketing questions go to the marketing agent, in French", answer["agent"]["key"] == "marketing" and "Les meilleurs moments" in answer["reply"], answer["reply"][:120])
answer = ask("what's trendy in posts?")
check("Yasmine: what is trending, in English", answer["agent"]["key"] == "marketing" and "marketing articles" in answer["reply"], answer["reply"][:120])
answer = ask("quoi publier cette semaine ?")
check("Yasmine: this week's plan", answer["agent"]["key"] == "marketing" and "plan" in answer["reply"].lower(), answer["reply"][:120])
answer = ask("mets le seuil de confiance à 85 %")
db.expire_all()
check("Yasmine changes a setting, and says what it was", answer["agent"]["key"] == "system" and get_settings(db).email_confidence_threshold == 0.85
      and "85" in answer["reply"], answer["reply"])
answer = ask("annule")
db.expire_all()
check("…and “undo” puts it back", get_settings(db).email_confidence_threshold != 0.85 and answer["agent"]["key"] == "system", answer["reply"])
ask("turn off the session reminder")
check("Yasmine switches an automatic reminder off", not email_templates.get(db, "session_reminder").sends_itself)
ask("turn on the session reminder")
check("…and on again", email_templates.get(db, "session_reminder").sends_itself)
ask("mets le menu à droite")
db.expire_all()
check("Yasmine moves the menu", get_settings(db).ui_menu_side == "right")
ask("mets le thème en mode sombre")
db.expire_all()
check("Yasmine changes the look (the value may be in the setting's own name)", get_settings(db).ui_mode == "night")
answer = ask("change le seuil à 30%")
check("Yasmine refuses what is out of bounds, and says why", "50" in answer["reply"] and "95" in answer["reply"], answer["reply"])
answer = ask("mets le lien du formulaire à abc")
check("Yasmine refuses a link that is not one", "http" in answer["reply"])
answer = ask("what can you change?")
check("Yasmine lists what she can change", "confidence" in answer["reply"] and "session" in answer["reply"])
check("never the mail account or passwords", not any(item.get("attr") in ("smtp_email", "smtp_host", "admin_email") for item in system_actions.SETTINGS))
reply_rules.talk(db, "“super” needs an answer")
answer = ask("annule")
check("“undo” undoes the latest change, wherever it was made (here, the inbox's rules)", "super" in reply_rules.rules(db)["thanks_words"], answer["reply"])
auth.create_user(db, email="sam.market@example.com", name="Sam", password="sam-market-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.market@example.com").one()
sam.permissions = access.dump({"inbox": "edit"})
db.commit()
before = get_settings(db).price_online
answer = orchestrator.dispatch_chat(db, "passe le prix en ligne à 999", user=sam)
db.expire_all()
check("a team member without the settings cannot change them", get_settings(db).price_online == before and "admin" in answer["reply"], answer["reply"])
check("the agents page shows the two new agents", "System agent" in admin.get("/assistant/agents").text and "Marketing agent" in admin.get("/assistant/agents").text)

# ======================================================================
# E. Every functionality, with its schema
# ======================================================================
missing = [url for key, label, icon, url, prefixes in access.SECTIONS if key != "assistant" and guides.for_path(url) is None]
check("every part of the menu explains itself", not missing, missing)
total = sum(len(group["pages"]) for group in guides.everything())
page = admin.get("/guide").text
check("“How it works”: every functionality, with its schema", page.count('class="fn-card"') == total >= 25 and 'id="every"' in page, (page.count('class="fn-card"'), total))
links = {step[3] for group in guides.everything() for item in group["pages"] for step in item["steps"] if len(step) > 3}
broken = [link for link in sorted(links) if admin.get(urlsplit(link).path + ("?" + urlsplit(link).query if urlsplit(link).query else "")).status_code != 200]
check("each step's link opens its page", not broken, broken)
social_intro = admin.get("/social").text
check("a page's explanation: its steps open where they are done", social_intro.count("guide-step has-link") == 4 and 'href="/guide#every"' in social_intro)
for lang in ("fr", "ar"):
    admin.cookies.set("lang", lang)
    check(f"pages in {lang}", all(admin.get(path).status_code == 200 for path in ("/", "/social", "/guide", "/reminders")))
admin.cookies.set("lang", "en")
check("what's new: this batch is told", any(release["key"] == "2026-11-02" for release in whats_new.RELEASES))

db.close()
check.done()
