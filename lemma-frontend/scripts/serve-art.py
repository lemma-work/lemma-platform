"""Derive the served teammate art from the PNG sources.

The rigs were pointed straight at the generated PNGs, which are 1254 square
and around a megabyte each. That is the right thing to keep — they are art,
not output, and nothing regenerates them deterministically — but it is the
wrong thing to send: the character gallery mounts twenty-one of them at once
and pulled about 19MB.

So the sources stay and the served copies are derived. WebP takes a smooth
3D render from ~1000KB to ~100KB at quality 85 with alpha untouched and a
mean error around 2/255, which is not visible at any size these are drawn.
A `-sm` variant at 512 covers everything rendered small — a rail mark, a
gallery card — where even the WebP is more pixels than the screen has.

    python3 scripts/serve-art.py

`public/teammates/cast/` is different: those portraits are themselves derived
(by `cast-portraits.py`, out of the concept boards), so they are WebP only
and carry no PNG.
"""
import pathlib
from PIL import Image

TEAMMATES = pathlib.Path("public/teammates")
FULL_Q, SMALL_Q, SMALL_PX = 85, 88, 512

# Everything a rig asks the browser for, by directory.
SERVED = {
    "extended": None,                      # every torso except the boards
    "pleat-v2": ["base", "arms", "body"],
    "frame-v2": ["base", "arms", "body"],
    "loop-v2": ["arm", "body"],
}

def derive(png: pathlib.Path) -> tuple[int, int, int]:
    im = Image.open(png).convert("RGBA")
    full = png.with_suffix(".webp")
    im.save(full, "WEBP", quality=FULL_Q, method=6)
    small = png.with_name(png.stem + "-sm.webp")
    im.resize((SMALL_PX, SMALL_PX), Image.LANCZOS).save(small, "WEBP", quality=SMALL_Q, method=6)
    return png.stat().st_size, full.stat().st_size, small.stat().st_size

before = after = 0
for folder, only in SERVED.items():
    here = TEAMMATES/folder
    if not here.is_dir():
        print(f"  ! {folder} missing"); continue
    names = sorted(p for p in here.glob("*.png")
                   if not p.stem.startswith("concepts") and (only is None or p.stem in only))
    src = full = small = 0
    for png in names:
        a, b, c = derive(png)
        src += a; full += b; small += c
    before += src; after += full + small
    print(f"{folder:10} {len(names):>3} files   png {src/1e6:6.2f}MB -> webp {full/1e6:5.2f}MB + sm {small/1e6:5.2f}MB")

print(f"\nserved art: {before/1e6:.2f}MB of PNG becomes {after/1e6:.2f}MB of WebP")
print(f"a gallery of 21 now costs about {(TEAMMATES/'extended').stat().st_size and sum(p.stat().st_size for p in (TEAMMATES/'extended').glob('*-sm.webp'))/1e6:.2f}MB")
