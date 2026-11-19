"""The Social media page: posts and stories made from the centre's figures and feedback, videos with music, “we
listen” (what was done about feedback), and how each post did (services/social_*.py)."""
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.social import FeedbackAnswer, SocialAsset
from app.services import (background, social_content, social_images, social_music, social_video)
from app.template_context import notification_context

router = APIRouter(prefix="/social", tags=["Social media"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _back(anchor: str = "", notice: str | None = None, error: str | None = None, **params) -> RedirectResponse:
    query = urlencode({key: value for key, value in {**params, "notice": notice, "error": error}.items() if value})
    return RedirectResponse(f"/social{'?' + query if query else ''}{anchor}", status_code=303)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def social_page(request: Request, db: Session = Depends(get_db)):
    params = request.query_params
    lang = params.get("lang") if params.get("lang") in social_content.LANGUAGES else social_content.DEFAULT_LANGUAGE
    look = params.get("look") if params.get("look") in social_images.LOOKS else social_images.DEFAULT_LOOK
    from app.services import marketing
    videos = db.query(SocialAsset).filter(SocialAsset.kind == "video").order_by(SocialAsset.created_at.desc()).limit(12).all()
    music = sorted((path.name for path in social_video.folder().glob("music-*")), reverse=True)[:6]
    return templates.TemplateResponse(request=request, name="social.html", context={
        "active_page": "social", "lang": lang, "look": look, "ideas": social_content.ideas(db, lang),
        "topics": social_content.TOPICS, "looks": social_images.LOOKS, "languages": {"tn": "Tounsi", "fr": "Français", "en": "English", "ar": "العربية"},
        "videos": [{"asset": asset, "spec": json.loads(asset.spec or "{}")} for asset in videos],
        "working": any(asset.status == "working" for asset in videos), "video_kinds": social_video.KINDS, "formats": social_video.FORMATS,
        "moods": social_music.MOODS, "music_files": music, "video_ready": social_video.available(),
        "answers": db.query(FeedbackAnswer).filter(FeedbackAnswer.active.is_(True)).order_by(FeedbackAnswer.updated_at.desc()).all(),
        "proposals": social_content.listen_proposals(db),
        "plan": marketing.week_plan(db, lang), "tips": marketing.tips(db), "trends": marketing.trends(db), "times": marketing.best_times(db),
        "trending_moods": social_music.TRENDING, "trend_looks": social_images.TREND_LOOKS,
        **_faq_context(db, params),
        "chosen_kind": params.get("kind") if params.get("kind") in social_video.KINDS else "both", "video_styles": social_video.STYLES,
        "notice": params.get("notice"), "error": params.get("error"),
    })


def _faq_context(db: Session, params) -> dict:
    """The FAQ posts tab: the season (by the date, or chosen), its topic, the questions and the caption. Without a choice,
    the general questions asked most; an event's questions (the Summer Camp) come with that event's own season."""
    from app.services import faq, faq_posts

    faq.ensure_seed(db)
    chosen_ids = [value for value in (params.get("items") or "").split(",") if value]
    items = _faq_items(db, chosen_ids)
    themes = {item["theme"] for item in items if item["theme"]}
    season = params.get("season") if params.get("season") in faq_posts.THEMES else (
        faq_posts.FOR_EVENT.get(next(iter(themes))) if len(themes) == 1 and all(item["theme"] for item in items) else None) or faq_posts.season_of()
    topic = params.get("topic") or (faq.THEMES[next(iter(themes))]["name"].upper() if len(themes) == 1 and all(item["theme"] for item in items)
                                     else faq_posts.THEMES[season]["topic"])
    return {"faq_season": season, "faq_seasons": {key: value["name"] for key, value in faq_posts.THEMES.items()},
            "faq_topic": topic, "faq_items": items,
            "faq_all": [row for row in faq.listing(db) if row["answer"] or row["item"].post_answer],
            "faq_caption": faq_posts.caption(items, season, faq_posts._phone(db), topic), "faq_handle": params.get("handle") or faq_posts.HANDLE}


def _faq_items(db: Session, ids: list[str]) -> list[dict]:
    """The questions of the carousel: those chosen (in the order given); else the general ones asked most (at least three),
    else an event's questions in their post's order."""
    from app.services import faq

    rows = [row for row in faq.listing(db) if row["answer"] or row["item"].post_answer]
    by_id = {row["item"].id: row for row in rows}
    chosen = [by_id[item_id] for item_id in ids if item_id in by_id]
    if not chosen:
        general = [row for row in rows if not row["item"].theme]
        if len(general) >= 3:
            chosen = general[:7]
        else:
            events = sorted((row for row in rows if row["item"].theme), key=lambda row: (row["item"].theme, row["item"].position or 99))
            first = events[0]["item"].theme if events else None
            chosen = [row for row in events if row["item"].theme == first][:7] or rows[:7]
    return [{"id": row["item"].id, "question": row["item"].post_question or row["item"].question, "theme": row["item"].theme,
             "answer": row["item"].post_answer or row["answer"]} for row in chosen]


@router.get("/faq/slide.png")
def faq_slide(item: str, theme: str = "", topic: str = "", db: Session = Depends(get_db)):
    from app.services import faq_posts

    found = _faq_items(db, [item])
    if not found or found[0]["id"] != item:
        raise HTTPException(status_code=404, detail="Unknown question")
    key = theme if theme in faq_posts.THEMES else faq_posts.season_of()
    return Response(faq_posts.slide(found[0]["question"], found[0]["answer"], key, topic or None, faq_posts._phone(db)), media_type="image/png")


@router.get("/faq/contact.png")
def faq_contact(theme: str = "", handle: str = "", db: Session = Depends(get_db)):
    from app.services import faq_posts

    key = theme if theme in faq_posts.THEMES else faq_posts.season_of()
    return Response(faq_posts.closing(key, faq_posts._phone(db), handle.strip()[:60] or faq_posts.HANDLE), media_type="image/png")


@router.get("/faq/carousel.zip")
def faq_carousel(theme: str = "", topic: str = "", handle: str = "", items: str = "", db: Session = Depends(get_db)):
    """Every slide, the contact slide and the caption, ready to post as a carousel."""
    from app.services import faq_posts

    key = theme if theme in faq_posts.THEMES else faq_posts.season_of()
    chosen = _faq_items(db, [value for value in items.split(",") if value])
    data = faq_posts.carousel_zip(chosen, key, faq_posts._phone(db), handle.strip()[:60] or faq_posts.HANDLE, topic or None)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="faq-{key}.zip"'})


@router.get("/image")
def image(idea: str, fmt: str = "post", look: str = social_images.DEFAULT_LOOK, lang: str = "tn", download: str = "", db: Session = Depends(get_db)):
    """The picture of an idea, drawn now (a post 1:1 or a story 9:16)."""
    found = social_content.idea(db, idea, lang)
    if found is None:
        raise HTTPException(status_code=404, detail="Nothing to show for this idea any more.")
    data = social_images.render_png(found, fmt, look)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="9antra-{found["topic"]}-{fmt}-{lang}.png"'
    return Response(content=data, media_type="image/png", headers=headers)


@router.post("/video")
async def make_video(request: Request, db: Session = Depends(get_db), kind: str = Form("both"), fmt: str = Form("story"),
                     lang: str = Form("tn"), look: str = Form(social_images.DEFAULT_LOOK), mood: str = Form("trend"), music_file: str = Form(""),
                     style: str = Form("hook"),
                     music: UploadFile | None = File(None)):
    """A video, made in the background (about a minute); the page shows it when it is ready."""
    if not social_video.available():
        return _back("#videos", error="Videos need ffmpeg: install the imageio-ffmpeg package.")
    chosen = None
    if music is not None and music.filename:
        try:
            chosen = social_video.save_music(await music.read(), music.filename)
        except ValueError as problem:
            return _back("#videos", error=str(problem))
    elif mood == "own" and music_file:
        chosen = music_file if (social_video.folder() / music_file).exists() and music_file.startswith("music-") else None
        if chosen is None:
            return _back("#videos", error="Choose your music file again.")
    asset = social_video.make(db, kind=kind, fmt=fmt, lang=lang, look=look, mood="trend" if mood == "own" else mood, music_file=chosen, style=style)
    background.run_in_background(social_video.render_task, asset.id)
    return _back("#videos", notice="The video is being made: about a minute. It appears here by itself when it is ready.")


@router.get("/video/{asset_id}")
def video_file(asset_id: str, download: str = "", db: Session = Depends(get_db)):
    asset = db.get(SocialAsset, asset_id)
    if asset is None or asset.status != "ready" or not asset.file:
        raise HTTPException(status_code=404, detail="This video is not ready.")
    path = social_video.folder() / asset.file
    if not path.exists():
        raise HTTPException(status_code=404, detail="The video's file is no longer there.")
    return FileResponse(path, media_type="video/mp4", filename=f"9antra-{asset.id[:8]}.mp4" if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.get("/videos/state")
def videos_state(db: Session = Depends(get_db)):
    """Whether a video is still being made (the page waits for it)."""
    return {"working": db.query(SocialAsset).filter(SocialAsset.kind == "video", SocialAsset.status == "working").count()}


@router.post("/video/{asset_id}/delete")
def delete_video(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(SocialAsset, asset_id)
    if asset is not None:
        if asset.file:
            path = social_video.folder() / asset.file
            if path.exists():
                path.unlink()
        db.delete(asset)
        db.commit()
    return _back("#videos", notice="The video was removed.")


@router.post("/listen")
def save_listen(you_said: str = Form(""), we_did: str = Form(""), theme: str = Form(""), answer_id: str = Form(""),
                db: Session = Depends(get_db)):
    try:
        social_content.save_answer(db, you_said=you_said, we_did=we_did, theme=theme or None, answer_id=answer_id or None)
    except ValueError as problem:
        db.rollback()
        return _back("#listen", error=str(problem))
    db.commit()
    return _back("#listen", notice="Kept. It is in the ideas to post and in the “we listen” videos." if we_did.strip() else
                 "Kept. Add what you did about it, and it becomes a post and a scene of the videos.")


@router.post("/listen/{answer_id}/delete")
def delete_listen(answer_id: str, db: Session = Depends(get_db)):
    row = db.get(FeedbackAnswer, answer_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return _back("#listen", notice="Removed.")


