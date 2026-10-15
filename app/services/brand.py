"""The application's name and icon.

The icon is the profile picture of the Facebook page (9antra.tn), taken
through Facebook's own picture address, which needs no account. It is looked
at again from time to time, so a new picture on the page becomes the icon
here. The admin can also give a picture of their own, which is then kept.
"""
from __future__ import annotations

import io
import logging
import os
import re
import time
from pathlib import Path

import requests
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_NAME = "9antra"
PAGE = os.getenv("FACEBOOK_PAGE_NAME", "9antra.tn")
FOLDER = Path(os.getenv("BRAND_DIR", "app/static/brand"))
SIZES = {"logo.png": 512, "icon-192.png": 192, "icon-64.png": 64}
LOOK_AGAIN_AFTER = 24 * 3600        # seconds between two looks at the Facebook page
MAX_UPLOAD = 4 * 1024 * 1024

# Names the admin can pick from in Settings. "9antra" (قنطرة) means "bridge".
NAME_IDEAS = [
    ("9antra Flow", "follows the name of your platform, TheBridgeFlow"),
    ("9antra Campus", "everything of the training centre in one place"),
    ("9antra Pilot", "it tells you what to do next"),
    ("The Bridge Desk", "the team's desk, in the words of your Facebook page"),
    ("Jisr", "“bridge” in Arabic, short and easy to say"),
]


def _source_file() -> Path:
    return FOLDER / "source.txt"


def source() -> str:
    """Where the current icon comes from: "facebook:<picture>", "upload" or ""."""
    try:
        return _source_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def has_logo() -> bool:
    return (FOLDER / "logo.png").is_file()


def logo_url(name: str = "logo.png") -> str | None:
    """The icon's address, changing when the picture does so browsers reload it."""
    path = FOLDER / name
    if not path.is_file():
        return None
    return f"/static/brand/{name}?v={int(path.stat().st_mtime)}"


def _save(content: bytes, origin: str) -> None:
    picture = Image.open(io.BytesIO(content))
    picture.load()
    picture = picture.convert("RGBA")
    side = min(picture.size)
    left, top = (picture.width - side) // 2, (picture.height - side) // 2
    picture = picture.crop((left, top, left + side, top + side))
    FOLDER.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        picture.resize((size, size), Image.LANCZOS).save(FOLDER / name, "PNG")
    _source_file().write_text(origin, encoding="utf-8")


def _picture_id(url: str) -> str:
    """The part of a Facebook picture address that names the picture itself."""
    found = re.search(r"/(\d+_\d+_\d+)_n\.", url or "")
    return found.group(1) if found else (url or "").split("?")[0][-60:]


def refresh_from_facebook(force: bool = False) -> str:
    """Take the page's current profile picture as the icon.

    Returns "updated", "same", "kept" (the admin's own picture stays),
    "waiting" (looked at recently) or "offline".
    """
    current = source()
    if current == "upload" and not force:
        return "kept"
    marker = _source_file()
    if not force and has_logo() and marker.is_file() and time.time() - marker.stat().st_mtime < LOOK_AGAIN_AFTER:
        return "waiting"
    try:
        answer = requests.get(
            f"https://graph.facebook.com/{PAGE}/picture", timeout=20,
            params={"width": 720, "height": 720, "redirect": "false"},
        ).json().get("data") or {}
        if not answer.get("url") or answer.get("is_silhouette"):
            return "offline"
        origin = "facebook:" + _picture_id(answer["url"])
        if origin == current and has_logo() and not force:
            marker.touch()
            return "same"
        picture = requests.get(answer["url"], timeout=30)
        if picture.status_code != 200:
            return "offline"
        _save(picture.content, origin)
        return "updated"
    except (requests.RequestException, ValueError, OSError) as exc:
        logger.warning("The Facebook page's picture could not be taken: %s", exc)
        return "offline"


def save_upload(content: bytes) -> str | None:
    """Use the admin's own picture. Returns the problem, if any."""
    if not content:
        return "Choose a picture first."
    if len(content) > MAX_UPLOAD:
        return "This picture is too heavy (4 MB at most)."
    try:
        _save(content, "upload")
    except Exception:
        return "This file is not a picture the application can read. Use a PNG or JPG."
    return None


def clean_name(value) -> str:
    return " ".join(str(value or "").split())[:40]


def view(settings) -> dict:
    """What pages need: {"name", "logo", "icon", "source"}."""
    return {
        "name": clean_name(getattr(settings, "app_name", None)) or DEFAULT_NAME,
        "logo": logo_url("logo.png"),
        "icon": logo_url("icon-64.png"),
        "icon_big": logo_url("icon-192.png"),
        "source": source(),
    }
