"""The files of a program (see services/program_files.py), and what to do with them: accept one that did not look like
part of the program, move it where it belongs, use its plan or its description, remove it."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClassGroup, Program
from app.models.course_material import ProgramFile
from app.services import auth, course_materials, program_files
from app.services.ui_words import t as _t

router = APIRouter(tags=["Program files"])


def _back(program_id: str, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error), ("open", program_id)) if value})
    return RedirectResponse(f"/programs/expertise?{query}#ex-{program_id}", status_code=303)


def _who(request: Request) -> str:
    user = auth.current(request)
    return user.name if user else "admin"


def _file(db: Session, file_id: str) -> ProgramFile:
    row = db.get(ProgramFile, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    return row


@router.post("/programs/{program_id}/files")
def add_program_files(program_id: str, request: Request, files: list[UploadFile] = File(default=[]), note: str = Form(""),
                      db: Session = Depends(get_db)):
    from app.services import program_assistant

    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    added, refused = [], []
    for upload in files:
        if not upload.filename:
            continue
        try:
            added.append(program_files.save(db, program, upload, note, by=_who(request)))
        except course_materials.UploadRefused as exc:
            db.rollback()
            refused.append(str(exc))
    if not added:
        return _back(program_id, error=" ".join(refused) or _t("Choose at least one file."))
    notice = _t("{0} file(s) added to {1}.", len(added), program.name)
    held = [row for row in added if row.review_status == "waiting"]
    if held:
        notice += " " + _t("“{0}” waits for your approval: it does not look like part of this program.", held[0].filename)
    planned = [row for row in added if row.review_status != "waiting" and len(program_files.read(row)["plan"]) >= 2]
    if planned:
        notice += " " + _t("A week-by-week plan was found in “{0}”: you can use it below.", planned[0].filename)
    if program_assistant.missing(program):
        program_assistant.request(db, program)          # what is missing is proposed from the file first
        notice += " " + _t("The details still missing are being proposed from it.")
    return _back(program_id, notice=notice, error=" ".join(refused) or None)


@router.get("/programs/files/{file_id}")
def open_program_file(file_id: str, db: Session = Depends(get_db)):
    row = _file(db, file_id)
    path = program_files.path_of(row)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This file is no longer on the server")
    inline = row.kind in course_materials.INLINE_KINDS or row.content_type == "application/pdf"
    return FileResponse(path, media_type=row.content_type, filename=row.filename, content_disposition_type="inline" if inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


@router.post("/programs/files/{file_id}/accept")
def accept_program_file(file_id: str, request: Request, db: Session = Depends(get_db)):
    row = _file(db, file_id)
    program_files.accept(db, row, _who(request))
    return _back(row.program_id, notice=_t("“{0}” is part of the program now: the assistant learns from it.", row.filename))


@router.post("/programs/files/{file_id}/move")
def move_program_file(file_id: str, request: Request, target: str = Form(""), db: Session = Depends(get_db)):
    row = _file(db, file_id)
    kind, _, rest = target.partition(":")
    if kind == "program":
        program = db.get(Program, rest)
        if program is None:
            return _back(row.program_id, error=_t("Choose where the file goes."))
        program_files.to_program(db, row, program, _who(request))
        return _back(program.id, notice=_t("“{0}” is a file of {1} now.", row.filename, program.name))
    group_id, _, week = rest.partition(":")
    group = db.get(ClassGroup, group_id) if kind == "class" else None
    if group is None:
        return _back(row.program_id, error=_t("Choose where the file goes."))
    try:
        material = program_files.to_class(db, row, group, int(week or 1), _who(request))
    except (ValueError, course_materials.UploadRefused) as exc:
        db.rollback()
        return _back(row.program_id, error=str(exc))
    return RedirectResponse(f"/classes/{group.id}/course?week={material.week_number}&" + urlencode({"notice": _t("“{0}” is in {1}, week {2} now.", material.filename, group.name, material.week_number)}), status_code=303)


@router.post("/programs/files/{file_id}/delete")
def delete_program_file(file_id: str, db: Session = Depends(get_db)):
    row = _file(db, file_id)
    program_id, name = row.program_id, row.filename
    program_files.delete(db, row)
    return _back(program_id, notice=_t("“{0}” removed.", name))


@router.post("/programs/files/{file_id}/plan")
def use_file_plan(file_id: str, db: Session = Depends(get_db)):
    """The program's week-by-week plan written from the file, as the admin chose."""
    row = _file(db, file_id)
    program = db.get(Program, row.program_id)
    plan = program_files.read(row)["plan"]
    if program is None or len(plan) < 2 or row.review_status == "waiting":
        return _back(row.program_id, error=_t("No week-by-week plan can be taken from this file."))
    done = program_files.apply_plan(db, program, plan)
    return _back(program.id, notice=_t("The plan of {0} now follows “{1}”: {2} week(s) rewritten, {3} added, {4} later kept.",
                                       program.name, row.filename, done["changed"], done["added"], done["kept"]))


@router.post("/programs/files/{file_id}/description")
def use_file_description(file_id: str, db: Session = Depends(get_db)):
    row = _file(db, file_id)
    program = db.get(Program, row.program_id)
    description = program_files.read(row)["description"]
    if program is None or not description or row.review_status == "waiting":
        return _back(row.program_id, error=_t("No description can be taken from this file."))
    program.description, program.embedding = description[:1000], None
    db.commit()
    return _back(program.id, notice=_t("The description of {0} now comes from “{1}”.", program.name, row.filename))
