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
        "chosen_kind": params.get("kind") if params.get("kind") in social_video.KINDS else "both", "video_styles": social_video.STYLES,
        "notice": params.get("notice"), "error": params.get("error"),
    })


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


