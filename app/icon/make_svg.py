#!/usr/bin/env python3
"""Write heliad_icon.svg: the Heliad app icon master, the Sun rising out of an open book.

The Sun is the main shape so it still reads at Dock size (an earlier version with a small sun on
the page read only as "a book" at 32-64 px). The book stays underneath as a low cream base, with
header-card text lines on its left page for the metadata Heliad keeps beside the data.

Colours are Studio's Sunset theme (app/Sources/Heliad/Theme.swift): the dark background and
accent family behind, Sunset Light's paper and orange on the book. Art fills the whole square;
make_app_icon.py applies the shape.
"""
import math, os

CX, CY = 512, 470                     # the Sun's centre
R_DISC = 170
N_RAYS, HALF = 16, 6.2                # rays and their half-width in degrees at the base
R_IN, R_LONG, R_SHORT = 196, 372, 306

X0, X1, YTOP, YBOT = 168, 856, 610, 856   # the book: outer page edges, top and bottom at the gutter
G = (X0 + X1) // 2                        # gutter x
DIP, BULGE = 28, 34                       # how far the page bottoms sag and the tops arch


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


def header_lines(n=3, gap=52, stroke=11, sag=22):
    """Each line a short keyword squiggle, a gap, then a longer value squiggle, like a FITS card."""
    x0, x1, y_top = X0 + 40, G - 44, YTOP + 70
    paths = []
    for k in range(n):
        y0 = y_top + gap * k
        for seg, (a, b) in enumerate([(x0, x0 + 62), (x0 + 84, x1 - (k % 3) * 34)]):
            pts, x = [], a
            while x <= b:
                t = (x - x0) / (G - x0)
                y = y0 + sag * t ** 2.6 + 3.0 * math.sin(x / 4.0 + k * 1.7 + seg)
                pts.append("%.1f,%.1f" % (x, y))
                x += 1.5
            paths.append("M" + " L".join(pts))
    return (f'<path d="{" ".join(paths)}" fill="none" stroke="#6E5A46" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>')


svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <radialGradient id="sky" cx="50%" cy="40%" r="75%">
      <stop offset="0" stop-color="#3B3161"/>
      <stop offset="0.55" stop-color="#221E33"/>
      <stop offset="1" stop-color="#120F1C"/>
    </radialGradient>
    <radialGradient id="glow" cx="{CX}" cy="{CY}" r="520" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFB347" stop-opacity="0.75"/>
      <stop offset="0.4" stop-color="#E0692A" stop-opacity="0.30"/>
      <stop offset="1" stop-color="#E0692A" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="disc" cx="{CX - 50}" cy="{CY - 56}" r="210" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFF1B8"/>
      <stop offset="0.45" stop-color="#FFB73D"/>
      <stop offset="1" stop-color="#E5651C"/>
    </radialGradient>
    <linearGradient id="paperL" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#FFFBF5"/>
      <stop offset="0.8" stop-color="#F6EFE6"/>
      <stop offset="1" stop-color="#D9CBB6"/>
    </linearGradient>
    <linearGradient id="paperR" x1="1" y1="0" x2="0" y2="0">
      <stop offset="0" stop-color="#FFFBF5"/>
      <stop offset="0.8" stop-color="#F6EFE6"/>
      <stop offset="1" stop-color="#D9CBB6"/>
    </linearGradient>
    <linearGradient id="cover" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#C8642A"/>
      <stop offset="1" stop-color="#8E3C12"/>
    </linearGradient>
  </defs>

  <rect width="1024" height="1024" fill="url(#sky)"/>
  <rect width="1024" height="1024" fill="url(#glow)"/>

  <!-- the Sun, behind the book -->
  <path d="{rays()}" fill="#F08A2A"/>
  <circle cx="{CX}" cy="{CY}" r="{R_DISC}" fill="url(#disc)"/>

  <!-- the book: cover edge, then the two pages -->
  <path d="M{G},{YTOP + 50} C{G - 80},{YTOP + 4} {X0 - 30},{YTOP - 6} {X0 - 18},{YTOP + 2} L{X0 - 18},{YBOT + 8} C{X0 + 160},{YBOT - 14} {G - 90},{YBOT - 4} {G},{YBOT + 34} C{G + 90},{YBOT - 4} {X1 - 160},{YBOT - 14} {X1 + 18},{YBOT + 8} L{X1 + 18},{YTOP + 2} C{X1 + 30},{YTOP - 6} {G + 80},{YTOP + 4} {G},{YTOP + 50} Z" fill="url(#cover)"/>
  <path d="{page(X0)}" fill="url(#paperL)"/>
  <path d="{page(X1)}" fill="url(#paperR)"/>
  {header_lines()}
  <path d="M{G},{YTOP + 34} L{G},{YBOT}" stroke="#8C7F66" stroke-width="3" opacity="0.6"/>
</svg>
'''
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heliad_icon.svg")
open(out, "w").write(svg)
print(out)
