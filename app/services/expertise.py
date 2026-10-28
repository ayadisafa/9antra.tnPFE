"""What the application knows about each program, week by week: what the questions and the assistant's answers come
from (the course plan, the website's page, the professors' files, the questions written), and what is still thin."""
from __future__ import annotations

from sqlalchemy.orm import Session


def of_program(db: Session, program) -> dict:
    from app.models import ClassGroup
    from app.models.course_material import ClassMaterial, WeekQuestion
    from app.models.knowledge import KnowledgeEntry
    from app.services import chatbot_sources, program_icons
    from app.services.session_check import _objectives

    weeks, page = chatbot_sources._weeks_of(db, program)
    classes = db.query(ClassGroup).filter(ClassGroup.program_id == program.id).all()
    ids = [group.id for group in classes]
    files: dict[int, list[str]] = {}
    questions: dict[int, int] = {}
    if ids:
        for row in db.query(ClassMaterial).filter(ClassMaterial.class_group_id.in_(ids)).all():
            files.setdefault(row.week_number, []).append(row.filename)
        for row in db.query(WeekQuestion).filter(WeekQuestion.class_group_id.in_(ids), WeekQuestion.status == "ready").all():
            questions[row.week_number] = questions.get(row.week_number, 0) + 1
    rows = []
    for week in weeks:
        number = week["number"]
        rows.append({
            "number": number, "title": (week["title"] or "").strip(), "points": _objectives(week["objectives"])[:6],
            "site": (week["site"] or "").strip(), "files": sorted(set(files.get(number, []))), "questions": questions.get(number, 0),
        })
    known = (db.query(KnowledgeEntry).filter(KnowledgeEntry.is_active.is_(True), KnowledgeEntry.program_id == program.id).count())
    total = len(rows) or 1
    cover = {
        "plan": sum(1 for row in rows if row["title"] or row["points"]),
        "site": sum(1 for row in rows if len(row["site"]) >= 40),
        "files": sum(1 for row in rows if row["files"]),
        "questions": sum(1 for row in rows if row["questions"]),
    }
    # How much it knows, out of 100: the plan and the website say what each week is; the files say what was really taught.
    score = round(100 * (0.35 * cover["plan"] + 0.25 * cover["site"] + 0.25 * cover["files"] + 0.15 * cover["questions"]) / total) if rows else 0
    from app.services import course_materials, knowledge_service, program_files

    own_files = [program_files.view(db, row, program) for row in program_files.of_program(db, program.id)]
    waiting_course = []
    if ids:
        for row in db.query(ClassMaterial).filter(ClassMaterial.class_group_id.in_(ids), ClassMaterial.review_status == "waiting").all():
            group = next((item for item in classes if item.id == row.class_group_id), None)
            waiting_course.append({"file": row, "class": group, "review": course_materials.review_view(db, row, program)})
    told = knowledge_service.facts(db, program.id)
    learnt = (db.query(KnowledgeEntry).filter(KnowledgeEntry.is_active.is_(True), KnowledgeEntry.program_id == program.id,
                                              KnowledgeEntry.origin == "system", KnowledgeEntry.kind != "fact")
              .order_by(KnowledgeEntry.kind, KnowledgeEntry.title).limit(60).all())
    if own_files:
        score = min(100, score + 5 * len([item for item in own_files if not item["review"]["waiting"]]))
    return {
        "program": program, "icon": program_icons.view(program), "weeks": rows, "cover": cover, "total": len(rows), "score": score,
        "knowledge": known, "classes": classes, "website": page is not None,
        "thin": [row["number"] for row in rows if not (row["title"] or row["points"]) and len(row["site"]) < 40],
        "files": own_files, "waiting_course": waiting_course, "told": told, "learnt": learnt,
        "waiting": len(waiting_course) + len([item for item in own_files if item["review"]["waiting"]]),
        "programs": [], "missing": __import__("app.services.program_assistant", fromlist=["missing_labels"]).missing_labels(program),
    }


def all_programs(db: Session) -> list[dict]:
    from app.models import Program

    programs = db.query(Program).order_by(Program.name).all()
    found = [of_program(db, program) for program in programs]
    for item in found:          # where a file can go: the other programs
        item["programs"] = [program for program in programs if program.id != item["program"].id]
    # What waits for the admin first, then the least known.
    found.sort(key=lambda item: (-item["waiting"], item["score"], item["program"].name or ""))
    return found
