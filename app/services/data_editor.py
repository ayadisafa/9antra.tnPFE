"""Every value of the application, readable and changeable by the admin.

One page per kind of record (candidates, registrations, payments, programs,
weeks, classes, sessions, professors, emails, reminders, the website's records,
the settings, …): search, open a record, change any field, add one, remove one.
The forms are built from the database itself, so a new column appears here by
itself. Passwords, session keys and computed vectors are never shown. Each
change is written in the edit log with who made it and what it was before.
"""
from __future__ import annotations

import enum
import json
import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, Integer, JSON, LargeBinary, Numeric, String, Text, Time, inspect, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (AdminReminder, Attendance, Candidate, ClassGroup, ClassSession, EmailTemplate, KnowledgeEntry, MarketOffer, Notification,
                        Payment, Professor, Program, Registration, SystemSettings, UiTranslation, WebsiteItem)
from app.models.edit_log import EditLog
from app.models.program import ProgramLevel, WeeklyCurriculum

# key -> (name shown, model, icon, the columns shown in the list, the field that names a record)
TABLES: dict[str, tuple] = {
    "candidates": ("Candidates", Candidate, "bi-people-fill", ("full_name", "email", "phone", "university", "city"), "full_name"),
    "registrations": ("Registrations", Registration, "bi-journal-check", ("candidate_id", "program_name", "delivery_mode", "total_amount", "paid_amount", "payment_status", "status"), "program_name"),
    "payments": ("Payments", Payment, "bi-cash-coin", ("registration_id", "amount", "payment_method", "payment_date"), "amount"),
    "programs": ("Programs", Program, "bi-journal-bookmark-fill", ("name", "price_online", "price_onsite", "hours_per_week", "total_duration_days"), "name"),
    "levels": ("Program levels", ProgramLevel, "bi-layers", ("program_id", "level_order", "title"), "title"),
    "weeks": ("Program weeks", WeeklyCurriculum, "bi-calendar-week", ("level_id", "week_number", "topic_title"), "topic_title"),
    "classes": ("Classes", ClassGroup, "bi-easel2-fill", ("name", "program_id", "professor_name", "status", "start_date"), "name"),
    "sessions": ("Sessions", ClassSession, "bi-calendar-event", ("class_group_id", "week_number", "session_date", "start_time", "topic"), "topic"),
    "attendance": ("Presence", Attendance, "bi-person-check", ("class_group_id", "candidate_id", "status", "marked_at"), "status"),
    "professors": ("Professors", Professor, "bi-person-video3", ("name", "email", "phone", "specialties", "is_available"), "name"),
    "email_templates": ("Email templates", EmailTemplate, "bi-envelope-paper", ("name", "subject", "style"), "name"),
    "knowledge": ("What the assistant knows", KnowledgeEntry, "bi-lightbulb", ("title", "kind", "origin", "is_active"), "title"),
    "reminders": ("Your reminders", AdminReminder, "bi-alarm", ("subject", "audience_label", "send_at", "status"), "subject"),
    "website": ("Records from the website", WebsiteItem, "bi-globe2", ("kind", "title", "category", "gone_at"), "title"),
    "market": ("Job offers", MarketOffer, "bi-briefcase", ("title", "company", "source", "posted_at"), "title"),
    "notifications": ("Notifications", Notification, "bi-bell", ("title", "category", "is_read", "created_at"), "title"),
    "translations": ("Translations", UiTranslation, "bi-translate", ("lang", "source", "text", "origin"), "source"),
    "settings": ("Settings (every value)", SystemSettings, "bi-sliders", ("platform_name", "app_name", "admin_email"), "app_name"),
}
_HIDDEN = {"password_hash", "token_hash", "embedding", "fingerprint", "source_hash"}
PAGE = 50


def table(key: str) -> tuple | None:
    return TABLES.get(key)


def _key_of(model):
    return inspect(model).primary_key[0]


def columns(model) -> list[dict]:
    """The fields of a kind of record, as a form needs them."""
    found = []
    for column in inspect(model).columns:
        if column.key in _HIDDEN or column.key.endswith("_hash") or isinstance(column.type, LargeBinary):
            continue
        kind = column.type
        if isinstance(kind, Enum):
            choices = list(kind.enums)          # the names the database keeps (PAID, not paid)
            widget = "choice"
        elif isinstance(kind, Boolean):
            widget, choices = "yesno", []
        elif isinstance(kind, DateTime):
            widget, choices = "datetime", []
        elif isinstance(kind, Date):
            widget, choices = "date", []
        elif isinstance(kind, Time):
            widget, choices = "time", []
        elif isinstance(kind, Integer):
            widget, choices = "integer", []
        elif isinstance(kind, (Float, Numeric)):
            widget, choices = "number", []
        elif isinstance(kind, JSON):
            widget, choices = "json", []
        elif isinstance(kind, Text):
            widget, choices = "text", []
        else:
            widget, choices = ("longtext" if (getattr(kind, "length", None) or 0) > 300 else "line"), []
        target = next((key.column.table.name for key in column.foreign_keys), None)
        found.append({"name": column.key, "label": column.key.replace("_", " ").capitalize(), "widget": widget, "choices": choices,
                      "enum": getattr(kind, "enum_class", None) if widget == "choice" else None,
                      "required": not column.nullable and column.default is None and not column.primary_key, "key": column.primary_key,
                      "readonly": column.primary_key or column.key in ("created_at",), "target": target})
    return found


def _label(row) -> str:
    for name in ("full_name", "name", "title", "subject", "topic_title", "program_name", "email", "source"):
        value = getattr(row, name, None)
        if value:
            return str(value)[:80]
    return str(getattr(row, _key_of(type(row)).key))[:12]


_BY_TABLE = {spec[1].__tablename__: spec[1] for spec in TABLES.values()}


def choices_for(db: Session, target: str | None) -> list[tuple[str, str]] | None:
    """The records a field may point to, by their names (when there are not too many)."""
    model = _BY_TABLE.get(target or "")
    if model is None or db.query(model).count() > 500:
        return None
    key = _key_of(model).key
    return sorted(((str(getattr(row, key)), _label(row)) for row in db.query(model).all()), key=lambda pair: pair[1].lower())


def shown(value) -> str:
    if value is None:
        return ""
    if isinstance(value, enum.Enum):
        return str(value.name)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (date, time)):
        return value.isoformat()[:5] if isinstance(value, time) else value.isoformat()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def form_value(value, widget: str) -> str:
    """A value as the form shows it."""
    if value is None:
        return ""
    if widget == "datetime" and isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    if widget == "time" and isinstance(value, time):
        return value.strftime("%H:%M")
    if widget == "json":
        return json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
    return shown(value)


def listing(db: Session, key: str, search: str = "", page: int = 1) -> dict:
    name, model, icon, shown_columns, _ = TABLES[key]
    query = db.query(model)
    words = [word for word in (search or "").split() if word]
    if words:
        text_columns = [column for column in inspect(model).columns if isinstance(column.type, (String, Text)) and column.key not in _HIDDEN]
        for word in words[:4]:
            query = query.filter(or_(*[column.ilike(f"%{word}%") for column in text_columns]))
    total = query.count()
    order = next((getattr(model, field) for field in ("created_at", "registration_date", "payment_date", "added_at", "send_at", "marked_at") if hasattr(model, field)), None)
    if order is not None:
        query = query.order_by(order.desc())
    rows = query.offset((max(1, page) - 1) * PAGE).limit(PAGE).all()
    fields = {field["name"]: field for field in columns(model)}
    labels: dict[str, dict[str, str]] = {}
    for column in shown_columns:
        target = (fields.get(column) or {}).get("target")
        if target:
            labels[column] = dict(choices_for(db, target) or [])
    key_name = _key_of(model).key
    return {
        "key": key, "name": name, "icon": icon, "total": total, "page": max(1, page), "pages": max(1, -(-total // PAGE)), "search": search,
        "columns": [fields[column]["label"] if column in fields else column for column in shown_columns],
        "rows": [{"id": str(getattr(row, key_name)), "label": _label(row),
                  "cells": [labels.get(column, {}).get(shown(getattr(row, column, None)), shown(getattr(row, column, None)))[:90] for column in shown_columns]}
                 for row in rows],
    }


def export(db: Session, key: str, search: str = "") -> tuple[str, bytes]:
    """(file name, content) of every record of a kind, for Excel: one column per field, linked records by their names."""
    import csv
    import io

    name, model, *_ = TABLES[key]
    query = db.query(model)
    words = [word for word in (search or "").split() if word]
    if words:
        text_columns = [column for column in inspect(model).columns if isinstance(column.type, (String, Text)) and column.key not in _HIDDEN]
        for word in words[:4]:
            query = query.filter(or_(*[column.ilike(f"%{word}%") for column in text_columns]))
    order = next((getattr(model, field) for field in ("created_at", "registration_date", "payment_date", "added_at", "send_at", "marked_at") if hasattr(model, field)), None)
    rows = (query.order_by(order.desc()) if order is not None else query).all()
    fields = [field for field in columns(model) if field["widget"] != "json"]
    names = {field["name"]: dict(choices_for(db, field["target"]) or []) for field in fields if field["target"]}
    buffer = io.StringIO()
    # Semicolons and a byte-order mark: what Excel opens directly with French settings, accents included.
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([field["label"] for field in fields])
    for row in rows:
        writer.writerow([names.get(field["name"], {}).get(shown(getattr(row, field["name"], None)), shown(getattr(row, field["name"], None)))
                         for field in fields])
    return f"{name}.csv", ("﻿" + buffer.getvalue()).encode("utf-8")


def get(db: Session, key: str, row_id: str):
    model = TABLES[key][1]
    return db.get(model, int(row_id) if isinstance(_key_of(model).type, Integer) and str(row_id).isdigit() else row_id)


def _convert(field: dict, raw: str | None):
    raw = (raw or "").strip() if field["widget"] not in ("text", "longtext", "json") else (raw or "")
    if raw == "" and field["widget"] != "yesno":
        if field["required"]:
            raise ValueError(f"“{field['label']}” cannot be empty.")
        return None
    widget = field["widget"]
    try:
        if widget == "yesno":
            return raw in ("yes", "true", "on", "1")
        if widget == "integer":
            return int(raw)
        if widget == "number":
            return float(raw.replace(",", "."))
        if widget == "datetime":
            return datetime.fromisoformat(raw.replace(" ", "T"))
        if widget == "date":
            return date.fromisoformat(raw)
        if widget == "time":
            return time.fromisoformat(raw)
        if widget == "json":
            return json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"“{field['label']}” is not written the right way ({raw[:30]}).") from exc
    if widget == "choice":
        if raw not in field["choices"]:
            raise ValueError(f"“{field['label']}” must be one of: {', '.join(map(str, field['choices']))}.")
        return field["enum"][raw] if field.get("enum") is not None else raw
    return raw


def save(db: Session, key: str, row, values: dict, who: str) -> tuple[object | None, str | None]:
    """Write the form into a record (a new one when `row` is None). Returns (record, problem)."""
    model = TABLES[key][1]
    fields = [field for field in columns(model) if not field["readonly"]]
    changes, fresh = {}, row is None
    try:
        converted = {field["name"]: _convert(field, values.get(field["name"])) for field in fields if field["name"] in values or field["widget"] == "yesno"}
    except ValueError as problem:
        return None, str(problem)
    if fresh:
        row = model()
        key_column = _key_of(model)
        if isinstance(key_column.type, String) and getattr(row, key_column.key, None) is None:
            setattr(row, key_column.key, str(uuid.uuid4()))
        db.add(row)
    for name, value in converted.items():
        before = getattr(row, name, None)
        if shown(before) != shown(value):
            changes[name] = [shown(before)[:300], shown(value)[:300]]
            setattr(row, name, value)
    if not changes and not fresh:
        return row, None
    try:
        db.flush()
        db.add(EditLog(table=key, row_id=str(getattr(row, _key_of(model).key)), action="add" if fresh else "edit", who=who,
                       changes=json.dumps(changes, ensure_ascii=False)))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        return None, "The database refused it: a value must be unique or point to an existing record (" + str(exc.orig)[:120] + ")."
    except Exception as exc:
        db.rollback()
        return None, f"It could not be saved: {exc.__class__.__name__}."
    return row, None


def remove(db: Session, key: str, row, who: str) -> str | None:
    model = TABLES[key][1]
    row_id, label = str(getattr(row, _key_of(model).key)), _label(row)
    try:
        db.delete(row)
        db.add(EditLog(table=key, row_id=row_id, action="delete", who=who, changes=json.dumps({"record": label}, ensure_ascii=False)))
        db.commit()
    except IntegrityError:
        db.rollback()
        return "Other records still point to this one (for example the registrations of a candidate). Change or remove them first."
    return None


def history(db: Session, key: str, row_id: str, limit: int = 20) -> list[dict]:
    rows = (db.query(EditLog).filter(EditLog.table == key, EditLog.row_id == str(row_id)).order_by(EditLog.at.desc()).limit(limit).all())
    return [{"at": row.at, "who": row.who, "action": row.action, "changes": json.loads(row.changes or "{}")} for row in rows]


def counts(db: Session) -> list[dict]:
    return [{"key": key, "name": name, "icon": icon, "count": db.query(model).count()} for key, (name, model, icon, _, _) in TABLES.items()]
