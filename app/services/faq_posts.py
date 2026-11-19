"""FAQ carousels drawn the way the centre's own posts are (the Summer Camp FAQ of The Bridge): a white brick wall, plum
arcs and a plum shape in the corners, the season's sticker, "The Bridge" with the S of the logo, an orange bar
"شنوا تحب تعرف على …", the question on a white card, the answer on pink highlighted lines, the phone in a pill; and a last
slide "contact us" in a phone. Each season has its theme (summer camp, back to school, winter, Ramadan, spring), chosen
by the date or by the admin. Pictures are 1080 × 1080 PNG, drawn on this computer (Pillow)."""
from __future__ import annotations

import io
import math
import random
import re
import zipfile
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services import social_images as art

SIZE = 1080
WALL, MORTAR = (246, 246, 245), (224, 224, 222)
PLUM, DARK, INK = (142, 52, 92), (60, 22, 52), (36, 36, 40)
HIGHLIGHT = (236, 214, 226)
ASK = "شنوا تحب تعرف على"
CALL = ["مالا فاش تستنى", "باش تضمن", "بلاصتك", "قيد على بكري"]
HANDLE = "9antra.tn_the_bridge"
# Each season: its sticker, its words, the colour of its bar, its hashtags.
THEMES = {
    "summer": {"name": "Summer camp", "tag": "SUMMER", "script": "CAMP", "sticker": "sunglasses", "bar": (247, 178, 92), "tag_colour": (247, 178, 92),
               "topic": "SUMMER CAMP", "tags": "#SummerCamp #9antra #TheBridge #Formation #Tunisie"},
    "rentree": {"name": "Back to school", "tag": "RENTRÉE", "script": "digitale", "sticker": "pencil", "bar": (240, 150, 78), "tag_colour": (255, 205, 96),
                "topic": "LA RENTRÉE", "tags": "#Rentrée #9antra #TheBridge #Formation #Tunisie"},
    "winter": {"name": "Winter", "tag": "WINTER", "script": "edition", "sticker": "snowflake", "bar": (110, 168, 228), "tag_colour": (190, 222, 250),
               "topic": "WINTER EDITION", "tags": "#Winter #9antra #TheBridge #Formation #Tunisie"},
    "ramadan": {"name": "Ramadan", "tag": "RAMADAN", "script": "karim", "sticker": "crescent", "bar": (214, 170, 72), "tag_colour": (240, 205, 120),
                "topic": "RAMADAN", "tags": "#Ramadan #9antra #TheBridge #Formation #Tunisie"},
    "spring": {"name": "Spring", "tag": "SPRING", "script": "edition", "sticker": "flower", "bar": (236, 150, 182), "tag_colour": (250, 200, 220),
               "topic": "SPRING EDITION", "tags": "#Spring #9antra #TheBridge #Formation #Tunisie"},
}
# The season an event's FAQ is drawn in (services/faq.THEMES).
FOR_EVENT = {"summer_camp": "summer"}
# Ramadan moves with the moon: its days for the years ahead (they are announced; a day may shift by one).
_RAMADAN = [(date(2026, 2, 18), date(2026, 3, 19)), (date(2027, 2, 8), date(2027, 3, 9)), (date(2028, 1, 28), date(2028, 2, 26)),
            (date(2029, 1, 16), date(2029, 2, 14))]


def season_of(day: date | None = None) -> str:
    day = day or date.today()
    if any(start <= day <= end for start, end in _RAMADAN):
        return "ramadan"
    if (day.month, day.day) >= (6, 1) and (day.month, day.day) <= (9, 14):
        return "summer"
    if (day.month, day.day) >= (9, 15) and day.month <= 11:
        return "rentree"
    if day.month in (12, 1, 2):
        return "winter"
    return "spring"


def _font(size: int, kind: str = "bold", text: str = "") -> ImageFont.FreeTypeFont:
    names = {"script": ["segoescb.ttf", "segoesc.ttf", "Inkfree.ttf"], "impact": ["impact.ttf", "seguibl.ttf"],
             "symbol": ["seguisym.ttf", "segoeui.ttf"]}.get(kind)
    if names:
        for name in names:
            path = art._FONTS / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return art._font(size, {"bold": "bold", "semi": "semi", "regular": "regular", "black": "black"}.get(kind, "bold"), text)


def _wall(seed: int = 7) -> Image.Image:
    """A white brick wall, each brick a shade apart."""
    picture = Image.new("RGB", (SIZE, SIZE), MORTAR)
    draw = ImageDraw.Draw(picture)
    rnd = random.Random(seed)
    height, width = 58, 132
    for row in range(SIZE // height + 2):
        offset = 0 if row % 2 == 0 else -width // 2
        for column in range(SIZE // width + 3):
            shade = rnd.randint(-4, 3)
            colour = tuple(max(0, min(255, value + shade)) for value in WALL)
            x, y = offset + column * width, row * height
            draw.rectangle((x + 2, y + 2, x + width - 2, y + height - 2), fill=colour)
    return picture.filter(ImageFilter.GaussianBlur(0.6))


def _corners(picture: Image.Image) -> None:
    """Plum arcs top left, a plum shape bottom right (and a touch of orange), as on the centre's slides."""
    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for radius in range(60, 250, 34):
        draw.arc((-60 - radius, -40 - radius, -60 + radius, -40 + radius), start=-90, end=90, fill=PLUM + (235,), width=7)
    draw.ellipse((-90, -120, 150, 70), fill=(247, 178, 92, 210))
    draw.ellipse((920, 840, 1240, 1240), fill=PLUM + (255,))
    draw.ellipse((860, 960, 1010, 1180), fill=PLUM + (255,))
    picture.paste(layer, (0, 0), layer)


def _logo_s(height: int) -> Image.Image | None:
    """The S of the logo, without its circle (it stands for the B of “The Bridge”)."""
    from app.services import brand

    path = brand.FOLDER / "logo.png"
    if not path.exists():
        return None
    logo = Image.open(path).convert("RGBA")
    width_full, height_full = logo.size
    s = logo.crop((int(width_full * 0.32), int(height_full * 0.15), int(width_full * 0.67), int(height_full * 0.86)))
    pixels = s.load()
    for x in range(s.size[0]):
        for y in range(s.size[1]):
            red, green, blue, alpha = pixels[x, y]
            if red > 225 and green > 225 and blue > 220:           # the logo's light background goes
                pixels[x, y] = (red, green, blue, 0)
    ratio = height / s.size[1]
    return s.resize((max(1, int(s.size[0] * ratio)), height), Image.LANCZOS)


def _wordmark(height: int = 70) -> Image.Image:
    """“The Bridge”, the B drawn as the logo's S."""
    font = _font(height, "semi")
    s = _logo_s(int(height * 1.15))
    the, rest = "The ", "ridge" if s is not None else "The Bridge"
    width = int(font.getlength(the) + (s.size[0] + 4 if s is not None else 0) + font.getlength(rest)) + 8
    mark = Image.new("RGBA", (width, int(height * 1.4)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    x = 0
    if s is not None:
        draw.text((x, 0), the, font=font, fill=INK)
        x += int(font.getlength(the))
        mark.paste(s, (x, int(height * 0.12)), s)
        x += s.size[0] + 4
        draw.text((x, 0), rest, font=font, fill=INK)
    else:
        draw.text((x, 0), "The Bridge", font=font, fill=INK)
    return mark


def _sticker(theme: dict, width: int = 330) -> Image.Image:
    """The season's sticker: its picture above a tilted tag (SUMMER) with a word in script (CAMP)."""
    sticker = Image.new("RGBA", (width, 250), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sticker)
    kind, cx = theme["sticker"], width // 2
    if kind == "sunglasses":
        red, lens = (232, 71, 95), (31, 42, 68)
        for x in (cx - 78, cx + 6):
            draw.rounded_rectangle((x, 18, x + 72, 70), radius=22, fill=lens, outline=red, width=9)
            draw.line((x + 18, 34, x + 34, 28), fill=(96, 165, 250), width=6)
            draw.line((x + 14, 50, x + 46, 38), fill=(96, 165, 250), width=6)
        draw.arc((cx - 14, 20, cx + 14, 44), start=200, end=340, fill=red, width=9)
        draw.line((cx - 78, 28, cx - 104, 18), fill=red, width=9)
        draw.line((cx + 78, 28, cx + 104, 18), fill=red, width=9)
    elif kind == "snowflake":
        for angle in range(0, 180, 30):
            dx, dy = 46 * math.cos(math.radians(angle)), 46 * math.sin(math.radians(angle))
            draw.line((cx - dx, 46 - dy, cx + dx, 46 + dy), fill=(70, 140, 220), width=7)
        draw.ellipse((cx - 10, 36, cx + 10, 56), fill=(70, 140, 220))
    elif kind == "crescent":
        draw.ellipse((cx - 48, 4, cx + 44, 96), fill=(214, 170, 72))
        draw.ellipse((cx - 22, -4, cx + 62, 80), fill=(0, 0, 0, 0))
        draw.polygon([(cx + 52, 30), (cx + 58, 46), (cx + 74, 46), (cx + 61, 56), (cx + 66, 72), (cx + 52, 62), (cx + 38, 72), (cx + 43, 56),
                      (cx + 30, 46), (cx + 46, 46)], fill=(214, 170, 72))
    elif kind == "pencil":
        draw.polygon([(cx - 90, 70), (cx + 60, 20), (cx + 74, 44), (cx - 76, 94)], fill=(255, 196, 70), outline=(60, 40, 20))
        draw.polygon([(cx + 60, 20), (cx + 96, 14), (cx + 74, 44)], fill=(240, 200, 160), outline=(60, 40, 20))
        draw.polygon([(cx - 90, 70), (cx - 76, 94), (cx - 104, 92)], fill=(232, 71, 95))
    else:                                                       # a flower
        for angle in range(0, 360, 60):
            px, py = cx + 30 * math.cos(math.radians(angle)), 50 + 30 * math.sin(math.radians(angle))
            draw.ellipse((px - 22, py - 22, px + 22, py + 22), fill=(236, 150, 182))
        draw.ellipse((cx - 16, 34, cx + 16, 66), fill=(247, 178, 92))
    tag = Image.new("RGBA", (width - 30, 92), (0, 0, 0, 0))
    tag_draw = ImageDraw.Draw(tag)
    tag_draw.polygon([(14, 6), (width - 30, 0), (width - 46, 86), (0, 92)], fill=theme["tag_colour"])
    word = _font(66 if len(theme["tag"]) <= 7 else 54, "impact")
    tag_draw.text(((width - 30) // 2, 46), theme["tag"], font=word, fill=(30, 30, 34), anchor="mm")
    tag = tag.rotate(6, expand=True, resample=Image.BICUBIC)
    sticker.paste(tag, ((width - tag.size[0]) // 2, 58), tag)          # the picture sits on the tag, as on the posts
    script = _font(64, "script")
    draw.text((cx + 10, 172), theme["script"], font=script, fill=PLUM, anchor="mm")
    if kind == "sunglasses":                                    # the glasses in front of the tag
        glasses = sticker.crop((0, 0, width, 80))
        sticker.paste(glasses, (0, 0), glasses)
    return sticker


def _lines(text: str, font, width: int) -> list[str]:
    found = []
    for paragraph in (text or "").split("\n"):
        found += art.wrap(paragraph.strip(), font, width) if paragraph.strip() else []
    return found


def _pill(phone: str) -> Image.Image:
    font = _font(46, "black")
    width = int(font.getlength(phone)) + 120
    pill = Image.new("RGBA", (width, 84), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pill)
    draw.rounded_rectangle((2, 2, width - 3, 81), radius=40, fill=(255, 255, 255), outline=(30, 30, 40), width=5)
    draw.text((44, 42), "✆", font=_font(44, "symbol"), fill=(30, 30, 40), anchor="mm")
    draw.text((80, 42), phone, font=font, fill=(20, 20, 26), anchor="lm")
    return pill


def _phone(db=None) -> str:
    if db is not None:
        from app.services.settings_service import get_settings

        number = (getattr(get_settings(db), "centre_phone", None) or "").strip()
        if number:
            return re.sub(r"^\+?216\s*", "", number)
    return "58 840 064"


def slide(question: str, answer: str, theme_key: str = "summer", topic: str | None = None, phone: str = "58 840 064") -> bytes:
    """One question of the carousel, as a 1080 × 1080 PNG."""
    theme = THEMES.get(theme_key) or THEMES["summer"]
    picture = _wall()
    _corners(picture)
    canvas = picture.convert("RGBA")
    sticker = _sticker(theme)
    canvas.paste(sticker, (SIZE // 2 - sticker.size[0] // 2 - 90, 6), sticker)
    mark = _wordmark(62)
    canvas.paste(mark, (SIZE - mark.size[0] - 46, 40), mark)
    draw = ImageDraw.Draw(canvas)
    left, right, top = 70, SIZE - 70, 300
    heading = f"{ASK} {topic or theme['topic']}"
    head_font = _font(50, "bold", heading)
    draw.rounded_rectangle((left, top, right, top + 112), radius=30, fill=theme["bar"])
    draw.text((SIZE // 2, top + 56), art._shown(heading), font=head_font, fill=(255, 255, 255), anchor="mm")
    question_font = _font(48, "regular", question)
    asked = _lines(question, question_font, right - left - 90)[:3]
    card_bottom = top + 112 + 70 + 62 * len(asked)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((left + 4, top + 104, right + 4, card_bottom + 8), radius=30, fill=(0, 0, 0, 40))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((left, top + 100, right, card_bottom), radius=30, fill=(255, 255, 255))
    draw.rounded_rectangle((left, top, right, top + 112), radius=30, fill=theme["bar"])
    draw.text((SIZE // 2, top + 56), art._shown(heading), font=head_font, fill=(255, 255, 255), anchor="mm")
    y = top + 112 + 34
    for line in asked:
        draw.text((SIZE // 2, y + 31), art._shown(line), font=question_font, fill=INK, anchor="mm")
        y += 62
    answer_font = _font(44, "bold", answer)
    told = _lines(answer, answer_font, right - left - 40)
    while len(told) > 7 and answer_font.size > 30:
        answer_font = _font(answer_font.size - 4, "bold", answer)
        told = _lines(answer, answer_font, right - left - 40)
    y = card_bottom + 70
    step = int(answer_font.size * 1.36)
    for line in told[:8]:
        shown = art._shown(line)
        width = answer_font.getlength(shown)
        draw.rounded_rectangle((SIZE // 2 - width / 2 - 16, y - 4, SIZE // 2 + width / 2 + 16, y + step - 8), radius=8, fill=HIGHLIGHT)
        draw.text((SIZE // 2, y + (step - 8) // 2), shown, font=answer_font, fill=DARK, anchor="mm")
        y += step
    pill = _pill(phone)
    canvas.paste(pill, (36, SIZE - pill.size[1] - 26), pill)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def closing(theme_key: str = "summer", phone: str = "58 840 064", handle: str = HANDLE) -> bytes:
    """The last slide: the FAQ in a phone, “ما لا فاش تستنى…”, contact us, the number and the networks."""
    theme = THEMES.get(theme_key) or THEMES["summer"]
    picture = _wall(11)
    _corners(picture)
    canvas = picture.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((150, 40, 930, 1160), radius=90, outline=(40, 40, 46), width=22)
    draw.rounded_rectangle((440, 52, 640, 92), radius=20, fill=(40, 40, 46))
    from app.services import social_images

    logo = social_images._logo(150)
    if logo is not None:
        canvas.paste(logo, (210, 120), logo)
    draw.text((285, 330), "FAQ", font=_font(70, "black"), fill=PLUM, anchor="mm")
    sticker = _sticker(theme, 300)
    canvas.paste(sticker, (600, 90), sticker)
    y = 385
    for line in CALL:
        font = _font(58, "bold", line)
        draw.text((SIZE // 2, y), art._shown(line), font=font, fill=PLUM, anchor="mm")
        y += 70
    draw.text((SIZE // 2 - 40, y + 30), "contact us", font=_font(80, "script"), fill=(247, 178, 92), anchor="mm")
    draw.ellipse((255, y + 95, 365, y + 205), fill=(124, 92, 214))
    draw.text((310, y + 150), "✆", font=_font(60, "symbol"), fill=(255, 255, 255), anchor="mm")
    draw.text((390, y + 150), f"+216 {phone}", font=_font(50, "black"), fill=(20, 20, 26), anchor="lm")
    draw.text((SIZE // 2, y + 240), handle, font=_font(32, "regular"), fill=(40, 40, 46), anchor="mm")
    x = SIZE // 2 - 2 * 60 + 4
    for letter, colour in (("f", (24, 119, 242)), ("◎", (225, 48, 108)), ("♪", (20, 20, 20)), ("in", (10, 102, 194))):
        draw.rounded_rectangle((x, y + 272, x + 48, y + 320), radius=12, fill=colour)
        draw.text((x + 24, y + 296), letter, font=_font(28, "symbol" if letter in "◎♪" else "black"), fill=(255, 255, 255), anchor="mm")
        x += 60
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def caption(items: list[dict], theme_key: str, phone: str = "58 840 064", topic: str | None = None) -> str:
    """The text under the carousel, in Tunisian like the centre's posts."""
    theme = THEMES.get(theme_key) or THEMES["summer"]
    lines = [f"❓ FAQ · {ASK} {topic or theme['topic']} 👇", ""]
    lines += [f"• {item['question']}" for item in items]
    lines += ["", "مالا فاش تستنى؟ قيد على بكري 🔥", f"📞 +216 {phone}", "", theme["tags"]]
    return "\n".join(lines)


def carousel_zip(items: list[dict], theme_key: str, phone: str, handle: str = HANDLE, topic: str | None = None) -> bytes:
    """Every slide and the last one, numbered as they go in the carousel, with the caption."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, item in enumerate(items, start=1):
            archive.writestr(f"{number:02d}.png", slide(item["question"], item["answer"], theme_key, topic, phone))
        archive.writestr(f"{len(items) + 1:02d}-contact.png", closing(theme_key, phone, handle))
        archive.writestr("caption.txt", caption(items, theme_key, phone, topic))
    return out.getvalue()
