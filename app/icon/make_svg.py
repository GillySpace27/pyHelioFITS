#!/usr/bin/env python3
"""Write heliad_icon.svg: the Heliad app icon master, an open book: the Sun on the right page and
lines of header text on the left, for the data and the metadata Heliad keeps.

Colours are Studio's Sunset theme (app/Sources/Heliad/Theme.swift): the dark background and
accent family behind, Sunset Light's paper and orange on the book.
"""
import math, os

CX, CY = 682, 566          # sun centre, on the right page
R_DISC, R_IN = 64, 78
N_RAYS, HALF = 16, 6.5     # rays and their half-width in degrees at the base
R_LONG, R_SHORT = 124, 102 # alternate long and short rays

rays = []
for i in range(N_RAYS):
    a = math.radians(360 * i / N_RAYS - 90)
    h = math.radians(HALF)
    r = R_LONG if i % 2 == 0 else R_SHORT
    p = [(CX + R_IN * math.cos(a - h), CY + R_IN * math.sin(a - h)),
         (CX + r * math.cos(a), CY + r * math.sin(a)),
         (CX + R_IN * math.cos(a + h), CY + R_IN * math.sin(a + h))]
    rays.append("M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f Z" % sum(p, ()))

# Header text on the left page: each line a short keyword squiggle, a gap, then a value squiggle,
# like a FITS header card. Lines sag toward the gutter as the page does.
def squiggle(x0, x1, y0, phase):
    pts = []
    x = x0
    while x <= x1:
        t = (x - 186) / 326
        y = y0 + 34 * t ** 2.6 + 2.6 * math.sin(x / 3.2 + phase)
        pts.append("%.1f,%.1f" % (x, y))
        x += 1.5
    return "M" + " L".join(pts)

CARDS = [(118, 214), (100, 250), (126, 196), (92, 236), (112, 260), (104, 180), (120, 222)]
text = []
for k, (key, val_end) in enumerate(CARDS):
    y = 452 + 42 * k
    text.append(squiggle(222, 222 + key - 70, y, k * 1.7))
    text.append(squiggle(222 + key - 40, min(222 + val_end, 470), y, k * 2.3 + 1))

LEFT = "M512,402 C432,350 300,338 186,366 L186,756 C300,730 432,738 512,786 Z"
RIGHT = "M512,402 C592,350 724,338 838,366 L838,756 C724,730 592,738 512,786 Z"

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <radialGradient id="sky" cx="50%" cy="38%" r="75%">
      <stop offset="0" stop-color="#3B3161"/>
      <stop offset="0.55" stop-color="#221E33"/>
      <stop offset="1" stop-color="#120F1C"/>
    </radialGradient>
    <radialGradient id="glow" cx="512" cy="460" r="440" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFB347" stop-opacity="0.55"/>
      <stop offset="0.45" stop-color="#E0692A" stop-opacity="0.18"/>
      <stop offset="1" stop-color="#E0692A" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="paperL" x1="186" y1="0" x2="512" y2="0" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFFBF5"/>
      <stop offset="0.8" stop-color="#F6EFE6"/>
      <stop offset="1" stop-color="#D9CBB6"/>
    </linearGradient>
    <linearGradient id="paperR" x1="838" y1="0" x2="512" y2="0" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFFBF5"/>
      <stop offset="0.8" stop-color="#F6EFE6"/>
      <stop offset="1" stop-color="#D9CBB6"/>
    </linearGradient>
    <linearGradient id="foldL" x1="400" y1="0" x2="512" y2="0" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#2B2118" stop-opacity="0"/>
      <stop offset="1" stop-color="#5A3A1E" stop-opacity="0.22"/>
    </linearGradient>
    <linearGradient id="foldR" x1="624" y1="0" x2="512" y2="0" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#2B2118" stop-opacity="0"/>
      <stop offset="1" stop-color="#5A3A1E" stop-opacity="0.22"/>
    </linearGradient>
    <radialGradient id="disc" cx="{CX - 30}" cy="{CY - 34}" r="{R_DISC * 1.35:.0f}" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFE7A6"/>
      <stop offset="0.5" stop-color="#F7A13A"/>
      <stop offset="1" stop-color="#D0581C"/>
    </radialGradient>
    <linearGradient id="cover" x1="0" y1="380" x2="0" y2="830" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#C8642A"/>
      <stop offset="1" stop-color="#8E3C12"/>
    </linearGradient>
    <clipPath id="pages"><path d="{LEFT}"/><path d="{RIGHT}"/></clipPath>
  </defs>

  <rect width="1024" height="1024" fill="url(#sky)"/>
  <rect width="1024" height="1024" fill="url(#glow)"/>
  <g transform="translate(512,512) scale(0.9) translate(-512,-552)">

  <!-- cover, then the page block's edges -->
  <path d="M512,426 C424,382 284,372 160,400 L160,800 C284,776 424,782 512,826 C600,782 740,776 864,800 L864,400 C740,372 600,382 512,426 Z" fill="url(#cover)"/>
  <path d="M512,418 C428,370 292,360 172,388 L172,782 C292,756 428,764 512,810 C596,764 732,756 852,782 L852,388 C732,360 596,370 512,418 Z" fill="#E3D5C0"/>
  <path d="M512,410 C430,360 296,349 179,377 L179,769 C296,743 430,751 512,798 C594,751 728,743 845,769 L845,377 C728,349 594,360 512,410 Z" fill="#EFE4D3"/>

  <!-- pages -->
  <path d="{LEFT}" fill="url(#paperL)"/>
  <path d="{RIGHT}" fill="url(#paperR)"/>

  <!-- the sun, printed across both pages -->
  <g clip-path="url(#pages)">
    <path d="{' '.join(text)}" fill="none" stroke="#7A6450" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>
    <path d="{' '.join(rays)}" fill="#E0782A"/>
    <circle cx="{CX}" cy="{CY}" r="{R_DISC}" fill="url(#disc)"/>
    <rect x="400" y="300" width="112" height="520" fill="url(#foldL)"/>
    <rect x="512" y="300" width="112" height="520" fill="url(#foldR)"/>
  </g>
  <path d="M512,402 L512,786" stroke="#8C7F66" stroke-width="3" opacity="0.6"/>
  </g>
</svg>
'''
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heliad_icon.svg")
open(out, "w").write(svg)
print(out)
