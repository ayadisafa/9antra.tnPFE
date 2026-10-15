"""One search for everything: the pages, the settings, what can be done, and the records.

Typed in the bar at the top of every page (or opened with Ctrl+K). A person
only finds what their access lets them open.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import AdminReminder, Candidate, ClassGroup, Professor, Program
from app.models.candidate_email import CandidateEmail
from app.services import access

# title, address, the part it belongs to, other words people may type, icon
PAGES = [
    ("Dashboard: what needs you today", "/", "dashboard", "home accueil today aujourd'hui", "bi-grid-1x2-fill"),
    ("Candidates", "/candidate/all", "candidates", "students etudiants people inscrits", "bi-people-fill"),
    ("Add a candidate", "/candidate/all?add=1", "candidates", "new candidate nouveau ajouter register inscrire", "bi-person-plus-fill"),
    ("Classes", "/classes/", "classes", "groups groupes sessions seances", "bi-easel2-fill"),
    ("Create a class", "/classes/#create", "classes", "new class nouvelle classe", "bi-plus-square"),
    ("Professors and their specialities", "/classes/?tab=professors", "classes", "teachers formateurs profs speciality specialite", "bi-person-video3"),
    ("Programs", "/programs/all", "programs", "formations courses catalogue", "bi-journal-bookmark-fill"),
    ("Add a program", "/programs/all#add", "programs", "new program nouvelle formation import pdf", "bi-journal-plus"),
    ("What the market asks", "/programs/market", "programs", "jobs emploi linkedin skills competences trends tendances offers", "bi-graph-up-arrow"),
    ("Level checks", "/quiz/all", "levels", "quiz test niveau placement", "bi-signpost-split-fill"),
    ("Inbox", "/inbox", "inbox", "emails mails messages answers reponses", "bi-envelope-paper-fill"),
    ("Write an email", "/inbox/compose", "inbox", "send email envoyer ecrire compose", "bi-pencil-square"),
    ("Email templates and automatic emails", "/inbox/templates", "inbox", "modeles templates automatic", "bi-envelope-open"),
    ("What the assistant knows", "/inbox/knowledge", "inbox", "knowledge connaissances answers taught", "bi-lightbulb"),
    ("Payments", "/payments", "payments", "money argent paid paye unpaid impaye dt", "bi-cash-coin"),
    ("Reminders", "/reminders", "reminders", "rappels remind relance", "bi-alarm-fill"),
    ("New reminder", "/reminders#new", "reminders", "plan programmer rappel", "bi-alarm"),
    ("PDF documents: programs and any information for clients", "/programs/documents", "programs",
     "pdf document summary resume résumé brochure catalogue clients imprimer print tarifs price list", "bi-file-earmark-pdf-fill"),
    ("All programs in short (PDF)", "/programs/documents#summary", "programs", "summary resume résumé bref short pdf", "bi-lightning-charge"),
    ("Write any document as a PDF", "/programs/documents#write", "programs", "write ecrire document pdf information letter lettre", "bi-pencil-square"),
    ("Website (9antra.tn)", "/website", "website", "site feedback avis events offers videos", "bi-globe2"),
    ("How it is working", "/system", "system", "system status jobs travail etat", "bi-activity"),
    ("Your data: copies and safety", "/system#safety", "system", "backup sauvegarde copies security securite", "bi-shield-lock-fill"),
    ("Open it on your phone", "/system#phone", "system", "phone telephone mobile", "bi-phone"),
    ("Settings", "/settings", "settings", "parametres configuration", "bi-gear-fill"),
    ("Email address and its password", "/settings#email", "settings", "smtp gmail app password mot de passe", "bi-envelope-at"),
    ("Colours, menu side, day or night", "/settings#appearance", "settings", "design couleur color theme night nuit dark", "bi-palette"),
    ("Language of the pages", "/settings#language", "settings", "langue arabic arabe french francais english translate traduire", "bi-translate"),
    ("Translations of the pages", "/settings/translations", "settings", "translate traduction correct", "bi-translate"),
    ("Team and access", "/settings/team", "settings", "users utilisateurs accounts comptes roles permissions acces team equipe", "bi-person-lock"),
    ("All the data: change any value", "/data", "data", "database base donnees edit modifier values valeurs tables records", "bi-table"),
    ("Your account and password", "/account", None, "password mot de passe account compte devices", "bi-person-circle"),
    ("How the application works", "/guide", "dashboard", "help aide guide", "bi-signpost-split-fill"),
]


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9@.+]+", " ", unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()).split())


def _may(user, section: str | None) -> bool:
    if user is None or user.is_admin or section is None:
        return True
    return bool(getattr(user, "permissions", None)) and (user.permissions or {}).get(section) in ("view", "edit")


def find(db: Session, query: str, user=None, most: int = 6) -> list[dict]:
    """[{"group", "items": [{"title", "detail", "url", "icon"}]}], the best first."""
    words = [word for word in _fold(query).split() if len(word) >= 2][:5]
    if not words:
        return []
    groups = []
    pages = []
    for title, url, section, more, icon in PAGES:
        text = _fold(f"{title} {more}")
        score = sum(1 for word in words if word in text)
        if score and _may(user, section):
            pages.append((score, {"title": title, "detail": "", "url": url, "icon": icon}))
    if pages:
        groups.append({"group": "Pages and actions", "items": [item for _, item in sorted(pages, key=lambda pair: -pair[0])[:most]]})

    def like(*columns):
        return [or_(*[column.ilike(f"%{word}%") for column in columns]) for word in words]

    if _may(user, "candidates"):
        rows = db.query(Candidate).filter(*like(Candidate.full_name, Candidate.email, Candidate.phone, Candidate.university, Candidate.city)).limit(most).all()
        if rows:
            groups.append({"group": "Candidates", "items": [{"title": row.full_name or row.email, "detail": " · ".join(part for part in (row.email, row.phone, row.university) if part),
                                                             "url": f"/candidate/{row.id}", "icon": "bi-person"} for row in rows]})
    if _may(user, "programs"):
        rows = db.query(Program).filter(*like(Program.name, Program.description)).limit(most).all()
        if rows:
            groups.append({"group": "Programs", "items": [{"title": row.name, "detail": f"{row.price_online or 0:.0f} DT online", "url": f"/programs/{row.id}", "icon": "bi-journal-bookmark"} for row in rows]})
    if _may(user, "classes"):
        rows = db.query(ClassGroup).filter(*like(ClassGroup.name, ClassGroup.professor_name)).limit(most).all()
        if rows:
            groups.append({"group": "Classes", "items": [{"title": row.name, "detail": row.professor_name or "", "url": f"/classes/{row.id}", "icon": "bi-easel2"} for row in rows]})
        rows = db.query(Professor).filter(*like(Professor.name, Professor.specialties, Professor.email)).limit(most).all()
        if rows:
            groups.append({"group": "Professors", "items": [{"title": row.name, "detail": row.specialties or "", "url": "/classes/?tab=professors", "icon": "bi-person-video3"} for row in rows]})
    if _may(user, "reminders"):
        rows = db.query(AdminReminder).filter(*like(AdminReminder.subject, AdminReminder.audience_label)).limit(most).all()
        if rows:
            groups.append({"group": "Reminders", "items": [{"title": row.subject, "detail": row.audience_label, "url": "/reminders#planned", "icon": "bi-alarm"} for row in rows]})
    if _may(user, "inbox"):
        rows = db.query(CandidateEmail).filter(*like(CandidateEmail.subject, CandidateEmail.body)).order_by(CandidateEmail.received_at.desc()).limit(most).all()
        if rows:
            groups.append({"group": "Emails", "items": [{"title": row.subject or "(no subject)", "detail": " ".join((row.body or "").split())[:90],
                                                         "url": f"/inbox?candidate={row.candidate_id}" if row.candidate_id else "/inbox", "icon": "bi-envelope"} for row in rows]})
    if _may(user, "data") and (user is None or user.is_admin):
        from app.services import data_editor

        tables = [(key, spec) for key, spec in data_editor.TABLES.items() if any(word in _fold(f"{spec[0]} {key}") for word in words)]
        if tables:
            groups.append({"group": "All the data", "items": [{"title": spec[0], "detail": "every value, to change", "url": f"/data/{key}", "icon": spec[2]} for key, spec in tables[:most]]})
    return groups
