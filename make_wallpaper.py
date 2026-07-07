#!/usr/bin/env python3
"""Render files/wallpaper.jpg — dark desktop with the official AgentMail logo.

Uses the real brand asset (assets/agentmail-logo-landscape.png, the white
dark-mode landscape logo from AgentMail's docs) composited with PIL, so the
wallpaper is reproducible from the repo with no design tool and no SVG deps.
Re-fetch assets: see assets/SOURCES.md.
"""
import os

from PIL import Image, ImageDraw, ImageFilter

W, H = 1280, 720
HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, "assets", "agentmail-logo-landscape.png")
OUT = os.path.join(HERE, "files", "wallpaper.jpg")

img = Image.new("RGB", (W, H), (10, 12, 16))
d = ImageDraw.Draw(img)

# Vertical near-black gradient with a faint blue cast (matches the dark
# desktop the template ships; the logo itself is the only brand element).
for y in range(H):
    t = y / H
    d.line([(0, y), (W, y)], fill=(int(10 + 8 * t), int(12 + 10 * t), int(16 + 22 * t)))

# Soft radial glow behind the logo so it doesn't sit flat on the gradient.
glow = Image.new("L", (W, H), 0)
gd = ImageDraw.Draw(glow)
gd.ellipse([W // 2 - 430, H // 2 - 200, W // 2 + 430, H // 2 + 200], fill=46)
glow = glow.filter(ImageFilter.GaussianBlur(130))
img = Image.composite(Image.new("RGB", (W, H), (34, 52, 86)), img, glow)

# Official AgentMail landscape logo (white variant), centered.
logo = Image.open(LOGO).convert("RGBA")
lw = int(W * 0.58)
lh = int(logo.height * lw / logo.width)
logo = logo.resize((lw, lh), Image.LANCZOS)
img.paste(logo, ((W - lw) // 2, (H - lh) // 2), logo)

img.save(OUT, "JPEG", quality=90, optimize=True)
print(OUT, os.path.getsize(OUT), "bytes")
