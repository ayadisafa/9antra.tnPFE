"""Yasmine's answers, short first: what a long answer says in a line or two, the whole answer one tap away ("More").

The short version is cut from the answer itself, never rewritten: its first sentences up to SHORT letters, or for a
list, its first line and the first names in it with how many more ("5 candidats doivent payer : Ali, Sami, Ines… +2").
An answer already short has no short version (nothing is shown twice).
"""
from __future__ import annotations

import re

LONG = 260                   # an answer longer than this gets a short version
SHORT = 200                  # about how long the short version is
_ITEM = re.compile(r"^\s*(?:[-•*·▪◦]|\d+[.)])\s+")
_SENTENCE = re.compile(r"(?<=[.!?؟])\s+")


def _name_of(item: str) -> str:
    """What a list line is about: before its first ":" or " — ", briefly."""
    item = _ITEM.sub("", item).strip()
    item = re.split(r"\s*(?::|—|–| - |\(|·)\s*", item, maxsplit=1)[0].strip()
    return item[:42] + ("…" if len(item) > 42 else "")


def _cut(text: str, most: int) -> str:
    if len(text) <= most:
        return text
    cut = text[:most].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def head(reply: str | None) -> str | None:
    """The first line of an answer that is not a list line, cut to its first sentence: what shows above its cards."""
    lines = [line.strip() for line in (reply or "").split("\n") if line.strip() and not _ITEM.match(line)]
    if not lines:
        return None
    return _cut(_SENTENCE.split(lines[0])[0], SHORT)


def shorten(reply: str | None) -> str | None:
    """The short version of a long answer, or None when it is short already."""
    text = (reply or "").strip()
    if len(text) <= LONG:
        return None
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    items = [line for line in lines if _ITEM.match(line)]
    if len(items) >= 3:
        head = next((line for line in lines if not _ITEM.match(line)), "")
        head = _cut(_SENTENCE.split(head)[0], 120) if head else ""
        names = [_name_of(item) for item in items[:3]]
        more = len(items) - len(names)
        short = (head.rstrip(": ") + " : " if head and head.endswith(":") else (head + " " if head else "")) + ", ".join(names)
        short += f"… +{more}" if more > 0 else "."
    else:
        kept = ""
        for sentence in _SENTENCE.split(" ".join(lines)):
            if kept and len(kept) + len(sentence) + 1 > SHORT:
                break
            kept = f"{kept} {sentence}".strip()
        short = _cut(kept, SHORT + 40)
    return short if len(short) < len(text) * 0.8 else None
