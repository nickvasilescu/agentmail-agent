#!/usr/bin/env python3
"""Render files/wallpaper.jpg — the AgentMail × Orgo co-branded desktop.

Pure PIL so the wallpaper is reproducible from the repo (no design tool).
Run on macOS (uses Helvetica); any TrueType sans works if you swap FONT.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720
FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "files", "wallpaper.jpg")

img = Image.new("RGB", (W, H), (10, 12, 16))
d = ImageDraw.Draw(img)

# Vertical near-black gradient with a faint blue cast (AgentMail dark look).
for y in range(H):
    t = y / H
    r = int(10 + 8 * t)
    g = int(12 + 10 * t)
    b = int(16 + 22 * t)
    d.line([(0, y), (W, y)], fill=(r, g, b))

# Soft radial glow behind the lockup.
glow = Image.new("L", (W, H), 0)
gd = ImageDraw.Draw(glow)
gd.ellipse([W // 2 - 420, H // 2 - 260, W // 2 + 420, H // 2 + 180], fill=70)
glow = glow.filter(ImageFilter.GaussianBlur(120))
img = Image.composite(Image.new("RGB", (W, H), (38, 66, 110)), img, glow)
d = ImageDraw.Draw(img)

# Subtle "mail route" dotted arcs, like envelopes in flight.
for k, (cy, amp, phase, col) in enumerate([
    (170, 26, 0.0, (66, 96, 140)),
    (560, 22, 1.8, (52, 76, 112)),
]):
    for i in range(0, 170):
        x = i * (W / 170.0)
        y = cy + amp * math.sin(phase + x / 150.0)
        if i % 4 < 2:
            d.ellipse([x - 1.4, y - 1.4, x + 1.4, y + 1.4], fill=col)

def font(size, weight=0):
    return ImageFont.truetype(FONT, size, index=weight)

def center(text, f):
    return (W - d.textlength(text, font=f)) / 2

# Envelope glyph — clean geometric mark above the wordmark.
ex, ey, ew, eh = W // 2 - 46, 168, 92, 64
d.rounded_rectangle([ex, ey, ex + ew, ey + eh], radius=10,
                    outline=(235, 240, 248), width=4)
d.line([(ex + 4, ey + 8), (ex + ew // 2, ey + eh // 2 + 6),
        (ex + ew - 4, ey + 8)], fill=(235, 240, 248), width=4, joint="curve")

# Wordmark lockup.
f_big = font(64, 1)
f_x = font(34)
f_sub = font(26)
f_tag = font(20)

y = 280
t1 = "AgentMail"
t2 = "  ×  "
t3 = "Orgo"
w1 = d.textlength(t1, font=f_big)
w2 = d.textlength(t2, font=f_x)
w3 = d.textlength(t3, font=f_big)
x0 = (W - (w1 + w2 + w3)) / 2
d.text((x0, y), t1, font=f_big, fill=(240, 244, 250))
d.text((x0 + w1, y + 22), t2, font=f_x, fill=(120, 150, 195))
d.text((x0 + w1 + w2, y), t3, font=f_big, fill=(126, 176, 255))

sub = "Your agent has an inbox. And a computer."
d.text((center(sub, f_sub), y + 96), sub, font=f_sub, fill=(168, 182, 205))

tag = "Email it anything — it replies within a minute, work included."
d.text((center(tag, f_tag), y + 142), tag, font=f_tag, fill=(110, 126, 152))

badge = "RUN WITH ORGO"
f_badge = font(17, 1)
bw = d.textlength(badge, font=f_badge)
bx, by = (W - bw) / 2, H - 92
d.rounded_rectangle([bx - 22, by - 12, bx + bw + 22, by + 30], radius=21,
                    outline=(96, 140, 210), width=2)
d.text((bx, by), badge, font=f_badge, fill=(150, 186, 240))

img.save(OUT, "JPEG", quality=88, optimize=True)
print(OUT, os.path.getsize(OUT), "bytes")
