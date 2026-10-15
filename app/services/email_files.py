"""Photos and documents that go with emails.

The content is kept on disk (EMAIL_FILES_DIR, default: data/email_files)
under a random name; the database keeps the name people see. A photo placed
in the body of an email travels inside the message itself, so it shows in the
candidate's mail client without anything being downloaded from this server.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.email_file import EmailFile

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 10 * 1024 * 1024
# Mail servers refuse messages above about 25 MB once encoded.
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENTS = 6
MAX_IMAGE_WIDTH = 1200
MAX_ANIMATION_BYTES = 2 * 1024 * 1024

# What the admin may attach: extension -> content type sent to the candidate.
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".ics": "text/calendar",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# Shown in the browser as they are; everything else is offered as a download.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
ACCEPT_DOCUMENTS = ",".join(sorted(DOCUMENT_TYPES))

_CID_DOMAIN = "9antra"
_CID = re.compile(r"cid:([0-9a-fA-F-]{36})@" + _CID_DOMAIN)
_UNSAFE_NAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def storage_dir() -> Path:
    folder = Path(os.getenv("EMAIL_FILES_DIR") or "data/email_files")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def path_of(file: EmailFile) -> Path:
    return storage_dir() / file.stored_name


def read_bytes(file: EmailFile) -> bytes | None:
    try:
        return path_of(file).read_bytes()
    except OSError:
        logger.warning("Email file %s (%s) is missing on disk.", file.id, file.filename)
        return None


def safe_name(filename: str | None, fallback: str = "fichier") -> str:
    """The file's own name, without any folder or character a mail client rejects."""
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = " ".join(_UNSAFE_NAME.sub(" ", name).split()).strip(". ")
    if not name:
        return fallback
    stem, dot, extension = name.rpartition(".")
    return f"{stem[:100]}.{extension[:10]}" if dot and stem else name[:110]


def human_size(size: int | None) -> str:
    size = int(size or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def url_of(file_id: str) -> str:
    return f"/inbox/files/{file_id}"


def describe(file: EmailFile, inline: bool = False) -> dict:
    """What pages and stored messages need to show one file."""
    return {
        "id": file.id,
        "name": file.filename,
        "size": file.size,
        "size_label": human_size(file.size),
        "type": file.content_type,
        "is_image": bool(file.is_image),
        "inline": inline,
        "url": url_of(file.id),
    }


def parse_ids(value) -> list[str]:
    """File ids from a form field, a stored JSON list or a stored message."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [part for part in value.split(",")]
    ids = []
    for item in value if isinstance(value, (list, tuple)) else []:
        file_id = item.get("id") if isinstance(item, dict) else item
        file_id = str(file_id or "").strip()
        if file_id and file_id not in ids:
            ids.append(file_id)
    return ids


def load(db: Session, ids) -> list[EmailFile]:
    """The files for these ids, in the same order; unknown ids are skipped."""
    wanted = parse_ids(ids)
    if not wanted:
        return []
    found = {file.id: file for file in db.query(EmailFile).filter(EmailFile.id.in_(wanted)).all()}
    return [found[file_id] for file_id in wanted if file_id in found]


def check_total(files: list[EmailFile]) -> None:
    if len(files) > MAX_ATTACHMENTS + 1:
        raise ValueError(f"An email can carry at most {MAX_ATTACHMENTS} attached files.")
    if sum(file.size or 0 for file in files) > MAX_TOTAL_BYTES:
        raise ValueError(
            f"These files weigh more than {human_size(MAX_TOTAL_BYTES)} together; mail servers would refuse the email. "
            "Remove one, or share a link instead."
        )


# ------------------------------------------------------------------
# Inside an email
# ------------------------------------------------------------------

def cid(file_id: str) -> str:
    """The reference a photo has inside the email that carries it."""
    return f"{file_id}@{_CID_DOMAIN}"


def cid_src(file: EmailFile) -> str:
    return f"cid:{cid(file.id)}"


def for_browser(markup: str, base: str = "") -> str:
    """The same email, with embedded photos pointing at this server so a browser shows them."""
    return _CID.sub(lambda match: f"{base}{url_of(match.group(1))}", markup or "")


def as_mime(files: list[EmailFile], inline: list[EmailFile] | None = None) -> tuple[list[dict], str | None]:
    """(parts for the mail sender, name of a file that could not be read)."""
    parts = []
    for file, embedded in [(item, True) for item in inline or []] + [(item, False) for item in files or []]:
        data = read_bytes(file)
        if data is None:
            return [], file.filename
        parts.append({
            "filename": file.filename,
            "content_type": file.content_type,
            "data": data,
            "cid": cid(file.id) if embedded else None,
        })
    return parts, None


# ------------------------------------------------------------------
# Storing
# ------------------------------------------------------------------

def _prepare_photo(data: bytes) -> tuple[bytes, str, str, int, int]:
    """(content, content type, extension, width, height) of a photo light enough for an email."""
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError("This file is not a picture the system can read. Use a JPG or PNG image.") from exc

    if (image.format or "").upper() == "GIF" and getattr(image, "is_animated", False):
        if len(data) > MAX_ANIMATION_BYTES:
            raise ValueError(f"An animated picture must weigh less than {human_size(MAX_ANIMATION_BYTES)}.")
        return data, "image/gif", ".gif", image.width, image.height

    image = ImageOps.exif_transpose(image)
    if image.width > MAX_IMAGE_WIDTH:
        image.thumbnail((MAX_IMAGE_WIDTH, MAX_IMAGE_WIDTH * 6))
    transparent = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    output = io.BytesIO()
    if transparent:
        image.convert("RGBA").save(output, "PNG", optimize=True)
        return output.getvalue(), "image/png", ".png", image.width, image.height
    image.convert("RGB").save(output, "JPEG", quality=85, optimize=True, progressive=True)
    return output.getvalue(), "image/jpeg", ".jpg", image.width, image.height


def _store(db: Session, *, filename: str, content_type: str, data: bytes, is_image: bool,
           origin: str, width: int | None = None, height: int | None = None) -> EmailFile:
    file_id = str(uuid.uuid4())
    (storage_dir() / file_id).write_bytes(data)
    file = EmailFile(
        id=file_id, filename=filename, content_type=content_type, size=len(data),
        stored_name=file_id, is_image=is_image, width=width, height=height, origin=origin,
    )
    db.add(file)
    db.flush()
    return file


def save_upload(db: Session, *, filename: str | None, data: bytes, purpose: str = "attachment") -> EmailFile:
    """Keep a file the admin chose. `purpose` is "image" for the photo shown in
    the email itself, "attachment" for a file the candidate opens separately."""
    if not data:
        raise ValueError("This file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"This file weighs {human_size(len(data))}; the limit is {human_size(MAX_FILE_BYTES)} per file.")
    name = safe_name(filename)
    extension = os.path.splitext(name)[1].lower()

    if purpose == "image":
        content, content_type, new_extension, width, height = _prepare_photo(data)
        stem = os.path.splitext(name)[0] or "photo"
        return _store(
            db, filename=f"{stem}{new_extension}", content_type=content_type, data=content,
            is_image=True, origin="admin", width=width, height=height,
        )

    content_type = DOCUMENT_TYPES.get(extension)
    if content_type is None:
        raise ValueError(
            f"“{name}” cannot be attached. Attach a PDF, a picture, or a Word, Excel or PowerPoint file."
        )
    return _store(
        db, filename=name, content_type=content_type, data=data,
        is_image=content_type.startswith("image/"), origin="admin",
    )


def store_inbound(db: Session, items) -> list[EmailFile]:
    """Keep the files a candidate attached to an email. Oversized ones are left out."""
    stored = []
    for filename, content_type, data in items or []:
        if not data or len(data) > MAX_FILE_BYTES or len(stored) >= MAX_ATTACHMENTS:
            continue
        content_type = (content_type or "application/octet-stream").split(";")[0].strip().lower()
        try:
            stored.append(_store(
                db, filename=safe_name(filename, "piece-jointe"), content_type=content_type, data=data,
                is_image=content_type in INLINE_TYPES and content_type.startswith("image/"), origin="candidate",
            ))
        except OSError:
            logger.exception("Could not keep the attachment %r from a candidate.", filename)
    return stored
