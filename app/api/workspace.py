"""Finding anything, changing any value, and the team: who signs in and what each person may see or change."""
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserSession
from app.services import access, auth, data_editor, finder
from app.template_context import notification_context

router = APIRouter(tags=["Workspace"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _go(url: str, notice: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(url + (("&" if "?" in url else "?") + query if query else "") + anchor, status_code=303)


def _who(request: Request) -> str:
    user = auth.current(request)
    return user.name if user else "?"


# ------------------------------------------------------------------
# Finding anything
# ------------------------------------------------------------------

@router.get("/find")
def find(request: Request, q: str = "", format: str = "", db: Session = Depends(get_db)):
    groups = finder.find(db, q, auth.current(request))
    if format == "json":
        return JSONResponse({"query": q, "groups": groups})
    return templates.TemplateResponse(request=request, name="find.html",
                                      context={"active_page": "find", "query": q, "groups": groups})


# ------------------------------------------------------------------
# Every value
# ------------------------------------------------------------------

def _table(key: str) -> tuple:
    spec = data_editor.table(key)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown kind of record")
    return spec


@router.get("/data", response_class=HTMLResponse)
def data_index(request: Request, db: Session = Depends(get_db)):
    from app.models.edit_log import EditLog

    recent = db.query(EditLog).order_by(EditLog.at.desc()).limit(12).all()
    return templates.TemplateResponse(request=request, name="data_index.html", context={
        "active_page": "data", "tables": data_editor.counts(db), "recent": recent, "names": {key: spec[0] for key, spec in data_editor.TABLES.items()},
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.get("/data/{key}.csv")
def data_export(key: str, q: str = "", db: Session = Depends(get_db)):
    """Every record of a kind as a file Excel opens (the search, when one is typed, narrows it)."""
    _table(key)
    name, content = data_editor.export(db, key, q)
    plain = name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "export.csv"
    return Response(content=content, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}", "Cache-Control": "no-store"})


@router.get("/data/{key}", response_class=HTMLResponse)
def data_table(key: str, request: Request, q: str = "", page: int = 1, db: Session = Depends(get_db)):
    _table(key)
    return templates.TemplateResponse(request=request, name="data_table.html", context={
        "active_page": "data", "view": data_editor.listing(db, key, q, page),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


def _form(request: Request, db: Session, key: str, row, error: str | None = None, typed: dict | None = None):
    name, model, icon, _, _ = _table(key)
    fields = data_editor.columns(model)
    for field in fields:
        field["value"] = typed[field["name"]] if typed and field["name"] in typed else data_editor.form_value(getattr(row, field["name"], None) if row is not None else None, field["widget"])
        field["options"] = data_editor.choices_for(db, field["target"]) if field["target"] else None
    row_id = None if row is None else str(getattr(row, data_editor._key_of(model).key))
    return templates.TemplateResponse(request=request, name="data_row.html", context={
        "active_page": "data", "key": key, "name": name, "icon": icon, "fields": fields, "row_id": row_id,
        "label": data_editor._label(row) if row is not None else None,
        "history": data_editor.history(db, key, row_id) if row_id else [],
        "notice": request.query_params.get("notice"), "error": error or request.query_params.get("error")})


@router.get("/data/{key}/new", response_class=HTMLResponse)
def data_new(key: str, request: Request, db: Session = Depends(get_db)):
    return _form(request, db, key, None)


@router.post("/data/{key}/new")
async def data_create(key: str, request: Request, db: Session = Depends(get_db)):
    _table(key)
    values = dict(await request.form())
    row, problem = data_editor.save(db, key, None, values, _who(request))
    if problem:
        return _form(request, db, key, None, problem, values)
    model = data_editor.TABLES[key][1]
    return _go(f"/data/{key}/{getattr(row, data_editor._key_of(model).key)}", notice="Added.")


@router.get("/data/{key}/{row_id}", response_class=HTMLResponse)
def data_row(key: str, row_id: str, request: Request, db: Session = Depends(get_db)):
    _table(key)
    row = data_editor.get(db, key, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _form(request, db, key, row)


@router.post("/data/{key}/{row_id}")
async def data_save(key: str, row_id: str, request: Request, db: Session = Depends(get_db)):
    _table(key)
    row = data_editor.get(db, key, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Record not found")
    values = dict(await request.form())
    saved, problem = data_editor.save(db, key, row, values, _who(request))
    if problem:
        db.rollback()
        return _form(request, db, key, data_editor.get(db, key, row_id), problem, values)
    return _go(f"/data/{key}/{row_id}", notice="Saved. The change is in the record's history below.")


@router.post("/data/{key}/{row_id}/delete")
def data_delete(key: str, row_id: str, request: Request, db: Session = Depends(get_db)):
    _table(key)
    row = data_editor.get(db, key, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Record not found")
    problem = data_editor.remove(db, key, row, _who(request))
    if problem:
        return _go(f"/data/{key}/{row_id}", error=problem)
    return _go(f"/data/{key}", notice="Removed. It is written in the edit log.")


# ------------------------------------------------------------------
# The team
# ------------------------------------------------------------------

def _admin_only(request: Request) -> auth.CurrentUser:
    """An admin, or a team member the admin allowed to manage the team."""
    user = auth.current(request)
    if not access.can_manage_team(user):
        raise HTTPException(status_code=403, detail="Only an admin, or someone they allowed, manages the team")
    return user


def _not_an_admin_unless_admin(me, person) -> None:
    """Someone who manages the team without being an admin never changes an admin's account."""
    if person is not None and person.role == auth.ADMIN and not me.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin changes an admin's account")


def _permissions(form) -> dict:
    if form.get("preset") == "all":
        return access.everything()
    values = {key: form.get(f"perm_{key}") for key, *_ in access.SECTIONS}
    values.update({key: "edit" if form.get(f"right_{key}") else "none" for key, *_ in access.RIGHTS})
    return access.clean(values)


@router.get("/settings/team", response_class=HTMLResponse)
def team_page(request: Request, db: Session = Depends(get_db)):
    me = _admin_only(request)
    people = db.query(User).order_by(User.role.asc(), User.name.asc()).all()
    from app.models import Professor

    with_account = {person.professor_id for person in people if person.professor_id}
    return templates.TemplateResponse(request=request, name="team.html", context={
        "active_page": "settings", "me": me, "levels": access.LEVELS, "sections": access.SECTIONS, "rights": access.RIGHTS,
        "free_professors": [professor for professor in db.query(Professor).order_by(Professor.name).all() if professor.id not in with_account],
        "people": [{"user": person, "permissions": access.clean(person.permissions) if person.role == auth.STAFF else None,
                    "devices": db.query(UserSession).filter(UserSession.user_id == person.id).count()} for person in people],
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.post("/settings/team/add")
async def team_add(request: Request, db: Session = Depends(get_db)):
    me = _admin_only(request)
    form = await request.form()
    wanted = form.get("role")
    role = auth.ADMIN if wanted == "admin" and me.is_admin else auth.PROFESSOR if wanted == "professor" else auth.STAFF
    professor = None
    if role == auth.PROFESSOR:
        from app.models import Professor

        professor = db.get(Professor, form.get("professor_id") or "")
        if professor is None:
            return _go("/settings/team", error="Choose the professor this account is for.", anchor="#add")
        if db.query(User).filter(User.professor_id == professor.id).first() is not None:
            return _go("/settings/team", error=f"{professor.name} already has an account.", anchor="#add")
    try:
        user = auth.create_user(db, email=form.get("email", ""), name=form.get("name", "") or (professor.name if professor else ""),
                                password=form.get("password", ""), role=role, professor_id=professor.id if professor else None)
    except ValueError as problem:
        return _go("/settings/team", error=str(problem), anchor="#add")
    if role == auth.STAFF:
        user.permissions = access.dump(_permissions(form))
        db.commit()
    if role == auth.PROFESSOR:
        return _go("/settings/team", notice=f"{user.name} can now sign in with {user.email}: their classes and their messages. Give them their password yourself.",
                   anchor=f"#person-{user.id}")
    return _go("/settings/team", notice=f"{user.name} can now sign in with {user.email}. Give them their password yourself.", anchor=f"#person-{user.id}")


@router.post("/settings/team/{user_id}/access")
async def team_access(user_id: str, request: Request, db: Session = Depends(get_db)):
    _admin_only(request)
    person = db.get(User, user_id)
    if person is None or person.role != auth.STAFF:
        raise HTTPException(status_code=404, detail="Team member not found")
    form = await request.form()
    person.permissions = access.dump(_permissions(form))
    db.commit()
    said = "everything (like an admin, still a team member)" if form.get("preset") == "all" else "what you chose"
    return _go("/settings/team", notice=f"{person.name} can now see and do {said}. It applies at once.", anchor=f"#person-{person.id}")


@router.post("/settings/team/{user_id}/cell")
def team_cell(user_id: str, request: Request, part: str = Form(""), level: str = Form(""), db: Session = Depends(get_db)):
    """One cell of the table “who can do what”: a part (or a special right) for one team member, saved at once."""
    me = _admin_only(request)
    person = db.get(User, user_id)
    if person is not None and person.id == me.id and not me.is_admin:
        return JSONResponse({"ok": False, "error": "Your own access is changed by an admin."}, status_code=403)
    if person is None or person.role != auth.STAFF:
        return JSONResponse({"ok": False, "error": "Only a team member's access is chosen part by part."}, status_code=400)
    permissions = access.clean(person.permissions)
    if part in {key for key, *_ in access.SECTIONS}:
        permissions[part] = level if level in access.LEVELS else "none"
    elif part in {key for key, *_ in access.RIGHTS}:
        permissions[part] = "edit" if level == "edit" else "none"
    else:
        return JSONResponse({"ok": False, "error": "Unknown part."}, status_code=400)
    person.permissions = access.dump(access.clean(permissions))
    db.commit()
    return JSONResponse({"ok": True, "level": access.clean(person.permissions).get(part, "none")})


@router.post("/settings/team/{user_id}/delete")
def team_delete(user_id: str, request: Request, db: Session = Depends(get_db)):
    """Remove an account for good: the person can no longer sign in. Their messages and what they did stay recorded;
    a professor's own record (classes, specialities) stays too."""
    me = _admin_only(request)
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _not_an_admin_unless_admin(me, person)
    if person.id == me.id:
        return _go("/settings/team", error="You cannot remove your own account.")
    name = person.name
    db.query(UserSession).filter(UserSession.user_id == person.id).delete()
    db.delete(person)
    db.commit()
    return _go("/settings/team", notice=f"{name}'s account was removed: they can no longer sign in.")


@router.post("/settings/team/{user_id}/view-as")
def team_view_as(user_id: str, request: Request, db: Session = Depends(get_db)):
    """See the application exactly as this person does (looking only: nothing can be changed in their name)."""
    me = auth.current(request)
    if me is None or not me.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin looks as someone else")
    person = db.get(User, user_id)
    if person is None or person.role == auth.ADMIN or not person.is_active:
        return _go("/settings/team", error="You can look as a team member or a professor (whose account is active).", anchor=f"#person-{user_id}")
    response = RedirectResponse("/teacher" if person.role == auth.PROFESSOR else "/", status_code=303)
    response.set_cookie(auth.VIEW_AS_COOKIE, person.id, httponly=True, samesite="lax", max_age=3600, path="/")
    return response


@router.post("/settings/team/{user_id}/details")
def team_details(user_id: str, request: Request, name: str = Form(""), email: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db)):
    """Change someone's name, the email they sign in with, and their phone (for WhatsApp and SMS from the team chat)."""
    from app.services import whatsapp

    me = _admin_only(request)
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _not_an_admin_unless_admin(me, person)
    address = auth.clean_email(email)
    if not name.strip() or address is None:
        return _go("/settings/team", error="A name and a valid email are needed.", anchor=f"#person-{person.id}")
    if db.query(User).filter(User.email == address, User.id != person.id).first() is not None:
        return _go("/settings/team", error=f"{address} is already someone's sign-in.", anchor=f"#person-{person.id}")
    if phone.strip() and whatsapp.normalise(phone) is None:
        return _go("/settings/team", error="This phone number cannot be read: write it like +216 98 123 456.", anchor=f"#person-{person.id}")
    person.name, person.email = " ".join(name.split())[:120], address
    person.phone = whatsapp.pretty(phone) if phone.strip() else None
    db.commit()
    return _go("/settings/team", notice=f"{person.name}'s details were saved.", anchor=f"#person-{person.id}")


@router.post("/settings/team/{user_id}/role")
def team_role(user_id: str, request: Request, role: str = Form(""), db: Session = Depends(get_db)):
    me = _admin_only(request)
    if not me.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin makes someone an admin")
    person = db.get(User, user_id)
    if person is None or person.role == auth.PROFESSOR or role not in (auth.ADMIN, auth.STAFF):
        raise HTTPException(status_code=404, detail="Account not found")
    if person.id == me.id:
        return _go("/settings/team", error="You cannot change your own role.")
    person.role = role
    if role == auth.STAFF and not person.permissions:
        person.permissions = access.dump({"dashboard": "view"})
    db.commit()
    return _go("/settings/team", notice=f"{person.name} is now {'an admin' if role == auth.ADMIN else 'a team member with chosen access'}.", anchor=f"#person-{person.id}")


@router.post("/settings/team/{user_id}/active")
def team_active(user_id: str, request: Request, db: Session = Depends(get_db)):
    me = _admin_only(request)
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _not_an_admin_unless_admin(me, person)
    if person.id == me.id:
        return _go("/settings/team", error="You cannot switch off your own account.")
    person.is_active = not person.is_active
    if not person.is_active:
        db.query(UserSession).filter(UserSession.user_id == person.id).delete()
    db.commit()
    return _go("/settings/team", notice=f"{person.name} {'can sign in again' if person.is_active else 'can no longer sign in, and was signed out everywhere'}.", anchor=f"#person-{person.id}")


@router.post("/settings/team/{user_id}/password")
def team_password(user_id: str, request: Request, password: str = Form(""), db: Session = Depends(get_db)):
    me = _admin_only(request)
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _not_an_admin_unless_admin(me, person)
    problem = auth.password_problem(password)
    if problem:
        return _go("/settings/team", error=problem, anchor=f"#person-{person.id}")
    auth.set_password(db, person, password)
    return _go("/settings/team", notice=f"{person.name}'s password was changed and they were signed out everywhere.", anchor=f"#person-{person.id}")
