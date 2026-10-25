#!/usr/bin/env python3
"""Build the HelioFITS Cache Manager icon from cachemanager_icon.svg: an open book with the Sun printed across its pages.

Follows HelioFITS Studio's release/make_app_icon.py so the suite's icons are made the same way:
the .icns carries the art masked to macOS's squircle (824 of 1024 px, exponent 4.6), and the asset
catalog gets the unmasked square, because macOS applies its own shape to a catalog icon and insets
art that is already a squircle onto a grey plate.

  python3 make_app_icon.py   ->  Assets.xcassets/AppIcon.appiconset/, CacheManager.icns,
                                  cachemanager_icon_1024.png, preview.png

tools/make_app.sh compiles Assets.xcassets with actool, or copies CacheManager.icns in as AppIcon.icns
where actool is missing.

Needs cairosvg, Pillow and numpy.
"""
import io, json, math, os, sys
import cairosvg
import numpy as np
from PIL import Image, ImageDraw

S = 1024
BODY = 824 / 1024
N = 4.6
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else HERE

APPICONSET = [("16x16", "1x", 16), ("16x16", "2x", 32), ("32x32", "1x", 32), ("32x32", "2x", 64),
              ("128x128", "1x", 128), ("128x128", "2x", 256), ("256x256", "1x", 256),
              ("256x256", "2x", 512), ("512x512", "1x", 512), ("512x512", "2x", 1024)]


def render(px=S):
    png = cairosvg.svg2png(url=os.path.join(HERE, "cachemanager_icon.svg"), output_width=px, output_height=px)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def squircle(size=S, frac=BODY, n=N, ss=4):
    big = size * ss
    m = Image.new("L", (big, big), 0)
    c, h = big / 2, big * frac / 2
    pts = []
    for i in range(4800):
        t = 2 * math.pi * i / 4800
        ct, st = math.cos(t), math.sin(t)
        pts.append((c + h * math.copysign(abs(ct) ** (2 / n), ct),
                    c + h * math.copysign(abs(st) ** (2 / n), st)))
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m.resize((size, size), Image.BOX)


def masked(img, frac=BODY):
    """Art scaled into the squircle body, as an .icns draws it (macOS before 26 shapes nothing).

    frac=1 is the shape macOS 26 gives the asset catalog's full square, which the preview uses."""
    body = round(S * frac)
    inner = img.resize((body, body), Image.LANCZOS)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(inner, ((S - body) // 2, (S - body) // 2))
    out.putalpha(squircle(frac=frac))
    px = np.array(out)
    px[px[..., 3] == 0, :3] = 0
    return Image.fromarray(px)


def small_first(img, px):
    """BOX at 32 and below: area coverage keeps edges crisp where LANCZOS would ring."""
    return img.resize((px, px), Image.BOX if px <= 32 else Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    art = render()
    shaped = masked(art)
    dock = masked(art, 0.98)   # the catalog's full square under macOS 26's shape
    dock.save(os.path.join(OUT, "cachemanager_icon_1024.png"))

    assets = os.path.join(OUT, "Assets.xcassets")
    cat = os.path.join(assets, "AppIcon.appiconset")
    os.makedirs(cat, exist_ok=True)
    with open(os.path.join(assets, "Contents.json"), "w") as f:
        json.dump({"info": {"version": 1, "author": "xcode"}}, f, indent=2)
        f.write("\n")
    images = []
    for size, scale, px in APPICONSET:
        name = f"icon_{size}@{scale}.png"
        small_first(art, px).save(os.path.join(cat, name))
        images.append({"idiom": "mac", "size": size, "scale": scale, "filename": name})
    with open(os.path.join(cat, "Contents.json"), "w") as f:
        json.dump({"images": images, "info": {"version": 1, "author": "xcode"}}, f, indent=2)
        f.write("\n")

    shaped.save(os.path.join(OUT, "CacheManager.icns"), sizes=[(16, 16), (32, 32), (64, 64), (128, 128),
                                                         (256, 256), (512, 512), (1024, 1024)])
    preview(dock, os.path.join(OUT, "preview.png"))


def preview(shaped, path):
    """The icon at Dock and Finder sizes on Sunset Dark and Sunset Light, with its siblings if present."""
    sizes = [512, 256, 128, 64, 48, 32, 16]
    W, pad = 1260, 40
    sheet = Image.new("RGBA", (W, 2 * 600), (0, 0, 0, 255))
    for row, bg in enumerate([(0x22, 0x1E, 0x33), (0xF6, 0xEF, 0xE6)]):
        y0 = row * 600
        ImageDraw.Draw(sheet).rectangle([0, y0, W, y0 + 600], fill=bg + (255,))
        x = pad
        for px in sizes:
            icon = small_first(shaped, px)
            sheet.alpha_composite(icon, (x, y0 + (600 - px) // 2))
            x += px + 30
    sheet.convert("RGB").save(path)


if __name__ == "__main__":
    main()
