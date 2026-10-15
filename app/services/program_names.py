"""Another way of writing a program's name is the same program.

A candidate who registers for "Artificial Intelligence" means the program
"Artificial Intelligence (AI Chatbot)"; "Digital Marketing" is "Marketing
Digital"; "Cybersecurity" is "Cyber Security". Such a name is linked to its
program at once, with the program's price, instead of waiting for a review.
The test is on the words: the same words in any order, or every word of the
shorter name inside the longer one, and only when exactly one program fits.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus

_NOISE = {"formation", "formations", "program", "programme", "course", "cours", "training", "de", "du", "des", "en", "et", "the",
          "and", "la", "le", "les", "in", "of", "a", "for", "pour"}
NO_PROGRAM = "no specific program"


def words(name: str | None) -> list[str]:
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return [word for word in re.findall(r"[a-z0-9+#]+", folded) if word not in _NOISE]


def same_program(name: str | None, programs: list[Program]) -> Program | None:
    """The one program this name is another way of writing; None when none fits, or several do."""
    mine = words(name)
    if not mine:
        return None
    joined, wanted = "".join(mine), set(mine)
    equal, inside = [], []
    for program in programs:
        theirs = words(program.name)
        if not theirs:
            continue
        if set(theirs) == wanted or "".join(theirs) == joined:
            equal.append(program)
        elif wanted < set(theirs) or set(theirs) < wanted:
            inside.append(program)
    if len(equal) == 1:
        return equal[0]
    if not equal and len(inside) == 1:
        return inside[0]
    return None


def price_of(program: Program, delivery_mode: str | None) -> float:
    value = program.price_onsite if (delivery_mode or "").lower() == "onsite" else program.price_online
    return float(value or 0)


def link(db: Session, registration: Registration, program: Program) -> None:
    """Give a registration its program and that program's price; the review that waited for it is closed."""
    registration.program_id, registration.program_name = program.id, program.name
    price = price_of(program, registration.delivery_mode)
    if not (registration.total_amount or 0):
        registration.total_amount = registration.total_price = price
    for row in db.query(Notification).filter(Notification.registration_id == registration.id, Notification.category == "PROGRAM_MATCH",
                                              Notification.is_read.is_(False)).all():
        row.is_read, row.status = True, "read"


def relink(db: Session) -> int:
    """Registrations left without a program whose name is one program's other name: link them. Returns how many."""
    programs = db.query(Program).filter(Program.name.isnot(None)).all()
    done = 0
    for registration in db.query(Registration).filter(Registration.program_id.is_(None), Registration.status != RegistrationStatus.CANCELLED).all():
        if not registration.program_name or registration.program_name.strip().lower() == NO_PROGRAM:
            continue
        program = same_program(registration.program_name, programs)
        if program is None:
            continue
        twin = db.query(Registration.id).filter(
            Registration.candidate_id == registration.candidate_id, Registration.program_id == program.id,
            Registration.status != RegistrationStatus.CANCELLED).first()
        if twin is not None:
            continue
        link(db, registration, program)
        done += 1
    if done:
        db.commit()
    return done


def _empty(db: Session, program: Program) -> bool:
    from app.models.classgroup import ClassGroup

    return (not program.levels and not (program.description or "").strip() and not program.website_slug
            and db.query(ClassGroup.id).filter(ClassGroup.program_id == program.id).first() is None)


def merge_twins(db: Session) -> list[tuple[str, str]]:
    """A program with nothing in it whose name is another way of writing one real program (as "Mobile Flutter"
    beside "Mobile Development (Flutter + Node/Spring Boot)", whose website page is mobile-flutter) is merged
    into it: its registrations go to the real one, then it is removed. Returns [(removed, kept)]."""
    from app.database import Base
    from app.models.program_proposal import ProgramProposal

    merged = []
    for twin in db.query(Program).all():
        if not _empty(db, twin):
            continue
        others = [program for program in db.query(Program).filter(Program.id != twin.id).all() if program.levels or program.website_slug]
        real = same_program(twin.name, others)
        # Only a shorter way of writing the real name, and only a program that registrations created:
        # an empty program the admin named on purpose ("Python Basics") is never touched.
        if real is None or not set(words(twin.name)) <= set(words(real.name)):
            continue
        if db.query(Registration.id).filter(Registration.program_id == twin.id).first() is None:
            continue
        # Somebody registered to both is one registration written twice: the copy that holds no money is cancelled
        # (it stays in their history). A copy with a payment on it is left for the admin.
        copies, clash = [], False
        for registration in db.query(Registration).filter(Registration.program_id == twin.id, Registration.status != RegistrationStatus.CANCELLED).all():
            if db.query(Registration.id).filter(Registration.candidate_id == registration.candidate_id, Registration.program_id == real.id,
                                                Registration.status != RegistrationStatus.CANCELLED).first() is not None:
                if (registration.paid_amount or 0) > 0:
                    clash = True
                copies.append(registration)
        if clash:
            continue
        for registration in copies:
            registration.status = RegistrationStatus.CANCELLED
        db.query(ProgramProposal).filter(ProgramProposal.program_id == twin.id).delete(synchronize_session=False)
        # The notes about proposals for the empty copy go with it: the proposals no longer exist.
        db.query(Notification).filter(Notification.program_id == twin.id, Notification.category == "PROGRAM_PROPOSAL").delete(synchronize_session=False)
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                if any(key.column.table.name == "programs" for key in column.foreign_keys):
                    db.execute(table.update().where(column == twin.id).values({column.name: real.id}))
        for registration in db.query(Registration).filter(Registration.program_id == real.id, Registration.program_name == twin.name).all():
            registration.program_name = real.name
            if not (registration.total_amount or 0):
                registration.total_amount = registration.total_price = price_of(real, registration.delivery_mode)
        merged.append((twin.name, real.name))
        db.delete(twin)
        db.commit()
    return merged
