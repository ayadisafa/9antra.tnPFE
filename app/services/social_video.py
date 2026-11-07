"""Motivating videos for social media, made on this PC from the centre's own records: their success (the figures, what
former candidates said in public), “you said, we did”, the programs that start soon, and how to join, with music.

Each scene is a picture of social_images whose pieces come in one after the other (they slide up and appear, the
figures count up), with story-like bars at the top; the scenes follow each other with a short fade. Frames are drawn
with Pillow and given to ffmpeg (from the imageio-ffmpeg package), which writes an MP4 (H.264 + AAC) that Facebook,
Instagram, TikTok and LinkedIn accept. The music is composed here (social_music) or is the admin's own file.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy.orm import Session

from app.models.social import SocialAsset
from app.services import social_content, social_images, social_music, social_tn

logger = logging.getLogger(__name__)
FOLDER = Path(os.getenv("SOCIAL_DIR") or "data/social")
FPS = 30
KINDS = {
    "success": ("Their success", "The figures, what former candidates said, and how to join."),
    "listen": ("We listen", "What people said, what the centre did about it, and how to join."),
    "both": ("Success and we listen", "The figures, two testimonials, “you said, we did”, and how to join."),
    "program": ("The next sessions", "The programs that start soon, and how to join."),
}
FORMATS = {"story": "Vertical 9:16 (stories, reels, TikTok)", "post": "Square 1:1 (feed)"}
# How it moves. "hook": what sticks in the mind on a feed: a punch line in the first second, short scenes cut fast,
# a zoom punch and a flash at each cut, and an end that comes back to the first image, so it loops without a seam.
STYLES = {"calm": ("Calm", "Scenes that come in softly, one after the other."),
          "hook": ("Hook", "A punch line in the first second, fast cuts, zoom punches, and an end that loops back to the start.")}
HOOKS = {"fr": "Attends… ça peut changer ta vie.", "en": "Wait… this could change your life.",
         "ar": "انتظر… هذا قد يغيّر حياتك.", "tn": "Stanna… hedha ynajjem ybaddel 7yetek."}
_HOOK_SECONDS, _HOOK_END, _HOOK_PACE = 1.3, 0.9, 0.72
_SECONDS = {"intro": 2.6, "success": 4.6, "testimonial": 5.2, "listen": 5.6, "program": 4.4, "trend": 4.2, "join": 4.0}
_INTRO = {
    "success": {"fr": ("ILS RÉUSSISSENT AVEC NOUS", "Leur réussite, notre fierté"), "en": ("THEY SUCCEED WITH US", "Their success, our pride"),
                "ar": ("ينجحون معنا", "نجاحهم فخرنا")},
    "listen": {"fr": ("VOUS PARLEZ, NOUS ÉCOUTONS", "Vos avis nous font grandir"), "en": ("YOU SPEAK, WE LISTEN", "Your feedback makes us better"),
               "ar": ("تتكلمون، نسمعكم", "آراؤكم تجعلنا أفضل")},
    "both": {"fr": ("ENSEMBLE, PLUS LOIN", "Votre réussite, votre voix"), "en": ("TOGETHER, FURTHER", "Your success, your voice"),
             "ar": ("معا نذهب أبعد", "نجاحكم وصوتكم")},
    "program": {"fr": ("BIENTÔT", "Les prochaines sessions"), "en": ("COMING SOON", "The next sessions"), "ar": ("قريبا", "الدورات القادمة")},
}
for _kind, _titles in social_tn.INTRO.items():          # Tunisian: kept apart, never translated
    _INTRO[_kind]["tn"] = _titles


def folder() -> Path:
    FOLDER.mkdir(parents=True, exist_ok=True)
    return FOLDER


def ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def available() -> bool:
    return ffmpeg() is not None


def scenes(db: Session, kind: str, lang: str, style: str = "calm") -> list[dict]:
    """The ideas a video is made of, in order (an intro first, how to join last; with the hook style, the punch line
    first and again at the very end, so the video loops)."""
    every = social_content.ideas(db, lang)
    by_topic: dict[str, list[dict]] = {}
    for item in every:
        by_topic.setdefault(item["topic"], []).append(item)
    join = by_topic.get("join", [])[:1]
    base = dict(every[0]) if every else {}
    kicker, title = _INTRO.get(kind, _INTRO["success"]).get(lang, _INTRO["success"]["fr"])
    intro = {**base, "key": "intro", "topic": "intro", "kicker": kicker, "headline": title}
    picked: list[dict] = []
    if kind == "success":
        picked = by_topic.get("success", [])[:1] + by_topic.get("testimonial", [])[:2]
    elif kind == "listen":
        picked = by_topic.get("listen", [])[:3]
    elif kind == "both":
        picked = by_topic.get("success", [])[:1] + by_topic.get("testimonial", [])[:2] + by_topic.get("listen", [])[:2]
    elif kind == "program":
        picked = by_topic.get("program", [])[:3] or by_topic.get("trend", [])[:2]
    if not picked:
        return []
    if style == "hook":
        hook = {**base, "key": "hook", "topic": "intro", "kicker": "", "headline": HOOKS.get(lang, HOOKS["fr"])}
        return [hook] + picked + join + [{**hook, "key": "hook-end"}]
    return [intro] + picked + join


def _faded(image: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return image
    faded = image.copy()
    faded.putalpha(faded.getchannel("A").point(lambda value: int(value * alpha)))
    return faded


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1 - (1 - value) ** 3


def _bars(canvas: Image.Image, index: int, count: int, share: float) -> None:
    """Story-like bars at the top: one per scene, the current one filling up."""
    draw = ImageDraw.Draw(canvas)
    width = canvas.width - 80
    gap = 10
    each = (width - gap * (count - 1)) / count
    for number in range(count):
        x = 40 + number * (each + gap)
        draw.rounded_rectangle((x, 26, x + each, 32), radius=3, fill=(255, 255, 255, 90))
        filled = 1.0 if number < index else share if number == index else 0.0
        if filled > 0:
            draw.rounded_rectangle((x, 26, x + each * filled, 32), radius=3, fill=(255, 255, 255, 235))


def _punch(canvas: Image.Image, t: float) -> Image.Image:
    """The zoom punch of a cut: the picture starts a little closer and settles in a quarter of a second."""
    zoom = 1 + 0.08 * (1 - _ease(t / 0.25))
    if zoom <= 1.001:
        return canvas
    width, height = canvas.size
    bigger = canvas.resize((int(width * zoom), int(height * zoom)), Image.BILINEAR)
    left, top = (bigger.width - width) // 2, (bigger.height - height) // 2
    return bigger.crop((left, top, left + width, top + height))


def frames(pictures: list, durations: list[float], style: str = "calm"):
    """Every frame of the video, as RGB bytes."""
    count = len(pictures)
    hook = style == "hook"
    for index, (shot, seconds) in enumerate(zip(pictures, durations)):
        total = int(seconds * FPS)
        background = shot.background
        for frame in range(total):
            t = frame / FPS
            canvas = background.copy()
            # The last moment: everything fades out (with the hook style, a hard cut instead, except at the very end).
            leaving = 0.0 if hook else max(0.0, (t - (seconds - 0.35)) / 0.35)
            for piece in shot.pieces:
                if hook:
                    start = 0.04 + (0.2 if piece.step >= 9 else piece.step * 0.2)
                    arrive = _ease((t - start) / 0.28)
                else:
                    start = 0.15 + (0.35 if piece.step >= 9 else piece.step * 0.42)
                    arrive = _ease((t - start) / 0.55)
                if arrive <= 0:
                    continue
                alpha = arrive * (1 - leaving)
                image = piece.image
                if piece.number:
                    counted = _ease((t - start) / 1.2)
                    value = piece.number["value"]
                    shown = round(value * counted, 1) if isinstance(value, float) else int(round(value * counted))
                    image = social_images.number_image(shown, piece.number["suffix"], piece.number["size"], piece.number["colour"],
                                                       piece.number["width"])
                canvas.alpha_composite(_faded(image, alpha), (piece.x, piece.y + int((1 - arrive) * (90 if hook else 46))))
            if hook:
                canvas = _punch(canvas, t)
                if frame < 4 and index > 0:
                    # A flash at the cut: the eye catches the new scene.
                    flash = Image.new("RGBA", canvas.size, (255, 255, 255, (150, 95, 50, 20)[frame]))
                    canvas.alpha_composite(flash)
            else:
                _bars(canvas, index, count, t / seconds)
            yield canvas.convert("RGB").tobytes()


def make(db: Session, *, kind: str = "both", fmt: str = "story", lang: str = social_content.DEFAULT_LANGUAGE, look: str = social_images.DEFAULT_LOOK, mood: str = "trend",
         music_file: str | None = None, title: str | None = None, style: str = "calm") -> SocialAsset:
    """Writes the video down as waiting (its file comes from render()). The music is the trending one that fits the
    kind of video unless another is chosen ("none" for none)."""
    kind = kind if kind in KINDS else "both"
    if mood not in social_music.MOODS and mood != "none":
        mood = social_music.FOR_VIDEO.get(kind, social_music.TRENDING[0])
    spec = {"kind": kind, "fmt": fmt if fmt in FORMATS else "story", "lang": lang if lang in social_content.LANGUAGES else social_content.DEFAULT_LANGUAGE,
            "look": look if look in social_images.LOOKS else social_images.DEFAULT_LOOK, "mood": mood, "music_file": music_file,
            "style": style if style in STYLES else "calm"}
    asset = SocialAsset(kind="video", title=title or KINDS[spec["kind"]][0], status="working", spec=json.dumps(spec))
    db.add(asset)
    db.commit()
    return asset


def render(db: Session, asset: SocialAsset) -> SocialAsset:
    """Draws the video of an asset made by make(); its status says how it went."""
    spec = json.loads(asset.spec or "{}")
    started = time.monotonic()
    tool = ffmpeg()
    try:
        if tool is None:
            raise RuntimeError("ffmpeg is missing (install the imageio-ffmpeg package).")
        style = spec.get("style") or "calm"
        ideas = scenes(db, spec["kind"], spec["lang"], style)
        if not ideas:
            raise ValueError("Nothing to show yet for this video: " + {"listen": "write what you did about a feedback first (We listen).",
                                                                        "program": "no program has a class starting soon."}.get(spec["kind"], "no figure or testimonial yet."))
        pictures = [social_images.picture(item, spec["fmt"], spec["look"]) for item in ideas]
        durations = [_SECONDS.get(item["topic"], 4.0) for item in ideas]
        if style == "hook":
            durations = [_HOOK_SECONDS if item["key"] == "hook" else _HOOK_END if item["key"] == "hook-end" else max(2.4, seconds * _HOOK_PACE)
                         for item, seconds in zip(ideas, durations)]
        seconds = sum(durations)
        width, height = social_images.SIZES[spec["fmt"]]
        name = f"video-{asset.id[:8]}-{datetime.utcnow():%Y%m%d%H%M%S}.mp4"
        target = folder() / name
        with tempfile.TemporaryDirectory() as work:
            command = [tool, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(FPS), "-i", "-"]
            audio = None
            if spec.get("music_file"):
                audio = str(folder() / spec["music_file"])
                if not os.path.exists(audio):
                    raise ValueError("The music file is no longer there: add it again.")
            elif spec.get("mood") != "none":
                audio = os.path.join(work, "music.wav")
                social_music.write(audio, seconds, spec["mood"])
            if audio:
                command += ["-i", audio, "-map", "0:v", "-map", "1:a", "-af", f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, seconds - 2):.2f}:d=2",
                            "-c:a", "aac", "-b:a", "160k"]
            command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-t", f"{seconds:.2f}",
                        "-movflags", "+faststart", str(target)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                for frame in frames(pictures, durations, style):
                    process.stdin.write(frame)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            problem = process.stderr.read().decode("utf-8", "replace")
            if process.wait() != 0 or not target.exists():
                raise RuntimeError("ffmpeg could not write the video: " + problem[-300:])
        asset.file, asset.size, asset.seconds, asset.status, asset.error = name, target.stat().st_size, int(round(seconds)), "ready", None
        logger.info("Social video %s made in %.0f s (%.0f s long).", asset.id, time.monotonic() - started, seconds)
    except Exception as error:
        asset.status, asset.error = "failed", str(error)[:500]
        logger.warning("The social video %s could not be made: %s", asset.id, error)
    db.commit()
    return asset


def render_task(asset_id: str) -> None:
    """Background entry point (own database session)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        asset = db.get(SocialAsset, asset_id)
        if asset is not None and asset.status == "working":
            render(db, asset)
    finally:
        db.close()


def save_music(data: bytes, filename: str) -> str:
    """Keeps an admin's own music file; returns its name in the folder."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in (".mp3", ".wav", ".m4a", ".aac", ".ogg"):
        raise ValueError("Music must be an MP3, WAV, M4A, AAC or OGG file.")
    if not data or len(data) > 25 * 1024 * 1024:
        raise ValueError("The music file must be under 25 MB.")
    name = f"music-{datetime.utcnow():%Y%m%d%H%M%S}{suffix}"
    (folder() / name).write_bytes(data)
    return name
