"""Cut full-character portraits out of the three concept boards.

The extended PNGs are eyeless torsos — body layers for the puppet rig. A rail
mark needs a whole character, and the boards already have twenty-one of them.

Two things make this more than a chroma key. Arch's arch, Ring's hole and
Grid's four squares are enclosed backdrop, so a border flood alone leaves them
filled — they have to come out on colour, which is safe because enclosed
backdrop is within a couple of units of the corner sample. And the soft contact
shadow cannot be separated from the art by colour at all: Peak's snow sits 43
from the backdrop, the shadow under Arch sits 76, so any threshold that takes
the shadow takes the snow first. The shadow is found by where it is instead —
a wide, flat, desaturated blob pinned to the bottom of the figure.
"""

import pathlib
import numpy as np
from PIL import Image
from scipy import ndimage

SRC = pathlib.Path("public/teammates/extended")
OUT = pathlib.Path("public/teammates/cast")
OUT.mkdir(parents=True, exist_ok=True)

BOARDS = {
    "concepts":   ["knot", "arch", "kite", "coil", "bloom", "stack", "spark"],
    "concepts-b": ["wedge", "cloud", "bolt", "ring", "sprout", "cube", "wave"],
    "concepts-c": ["moon", "prism", "bean", "spool", "grid", "peak", "gem"],
}
SIZE = 512

def cut(board, names):
    im = Image.open(SRC/f"{board}.png").convert("RGB")
    a = np.asarray(im).astype(int)
    h, w, _ = a.shape
    backdrop = np.median(np.concatenate([a[:12, :12].reshape(-1, 3), a[:12, -12:].reshape(-1, 3),
                                         a[-12:, :12].reshape(-1, 3), a[-12:, -12:].reshape(-1, 3)]), axis=0)
    dist = np.abs(a - backdrop).sum(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    val = a.max(axis=2)

    lbl, _ = ndimage.label(dist < 46)                 # exterior, incl. the soft ramp
    edge = set(lbl[0]) | set(lbl[-1]) | set(lbl[:, 0]) | set(lbl[:, -1]); edge.discard(0)
    bg = np.isin(lbl, list(edge)) | (dist < 22)       # plus enclosed backdrop: holes
    fg = ndimage.binary_closing(~bg, np.ones((5, 5)))

    lbl, n = ndimage.label(fg)
    figures = []
    for i in range(1, n+1):
        ys, xs = np.where(lbl == i)
        if len(ys) < 4000: continue
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        if (y1-y0) < 90 or (x1-x0) < 60: continue     # the name captions
        figures.append((i, y0, y1, x0, x1))
    figures.sort(key=lambda b: (0 if b[1] < h*0.45 else 1, b[3]))
    if len(figures) != len(names):
        print(f"  !! {board}: {len(figures)} figures, expected {len(names)}"); return

    for (i, y0, y1, x0, x1), name in zip(figures, names):
        keep = lbl == i
        # Drop the contact shadow: pale, desaturated, wider than tall, and
        # sitting in the bottom fifth of the figure. Snow and cream faces are
        # equally pale but are never all three of those things.
        pale, _ = ndimage.label(keep & (sat < 38) & (val > 222))
        for s in ndimage.find_objects(pale):
            if s is None: continue
            sy, sx = s
            tall, wide = sy.stop-sy.start, sx.stop-sx.start
            if sy.start > y0 + (y1-y0)*0.80 and wide > tall:
                keep[sy, sx] &= ~(pale[sy, sx] > 0)
        keep = ndimage.binary_opening(keep, np.ones((3, 3)))

        alpha = np.where(keep, 255, 0).astype(np.uint8)
        alpha = np.asarray(Image.fromarray(alpha, "L").filter(
            __import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(0.6)))
        piece = Image.fromarray(np.dstack([np.asarray(im), alpha]), "RGBA")
        pad = int(max(y1-y0, x1-x0) * .07)
        side = max(y1-y0, x1-x0) + pad*2
        cx, cy = (x0+x1)//2, (y0+y1)//2
        piece = piece.crop((cx-side//2, cy-side//2, cx+side//2, cy+side//2)).resize((SIZE, SIZE), Image.LANCZOS)
        piece.save(OUT/f"{name}.png")
    print(f"  {board}: {len(figures)} cut")

for board, names in BOARDS.items():
    cut(board, names)

# --- face crops, tile tints, and the two asset sizes ---
import numpy as np
from PIL import Image
from scipy import ndimage

CAST = pathlib.Path("public/teammates/cast")
SOURCES = {n: CAST/f"{n}.png" for n in [
    "knot","arch","kite","coil","bloom","stack","spark","wedge","cloud","bolt","ring",
    "sprout","cube","wave","moon","prism","bean","spool","grid","peak","gem"]}
for n in ("loop", "pleat", "frame"):
    SOURCES[n] = pathlib.Path(f"public/teammates/{n}-v1.png")

BIG, SMALL, ZOOM = 512, 128, 170   # WebP: these are served, never edited

def face_and_tint(path):
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im).astype(int)
    rgb, alpha = a[..., :3], a[..., 3]
    solid = alpha > 140
    # Pupils: near-black, and — the part that matters — ringed by ivory. Stack
    # carries a notebook and stands on dark boots, and the shadowed gaps
    # between those are darker, bigger and better aligned than its own eyes.
    # What no gap has is a sclera around it, so each candidate is judged by
    # the brightness of the ring just outside it.
    dark = solid & (rgb.max(axis=2) < 78)
    lbl, n = ndimage.label(dark)
    area = ndimage.sum(dark, lbl, range(1, n+1))
    floor = (im.size[0]*im.size[1]) * 0.00035
    value = rgb.max(axis=2)
    eyes = []
    for i in sorted((i+1 for i in range(n) if area[i] > floor), key=lambda i: -area[i-1])[:10]:
        blob = lbl == i
        ring = ndimage.binary_dilation(blob, np.ones((9, 9))) & ~blob & solid
        if ring.any() and value[ring].mean() > 170:
            y, x = ndimage.center_of_mass(blob)
            eyes.append((area[i-1], x, y, i))
    best, pick = None, [e[3] for e in eyes[:1]]
    for a in range(len(eyes)):
        for b in range(a+1, len(eyes)):
            (ai, xi, yi, i), (aj, xj, yj, j) = eyes[a], eyes[b]
            dx, dy = abs(xi-xj), abs(yi-yj)
            if dx < im.size[0]*0.02: continue          # one eye split by a highlight
            score = dy/max(dx, 1.0) + (max(ai, aj)/max(1.0, min(ai, aj)) - 1)
            if best is None or score < best:
                best, pick = score, [i, j]
    ys, xs = np.where(np.isin(lbl, pick)) if pick else np.where(solid)
    px, py = xs.mean()/im.size[0], ys.mean()/im.size[1]
    return px, py, len(pick)

rows = []
for name, path in sorted(SOURCES.items()):
    px, py, eyes = face_and_tint(path)
    rows.append((name, round(px, 3), round(py, 3)))
    im = Image.open(path).convert("RGBA")
    im.resize((BIG, BIG), Image.LANCZOS).save(CAST/f"{name}.webp", "WEBP", quality=86, method=6)
    im.resize((SMALL, SMALL), Image.LANCZOS).save(CAST/f"{name}-sm.webp", "WEBP", quality=88, method=6)
    print(f"{name:7} face ({px:.3f},{py:.3f})")

pathlib.Path("src/shell/cast.ts").write_text(
    """/** The cast, and how each one is cropped into a small mark.
 *
 *  `face` is where the eyes are, as a fraction of the portrait: every
 *  character in this cast has two near-black pupils ringed by ivory, so the
 *  centroid of that pair is the face. The mark does not crop to it — cropping
 *  at 170% cuts legs and arms off at every size — so the tile contains the
 *  whole character and the measurement is kept for anything that wants to aim
 *  at a face.
 *
 *  Generated by `scripts/cast-portraits.py`; edit there, not here. */

export const CHARACTERS = [
"""
    + "".join(f'    "{n}",\n' for n, *_ in rows)
    + """] as const;
export type CharacterName = typeof CHARACTERS[number];

export const CAST: Record<CharacterName, { face: [number, number] }> = {
"""
    + "".join(f'    {n}: {{ face: [{l}, {t}] }},\n' for n, l, t in rows)
    + "};\n")
print("wrote src/shell/cast.ts")
