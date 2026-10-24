"""Reading the programs catalogue (the PDF "Planning Courses") on this computer, without any outside service.

The catalogue has one "COURSE PLAN" page per program (its name in brackets, the prices, the duration, the hours, the
session times, the technologies), followed by "PLANNING ..." pages listing, week by week, what is taught. Its text
comes letter-spaced ("P L A N N I N G"): the spaces are put back first. Every value is read by fixed rules; what
was read is shown to the admin, who confirms each program before it is saved (the import page).
"""
from __future__ import annotations

import io
import re

_LABELS = re.compile(r"^(course plan|class session time|supervision time|supervision|p i n|pin|reserve your place|registration|"
                     r"\d+ month|week \d+|planning\b|saturday|sunday|monday|tuesday|wednesday|thursday|friday|until|\(online|"
                     r"face to face|online course|duration|number of hours|training|level \d|month \d|frontend|backend|\.+$)", re.I)
_NOT_NAMES = re.compile(r"(online|onsite|check pages|\.\.\.|,|^\d|month)", re.I)
_MONEY = r"\D{0,6}?(\d{2,5})\s*DT"


def despace(text: str) -> str:
    """"P L A N N I N G  C O U R S E S" -> "PLANNING COURSES": letters spaced one by one, words by two spaces."""
    lines = []
    for line in (text or "").splitlines():
        if re.search(r"(\S ){3,}\S", line):
            line = re.sub(r"  +", "\0", line).replace(" ", "").replace("\0", " ")
        lines.append(line.rstrip())
    return "\n".join(lines)


def pages_of(data: bytes) -> list[str]:
    from pypdf import PdfReader

    return [despace(page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages]


def _number(pattern: str, text: str) -> int | None:
    found = re.search(pattern, text, re.I)
    return int(found.group(1)) if found else None


def _name_of(page: str) -> str | None:
    """The program's name: the line written alone in brackets that is not "(Online)", "(1 month)", "(Check pages…)"."""
    names = []
    for line in page.splitlines():
        found = re.fullmatch(r"\s*\(\s*(.+?)\s*\)\s*", line)
        if found and not _NOT_NAMES.search(found.group(1)) and len(found.group(1)) >= 2 and not found.group(1)[:1].islower():
            names.append(found.group(1).replace("“", "").replace("”", "").replace('"', "").strip())
    return max(names, key=len) if names else None


def _session_time(page: str) -> str:
    found = re.search(r"(\w+day)\s+from\s+(\d{1,2}[:h]\d{2})\s*\n?\s*until\s+(\d{1,2}[:h]\d{2})", page, re.I)
    return f"{found.group(1).capitalize()} {found.group(2)} - {found.group(3)}" if found else ""


def _technologies(page: str) -> list[str]:
    found = []
    for line in page.splitlines():
        line = line.strip()
        if not line or len(line) > 45 or ":" in line or line.startswith("(") or _LABELS.match(line) or "DT/Month" in line or "http" in line:
            continue
        if re.search(r"[A-Za-z]", line) and line not in found:
            found.append(line)
    return found[:20]


def _levels_named(page: str) -> list[str]:
    """"Level 1 ( 1 month)Level 2 ( 1 month)", "Frontend (1 month)Backend ( 1 month)", "Month 1Month 2"."""
    for line in page.splitlines():
        parts = re.findall(r"((?:level\s*\d|month\s*\d|frontend|backend)[^()]*?)(?:\(\s*\d\s*months?\s*\)|(?=month\s*\d)|$)", line, re.I)
        parts = [part.strip() for part in parts if part.strip()]
        if len(parts) >= 2:
            return parts
    return []


def _bullets(page: str) -> tuple[str, list[str], list[int]]:
    """(level title, the lines taught, the week numbers) of a "PLANNING …" page."""
    title_line = next((line for line in page.splitlines() if re.match(r"^\s*PLANNING\s+\S", line)), "")
    title = re.sub(r"^\s*PLANNING\s+", "", title_line).strip()
    weeks = [int(number) for number in re.findall(r"^\s*week\s+(\d+)\s*$", page, re.I | re.M)]
    body = page
    marker = body.find("completion.")
    if marker != -1:                         # a hidden copy of another page comes first: what follows is this page
        body = body[marker + len("completion."):]
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if not line or _LABELS.match(line) or line.lower().startswith(("planning", "1 month", "reserve", "registration")):
            continue
        if lines and not lines[-1].endswith(".") and (line[:1].islower() or len(line) < 28
                                                       or lines[-1].endswith((",", ":", "(", "and", "for", "with"))):
            lines[-1] = f"{lines[-1]} {line}"       # a sentence cut in two by the page
        else:
            lines.append(line)
    return title, lines, sorted(set(weeks))


def _split(lines: list[str], count: int) -> list[list[str]]:
    count = max(1, min(count, len(lines))) if lines else 0
    if not count:
        return []
    size, extra = divmod(len(lines), count)
    parts, start = [], 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        parts.append(lines[start:end])
        start = end
    return parts


def read(data: bytes) -> list[dict]:
    """The programs of the catalogue, in the shape the import page shows and saves."""
    programs, current = [], None
    for page in pages_of(data):
        if "COURSE PLAN" in page:
            name = _name_of(page)
            if not name:
                current = None
                continue
            months = _number(r"Duration\s*:\s*(\d+)\s*months?", page)
            technologies = _technologies(page)
            current = {
                "name": name,
                "description": ("Technologies : " + ", ".join(technologies) + ".") if technologies else "",
                "total_duration_days": (months or 2) * 30,
                "price_onsite": float(_number(r"Face to face course\s*:" + _MONEY, page) or 0),
                "price_online": float(_number(r"Online course\s*:" + _MONEY, page) or 0),
                "hours_per_week": _number(r"Number of hours per week\s*:\s*(\d+)", page) or 0,
                "training_hours": _number(r"Training\s*:\s*(\d+)", page) or 0,
                "supervision_hours": _number(r"Supervision\s*:\s*(\d+)", page) or 0,
                "class_session_time": _session_time(page),
                "levels": [], "_named_levels": _levels_named(page), "_technologies": technologies,
            }
            programs.append(current)
        elif current is not None and re.search(r"^\s*PLANNING\s+\S", page, re.M):
            title, lines, weeks = _bullets(page)
            if not lines:
                continue
            first_week = weeks[0] if weeks else sum(len(level["weeks"]) for level in current["levels"]) + 1
            level_weeks = []
            for offset, part in enumerate(_split(lines, len(weeks) or 4)):
                level_weeks.append({"week_number": first_week + offset, "topic_title": part[0][:200],
                                    "learning_objectives": " ".join(part[1:])[:1500]})
            current["levels"].append({"level_order": len(current["levels"]) + 1, "title": title or f"Level {len(current['levels']) + 1}",
                                      "description": "", "hours_per_week": current["hours_per_week"],
                                      "class_session_time": current["class_session_time"], "weeks": level_weeks})
    for program in programs:
        if not program["levels"] and program["_technologies"]:
            # No week-by-week page in the catalogue: what the program page lists becomes its weeks, to complete later.
            program["levels"] = [{"level_order": 1, "title": "Level 1", "description": "", "hours_per_week": program["hours_per_week"],
                                  "class_session_time": program["class_session_time"],
                                  "weeks": [{"week_number": index + 1, "topic_title": item, "learning_objectives": ""}
                                            for index, item in enumerate(program["_technologies"])]}]
        program.pop("_named_levels", None)
        program.pop("_technologies", None)
    return programs
