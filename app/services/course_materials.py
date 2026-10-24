"""Course files of a class, week by week, and what the system reads in them.

The professor adds PDFs, slides, photos or videos to a week. The content is
kept on disk (CLASS_FILES_DIR, default: data/class_files) under a random
name. Text is read from PDF, Word, PowerPoint and text files; the system
cannot watch a video or look at a photo, so for those it learns from the
note the professor writes next to the file.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup
from app.models.classsession import ClassSession
from app.models.course_material import ClassMaterial, ClassWeek, WeekQuestion
from app.services.email_files import human_size, safe_name

logger = logging.getLogger(__name__)

MB = 1024 * 1024
MAX_DOCUMENT_BYTES = 40 * MB
MAX_PHOTO_BYTES = 15 * MB
MAX_MEDIA_BYTES = int(os.getenv("CLASS_VIDEO_MAX_MB", "500")) * MB
MAX_FILES_PER_WEEK = 20
MAX_LEARNED_CHARS = 200_000
CHUNK = MB

# extension -> (kind, content type)
FILE_TYPES = {
    ".pdf": ("document", "application/pdf"),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".txt": ("document", "text/plain"),
    ".md": ("document", "text/markdown"),
    ".csv": ("document", "text/csv"),
    ".doc": ("document", "application/msword"),
    ".ppt": ("document", "application/vnd.ms-powerpoint"),
    ".xlsx": ("document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".png": ("photo", "image/png"),
    ".jpg": ("photo", "image/jpeg"),
    ".jpeg": ("photo", "image/jpeg"),
    ".webp": ("photo", "image/webp"),
    ".gif": ("photo", "image/gif"),
    ".mp4": ("video", "video/mp4"),
    ".webm": ("video", "video/webm"),
    ".mov": ("video", "video/quicktime"),
    ".mkv": ("video", "video/x-matroska"),
    ".m4v": ("video", "video/x-m4v"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
    ".m4a": ("audio", "audio/mp4"),
    ".ogg": ("audio", "audio/ogg"),
}
READABLE = {".pdf", ".docx", ".pptx", ".txt", ".md", ".csv"}
ACCEPT = ",".join(sorted(FILE_TYPES))
# Shown in the browser as they are; everything else is offered as a download.
INLINE_KINDS = {"photo", "video", "audio"}

KIND_ICONS = {
    "document": "bi-file-earmark-text", "photo": "bi-image", "video": "bi-camera-video", "audio": "bi-mic",
}
_LIMITS = {"document": MAX_DOCUMENT_BYTES, "photo": MAX_PHOTO_BYTES, "video": MAX_MEDIA_BYTES, "audio": MAX_MEDIA_BYTES}
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE = re.compile(r"ppt/(slides|notesSlides)/(?:slide|notesSlide)(\d+)\.xml$")


class UploadRefused(ValueError):
    """The file was not kept; the message says why, for the person who added it."""


# ------------------------------------------------------------------
# Storage
# ------------------------------------------------------------------

def storage_dir() -> Path:
    folder = Path(os.getenv("CLASS_FILES_DIR") or "data/class_files")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def path_of(material: ClassMaterial) -> Path:
    return storage_dir() / material.stored_name


def url_of(material: ClassMaterial) -> str:
    return f"/classes/files/{material.id}"


# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------

def _tidy(text: str) -> str:
    """Readable text: no stray spaces, no long runs of blank lines."""
    lines = [" ".join(line.split()) for line in (text or "").replace("\x00", " ").splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_LEARNED_CHARS]


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    pages = []
    for page in PdfReader(str(path)).pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
        if sum(len(item) for item in pages) > MAX_LEARNED_CHARS:
            break
    return "\n\n".join(pages)


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{_WORD}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_WORD}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _read_pptx(path: Path) -> str:
    slides: dict[int, list[str]] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            match = _SLIDE.match(name)
            if not match:
                continue
            root = ElementTree.fromstring(archive.read(name))
            lines = []
            for paragraph in root.iter(f"{_DRAWING}p"):
                text = "".join(node.text or "" for node in paragraph.iter(f"{_DRAWING}t"))
                if text.strip() and not text.strip().isdigit():     # slide numbers
                    lines.append(text)
            # The speaker's notes of a slide follow its text.
            slides.setdefault(int(match.group(2)), []).extend(lines)
    return "\n\n".join("\n".join(lines) for _, lines in sorted(slides.items()) if lines)


def _read_text(path: Path) -> str:
    data = path.read_bytes()[: MAX_LEARNED_CHARS * 4]
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_READERS = {".pdf": _read_pdf, ".docx": _read_docx, ".pptx": _read_pptx, ".txt": _read_text, ".md": _read_text, ".csv": _read_text}


def read_file(path: Path, extension: str, kind: str) -> tuple[str, str]:
    """(text, status) for a stored file. Status: read | nothing | unreadable | notes_only."""
    if kind != "document":
        return "", "notes_only"
    reader = _READERS.get(extension)
    if reader is None:
        return "", "unreadable"
    try:
        text = _tidy(reader(path))
    except Exception as exc:
        logger.warning("The course file %s could not be read: %s", path.name, exc)
        return "", "unreadable"
    return (text, "read") if len(text) >= 40 else ("", "nothing")


def learned_label(material: ClassMaterial) -> dict:
    """What the system got out of a file, in words for the page."""
    words = len((material.learned_text or "").split())
    has_note = bool((material.note or "").strip())
    if material.learned_status == "read":
        return {"tone": "ok", "text": f"Read: about {words:,} words".replace(",", " ")}
    if has_note:
        return {"tone": "ok", "text": "The system learns from your note"}
    if material.learned_status == "notes_only":
        what = {"video": "watch a video", "audio": "listen to a recording", "photo": "look at a photo"}.get(material.kind, "read this file")
        return {"tone": "todo", "text": f"The system cannot {what}: write a few lines about what it shows"}
    if material.learned_status == "nothing":
        return {"tone": "todo", "text": "No text found (scanned pages?): write a few lines about what it contains"}
    return {"tone": "todo", "text": "This format cannot be read: save it as PDF, or write a few lines about it"}


# ------------------------------------------------------------------
# Adding, changing, removing
# ------------------------------------------------------------------

def get_week(db: Session, class_group: ClassGroup, week_number: int) -> ClassWeek:
    """The row of one class week, created on first use."""
    row = db.query(ClassWeek).filter(
        ClassWeek.class_group_id == class_group.id, ClassWeek.week_number == week_number,
    ).first()
    if row is None:
        row = ClassWeek(class_group_id=class_group.id, week_number=week_number)
        db.add(row)
        db.flush()
    return row


def week_count(class_group: ClassGroup) -> int:
    planned = max((session.week_number or 0 for session in class_group.sessions), default=0)
    return max(1, min(60, max(planned, class_group.total_weeks or 0)))


def check_week(class_group: ClassGroup, week_number: int) -> None:
    if not 1 <= week_number <= week_count(class_group):
        raise UploadRefused("This week is not part of the class.")


def save_upload(db: Session, class_group: ClassGroup, week_number: int, upload, note: str = "") -> ClassMaterial:
    """Keep one uploaded file (a Starlette UploadFile) and read what it says.

    The file is copied piece by piece, so a long video never sits in memory.
    """
    check_week(class_group, week_number)
    filename = safe_name(upload.filename, fallback="file")
    extension = Path(filename).suffix.lower()
    if extension not in FILE_TYPES:
        raise UploadRefused(
            f"“{filename}” was not added: this kind of file is not accepted. "
            "Use PDF, Word, PowerPoint, text, a photo, a video or a recording."
        )
    kind, content_type = FILE_TYPES[extension]
    existing = db.query(ClassMaterial).filter(
        ClassMaterial.class_group_id == class_group.id, ClassMaterial.week_number == week_number,
    ).count()
    if existing >= MAX_FILES_PER_WEEK:
        raise UploadRefused(f"This week already has {MAX_FILES_PER_WEEK} files. Remove one first.")

    limit = _LIMITS[kind]
    stored = f"{uuid.uuid4().hex}{extension}"
    path = storage_dir() / stored
    size = 0
    try:
        with path.open("wb") as target:
            while True:
                piece = upload.file.read(CHUNK)
                if not piece:
                    break
                size += len(piece)
                if size > limit:
                    raise UploadRefused(
                        f"“{filename}” was not added: it is larger than {human_size(limit)}."
                    )
                target.write(piece)
        if size == 0:
            raise UploadRefused(f"“{filename}” was not added: the file is empty.")
    except UploadRefused:
        path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise UploadRefused(f"“{filename}” could not be saved on the server.") from exc

    text, status = read_file(path, extension, kind)
    material = ClassMaterial(
        class_group_id=class_group.id, week_number=week_number,
        filename=filename, stored_name=stored, content_type=content_type, size=size, kind=kind,
        note=" ".join((note or "").split())[:4000] or None,
        learned_text=text or None, learned_status=status,
    )
    db.add(material)
    get_week(db, class_group, week_number)
    db.commit()
    return material


def set_note(db: Session, material: ClassMaterial, note: str) -> None:
    material.note = (note or "").strip()[:4000] or None
    db.commit()


def delete(db: Session, material: ClassMaterial) -> None:
    path = path_of(material)
    db.delete(material)
    db.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("The course file %s could not be removed from disk.", path)


def forget_class(db: Session, class_id: str) -> None:
    """A class was deleted: remove its course files, weeks and questions."""
    for material in db.query(ClassMaterial).filter(ClassMaterial.class_group_id == class_id).all():
        try:
            path_of(material).unlink(missing_ok=True)
        except OSError:
            logger.warning("The course file %s could not be removed from disk.", material.stored_name)
        db.delete(material)
    db.query(WeekQuestion).filter(WeekQuestion.class_group_id == class_id).delete(synchronize_session=False)
    db.query(ClassWeek).filter(ClassWeek.class_group_id == class_id).delete(synchronize_session=False)
    db.commit()


def set_week_notes(db: Session, class_group: ClassGroup, week_number: int, notes: str) -> ClassWeek:
    check_week(class_group, week_number)
    week = get_week(db, class_group, week_number)
    week.notes = (notes or "").strip()[:20000] or None
    db.commit()
    return week


# ------------------------------------------------------------------
# What a week teaches
# ------------------------------------------------------------------

def week_topic(db: Session, class_id: str, week_number: int) -> str:
    """The subject of the week, from the class's weekly plan."""
    sessions = (
        db.query(ClassSession)
        .filter(ClassSession.class_group_id == class_id, ClassSession.week_number == week_number)
        .order_by(ClassSession.session_date.asc())
        .all()
    )
    return " / ".join(dict.fromkeys(session.topic for session in sessions if session.topic))


def sources(db: Session, class_id: str, week_number: int) -> list[dict]:
    """Everything the system can learn from for one week:
    [{"key": material id or "notes", "name": shown to the professor, "text": ...}]."""
    found = []
    materials = (
        db.query(ClassMaterial)
        .filter(ClassMaterial.class_group_id == class_id, ClassMaterial.week_number == week_number)
        .order_by(ClassMaterial.created_at.asc())
        .all()
    )
    for material in materials:
        parts = [part for part in ((material.note or "").strip(), (material.learned_text or "").strip()) if part]
        if parts:
            found.append({"key": material.id, "name": material.filename, "text": "\n\n".join(parts)})
    week = db.query(ClassWeek).filter(
        ClassWeek.class_group_id == class_id, ClassWeek.week_number == week_number,
    ).first()
    if week is not None and (week.notes or "").strip():
        found.append({"key": "notes", "name": "Your notes for the week", "text": week.notes.strip()})
    # What the system knows of the course itself: its plan for the week and what 9antra.tn says of it. So any course,
    # a new one too, can be asked about after its sessions, even before a professor adds a file.
    from app.models import ClassGroup
    from app.services import chatbot_sources

    group = db.get(ClassGroup, class_id)
    if group is not None and group.program is not None:
        weeks, _ = chatbot_sources._weeks_of(db, group.program)
        this = next((item for item in weeks if item["number"] == week_number), None)
        if this is not None:
            plan = ". ".join(part for part in (this["title"], this["objectives"].replace(";", ".")) if part).strip()
            if len(plan) >= 40:
                found.append({"key": f"plan:{group.program_id}:{week_number}", "name": "The course plan for this week", "text": plan})
            if len(this["site"]) >= 40:
                found.append({"key": f"site:{group.program_id}:{week_number}", "name": "9antra.tn, this week of the course", "text": this["site"]})
    return found
