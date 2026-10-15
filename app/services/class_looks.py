"""How a class is recognised at a glance: a symbol and a colour.

Each class has one of each. Until the admin chooses them, they are picked
from the class's program (a phone for mobile development, a lock for
security…) and from its name, so two classes rarely look alike.
"""
from __future__ import annotations

import re
import zlib

from app.services import theme

# What the admin can choose from.
SYMBOLS = [
    "🚀", "💻", "📱", "🌐", "🐍", "⚛️", "🤖", "🧠", "🔒", "🛡️", "📊", "📈",
    "☁️", "⚙️", "🐳", "🛰️", "🔌", "🎨", "📣", "🎯", "🧪", "📚", "🎓", "💡",
    "🧩", "🏆", "⭐", "🔥", "🌱", "🎬", "🗣️", "💼",
]
COLOURS = ["#a42c67", "#1d4ed8", "#0f766e", "#b45309", "#6d28d9", "#be123c", "#15803d", "#0369a1", "#9333ea", "#334155"]

# The symbol a program suggests, most telling first.
_BY_SUBJECT = [
    (r"flutter|mobile|android|ios", "📱"), (r"react", "⚛️"), (r"python|django", "🐍"),
    (r"devops|docker|kubernetes", "🐳"), (r"cyber|s[ée]curit", "🔒"),
    (r"\bai\b|artificial|intelligence artificielle|chatbot|machine learning", "🤖"),
    (r"\bbi\b|business intelligence|data|analytics", "📊"), (r"iot|internet of things|embarqu|arduino", "🛰️"),
    (r"marketing|communication", "📣"), (r"bootcamp", "🚀"), (r"cloud|aws|azure", "☁️"),
    (r"design|ux|ui\b|graphi", "🎨"), (r"web|full ?stack|angular|spring|node|javascript", "🌐"),
    (r"english|anglais|fran[çc]ais|langue", "🗣️"), (r"gestion|management|business", "💼"),
]


def _from_name(text: str, choices: list[str]) -> str:
    """Always the same choice for the same name, and well spread between names."""
    return choices[zlib.crc32(text.encode("utf-8")) % len(choices)]


def clean_symbol(value: str | None) -> str | None:
    """A symbol of the list, or None to let the system choose."""
    value = (value or "").strip()
    return value if value in SYMBOLS else None


def clean_colour(value: str | None) -> str | None:
    return theme.clean(value)


def look(class_group) -> dict:
    """What pages need to draw a class: its symbol, its colour and a soft tint of it."""
    program = (class_group.program.name if class_group.program else "") or ""
    symbol = clean_symbol(class_group.symbol)
    if symbol is None:
        lowered = program.lower()
        symbol = next((mark for pattern, mark in _BY_SUBJECT if re.search(pattern, lowered)), None) \
            or _from_name(class_group.name or "class", SYMBOLS)
    colour = clean_colour(class_group.color) or _from_name(class_group.name or "class", COLOURS)
    colour = theme.readable(colour)
    hue, _, saturation = theme._hls(colour)
    return {
        "symbol": symbol,
        "colour": colour,
        "soft": theme._hex(hue, 0.955, min(saturation, 0.6)),
        "chosen_symbol": clean_symbol(class_group.symbol),
        "chosen_colour": clean_colour(class_group.color),
    }
