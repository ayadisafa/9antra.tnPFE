"""Marketing: what is trendy in posts, the best times to post, this week's plan, and advice.

Counted, never guessed: the subjects the journal's marketing articles talk about (the last 45 days), what works on the
centre's own posts (services/social_engagement.py), and when its people are active (the hours they write, answer their
questions and register). What designers and the networks do this year is said as general practice (`DESIGN`), never
as a count. Yasmine's marketing agent answers from here (`answer`); nothing is posted by itself.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.schedule_utils import local_now, utc_to_local
from app.services.ui_words import t as _t

WINDOW_DAYS = 45
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BANDS = [(6, 9), (9, 12), (12, 15), (15, 18), (18, 21), (21, 24)]
# When people in Tunisia look at their phones, as a general starting point (not counted): the evening most, then lunch.
_BASE = {(6, 9): 0.2, (9, 12): 0.35, (12, 15): 0.6, (15, 18): 0.45, (18, 21): 1.0, (21, 24): 0.8}
# What the marketing articles talk about: label, words, what it means for the centre, where to act.
SUBJECTS = {
    "ai": ("AI in marketing", r"\b(ai|artificial intelligence|chatgpt|gemini|claude|llm|llms|genai|agentic)\b",
           "Everyone talks about AI: show what your candidates build with it, and the programs that teach it.", "/social#create"),
    "answers": ("Being found in AI answers", r"\b(aeo|geo|answer engines?|ai visibility|llm visibility|ai overviews?|ai search|brand radar)\b",
                "People now ask ChatGPT or Gemini where to train: keep 9antra.tn's program pages clear, with prices, dates and answers.", "/website"),
    "search": ("Search", r"\b(seo|search|google)\b", "Search still brings people: each program's page on 9antra.tn says what it teaches in plain words.", "/website"),
    "video": ("Short videos", r"\b(videos?|reels?|shorts|short-form|youtube)\b",
              "Short vertical videos keep growing: one video a week, 9:16, with the words on screen.", "/social#videos"),
    "linkedin": ("LinkedIn", r"\blinkedin\b", "LinkedIn reaches people looking for work: post the success figures and the skills each program gives.", "/social#create"),
    "instagram": ("Instagram", r"\b(instagram|stories)\b", "Instagram lives on stories: post the program that starts soon as a story.", "/social#create"),
    "tiktok": ("TikTok", r"\btiktok\b", "TikTok rewards short, real videos: a candidate's result in 15 seconds.", "/social#videos"),
    "threads": ("Threads", r"\bthreads\b", "Threads is a place for short news: one line when a session starts.", "/social#create"),
    "email": ("Email and newsletters", r"\b(e-?mails?|newsletters?)\b", "Email still works: one letter a month to former candidates with the next sessions.", "/inbox/compose"),
    "community": ("Community and creators", r"\b(community|creators?|influencers?|ugc|user-generated)\b",
                  "Real people convince: ask two former candidates for a short video saying what they do now.", "/social#listen"),
    "automation": ("Automation", r"\b(automation|automated|workflows?)\b", "Marketing runs by itself more and more: your reminders and posts can be planned.", "/reminders"),
}
# What posts look like this year: general practice, said as such (icon, title, text, what it asks here).
DESIGN = [
    ("bi-type-bold", "Big, bold type", "Few words, very large, in strong contrast: read in one second while scrolling. The “Bold” look does it.", "/social?look=bold#create"),
    ("bi-phone", "Vertical first", "Stories and reels fill the screen (9:16); the square post comes second.", "/social#videos"),
    ("bi-lightning-charge", "The result in the first two seconds", "A video's first scene says the result (“93% happy”), not the logo.", "/social#videos"),
    ("bi-badge-cc", "Words on screen", "Most people watch without sound: every video made here has its words on screen.", "/social#videos"),
    ("bi-people", "Real faces, real results", "Testimonials and “you said, we did” beat stock pictures.", "/social#listen"),
    ("bi-music-note-beamed", "The sound of the moment", "The videos here use this year's styles (dance-pop, lo-fi, cinematic). On TikTok and Instagram you can also add "
     "the week's trending sound in the app when you post: it is licensed there, not in a file.", "/social#videos"),
]


def _naive(moment):
    return moment.replace(tzinfo=None) if moment is not None and getattr(moment, "tzinfo", None) else moment


def subjects(db: Session, now: datetime | None = None) -> dict:
    """What the marketing articles of the last weeks talk about: [{"key", "label", "articles", "share", "means", "href"}]."""
    from app.models.news_item import NewsItem

    now = now or datetime.utcnow()
    rows = db.query(NewsItem).filter(NewsItem.domain == "marketing", NewsItem.published_at >= now - timedelta(days=WINDOW_DAYS)).all()
    counts: Counter = Counter()
    for row in rows:
        text = f"{row.title or ''} {row.summary or ''}".lower()
        for key, (_, words, _, _) in SUBJECTS.items():
            if re.search(words, text):
                counts[key] += 1
    found = [{"key": key, "label": _t(SUBJECTS[key][0]), "articles": count, "share": round(100 * count / len(rows)) if rows else 0,
              "means": _t(SUBJECTS[key][2]), "href": SUBJECTS[key][3]} for key, count in counts.most_common() if count >= 2]
    return {"articles": len(rows), "subjects": found}


def own_best(db: Session) -> list[dict]:
    """What works best on the centre's own posts (only once there are posts enough to compare)."""
    from app.services import social_engagement

    view = social_engagement.overview(db)
    found = []
    for key, label in (("by_kind", "Kind"), ("by_topic", "Subject"), ("by_platform", "Network")):
        rows = [row for row in view[key] if row["count"] >= social_engagement.ENOUGH]
        if len(rows) >= 2:
            found.append({"what": _t(label), "best": _t(rows[0]["label"]), "average": rows[0]["average"], "other": _t(rows[-1]["label"]),
                          "other_average": rows[-1]["average"]})
    return found


def trends(db: Session) -> dict:
    """Everything the “What's trending” part shows."""
    counted = subjects(db)
    return {**counted, "own": own_best(db), "design": [{"icon": icon, "title": _t(title), "text": _t(text), "href": href}
                                                      for icon, title, text, href in DESIGN]}


# ------------------------------------------------------------------
# When to post
# ------------------------------------------------------------------

def best_times(db: Session, now: datetime | None = None) -> dict:
    """A week of 3-hour bands, each scored 0–100 from when the centre's people are active (the hours they write, answer
    their weekly questions and register, last 180 days), how its own posts did at that time, and a general starting
    point; the three best moments, with why."""
    from app.models.candidate_email import CandidateEmail
    from app.models.quiz import Quiz
    from app.models.registration import Registration
    from app.models.social import SocialPost
    from app.services import social_engagement

    now = now or datetime.utcnow()
    since = now - timedelta(days=180)
    active: Counter = Counter()

    def band_of(hour: int):
        return next((band for band in BANDS if band[0] <= hour < band[1]), None)

    moments = db.query(CandidateEmail.candidate_id, CandidateEmail.received_at).filter(CandidateEmail.direction == "inbound",
                                                                                        CandidateEmail.received_at >= since).all()
    moments += db.query(Quiz.candidate_id, Quiz.completed_at).filter(Quiz.completed_at.isnot(None), Quiz.completed_at >= since).all()
    moments += db.query(Registration.candidate_id, Registration.registration_date).filter(Registration.registration_date >= since).all()
    seen = set()
    for person, moment in moments:
        local = utc_to_local(_naive(moment))
        band = band_of(local.hour) if local is not None else None
        # One person counts once per day and band: a batch of registrations brought in together is not a crowd.
        if band is not None and (person, local.date(), band) not in seen:
            seen.add((person, local.date(), band))
            active[(local.weekday(), band)] += 1
    rated: dict[tuple, list[float]] = {}
    for post in db.query(SocialPost).filter(SocialPost.published_at.isnot(None)).all():
        rate = social_engagement.rate(post)
        band = band_of(post.published_at.hour)
        if rate is not None and band is not None and not (post.published_at.hour == 0 and post.published_at.minute == 0):
            rated.setdefault((post.published_at.weekday(), band), []).append(rate)
    most_active = max(active.values(), default=0)
    best_rate = max((sum(values) / len(values) for values in rated.values()), default=0)
    total_active = sum(active.values())
    grid = []
    for day in range(7):
        cells = []
        for band in BANDS:
            base = _BASE[band] + (0.1 if day >= 5 and band[0] >= 9 else 0)
            act = active.get((day, band), 0) / most_active if most_active else None
            rates = rated.get((day, band))
            eng = (sum(rates) / len(rates)) / best_rate if rates and best_rate else None
            if act is not None and eng is not None:
                score = 0.55 * act + 0.3 * eng + 0.15 * base
            elif act is not None:
                score = 0.75 * act + 0.25 * base
            else:
                score = base
            cells.append({"band": band, "score": round(100 * min(score, 1)), "active": active.get((day, band), 0),
                          "rate": round(sum(rates) / len(rates), 2) if rates else None})
        grid.append({"day": _t(DAYS[day]), "cells": cells})
    order = sorted(((cell["score"], day, cell) for day, row in enumerate(grid) for cell in row["cells"]), key=lambda item: -item[0])
    best, days_taken = [], set()
    for score, day, cell in order:
        if day in days_taken:
            continue
        days_taken.add(day)
        why = []
        if cell["active"] and total_active:
            why.append(_t("{0}% of your people's messages, answers and registrations come then.", round(100 * cell["active"] / total_active)))
        if cell["rate"] is not None:
            why.append(_t("Your posts at that time got {0}% engagement.", cell["rate"]))
        if not why:
            why.append(_t("Evenings and lunch are when most people look at their phones (a general starting point)."))
        best.append({"day": _t(DAYS[day]), "weekday": day, "hour": cell["band"][0] + 1, "band": cell["band"], "score": score, "why": why})
        if len(best) == 3:
            break
    return {"grid": grid, "bands": [f"{start}–{end} h" for start, end in BANDS], "best": best, "counted": total_active,
            "posts": sum(len(values) for values in rated.values())}


# ------------------------------------------------------------------
# This week
# ------------------------------------------------------------------

_FORMATS = {"video": ("Reel or video (9:16)", "bi-camera-reels"), "story": ("Story (9:16)", "bi-phone"), "post": ("Post (1:1)", "bi-image")}


def week_plan(db: Session, lang: str = "fr", now: datetime | None = None) -> list[dict]:
    """Four posts for the next seven days, each at one of the best moments: what, which format, which look and music,
    why, and where to make it."""
    from app.services import social_content, social_images, social_music

    now = now or local_now()
    ideas = social_content.ideas(db, lang)
    by_topic: dict[str, list] = {}
    for idea in ideas:
        by_topic.setdefault(idea["topic"], []).append(idea)
    wanted = []        # (idea, format, why): the program that starts soonest first, then proof, then listening, then news
    if by_topic.get("program"):
        wanted.append((by_topic["program"][0], "story", _t("It starts soon: a countdown brings late registrations.")))
    if by_topic.get("success"):
        wanted.append((by_topic["success"][0], "video", _t("Figures that count up, with music: what short videos do best.")))
    if by_topic.get("testimonial"):
        wanted.append((by_topic["testimonial"][0], "post", _t("What a former candidate says convinces more than what you say.")))
    if by_topic.get("listen"):
        wanted.append((by_topic["listen"][0], "video", _t("“You said, we did” shows you listen.")))
    if by_topic.get("trend"):
        wanted.append((by_topic["trend"][0], "post", _t("What the news talks about, and that you teach it.")))
    if by_topic.get("join"):
        wanted.append((by_topic["join"][0], "story", _t("How to register, in one screen.")))
    times = best_times(db)
    slots = []
    for slot in times["best"] + [{"weekday": day, "hour": 19, "why": []} for day in (1, 3, 5, 0)]:
        moment = (now + timedelta(days=(slot["weekday"] - now.weekday()) % 7)).replace(hour=slot["hour"], minute=0, second=0, microsecond=0)
        if moment <= now:
            moment += timedelta(days=7)
        if all(abs((moment - taken).days) >= 1 for taken in slots):
            slots.append(moment)
    slots.sort()
    plan = []
    for (idea, form, why), moment in zip(wanted[:4], slots):
        look = social_images.DEFAULT_LOOK if form != "post" or idea["topic"] != "trend" else "bright"
        video_kind = {"success": "success", "listen": "listen", "program": "program"}.get(idea["topic"], "both")
        plan.append({
            "when": moment, "day": _t(DAYS[moment.weekday()]), "date": moment.strftime("%d/%m"), "time": moment.strftime("%H:%M"),
            "idea": idea, "title": _t(social_content.TOPICS[idea["topic"]][0]), "icon": social_content.TOPICS[idea["topic"]][1],
            "headline": idea.get("headline") or "", "format": form, "format_label": _t(_FORMATS[form][0]), "format_icon": _FORMATS[form][1],
            "look": look, "music": _t(social_music.MOODS[social_music.FOR_VIDEO.get(video_kind, "pop")]["name"]) if form == "video" else None,
            "why": why, "href": (f"/social?kind={video_kind}#videos" if form == "video" else f"/social?look={look}#idea-{idea['key'].replace(':', '-')}"),
        })
    return plan


def tips(db: Session) -> list[dict]:
    """Marketing advice counted from the records: {"icon", "text", "why", "href"}."""
    from app.models.social import FeedbackAnswer, SocialAsset, SocialPost
    from app.services import engagement_ideas, social_content, social_engagement

    found = []
    view = social_engagement.overview(db)
    for item in view["advice"][:2]:
        found.append({"icon": item["icon"], "text": item["text"], "why": _t("Counted from your posts' numbers."), "href": "/social#engagement", "shown": True})
    month = db.query(SocialPost).filter(SocialPost.published_at >= datetime.now() - timedelta(days=30)).count()
    if not view["count"]:                                          # with posts, the engagement advice says it
        found.append({"icon": "bi-calendar-week", "text": _t("Post two to three times a week: the plan below is ready."),
                      "why": _t("{0} post(s) written down in 30 days.", month), "href": "/social#plan"})
    for session in social_content.next_sessions(db, most=1):
        days = (session["starts"].date() - datetime.utcnow().date()).days
        if 0 < days <= 21:
            found.append({"icon": "bi-hourglass-split", "text": _t("A countdown for {0}: a story at 7 days, 3 days and the day before.", session["program"]),
                          "why": _t("It starts in {0} days.", days), "href": "/social#create"})
    if not db.query(SocialAsset).filter(SocialAsset.kind == "video", SocialAsset.status == "ready").count():
        found.append({"icon": "bi-camera-reels", "text": _t("Make your first video: short videos are what the networks show most."),
                      "why": _t("No video made yet."), "href": "/social#videos"})
    if not db.query(FeedbackAnswer).filter(FeedbackAnswer.active.is_(True)).count():
        found.append({"icon": "bi-ear", "text": _t("Say what you changed after a feedback: “you said, we did” builds trust."),
                      "why": _t("No answer published yet."), "href": "/social#listen"})
    for field in engagement_ideas.industries(db):
        if field.get("idea") and not field.get("taught"):
            found.append({"icon": "bi-mortarboard", "text": field["idea"], "why": _t("Counted from the journal's {0} articles.", field["articles"]),
                          "href": "/journal?domain=" + field["domain"]})
            break
    counted = subjects(db)
    if any(item["key"] == "answers" for item in counted["subjects"]):
        item = next(item for item in counted["subjects"] if item["key"] == "answers")
        found.append({"icon": "bi-stars", "text": item["means"], "why": _t("{0} marketing articles of the last weeks talk about it.", item["articles"]),
                      "href": "/website"})
    found.append({"icon": "bi-music-note-beamed", "text": _t("When you post a video on TikTok or Instagram, add the week's trending sound in the app."),
                  "why": _t("It is licensed there; a file cannot carry it."), "href": "/social#videos"})
    return found


def for_dashboard(db: Session) -> dict:
    plan = week_plan(db)
    times = best_times(db)
    return {"plan": plan, "best": times["best"], "tips": tips(db)[:4], "counted": times["counted"]}


# ------------------------------------------------------------------
# Yasmine's marketing agent
# ------------------------------------------------------------------

_WANTS = re.compile(r"\b(marketing|posts?|poster|publi(?:er|e|ez|cation|cations)|stor(?:y|ies)|reels?|instagram|facebook|tiktok|linkedin|r[ée]seaux sociaux|"
                    r"social media|engagement|hashtags?|caption|l[ée]gende|campagne|campaign|audience|followers|abonn[ée]s|"
                    r"best time|meilleur moment|quand publier|when to post|tendances? (?:des )?(?:posts?|publications?)|trending posts?|promo|publicit[ée])\b|"
                    r"منشور|تسويق|فيسبوك|انستغرام|تيك توك", re.I)
_WHEN = re.compile(r"\b(when|quand|heure|hour|time|timing|moment|jour|day)\b|متى|وقت|waqtech|wa9tech", re.I)
_TREND = re.compile(r"\b(trend\w*|tendance\w*|à la mode|what works|ce qui marche|design|style)\b|ترند|رائج", re.I)
_PLAN = re.compile(r"\b(plan|calendar|calendrier|this week|cette semaine|what (?:to|should i) post|quoi publier|que publier|id[ée]es?|ideas?)\b|ماذا أنشر|خطة", re.I)
_HOW = re.compile(r"\b(how did|how are|comment (?:ont|vont|marchent)|stats?|statisti\w+|r[ée]sultats?|results?|performance|engagement)\b|نتائج", re.I)
_MUSIC = re.compile(r"\b(music|musique|son|sound|audio)\b|موسيقى", re.I)
_TIPS = re.compile(r"\b(advice|conseils?|suggestions?|tips?|astuces?|improve|am[ée]liorer)\b|نصائح|اقتراح", re.I)


def wants(text: str) -> bool:
    return bool(_WANTS.search(text or ""))


def answer(db: Session, text: str, *, lang: str | None = None) -> dict:
    """The marketing agent's answer, in the language of the question."""
    from app.services import howto, ui_words

    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(text)
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        reply, links = _answer(db, text or "")
    finally:
        ui_words.current.reset(token)
    return {"reply": reply, "links": links, "source": "system", "sources": [{"label": _t("Social media"), "url": "/social"}], "resources": []}


def _answer(db: Session, text: str) -> tuple[str, list[dict]]:
    if _WHEN.search(text) and not _TREND.search(text):
        times = best_times(db)
        lines = [_t("The best moments to post:")]
        for slot in times["best"]:
            lines.append(f"• {slot['day']} {slot['band'][0]}–{slot['band'][1]} h: " + " ".join(slot["why"]))
        return "\n".join(lines), [{"label": "Best times", "url": "/social#timing"}]
    if _TREND.search(text):
        found = trends(db)
        lines = [_t("What the marketing articles of the last weeks talk about ({0} articles):", found["articles"])]
        lines += [f"• {item['label']} ({item['articles']}): {item['means']}" for item in found["subjects"][:4]]
        lines.append(_t("What posts look like this year (general practice):"))
        lines += [f"• {item['title']}: {item['text']}" for item in found["design"][:4]]
        for item in found["own"][:1]:
            lines.append(_t("On your own posts, “{0}” does best ({1}%).", item["best"], item["average"]))
        return "\n".join(lines), [{"label": "What's trending", "url": "/social#trends"}]
    if _HOW.search(text):
        from app.services import social_engagement

        view = social_engagement.overview(db)
        if not view["rated"]:
            return _t("No post has its numbers yet: add them on the Social media page (reactions, comments, shares, views)."), [
                {"label": "Engagement", "url": "/social#engagement"}]
        lines = [_t("{0} post(s) with numbers, {1}% engagement on average.", view["rated"], view["average"])]
        lines += [f"• {item['text']}" for item in view["advice"][:3]]
        return "\n".join(lines), [{"label": "Engagement", "url": "/social#engagement"}]
    if _MUSIC.search(text):
        from app.services import social_music

        names = ", ".join(_t(social_music.MOODS[key]["name"]) for key in social_music.TRENDING)
        return (_t("The videos use this year's music styles: {0}, chosen for each kind of video. On TikTok and Instagram, you can add the week's trending sound in the app.", names),
                [{"label": "Videos", "url": "/social#videos"}])
    if _PLAN.search(text):
        plan = week_plan(db)
        if not plan:
            return _t("Nothing to post yet: the ideas come from your programs, feedback and classes."), [{"label": "Social media", "url": "/social"}]
        lines = [_t("This week's plan:")]
        lines += [f"• {item['day']} {item['date']} {item['time']} · {item['format_label']} · {item['title']}" + (f" ({item['headline']})" if item["headline"] else "")
                  for item in plan]
        return "\n".join(lines), [{"label": "This week", "url": "/social#plan"}]
    lines = [_t("A few things to do for your marketing:")]
    lines += [f"• {item['text']} ({item['why']})" for item in tips(db)[:5]]
    return "\n".join(lines), [{"label": "Social media", "url": "/social#plan"}]
