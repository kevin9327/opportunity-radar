# -*- coding: utf-8 -*-
"""Title and explainer cards for the demo video, drawn to match the app."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "cards"
W, H = 1280, 776
BG, PANEL, LINE = (8, 17, 28), (15, 28, 46), (29, 48, 74)
INK, DIM, CYAN, OK, WARN = (232, 239, 247), (139, 163, 191), (49, 208, 245), (56, 211, 159), (255, 180, 84)


def font(sz, bold=True):
    p = r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"
    return ImageFont.truetype(p, sz)


def base():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    for y in range(260):  # soft glow at the top, like the app
        a = int(18 * (1 - y / 260))
        d.line([(0, y), (W, y)], fill=(BG[0] + a, BG[1] + a + 4, BG[2] + a + 8))
    return im, d


def centred(d, text, f, y, fill=INK):
    w = d.textlength(text, font=f)
    d.text(((W - w) / 2, y), text, font=f, fill=fill)


def title_card():
    im, d = base()
    d.ellipse([(W // 2 - 74, 128), (W // 2 + 74, 276)], outline=CYAN, width=3)
    centred(d, "Opportunity Radar", font(66), 330)
    centred(d, "Ask out loud what public funding you can actually apply for", font(28, False), 416, DIM)
    centred(d, "a self-hosted MCP server for Alexa+", font(24, False), 462, CYAN)
    centred(d, "Build, Ship, Shape · Alexa+ track", font(21, False), 690, DIM)
    im.save(OUT / "title.png")


def problem_card():
    im, d = base()
    centred(d, "Billions in public funding", font(56), 190)
    centred(d, "goes unclaimed every year", font(56), 262)
    centred(d, "Not because people don't qualify —", font(30, False), 380, DIM)
    centred(d, "because nobody reads a government database on a Tuesday night.", font(30, False), 428, DIM)
    d.rounded_rectangle([(300, 530), (980, 620)], 14, fill=PANEL, outline=LINE)
    centred(d, "“Alexa, any grants open for artificial intelligence?”", font(28, False), 560, CYAN)
    im.save(OUT / "problem.png")


def honesty_card():
    im, d = base()
    centred(d, "It refuses to guess", font(54), 92)
    centred(d, "the honesty rules are structural, and the tests assert them", font(24, False), 168, DIM)
    rows = [
        ("Source is down", "says the data is unavailable — never an estimate"),
        ("No applicant list published", "“read the notice”, not “you're ineligible”"),
        ("No published deadline", "counted and reported, never quietly dropped"),
        ("Model ranks fit", "may set a score and reason — never a number"),
    ]
    y = 248
    for left, right in rows:
        d.rounded_rectangle([(120, y), (1160, y + 92)], 13, fill=PANEL, outline=LINE)
        d.text((152, y + 20), left, font=font(27), fill=WARN)
        d.text((152, y + 54), right, font=font(23, False), fill=DIM)
        y += 106
    im.save(OUT / "honesty.png")


def stack_card():
    im, d = base()
    centred(d, "What it is made of", font(52), 96)
    items = [
        ("MCP, spec 2025-11-25", "Streamable HTTP, implemented by hand — no framework in the way"),
        ("grants.gov, live", "public API, no key; 6-hour cache; two date formats handled"),
        ("A model on your machine", "Ollama by default; Bedrock and keyword overlap as labelled fallbacks"),
        ("21 tests + weekly CI", "offline suite on every push, live-source check every Monday"),
    ]
    y = 200
    for head, sub in items:
        d.rounded_rectangle([(120, y), (1160, y + 108)], 13, fill=PANEL, outline=LINE)
        d.ellipse([(150, y + 44), (168, y + 62)], fill=CYAN)
        d.text((196, y + 24), head, font=font(28), fill=INK)
        d.text((196, y + 62), sub, font=font(22, False), fill=DIM)
        y += 122
    im.save(OUT / "stack.png")


def end_card():
    im, d = base()
    centred(d, "Opportunity Radar", font(58), 210)
    centred(d, "It refuses to guess.", font(34, False), 300, CYAN)
    d.rounded_rectangle([(330, 400), (950, 476)], 14, fill=PANEL, outline=LINE)
    centred(d, "github.com/kevin9327/opportunity-radar", font(27, False), 424, INK)
    centred(d, "MIT · Alexa+ track · Open Source mini challenge", font(22, False), 540, DIM)
    im.save(OUT / "end.png")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    title_card(); problem_card(); honesty_card(); stack_card(); end_card()
    print("cards:", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))
