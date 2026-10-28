"""What a program's document says of the program (a syllabus, a brochure, a course outline), read by fixed rules:
its week-by-week plan (“Semaine 3 : Docker”, “Module 2 - Les bases”, “Week 4: APIs” and the points under each),
a description, the duration and the hours, and the tools it speaks of. Nothing is invented: what is not written is
not proposed, and the admin chooses what to use.
"""
from __future__ import annotations

import re

_HEAD = re.compile(
    r"^\s*(?:(?:semaine|week|module|jour|day|s[ée]ance|session|chapitre|chapter|partie|part|unit[ée]?|le[çc]on|lesson)\s*(?:n\s*[°o]\s*)?(\d{1,2})"
    r"|(\d{1,2})\s*(?:e|[èe]me|er|th|st|nd|rd)?\s+(?:semaine|week))\s*(?:[:.\-–—)|]\s*|\s+)(.*)$", re.I)
_BULLET = re.compile(r"^\s*(?:[-•●▪◦*✓✔➤►▸–]|\d{1,2}[.)]|[a-z][.)])\s+(.+)$", re.I)
_DESCRIBE = re.compile(r"^\s*(?:description|pr[ée]sentation|objectifs?(?:\s+de\s+la\s+formation)?|objectives?|about|[àa]\s+propos|overview|"
                       r"la\s+formation|the\s+program(?:me)?)\s*[:\-–]?\s*(.*)$", re.I)
_WEEKS = re.compile(r"(\d{1,3})\s*(?:semaines|weeks)\b", re.I)
_MONTHS = re.compile(r"(\d{1,2})\s*(?:mois|months?)\b", re.I)
_HOURS_WEEK = re.compile(r"(\d{1,2})\s*(?:h|heures|hours)\s*(?:par|per|/)\s*(?:semaine|week)", re.I)
_PREREQ = re.compile(r"^\s*(?:pr[ée]\s*-?\s*requis|prerequisites?|pr[ée]alables?)\s*[:\-–]?\s*(.*)$", re.I)


def _clean(text: str, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip(" :;,.-–—|•*")
    return text[:limit].rstrip()


def plan_in(text: str) -> list[dict]:
    """[{"number", "title", "points": [...]}]: the plan the document writes, week (or module) by week, in its order.
    Empty when it does not write one (fewer than two)."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    found: list[dict] = []
    current = None
    for line in lines:
        head = _HEAD.match(line)
        if head:
            number = int(head.group(1) or head.group(2))
            title = _clean(head.group(3))
            if current is not None and number == current["number"]:
                if not current["title"] and title:
                    current["title"] = title
                continue
            if any(item["number"] == number for item in found):
                continue                    # a table of contents named the same weeks first: keep the first ones
            current = {"number": number, "title": title, "points": []}
            found.append(current)
            continue
        if current is None:
            continue
        bullet = _BULLET.match(line)
        point = bullet.group(1) if bullet else (line if len(line) <= 160 else None)
        if not point:
            continue
        point = _clean(point, 160)
        if len(point) < 3:
            continue
        if not current["title"]:
            current["title"] = point
        elif len(current["points"]) < 6 and point.lower() != current["title"].lower():
            current["points"].append(point)
    weeks = [item for item in found if item["title"]]
    weeks.sort(key=lambda item: item["number"])
    return weeks if len(weeks) >= 2 else []


def description_in(text: str) -> str:
    """The document's own presentation of the program: after “Description”, “Objectifs”, “About”…, else its first long
    paragraph that is not a heading of the plan."""
    lines = [line.strip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        found = _DESCRIBE.match(line)
        if not found:
            continue
        words = [found.group(1)] if found.group(1) else []
        for following in lines[index + 1:index + 6]:
            if not following or _HEAD.match(following) or _DESCRIBE.match(following):
                break
            words.append(following)
        sentence = _clean(" ".join(words), 600)
        if len(sentence.split()) >= 8:
            return sentence if sentence[-1] in ".!?" else sentence + "."
    for line in lines:
        if len(line.split()) >= 15 and not _HEAD.match(line) and not _BULLET.match(line):
            sentence = _clean(line, 600)
            return sentence if sentence[-1] in ".!?" else sentence + "."
    return ""


def facts_in(text: str) -> dict:
    """{"days", "hours_per_week", "prerequisites", "tools"}: what the document says of the program's length and needs."""
    from app.services.program_match import SUBJECT_WORDS

    days = None
    weeks = _WEEKS.search(text or "")
    months = _MONTHS.search(text or "")
    if weeks and 1 <= int(weeks.group(1)) <= 104:
        days = int(weeks.group(1)) * 7
    elif months and 1 <= int(months.group(1)) <= 24:
        days = int(months.group(1)) * 30
    hours = _HOURS_WEEK.search(text or "")
    prerequisites = ""
    for line in (text or "").splitlines():
        found = _PREREQ.match(line)
        if found and found.group(1):
            prerequisites = _clean(found.group(1), 300)
            break
    plain = " " + re.sub(r"[^a-z0-9+#]+", " ", (text or "").lower()) + " "
    vocabulary = {word for words in SUBJECT_WORDS.values() for word in words.split() if len(word) >= 3}
    tools = [word for word in sorted(vocabulary) if f" {word} " in plain]
    counted = sorted(tools, key=lambda word: -plain.count(f" {word} "))[:14]
    return {"days": days, "hours_per_week": int(hours.group(1)) if hours and 1 <= int(hours.group(1)) <= 60 else None,
            "prerequisites": prerequisites, "tools": counted}


def read(text: str) -> dict:
    """Everything above at once: {"plan", "description", "days", "hours_per_week", "prerequisites", "tools"}."""
    return {"plan": plan_in(text), "description": description_in(text), **facts_in(text)}
