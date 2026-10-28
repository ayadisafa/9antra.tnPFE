"""The files of a program itself: its syllabus, a brochure, a course outline, a recording.

Kept and read like course files (course_materials), and checked against the program like them (program_match): a file
that looks like part of another program, or of none, waits for the admin. Once accepted, the assistant learns from it,
and the program's plan and description can be taken from it (program_reader), only when the admin chooses to.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.course_material import ClassMaterial, ProgramFile
from app.services import course_materials, program_reader

logger = logging.getLogger(__name__)
MAX_FILES = 40


def path_of(row: ProgramFile):
    return course_materials.storage_dir() / row.stored_name


def url_of(row: ProgramFile) -> str:
    return f"/programs/files/{row.id}"


def of_program(db: Session, program_id: str) -> list[ProgramFile]:
    return db.query(ProgramFile).filter(ProgramFile.program_id == program_id).order_by(ProgramFile.created_at.desc()).all()


def waiting(db: Session) -> list[ProgramFile]:
    return db.query(ProgramFile).filter(ProgramFile.review_status == "waiting").order_by(ProgramFile.created_at.desc()).all()


def save(db: Session, program, upload, note: str = "", by: str | None = None) -> ProgramFile:
    if db.query(ProgramFile).filter(ProgramFile.program_id == program.id).count() >= MAX_FILES:
        raise course_materials.UploadRefused(f"This program already has {MAX_FILES} files. Remove one first.")
    stored = course_materials.store_file(upload)
    row = ProgramFile(program_id=program.id, filename=stored["filename"], stored_name=stored["stored"], content_type=stored["content_type"],
                      size=stored["size"], kind=stored["kind"], note=" ".join((note or "").split())[:4000] or None,
                      learned_text=stored["text"] or None, learned_status=stored["status"], uploaded_by=by)
    course_materials.review(db, row, program.id)
    db.add(row)
    db.commit()
    return row


def accept(db: Session, row: ProgramFile, by: str) -> None:
    row.review_status, row.reviewed_by, row.reviewed_at = "ok", by, datetime.utcnow()
    db.commit()


def to_program(db: Session, row: ProgramFile, program, by: str) -> ProgramFile:
    row.program_id, row.fit_program_id = program.id, program.id
    row.review_status, row.reviewed_by, row.reviewed_at = "ok", by, datetime.utcnow()
    db.commit()
    return row


def from_material(db: Session, material: ClassMaterial, program, by: str) -> ProgramFile:
    """A course file that belongs to another program: it becomes a file of that program (the same file on disk)."""
    row = ProgramFile(program_id=program.id, filename=material.filename, stored_name=material.stored_name, content_type=material.content_type,
                      size=material.size, kind=material.kind, note=material.note, learned_text=material.learned_text,
                      learned_status=material.learned_status, review_status="ok", fit="fits", fit_program_id=program.id,
                      fit_words=material.fit_words, uploaded_by=material.uploaded_by, reviewed_by=by, reviewed_at=datetime.utcnow())
    db.add(row)
    db.delete(material)
    db.commit()
    return row


def to_class(db: Session, row: ProgramFile, class_group, week: int, by: str) -> ClassMaterial:
    """A program's file given to one week of a class (the same file on disk)."""
    course_materials.check_week(class_group, week)
    material = ClassMaterial(class_group_id=class_group.id, week_number=week, filename=row.filename, stored_name=row.stored_name,
                             content_type=row.content_type, size=row.size, kind=row.kind, note=row.note, learned_text=row.learned_text,
                             learned_status=row.learned_status, review_status="ok", fit="fits", fit_program_id=class_group.program_id,
                             fit_words=row.fit_words, uploaded_by=row.uploaded_by, reviewed_by=by, reviewed_at=datetime.utcnow())
    db.add(material)
    course_materials.get_week(db, class_group, week)
    db.delete(row)
    db.commit()
    return material


def delete(db: Session, row: ProgramFile) -> None:
    path = path_of(row)
    shared = db.query(ClassMaterial.id).filter(ClassMaterial.stored_name == row.stored_name).first() is not None
    db.delete(row)
    db.commit()
    if not shared:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("The program file %s could not be removed from disk.", path)


def read(row: ProgramFile) -> dict:
    """What the file says of the program (program_reader.read)."""
    return program_reader.read("\n".join(part for part in ((row.note or ""), (row.learned_text or "")) if part))


def best_plan(db: Session, program) -> tuple[ProgramFile | None, dict]:
    """The accepted file of the program that writes the longest plan, and what it says: (file, read)."""
    best, found = None, {"plan": [], "description": ""}
    for row in of_program(db, program.id):
        if row.review_status == "waiting" or not row.learned_text:
            continue
        said = read(row)
        if len(said["plan"]) > len(found["plan"]) or (not found["description"] and said["description"] and not best):
            best, found = row, said
    return best, found


def apply_plan(db: Session, program, plan: list[dict]) -> dict:
    """The program's week-by-week plan from a file: each week of the file writes that week of the plan (its subject and
    its points); weeks the plan does not have yet are added to its last level; later weeks it already has are kept.
    Returns {"changed", "added", "kept"}."""
    from app.models.program import ProgramLevel, WeeklyCurriculum

    weeks = {week.week_number: week for level in program.levels for week in level.weeks}
    levels = sorted(program.levels, key=lambda level: level.level_order or 0)
    last = levels[-1] if levels else None
    if last is None:
        last = ProgramLevel(program_id=program.id, level_order=1, title="Level 1", hours_per_week=program.hours_per_week or None,
                            class_session_time=program.class_session_time or None)
        db.add(last)
        db.flush()
    changed = added = 0
    for position, item in enumerate(plan, 1):
        objectives = "; ".join(item["points"]) or None
        week = weeks.get(position)
        if week is not None:
            week.topic_title, week.learning_objectives = item["title"][:150], objectives
            changed += 1
        else:
            db.add(WeeklyCurriculum(level_id=last.id, week_number=position, topic_title=item["title"][:150], learning_objectives=objectives))
            added += 1
    kept = len([number for number in weeks if number > len(plan)])
    program.embedding = None
    db.commit()
    return {"changed": changed, "added": added, "kept": kept}


def view(db: Session, row: ProgramFile, program) -> dict:
    said = read(row) if row.learned_text or row.note else {"plan": [], "description": "", "tools": []}
    return {"file": row, "url": url_of(row), "icon": course_materials.KIND_ICONS.get(row.kind, "bi-file-earmark"),
            "size": course_materials.human_size(row.size), "learned": course_materials.learned_label(row),
            "review": course_materials.review_view(db, row, program), "plan": said.get("plan") or [],
            "description": said.get("description") or "", "tools": said.get("tools") or []}
