"""Generate the social preview card (1280x640, GitHub's og:image size).

Design notes
------------
The card has to communicate one thing in the two seconds someone spends on a
timeline: *the same YAML file means different things to different parsers*. So
the card is a side-by-side of one input and two verdicts, with the differing
tokens highlighted. No stock imagery, no gradient mush.

Palette is deliberately restrained -- warm ink on paper, with a single amber
accent for "this is where it breaks" and a muted red for the wrong value. Warm
editorial, not corporate blue.

Run: python assets/make_cover.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent / "cover.png"

W, H = 1280, 640

INK = (26, 24, 22)
PAPER = (250, 248, 244)
MUTED = (138, 130, 120)
RULE = (223, 217, 208)
AMBER = (196, 132, 42)
WRONG = (176, 68, 52)
RIGHT = (86, 118, 92)

MONO = "C:/Windows/Fonts/consola.ttf"
MONO_B = "C:/Windows/Fonts/consolab.ttf"
SANS_B = "C:/Windows/Fonts/segoeuib.ttf"
SANS = "C:/Windows/Fonts/segoeui.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def main() -> None:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # Subtle paper grain: a few thousand near-invisible dots beat a flat fill.
    for y in range(0, H, 3):
        for x in range(0, W, 3):
            if (x * 7 + y * 13) % 97 == 0:
                d.point((x, y), fill=(244, 241, 236))

    # --- header ------------------------------------------------------------
    d.text((64, 52), "yaml12-bridge", font=font(SANS_B, 52), fill=INK)
    d.text(
        (66, 118),
        "Read, audit and migrate YAML under the 1.2 Core Schema",
        font=font(SANS, 24),
        fill=MUTED,
    )

    # accent rule under the header
    d.rectangle([64, 160, 132, 165], fill=AMBER)

    # --- the three panels --------------------------------------------------
    top = 200
    gap = 24
    panel_w = (W - 64 * 2 - gap * 2) // 3
    panel_h = 300

    panels = [
        ("YOUR FILE", None, MUTED),
        ("PyYAML  ·  YAML 1.1", WRONG, WRONG),
        ("yaml12  ·  YAML 1.2", RIGHT, RIGHT),
    ]

    for index, (title, accent, title_colour) in enumerate(panels):
        x = 64 + index * (panel_w + gap)
        d.rounded_rectangle(
            [x, top, x + panel_w, top + panel_h],
            radius=10,
            fill=(255, 255, 255),
            outline=RULE,
            width=2,
        )
        d.text((x + 22, top + 20), title, font=font(SANS_B, 19), fill=title_colour)
        d.line([x + 22, top + 52, x + panel_w - 22, top + 52], fill=RULE, width=1)

    mono = font(MONO, 21)
    mono_sm = font(MONO, 19)

    # panel 1: the input
    x1 = 64 + 22
    y = top + 74
    for line, colour in [
        ("country: NO", INK),
        ("at: 12:30", INK),
        ("on:", INK),
        ("  push: 1", INK),
        ("hash: 3900e128", INK),
    ]:
        d.text((x1, y), line, font=mono, fill=colour)
        y += 32
    d.text((x1, y + 12), "plain scalars, as written", font=font(SANS, 17), fill=MUTED)

    # panel 2: what 1.1 does
    x2 = 64 + panel_w + gap + 22
    y = top + 74
    for line, colour in [
        ("country: False", WRONG),
        ("at: 750", WRONG),
        ("True:", WRONG),
        ("  push: 1", MUTED),
        ("hash: '3900e128'", MUTED),
    ]:
        d.text((x2, y), line, font=mono_sm, fill=colour)
        y += 32
    d.text((x2, y + 12), "no error, silently wrong", font=font(SANS, 17), fill=WRONG)

    # panel 3: what 1.2 does
    x3 = 64 + (panel_w + gap) * 2 + 22
    y = top + 74
    for line, colour in [
        ("country: 'NO'", AMBER),
        ("at: '12:30'", AMBER),
        ("on:", AMBER),
        ("  push: 1", MUTED),
        ("hash: 3.9e+131", AMBER),
    ]:
        d.text((x3, y), line, font=mono_sm, fill=colour)
        y += 32
    d.text((x3, y + 12), "no error, silently different", font=font(SANS, 17), fill=AMBER)

    # --- footer ------------------------------------------------------------
    d.line([64, 548, W - 64, 548], fill=RULE, width=1)
    d.text(
        (64, 570),
        "same file  ·  two parsers  ·  two different values",
        font=font(SANS, 23),
        fill=INK,
    )
    d.text(
        (W - 64 - 372, 574),
        "github.com/linmoxi33/yaml12-bridge",
        font=font(MONO, 18),
        fill=MUTED,
    )

    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {W}x{H})")


if __name__ == "__main__":
    main()
