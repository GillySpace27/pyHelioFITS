#!/usr/bin/env python3
"""Write cachemanager_icon.svg: the HelioFITS Cache Manager icon master, the Sun rising out of an open book.

The Sun is the main shape so it still reads at Dock size. The book is drawn as a thick white
outline with header-card text lines on both pages, for the metadata HelioFITS Core keeps beside the data.

The family look is Heliogram's (gillyspace27/heliogram, icons/make_app_icon.py): the same violet-black
ground (#2F2263 to #15102A since Gilly's family-wide lift of 2026-10-28), the same chrome white
(#E7EAF3) outline at the same weight, and the same dark inside. Each
product's outline names it: a monitor for Heliogram, a file for Peek, a book for the Cache Manager. The art
fills the whole square; make_app_icon.py applies the shape.
"""
import math, os

CHROME, INK, STROKE = "#E7EAF3", "#06070C", 44     # Heliogram's bezel colour, screen colour, bezel weight
LINE = "#8F89B8"                                    # header lines, a muted lavender

CX, CY = 512, 450                     # the Sun's centre
R_DISC = 170
N_RAYS, HALF = 16, 6.2                # rays and their half-width in degrees at the base
R_IN, R_LONG, R_SHORT = 196, 372, 306

X0, X1, YTOP, YBOT = 150, 874, 590, 850   # the book: outer page edges, top and bottom at the gutter
G = (X0 + X1) // 2                        # gutter x
DIP, BULGE = 26, 34                       # how far the page bottoms sag and the tops arch


def rays():
    out = []
    for i in range(N_RAYS):
        a = math.radians(360 * i / N_RAYS - 90)
        h = math.radians(HALF)
        r = R_LONG if i % 2 == 0 else R_SHORT
        p = [(CX + R_IN * math.cos(a - h), CY + R_IN * math.sin(a - h)),
             (CX + r * math.cos(a), CY + r * math.sin(a)),
             (CX + R_IN * math.cos(a + h), CY + R_IN * math.sin(a + h))]
        out.append("M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f Z" % sum(p, ()))
    return " ".join(out)


def page(xo):
    """One page: its top arches up from the gutter, its bottom sags toward it."""
    return (f"M{G},{YTOP + BULGE} C{(G + xo) // 2},{YTOP - BULGE * 0.4:.0f} "
            f"{xo + (G - xo) * 0.35:.0f},{YTOP - BULGE * 0.2:.0f} {xo},{YTOP} "
            f"L{xo},{YBOT - DIP} C{xo + (G - xo) * 0.35:.0f},{YBOT - DIP - BULGE * 0.5:.0f} "
            f"{xo + (G - xo) * 0.65:.0f},{YBOT - DIP - BULGE * 0.4:.0f} {G},{YBOT} Z")


def header_lines(n=3, gap=45, stroke=13, sag=22):
    """On each page, lines of a short keyword squiggle, a gap, then a longer value squiggle,
    like the cards of a FITS header. They sag toward the gutter as the pages do."""
    paths = []
    for side in (-1, 1):
        for k in range(n):
            y0 = YTOP + 80 + gap * k
            if side < 0:
                segs = [(X0 + 52, X0 + 116), (X0 + 142, G - 62 - (k % 3) * 30)]
            else:
                segs = [(G + 62, G + 126 + (k % 2) * 14), (G + 152 + (k % 2) * 14, X1 - 52 - ((k + 1) % 3) * 30)]
            for seg, (a, b) in enumerate(segs):
                pts, x = [], a
                while x <= b:
                    t = 1 - abs(x - G) / (X1 - G)
                    y = y0 + sag * t ** 2.6 + 3.0 * math.sin(x / 4.0 + k * 1.7 + seg + (2.3 if side > 0 else 0))
                    pts.append("%.1f,%.1f" % (x, y))
                    x += 1.5
                paths.append("M" + " L".join(pts))
    return (f'<path d="{" ".join(paths)}" fill="none" stroke="{LINE}" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2F2263"/>
      <stop offset="1" stop-color="#15102A"/>
    </linearGradient>
    <radialGradient id="glow" cx="{CX}" cy="{CY}" r="520" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFB347" stop-opacity="0.55"/>
      <stop offset="0.4" stop-color="#E0692A" stop-opacity="0.22"/>
      <stop offset="1" stop-color="#E0692A" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="disc" cx="{CX - 50}" cy="{CY - 56}" r="210" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFF1B8"/>
      <stop offset="0.45" stop-color="#FFB73D"/>
      <stop offset="1" stop-color="#E5651C"/>
    </radialGradient>
  </defs>

  <rect width="1024" height="1024" fill="url(#ground)"/>
  <rect width="1024" height="1024" fill="url(#glow)"/>

  <!-- the Sun, behind the book -->
  <path d="{rays()}" fill="#F08A2A"/>
  <circle cx="{CX}" cy="{CY}" r="{R_DISC}" fill="url(#disc)"/>

  <!-- the book: dark pages, header lines, then the white outline -->
  <path d="{page(X0)}" fill="{INK}"/>
  <path d="{page(X1)}" fill="{INK}"/>
  {header_lines()}
  <path d="{page(X0)}" fill="none" stroke="{CHROME}" stroke-width="{STROKE}" stroke-linejoin="round"/>
  <path d="{page(X1)}" fill="none" stroke="{CHROME}" stroke-width="{STROKE}" stroke-linejoin="round"/>
</svg>
'''
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cachemanager_icon.svg")
open(out, "w").write(svg)
print(out)
