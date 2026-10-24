"""Every video the application knows, played inside it.

The programs' videos come from 9antra.tn (one per week of a course, on Google Drive or YouTube), the website's own
videos (testimonials, presentation), and the videos professors add to a class week (played from this computer).
A link from Google Drive, YouTube or Vimeo becomes the address of its player, so it opens in a window of the
application instead of another site. A Drive video plays only when it is shared with "anyone with the link".
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

_DRIVE = re.compile(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=\w+&)?id=)([A-Za-z0-9_-]{10,})")
_YOUTUBE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{6,})")
_VIMEO = re.compile(r"vimeo\.com/(?:video/)?(\d{5,})")
_FILE = re.compile(r"\.(mp4|webm|m4v|mov)(\?|$)", re.I)


def embed_url(url: str | None) -> str | None:
    """The address of the video's player, or None when the link is not a video the application can play."""
    url = (url or "").strip()
    if not url.lower().startswith(("https://", "http://")):
        return None
    found = _DRIVE.search(url)
    if found:
        return f"https://drive.google.com/file/d/{found.group(1)}/preview"
    found = _YOUTUBE.search(url)
    if found:
        return f"https://www.youtube-nocookie.com/embed/{found.group(1)}?rel=0"
    found = _VIMEO.search(url)
    if found:
        return f"https://player.vimeo.com/video/{found.group(1)}"
    if _FILE.search(url):
        return url
    return None


def kind_of(embed: str | None) -> str:
    """frame (a player page) or file (a video file the browser plays itself)."""
    return "file" if embed and _FILE.search(embed) and "drive.google" not in embed else "frame"


def thumbnail(url: str | None) -> str | None:
    url = url or ""
    found = _DRIVE.search(url)
    if found:
        return f"https://drive.google.com/thumbnail?id={found.group(1)}&sz=w480"
    found = _YOUTUBE.search(url)
    if found:
        return f"https://i.ytimg.com/vi/{found.group(1)}/hqdefault.jpg"
    return None


def _item(title: str, url: str, **more) -> dict | None:
    embed = embed_url(url)
    if not embed:
        return None
    return {"title": (title or "Video").strip()[:160], "url": url, "embed": embed, "kind": kind_of(embed),
            "thumbnail": more.pop("thumbnail", None) or thumbnail(url), **more}


def of_program(db: Session, program) -> list[dict]:
    """The program's videos, week by week (from the website's course content)."""
    from app.models.website import WebsiteItem

    found, seen = [], set()
    for item in (db.query(WebsiteItem).filter(WebsiteItem.kind == "formation", WebsiteItem.program_id == program.id,
                                              WebsiteItem.gone_at.is_(None)).all()):
        for week in item.details.get("weeks") or []:
            if not isinstance(week, dict):
                continue
            video = _item(week.get("video_title") or f"Semaine {week.get('week')}", week.get("video_url") or "",
                          week=week.get("week"), length=str(week.get("video_length") or "")[:12], free=bool(week.get("free")))
            if video and video["embed"] not in seen:
                seen.add(video["embed"])
                found.append(video)
    for level in program.levels:
        for week in level.weeks:
            for resource in week.resources:
                if (resource.resource_type or "") != "video":
                    continue
                video = _item(resource.title, resource.url or "", week=week.week_number, length="", free=False)
                if video and video["embed"] not in seen:
                    seen.add(video["embed"])
                    found.append(video)
    found.sort(key=lambda video: (int(video["week"]) if str(video.get("week") or "").isdigit() else 999))
    return found


def of_website(db: Session) -> list[dict]:
    """The website's own videos: testimonials and the presentation."""
    from app.models.website import WebsiteItem

    found = []
    for item in (db.query(WebsiteItem).filter(WebsiteItem.kind == "video", WebsiteItem.removed_at.is_(None))
                 .order_by(WebsiteItem.title).all()):
        details = item.details
        video = _item(item.title, details.get("embed") or item.url or "", thumbnail=details.get("thumbnail") or None,
                      category=item.category or "")
        if video:
            found.append(video)
    return found


def library(db: Session) -> dict:
    """Everything, for the Videos page: {"programs": [{"program", "videos"}], "website": [...], "count"}."""
    from app.models import Program

    programs = []
    for program in db.query(Program).order_by(Program.name).all():
        items = of_program(db, program)
        if items:
            programs.append({"program": program, "videos": items})
    website = of_website(db)
    return {"programs": programs, "website": website, "count": sum(len(group["videos"]) for group in programs) + len(website)}
