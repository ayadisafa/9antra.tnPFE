"""Pictures for social media, drawn here (Pillow) from an idea of social_content: a post (1080 × 1080) or a story
(1080 × 1920), in the colours of the logo (berry, gold, blue), in three looks. Arabic is shaped and written right to
left. A picture is made of pieces (the background, then each block), so the videos (social_video) can bring the pieces
in one after the other.
"""
from __future__ import annotations

import io
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZES = {"post": (1080, 1080), "story": (1080, 1920)}
LOOKS = {"berry": "Berry", "light": "Light", "night": "Night"}
BERRY, PLUM, GOLD, BLUE, INK, CREAM = "#b0306a", "#5a1440", "#f9b555", "#1aa3dc", "#241a38", "#fbf7f2"
_FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
_ARABIC = re.compile(r"[؀-ۿ]")
_cache: dict = {}


def _font(size: int, weight: str = "regular", text: str = "") -> ImageFont.FreeTypeFont:
    if weight == "black" and _ARABIC.search(text or ""):
        weight = "bold"                   # Segoe UI Black has no Arabic letters; Segoe UI Bold does
    names = {"black": ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"], "bold": ["segoeuib.ttf", "arialbd.ttf"],
             "semi": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], "regular": ["segoeui.ttf", "arial.ttf"]}[weight]
    key = (size, weight)
    if key not in _cache:
        for name in names:
            path = _FONTS / name
            if path.exists():
                _cache[key] = ImageFont.truetype(str(path), size)
                break
        else:
            _cache[key] = ImageFont.load_default(size)
    return _cache[key]


def _shown(text: str) -> str:
    """Arabic joined and in reading order (Pillow draws letters one by one, left to right)."""
    if not _ARABIC.search(text or ""):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _rtl(text: str) -> bool:
    return bool(_ARABIC.search(text or ""))


def wrap(text: str, font, width: int, most: int = 99) -> list[str]:
    lines, line = [], ""
    for word in (text or "").split():
        trial = f"{line} {word}".strip()
        if font.getlength(_shown(trial)) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > most:
        lines = lines[:most]
        while lines[-1] and font.getlength(_shown(lines[-1] + "…")) > width:
            lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
        lines[-1] += "…"
    return lines


@dataclass
class Piece:
    """One block of a picture: its image, where it goes, and when it comes in a video (its step)."""
    image: Image.Image
    x: int
    y: int
    step: int = 0
    number: dict | None = None      # a figure that counts up in a video: {"value", "suffix", "size", "colour", "box"}


@dataclass
class Picture:
    size: tuple[int, int]
    background: Image.Image
    pieces: list[Piece] = field(default_factory=list)

    def flat(self) -> Image.Image:
        canvas = self.background.copy()
        for piece in self.pieces:
            canvas.alpha_composite(piece.image, (piece.x, piece.y))
        return canvas.convert("RGB")


def palette(look: str, accent: str | None = None) -> dict:
    berry = accent or BERRY
    if look == "light":
        return {"top": CREAM, "bottom": "#f3e7f1", "text": INK, "soft": "#5b5670", "accent": berry, "gold": "#e39a1f", "blue": BLUE,
                "card": (255, 255, 255, 235), "card_line": (176, 48, 106, 60), "chip_text": "#ffffff", "chip": berry}
    if look == "night":
        return {"top": "#1b1330", "bottom": "#2e1a4c", "text": "#ffffff", "soft": "#c9bfe0", "accent": GOLD, "gold": GOLD, "blue": "#55c4f0",
                "card": (255, 255, 255, 26), "card_line": (249, 181, 85, 90), "chip_text": INK, "chip": GOLD}
    return {"top": berry, "bottom": PLUM, "text": "#ffffff", "soft": "#f6dbe8", "accent": GOLD, "gold": GOLD, "blue": "#7fd3f5",
            "card": (255, 255, 255, 34), "card_line": (255, 255, 255, 70), "chip_text": INK, "chip": GOLD}


def _rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return tuple(int(colour[index:index + 2], 16) for index in (0, 2, 4))


def background(size: tuple[int, int], look: str, accent: str | None = None, seed: int = 0) -> Image.Image:
    """A soft gradient, two blurred lights in the logo's gold and blue, a dotted corner and a thin ring."""
    colours = palette(look, accent)
    width, height = size
    top, bottom = _rgb(colours["top"]), _rgb(colours["bottom"])
    gradient = Image.new("RGBA", (1, 256))
    for index in range(256):
        share = index / 255
        gradient.putpixel((0, index), tuple(int(top[c] + (bottom[c] - top[c]) * share) for c in range(3)) + (255,))
    canvas = gradient.resize((width, height), Image.BILINEAR)
    lights = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lights)
    shift = (seed * 97) % 160
    draw.ellipse((width - 420 + shift // 4, -260, width + 260, 420 - shift // 3), fill=_rgb(colours["gold"]) + (120,))
    draw.ellipse((-300, height - 520 - shift, 380, height + 160 - shift), fill=_rgb(colours["blue"]) + (95,))
    canvas.alpha_composite(lights.filter(ImageFilter.GaussianBlur(110)))
    marks = ImageDraw.Draw(canvas)
    dot = _rgb(colours["text"]) + (46,)
    for row in range(4):                      # a dotted corner, top right, where no text goes
        for column in range(6):
            x, y = width - 260 + column * 34, 54 + row * 34
            marks.ellipse((x, y, x + 6, y + 6), fill=dot)
    ring = _rgb(colours["accent"]) + (55,)
    marks.ellipse((-330, height // 2 - 210, 90, height // 2 + 210), outline=ring, width=4)     # mostly outside, on the left edge
    return canvas


def _card(size: tuple[int, int], colours: dict, radius: int = 36, line: bool = True) -> Image.Image:
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=colours["card"],
                           outline=colours["card_line"] if line else None, width=2)
    return card


def _text_block(text: str, font, width: int, fill, *, spacing: float = 1.22, most: int = 99, align: str = "left") -> Image.Image:
    lines = wrap(text, font, width, most)
    ascent, descent = font.getmetrics()
    step = int((ascent + descent) * spacing)
    block = Image.new("RGBA", (width, max(step * len(lines), 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(block)
    rtl = _rtl(text)
    for index, line in enumerate(lines):
        shown = _shown(line)
        if rtl or align == "right":
            draw.text((width, index * step), shown, font=font, fill=fill, anchor="ra")
        elif align == "center":
            draw.text((width // 2, index * step), shown, font=font, fill=fill, anchor="ma")
        else:
            draw.text((0, index * step), shown, font=font, fill=fill)
    return block


def _star(draw, cx, cy, radius, fill):
    points = []
    for index in range(10):
        angle = math.pi / 2 + index * math.pi / 5
        length = radius if index % 2 == 0 else radius * 0.45
        points.append((cx + length * math.cos(angle), cy - length * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _logo(size: int) -> Image.Image | None:
    from app.services import brand

    path = brand.FOLDER / "logo.png"
    if not path.exists():
        return None
    logo = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    round_logo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    round_logo.paste(logo, (0, 0), mask)
    return round_logo


def _header(idea: dict, colours: dict, width: int) -> Image.Image:
    header = Image.new("RGBA", (width, 130), (0, 0, 0, 0))
    logo = _logo(110)
    x = 0
    if logo is not None:
        ring = Image.new("RGBA", (122, 122), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((0, 0, 121, 121), fill=(255, 255, 255, 255))
        header.alpha_composite(ring, (0, 4))
        header.alpha_composite(logo, (6, 10))
        x = 146
    draw = ImageDraw.Draw(header)
    draw.text((x, 18), idea["contact"]["brand"], font=_font(50, "black"), fill=colours["text"])
    draw.text((x, 80), _shown(idea["centre"]), font=_font(28, "semi"), fill=colours["soft"])
    return header


def _chip(text: str, colours: dict) -> Image.Image:
    font = _font(30, "black", text)
    shown = _shown(text)
    width = int(font.getlength(shown)) + 56
    chip = Image.new("RGBA", (width, 62), (0, 0, 0, 0))
    draw = ImageDraw.Draw(chip)
    draw.rounded_rectangle((0, 0, width - 1, 61), radius=31, fill=colours["chip"])
    draw.text((width // 2, 31), shown, font=font, fill=colours["chip_text"], anchor="mm")
    return chip


def _number_text(value, suffix: str) -> str:
    return (f"{value:g}" if isinstance(value, float) else str(value)) + suffix


def number_image(value, suffix: str, size: int, colour, width: int, align: str = "left") -> Image.Image:
    """A figure as a picture (redrawn at each frame of a video while it counts up)."""
    font = _font(size, "black")
    image = Image.new("RGBA", (width, int(size * 1.25)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = _number_text(value, suffix)
    if align == "center":
        draw.text((width // 2, 0), text, font=font, fill=colour, anchor="ma")
    else:
        draw.text((0, 0), text, font=font, fill=colour)
    return image


def _figures(idea: dict, colours: dict, width: int, story: bool) -> list[tuple[Image.Image, int, int, dict | None]]:
    """The figure cards: (image, x, y, number) relative to the block's top left."""
    figures = idea.get("figures") or []
    columns = 1 if story else 2
    gap = 26
    card_width = (width - gap * (columns - 1)) // columns
    card_height = 210 if story else 230
    out = []
    for index, figure in enumerate(figures[:4]):
        card = _card((card_width, card_height), colours)
        draw = ImageDraw.Draw(card)
        label = _text_block(figure["label"], _font(34, "semi"), card_width - 64, colours["soft"], most=2,
                            align="right" if _rtl(figure["label"]) else "left")
        card.alpha_composite(label, (32, card_height - label.height - 30))
        row, column = divmod(index, columns)
        x, y = column * (card_width + gap), row * (card_height + gap)
        out.append((card, x, y, None))
        out.append((number_image(figure["value"], figure["suffix"], 96, colours["accent"], card_width - 64), x + 32, y + 18,
                    {"value": figure["value"], "suffix": figure["suffix"], "size": 96, "colour": colours["accent"], "width": card_width - 64}))
    return out


def _quote(idea: dict, colours: dict, width: int, story: bool) -> Image.Image:
    quote = idea.get("quote") or ""
    size = 50 if len(quote) < 120 else 44 if len(quote) < 200 else 38
    if story:
        size += 6
    font = _font(size, "semi")
    text = _text_block(quote, font, width - 110, colours["text"], spacing=1.3, most=9 if story else 6)
    name_font = _font(36, "bold")
    height = 120 + text.height + 110
    card = _card((width, height), colours)
    draw = ImageDraw.Draw(card)
    draw.text((44, -10), "“", font=_font(200, "black"), fill=colours["accent"])
    card.alpha_composite(text, (56, 120))
    y = 120 + text.height + 34
    if idea.get("name"):
        draw.text((56, y), "— " + idea["name"], font=name_font, fill=colours["text"])
    if idea.get("stars"):
        for index in range(5):
            _star(draw, width - 260 + index * 46, y + 24, 19, colours["gold"] if index < idea["stars"] else (255, 255, 255, 70))
    return card


def _listen(idea: dict, colours: dict, width: int, story: bool) -> list[tuple[Image.Image, int, int]]:
    out, y = [], 0
    for label, text, strong in ((idea["you_said"], idea.get("said") or "", False), (idea["we_did"], idea.get("did") or "", True)):
        font = _font(44 if story else 40, "semi")
        body = _text_block(text, font, width - 100, colours["text"], spacing=1.25, most=5 if story else 3)
        height = 96 + body.height + 40
        card = _card((width, height), colours)
        draw = ImageDraw.Draw(card)
        if strong:
            draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=36, outline=colours["gold"], width=5)
        tag = _chip(label, colours if strong else {**colours, "chip": colours["text"], "chip_text": colours["top"] if colours["text"] == "#ffffff" else "#ffffff"})
        card.alpha_composite(tag, (44 if not _rtl(label) else width - tag.width - 44, 30))
        card.alpha_composite(body, (50, 112))
        out.append((card, 0, y))
        y += height + (70 if story else 44)
        if not strong:
            arrow = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
            ImageDraw.Draw(arrow).polygon([(10, 6), (70, 6), (40, 54)], fill=colours["gold"])
            out.append((arrow, width // 2 - 40, y - (62 if story else 50)))
    return out


def _facts(idea: dict, colours: dict, width: int, story: bool) -> Image.Image:
    rows = idea.get("facts") or []
    font = _font(42 if story else 38, "semi")
    lines = []
    for label, value in rows[:6]:
        lines.append(f"{label} : {value}" if label else value)
    line_height = 74 if story else 66
    card = _card((width, 50 + line_height * len(lines) + 20), colours)
    draw = ImageDraw.Draw(card)
    for index, line in enumerate(lines):
        y = 44 + index * line_height
        shown = _shown(line)
        if _rtl(line):
            draw.ellipse((width - 64, y + 14, width - 44, y + 34), fill=colours["gold"])
            draw.text((width - 88, y), shown, font=font, fill=colours["text"], anchor="ra")
        else:
            draw.ellipse((44, y + 14, 64, y + 34), fill=colours["gold"])
            draw.text((88, y), shown, font=font, fill=colours["text"])
    return card


def _footer(idea: dict, colours: dict, width: int) -> Image.Image:
    footer = Image.new("RGBA", (width, 110), (0, 0, 0, 0))
    draw = ImageDraw.Draw(footer)
    font = _font(36, "black", idea["cta"])
    label = _shown(idea["cta"])
    pill = int(font.getlength(label)) + 80
    draw.rounded_rectangle((0, 10, pill, 100), radius=45, fill=colours["chip"])
    draw.text((pill // 2, 55), label, font=font, fill=colours["chip_text"], anchor="mm")
    small = _font(30, "semi")
    phone = idea["contact"]["phone"]
    site = idea["contact"]["website"]
    draw.text((pill + 34, 22), site, font=small, fill=colours["text"])
    if phone:
        draw.text((pill + 34, 62), phone, font=small, fill=colours["soft"])
    return footer


def _fit(parts: list[tuple], width: int, room: int, centre: bool) -> tuple[list[tuple], int, int, float]:
    """The content's pieces scaled down to fit the room left above the footer (never up), and where the block starts:
    (pieces, x offset, y offset, scale)."""
    height = max((dy + image.height for image, dx, dy, step, number in parts), default=0)
    scale = min(1.0, room / height) if height else 1.0
    fitted = []
    for image, dx, dy, step, number in parts:
        if scale < 0.999:
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
            dx, dy = int(dx * scale), int(dy * scale)
            if number:
                number = {**number, "size": int(number["size"] * scale), "width": int(number["width"] * scale)}
        fitted.append((image, dx, dy, step, number))
    offset_x = int(width * (1 - scale) / 2)
    offset_y = max(0, (room - int(height * scale)) // 2) if centre else 0
    return fitted, offset_x, offset_y, scale


def picture(idea: dict, fmt: str = "post", look: str = "berry", accent: str | None = None) -> Picture:
    """The picture of an idea, in pieces (see Piece)."""
    fmt = fmt if fmt in SIZES else "post"
    look = look if look in LOOKS else "berry"
    size = SIZES[fmt]
    story = fmt == "story"
    colours = palette(look, accent)
    margin = 80
    inner = size[0] - 2 * margin
    shot = Picture(size, background(size, look, accent, seed=len(idea.get("key", ""))))
    y = 90 if story else 64
    shot.pieces.append(Piece(_header(idea, colours, inner), margin, y, step=0))
    y += 190 if story else 160
    kicker = _chip(idea["kicker"], colours)
    rtl = _rtl(idea["kicker"])
    shot.pieces.append(Piece(kicker, margin if not rtl else size[0] - margin - kicker.width, y, step=1))
    y += 96
    if topic_is_intro := idea.get("topic") == "intro":
        y += 260 if story else 80                    # the opening scene: its title alone, lower and bigger
    if idea.get("headline"):
        font_size = 110 if topic_is_intro and story else 88 if topic_is_intro else 84 if story else (66 if len(idea["headline"]) < 26 else 56)
        font = _font(font_size, "black", idea["headline"])
        headline = _text_block(idea["headline"], font, inner, colours["text"], spacing=1.12, most=3 if story else 2)
        shot.pieces.append(Piece(headline, margin, y, step=1))
        y += headline.height + (60 if story else 30)
    footer_top = size[1] - (230 if story else 150)
    topic = idea["topic"]
    parts: list[tuple] = []
    if topic == "success":
        for image, x, dy, number in _figures(idea, colours, inner, story):
            parts.append((image, x, dy, 2 + dy // 200, number))
    elif topic == "testimonial":
        parts.append((_quote(idea, colours, inner, story), 0, 0, 2, None))
    elif topic == "listen":
        for index, (image, x, dy) in enumerate(_listen(idea, colours, inner, story)):
            parts.append((image, x, dy, 2 + index, None))
    elif topic != "intro":
        parts.append((_facts(idea, colours, inner, story), 0, 0, 2, None))
    fitted, offset_x, offset_y, scale = _fit(parts, inner, footer_top - y - 30, centre=story)
    for image, dx, dy, step, number in fitted:
        shot.pieces.append(Piece(image, margin + offset_x + dx, y + offset_y + dy, step=step, number=number))
    shot.pieces.append(Piece(_footer(idea, colours, inner), margin, footer_top + (40 if story else 0), step=9))
    return shot


def render_png(idea: dict, fmt: str = "post", look: str = "berry", accent: str | None = None) -> bytes:
    buffer = io.BytesIO()
    picture(idea, fmt, look, accent).flat().save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
