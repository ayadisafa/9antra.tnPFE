"""The colour of the application's design.

The admin picks one colour in Settings; the rest of the look (the darker
shade of buttons, the soft backgrounds, the dark side menu) is worked out
from it, so every page changes together. The original berry is kept exactly
as it was designed.
"""
from __future__ import annotations

import colorsys
import re

DEFAULT = "#a42c67"

# Colours offered in Settings; any other colour can be typed or picked.
PRESETS = [
    ("Berry", "#a42c67"), ("Ocean", "#1d4ed8"), ("Teal", "#0f766e"), ("Forest", "#15803d"),
    ("Sunset", "#c2410c"), ("Violet", "#6d28d9"), ("Rose", "#be185d"), ("Graphite", "#334155"),
]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean(value: str | None) -> str | None:
    """A colour as "#rrggbb", or None when the text is not one."""
    value = (value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "#" + value
    return value.lower() if _HEX.match(value) else None


def _hls(colour: str) -> tuple[float, float, float]:
    red, green, blue = (int(colour[index:index + 2], 16) / 255 for index in (1, 3, 5))
    return colorsys.rgb_to_hls(red, green, blue)


def _hex(hue: float, lightness: float, saturation: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(hue, max(0.0, min(1.0, lightness)), max(0.0, min(1.0, saturation)))
    return "#{:02x}{:02x}{:02x}".format(round(red * 255), round(green * 255), round(blue * 255))


def _contrast_with_white(colour: str) -> float:
    """How well white text reads on the colour (4.5 and more is comfortable)."""
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (channel(int(colour[index:index + 2], 16) / 255) for index in (1, 3, 5))
    return 1.05 / (0.2126 * red + 0.7152 * green + 0.0722 * blue + 0.05)


def readable(colour: str) -> str:
    """The colour as chosen, or darkened just enough for white text on it to read well."""
    hue, lightness, saturation = _hls(colour)
    while _contrast_with_white(colour) < 4.5 and lightness > 0.05:
        lightness -= 0.02
        colour = _hex(hue, lightness, saturation)
    return colour


def palette(colour: str | None) -> dict[str, str]:
    """CSS variables for a chosen colour. Empty for the original design."""
    colour = clean(colour)
    if colour is None or colour == DEFAULT:
        return {}
    main = readable(colour)
    hue, lightness, saturation = _hls(main)
    if saturation < 0.08:
        hue = 0.72      # a grey has no hue of its own: the dark parts lean to slate
    soft_saturation = min(saturation, 0.55)
    return {
        "--primary-berry": main,
        "--berry-dark": _hex(hue, lightness * 0.8, saturation),
        "--berry-subtle": _hex(hue, 0.965, soft_saturation),
        "--ink": _hex(hue, 0.115, min(saturation, 0.5)),
        "--ink-2": _hex(hue, 0.17, min(saturation, 0.48)),
        "--ink-3": _hex(hue, 0.25, min(saturation, 0.42)),
        "--ink-text": _hex(hue, 0.15, min(saturation, 0.4)),
        "--canvas": _hex(hue, 0.972, min(saturation, 0.35)),
        "--border-color": _hex(hue, 0.93, min(saturation, 0.3)),
    }


def css(colour: str | None) -> str:
    """The style rule that applies a chosen colour, ready to put in a page."""
    variables = palette(colour)
    if not variables:
        return ""
    return ":root{" + ";".join(f"{name}:{value}" for name, value in variables.items()) + "}"


def current_colour(settings) -> str:
    return clean(getattr(settings, "ui_color", None)) or DEFAULT
