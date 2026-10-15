"""The 9antra email design: one branded layout shared by every email.

Class emails, reminders, the assistant's answers and the admin's personalized
emails all go through `render_email`, so candidates always receive the same
look as the dashboard: plum header, berry accent, soft rounded cards.

The markup is table-based with inline styles only, because that is what mail
clients (Gmail, Outlook, phone apps) render reliably.
"""
from __future__ import annotations

import html
import re

BRAND = "9antra"
DEFAULT_ACCENT = "#a42c67"
DEFAULT_SIGNATURE = "Merci pour votre confiance,\nL'équipe 9antra"
DEFAULT_SIGNATURE_EN = "Thank you for your trust,\nThe 9antra team"

INK = "#211433"
INK_SOFT = "#3c2a60"
TEXT = "#241a38"
MUTED = "#85809c"
CANVAS = "#f6f3fb"
BORDER = "#eee9f5"
GOLD = "#d97706"
FONT = "'Plus Jakarta Sans','Segoe UI',Helvetica,Arial,sans-serif"

# key -> (name, what it is for)
STYLES = {
    "brand": ("Signature", "Plum header and berry title band. The default for every email."),
    "letter": ("Personal note", "Light and quiet, like a letter. Good for one-to-one messages."),
    "announcement": ("Announcement", "Large coloured banner. Good for news sent to a whole class."),
}

_FIXED = {
    "fr": {
        "footer": "Vous recevez cet email car vous êtes inscrit(e) à une formation 9antra. "
                  "Vous pouvez répondre directement à ce message.",
        "quote": "Votre message",
        "tagline": "Formation",
        "more": "Pour aller plus loin",
    },
    "en": {
        "footer": "You are receiving this email because you registered for a 9antra training. "
                  "You can reply directly to this message.",
        "quote": "Your message",
        "tagline": "Training",
        "more": "To go further",
    },
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_URL = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)])")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BULLET = re.compile(r"^\s*[-•*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
# A paragraph that starts with [box] is drawn as a coloured square: [box], [box gold], [box green], [box grey].
_BOX = re.compile(r"^\s*\[(?:box|cadre|carre|carré)(?:[ :]+([a-zéè]+))?\]\s*(.*)$", re.IGNORECASE)
_BOX_COLOURS = {"gold": "#d97706", "or": "#d97706", "green": "#16a34a", "vert": "#16a34a", "grey": "#8a84a0", "gray": "#8a84a0", "gris": "#8a84a0"}


def strip_marks(text: str | None) -> str:
    """The text without the marks that only shape the HTML version."""
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        found = _BOX.match(line)
        if found is None:
            lines.append(line)
        elif found.group(2).strip():
            lines.append(found.group(2).strip())
    return "\n".join(lines)


def clean_accent(value: str | None) -> str:
    value = (value or "").strip()
    return value.lower() if _HEX.match(value) else DEFAULT_ACCENT


def _mix(color: str, other: tuple[int, int, int], amount: float) -> str:
    channels = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    mixed = [round(channel + (target - channel) * amount) for channel, target in zip(channels, other)]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _tint(color: str, amount: float = 0.92) -> str:
    return _mix(color, (255, 255, 255), amount)


def _shade(color: str, amount: float = 0.18) -> str:
    return _mix(color, (0, 0, 0), amount)


def html_to_text(markup: str) -> str:
    """Readable text of an HTML email, without quoted earlier messages."""
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?is)<blockquote\b.*?</blockquote>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def branding(settings=None, lang: str = "fr") -> dict:
    """Signature, footer and accent colour chosen by the admin in Settings."""
    default = DEFAULT_SIGNATURE_EN if lang == "en" else DEFAULT_SIGNATURE
    signature = (getattr(settings, "email_signature", None) or "").strip() or default
    return {
        "signature": signature,
        "footer_note": (getattr(settings, "email_footer_note", None) or "").strip(),
        "accent": clean_accent(getattr(settings, "email_accent_color", None)),
    }


# ------------------------------------------------------------------
# Plain text -> safe HTML
# ------------------------------------------------------------------

# A long address is unreadable in a sentence: it is shown as a small button
# that says where it leads. (host pattern, French, English, sign)
_LINK_NAMES = [
    (r"(^|\.)google\.[a-z.]+/maps|maps\.app\.goo\.gl|goo\.gl/maps|maps\.google\.", "Ouvrir dans Google Maps", "Open in Google Maps", "&#128205;"),
    (r"youtube\.com|youtu\.be", "Voir la vidéo", "Watch the video", "&#9654;"),
    (r"docs\.google\.com/forms|forms\.gle", "Ouvrir le formulaire", "Open the form", "&#128221;"),
    (r"drive\.google\.com|docs\.google\.com", "Ouvrir le document", "Open the document", "&#128196;"),
    (r"facebook\.com|fb\.me|fb\.watch", "Voir sur Facebook", "See it on Facebook", "&#128077;"),
    (r"wa\.me|whatsapp\.com", "Écrire sur WhatsApp", "Write on WhatsApp", "&#128172;"),
]
LONG_LINK = 60


def link_label(url: str, lang: str = "fr") -> tuple[str, str] | None:
    """(label, sign) for a link shown as a button; None when it reads fine as it is."""
    address = re.sub(r"^https?://(www\.)?", "", url.strip(), flags=re.IGNORECASE)
    for pattern, french, english, sign in _LINK_NAMES:
        if re.search(pattern, address, re.IGNORECASE):
            return (english if lang == "en" else french), sign
    if len(url) <= LONG_LINK:
        return None
    host = address.split("/")[0].split("?")[0]
    return (f"Open {host}" if lang == "en" else f"Ouvrir {host}"), "&#128279;"


def _link(url: str, accent: str, lang: str) -> str:
    target = html.escape(html.unescape(url), quote=True)
    named = link_label(html.unescape(url), lang)
    if named is None:
        return f'<a href="{target}" style="color:{accent};font-weight:600;">{url}</a>'
    label, sign = named
    return (
        f'<a href="{target}" style="display:inline-block;margin:2px 0;padding:5px 13px;border-radius:999px;'
        f'background:{_tint(accent, .9)};color:{accent};font-weight:700;font-size:13px;line-height:1.5;'
        f'text-decoration:none;white-space:nowrap;">{sign}&nbsp;{html.escape(label)}</a>'
    )


def _inline(text: str, accent: str, lang: str = "fr") -> str:
    escaped = html.escape(text, quote=False)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _URL.sub(lambda match: _link(match.group(1), accent, lang), escaped)


def text_to_html(text: str | None, accent: str = DEFAULT_ACCENT, lang: str = "fr") -> str:
    """Turn the text an admin (or the assistant) wrote into email-safe HTML.

    Blank lines separate paragraphs, lines starting with "-" become a list,
    **double stars** make bold, and links become clickable (a long one as a
    small button naming where it leads). Everything else is escaped, so text
    can never inject markup into the email.
    """
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip())
    parts = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        box = _BOX.match(lines[0]) if lines else None
        if box is not None:
            colour = _BOX_COLOURS.get((box.group(1) or "").lower(), accent)
            inside = "\n".join(([box.group(2)] if box.group(2).strip() else []) + lines[1:])
            if inside.strip():
                parts.append(
                    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;"><tr>'
                    f'<td bgcolor="{_tint(colour, .92)}" style="background:{_tint(colour, .92)};border-left:4px solid {colour};'
                    f'border-radius:14px;padding:16px 18px 2px;">{text_to_html(inside, accent, lang)}</td></tr></table>'
                )
            continue
        # A sentence followed by its list ("Nous avons deux centres :" then "- …") is two runs.
        runs: list[tuple[str | None, list[str]]] = []
        for line in lines:
            kind = "ul" if _BULLET.match(line) else "ol" if _NUMBERED.match(line) else None
            if runs and runs[-1][0] == kind:
                runs[-1][1].append(line)
            else:
                runs.append((kind, [line]))
        for kind, run in runs:
            if kind is None:
                content = "<br>".join(_inline(line.strip(), accent, lang) for line in run)
                parts.append(f'<p style="margin:0 0 16px;">{content}</p>')
                continue
            pattern = _BULLET if kind == "ul" else _NUMBERED
            items = "".join(
                f'<li style="margin:0 0 8px;">{_inline(pattern.match(line).group(1), accent, lang)}</li>'
                for line in run
            )
            parts.append(f'<{kind} style="margin:0 0 16px;padding-left:22px;color:{TEXT};">{items}</{kind}>')
    return "".join(parts)


def _more(links, accent: str, heading: str) -> str:
    """A quiet row of buttons under the answer: where to read more."""
    shown = [(label, url) for label, url in links or [] if label and re.match(r"^https?://", (url or "").strip(), re.IGNORECASE)]
    if not shown:
        return ""
    buttons = "".join(
        f'<a href="{html.escape(url.strip(), quote=True)}" style="display:inline-block;margin:0 8px 8px 0;padding:8px 15px;'
        f'border-radius:999px;border:1px solid {_tint(accent, .72)};background:#ffffff;color:{accent};font-family:{FONT};'
        f'font-size:13px;font-weight:700;text-decoration:none;">{html.escape(label)}&nbsp;&rarr;</a>'
        for label, url in shown
    )
    return (
        f'<div style="margin:22px 0 0;padding:16px 18px 8px;border-radius:16px;background:{_tint(accent, .95)};">'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;'
        f'color:{MUTED};margin-bottom:10px;">{html.escape(heading)}</div>{buttons}</div>'
    )


# ------------------------------------------------------------------
# Building blocks
# ------------------------------------------------------------------

def _logo(accent: str, name_color: str, tagline_color: str, tagline: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="42" height="42" align="center" valign="middle" bgcolor="{GOLD}" '
        f'style="width:42px;height:42px;border-radius:13px;background:{GOLD};'
        f'background-image:linear-gradient(135deg,{GOLD},{accent});font-size:20px;line-height:42px;">&#127891;</td>'
        '<td style="padding-left:12px;">'
        f'<div style="font-family:{FONT};font-size:19px;font-weight:800;color:{name_color};letter-spacing:-.01em;line-height:1.1;">{BRAND}</div>'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:700;color:{tagline_color};letter-spacing:.14em;text-transform:uppercase;line-height:1.6;">{html.escape(tagline)}</div>'
        '</td></tr></table>'
    )


def _header(style: str, title: str, accent: str, tagline: str) -> str:
    title_html = html.escape(title)
    if style == "letter":
        return (
            f'<tr><td height="6" bgcolor="{accent}" style="height:6px;line-height:6px;font-size:0;background:{accent};'
            f'background-image:linear-gradient(90deg,{accent},{GOLD});border-radius:20px 20px 0 0;">&nbsp;</td></tr>'
            f'<tr><td bgcolor="#ffffff" style="background:#ffffff;border-left:1px solid {BORDER};border-right:1px solid {BORDER};padding:26px 32px 0;">'
            f'{_logo(accent, INK, MUTED, tagline)}'
            f'<div style="font-family:{FONT};font-size:23px;font-weight:800;color:{INK};line-height:1.3;margin:24px 0 0;">{title_html}</div>'
            f'<div style="width:44px;height:4px;border-radius:4px;background:{accent};margin:14px 0 0;line-height:4px;font-size:0;">&nbsp;</div>'
            '</td></tr>'
        )
    if style == "announcement":
        return (
            f'<tr><td align="center" bgcolor="{accent}" style="background:{accent};'
            f'background-image:linear-gradient(135deg,{accent} 0%,{_shade(accent, .32)} 100%);border-radius:20px 20px 0 0;padding:34px 32px 36px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"><tr>'
            f'<td width="46" height="46" align="center" valign="middle" bgcolor="{GOLD}" style="width:46px;height:46px;border-radius:14px;'
            f'background:{GOLD};font-size:22px;line-height:46px;">&#127891;</td></tr></table>'
            f'<div style="font-family:{FONT};font-size:11px;font-weight:800;color:#ffd9a8;letter-spacing:.2em;text-transform:uppercase;margin:16px 0 8px;">{BRAND}</div>'
            f'<div style="font-family:{FONT};font-size:27px;font-weight:800;color:#ffffff;line-height:1.25;">{title_html}</div>'
            '</td></tr>'
        )
    return (
        f'<tr><td bgcolor="{INK}" style="background:{INK};background-image:linear-gradient(135deg,{INK} 0%,{INK_SOFT} 100%);'
        f'border-radius:20px 20px 0 0;padding:22px 32px;">{_logo(accent, "#ffffff", "#b9aecf", tagline)}</td></tr>'
        f'<tr><td bgcolor="{accent}" style="background:{accent};background-image:linear-gradient(135deg,{accent} 0%,{_shade(accent)} 100%);padding:18px 32px;">'
        f'<div style="font-family:{FONT};font-size:21px;font-weight:800;color:#ffffff;line-height:1.3;">{title_html}</div>'
        '</td></tr>'
    )


def _rows_table(rows, accent: str) -> str:
    if not rows:
        return ""
    cells = "".join(
        '<tr>'
        f'<td valign="top" style="padding:9px 14px;font-family:{FONT};font-size:13px;color:{MUTED};white-space:nowrap;'
        f'border-bottom:1px solid #ffffff;">{html.escape(str(label))}</td>'
        f'<td valign="top" style="padding:9px 14px;font-family:{FONT};font-size:14px;font-weight:600;color:{TEXT};'
        f'border-bottom:1px solid #ffffff;">{html.escape(str(value))}</td>'
        '</tr>'
        for label, value in rows
    )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_tint(accent)}" '
        f'style="width:100%;background:{_tint(accent)};border-radius:14px;margin:4px 0 18px;">{cells}</table>'
    )


def _bullets(title: str | None, bullets, accent: str) -> str:
    if not bullets:
        return ""
    items = "".join(f'<li style="margin:0 0 6px;">{html.escape(str(item))}</li>' for item in bullets)
    heading = f'<p style="margin:0 0 8px;font-weight:700;color:{INK};">{html.escape(title)}</p>' if title else ""
    return f'{heading}<ul style="margin:0 0 16px;padding-left:22px;color:{INK_SOFT};">{items}</ul>'


def _button(cta, accent: str) -> str:
    if not cta or not cta[0] or not cta[1]:
        return ""
    label, url = cta
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 20px;"><tr>'
        f'<td align="center" bgcolor="{accent}" style="background:{accent};border-radius:999px;">'
        f'<a href="{html.escape(url.strip(), quote=True)}" style="display:inline-block;padding:12px 26px;font-family:{FONT};'
        f'font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;">{html.escape(label)}</a>'
        '</td></tr></table>'
    )


CONTENT_WIDTH = 536  # the 600px card minus its side padding


def _photo(src: str | None, width: int | None, top: bool) -> str:
    if not src:
        return ""
    shown = min(int(width or CONTENT_WIDTH), CONTENT_WIDTH)
    margin = "0 0 20px" if top else "6px 0 18px"
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:{margin};"><tr>'
        f'<td align="center"><img src="{html.escape(src, quote=True)}" alt="" width="{shown}" '
        f'style="display:block;width:100%;max-width:{shown}px;height:auto;border:0;border-radius:14px;"></td>'
        '</tr></table>'
    )


def _quote(quote: str | None, label: str) -> str:
    if not quote or not quote.strip():
        return ""
    excerpt = quote.strip()
    if len(excerpt) > 600:
        excerpt = excerpt[:600].rstrip() + "…"
    body = "<br>".join(html.escape(line) for line in excerpt.split("\n"))
    return (
        f'<div style="margin:22px 0 0;padding:12px 16px;border-left:3px solid #ddd4ec;background:#faf8fd;border-radius:0 12px 12px 0;">'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:{MUTED};margin-bottom:4px;">{html.escape(label)}</div>'
        f'<div style="font-family:{FONT};font-size:13px;line-height:1.55;color:{MUTED};">{body}</div>'
        '</div>'
    )


def _signature(signature: str) -> str:
    lines = [line.strip() for line in signature.split("\n") if line.strip()]
    if not lines:
        return ""
    rendered = "".join(
        f'<div style="font-family:{FONT};font-size:15px;font-weight:700;color:{INK};">{html.escape(line)}</div>'
        if index == len(lines) - 1 else
        f'<div style="font-family:{FONT};font-size:13px;color:{MUTED};">{html.escape(line)}</div>'
        for index, line in enumerate(lines)
    )
    return f'<div style="margin:26px 0 0;padding:18px 0 0;border-top:1px solid {BORDER};">{rendered}</div>'


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def render_email(
    *,
    title: str,
    body_html: str = "",
    greeting: str | None = None,
    rows=None,
    bullets=None,
    bullets_title: str | None = None,
    closing: str | None = None,
    closing_html: str = "",
    cta: tuple[str, str] | None = None,
    quote: str | None = None,
    preheader: str | None = None,
    signature: str | None = None,
    footer_note: str | None = None,
    accent: str | None = None,
    style: str = "brand",
    lang: str = "fr",
    image_src: str | None = None,
    image_width: int | None = None,
    image_position: str = "top",
    links=None,
) -> str:
    """Render one complete HTML email in the 9antra design.

    `body_html` and `closing_html` must already be safe HTML (use
    `text_to_html`); every other argument is plain text and is escaped here.
    `image_src` is a photo shown above the text ("top") or under it ("bottom").
    `links` are (label, address) pairs shown as buttons under the text.
    """
    accent = clean_accent(accent)
    style = style if style in STYLES else "brand"
    fixed = _FIXED.get(lang, _FIXED["fr"])
    signature = DEFAULT_SIGNATURE if signature is None else signature

    greeting_html = f'<p style="margin:0 0 16px;">{html.escape(greeting)}</p>' if greeting else ""
    closing_html = closing_html or (f'<p style="margin:0 0 16px;">{html.escape(closing)}</p>' if closing else "")
    photo_top = image_position != "bottom"
    photo_html = _photo(image_src, image_width, photo_top)
    footer_lines = []
    if footer_note:
        footer_lines.append("<br>".join(html.escape(line) for line in footer_note.split("\n") if line.strip()))
    footer_lines.append(html.escape(fixed["footer"]))
    footer_html = "".join(
        f'<div style="margin:0 0 6px;">{line}</div>' for line in footer_lines
    )
    hidden = (
        f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;color:{CANVAS};">'
        f'{html.escape(preheader)}</div>'
        if preheader else ""
    )
    top_padding = "22px" if style == "letter" else "30px"

    return f"""<!DOCTYPE html>
<html lang="{html.escape(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{html.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
{hidden}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CANVAS}" style="width:100%;background:{CANVAS};">
<tr><td align="center" style="padding:28px 12px 34px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">
{_header(style, title, accent, fixed["tagline"])}
<tr><td bgcolor="#ffffff" style="background:#ffffff;border:1px solid {BORDER};border-top:0;border-radius:0 0 20px 20px;padding:{top_padding} 32px 30px;font-family:{FONT};font-size:15px;line-height:1.65;color:{TEXT};">
{photo_html if photo_top else ""}
{greeting_html}
{body_html}
{_rows_table(rows, accent)}
{_bullets(bullets_title, bullets, accent)}
{_button(cta, accent)}
{closing_html}
{"" if photo_top else photo_html}
{_more(links, accent, fixed["more"])}
{_quote(quote, fixed["quote"])}
{_signature(signature)}
</td></tr>
<tr><td align="center" style="padding:18px 24px 0;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};">
{footer_html}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def render_plain(
    *,
    text: str = "",
    greeting: str | None = None,
    rows=None,
    bullets=None,
    bullets_title: str | None = None,
    closing: str | None = None,
    cta: tuple[str, str] | None = None,
    signature: str | None = None,
    links=None,
) -> str:
    """The plain-text version of the same email (also what the discussion stores)."""
    lines: list[str] = []
    if greeting:
        lines += [greeting, ""]
    if text and text.strip():
        lines += [text.strip(), ""]
    if rows:
        lines += [f"{label} : {value}" for label, value in rows] + [""]
    if bullets:
        if bullets_title:
            lines.append(bullets_title)
        lines += [f"- {item}" for item in bullets] + [""]
    if cta and cta[0] and cta[1]:
        lines += [f"{cta[0]} : {cta[1]}", ""]
    if closing:
        lines += [closing, ""]
    if links:
        lines += [f"{label} : {url}" for label, url in links if label and url] + [""]
    signature = DEFAULT_SIGNATURE if signature is None else signature
    if signature.strip():
        lines.append(signature.strip())
    return "\n".join(lines).strip()
