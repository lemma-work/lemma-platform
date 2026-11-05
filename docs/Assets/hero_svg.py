"""The README's hero: what a teammate makes, not what it says. Three real
scenes, the apps teammates built, a workflow one runs, a draft waiting for a person,
cross-fade in one window while the line beside it names each one and a thin
bar fills for as long as it is up. SVG + CSS, so it plays inside a README.

    uv run python hero_svg.py Screenshots/hero <light|dark> <out.svg>
"""
import base64
import sys

if len(sys.argv) != 4 or sys.argv[2] not in ("light", "dark"):
    sys.exit(__doc__)
d, theme, out = sys.argv[1:4]
dark = theme == "dark"


def data(path):
    return "data:image/jpeg;base64," + base64.b64encode(open(path, "rb").read()).decode()


SCENES = [
    ("grid", "Apps", ["It builds the screens your team", "works in, on the data it keeps."]),
    ("june-workflow", "Workflows", ["It runs the process end to end,", "and asks a person where it matters."]),
    ("remy-dealdesk", "People", ["It drafts the reply.", "A person reviews it and sends."]),
]
# The grid: four real apps the sample teammates built, by name only.
APPS = [("app-kit", "Feedback loop"), ("app-june", "Customer launchpad"), ("app-remy", "Deal desk"), ("app-scout", "Evidence notebook")]

W, H = 1000, 500
FX, FY, FW = 352, 22, 630          # the window
FH = H - 2 * FY
T = 4.5                            # seconds per scene
P = T * len(SCENES)
on = 100 / len(SCENES)             # share of the cycle a scene is up

violet = "#a594ff" if dark else "#5a3fd4"
ink = "#ece9f7" if dark else "#14121c"
ink2 = "#9a95ad" if dark else "#6b6778"
track = "#2a2540" if dark else "#ebe7fb"
frame = "#1a1729" if dark else "#ffffff"
edge = "#2e2848" if dark else "#e6e1f8"

grid_bg = "#141221" if dark else "#f6f5fa"
tile_bg = "#1d1a2e" if dark else "#ffffff"


def grid(delay):
    pad, gap = 22, 16
    tw = (FW - 2 * pad - gap) / 2
    ih = tw * 9 / 16
    th = (FH - 2 * pad - gap) / 2
    parts = [f'<rect x="{FX}" y="{FY}" width="{FW}" height="{FH}" rx="12" fill="{grid_bg}"/>']
    for k, (img, name) in enumerate(APPS):
        x = FX + pad + (k % 2) * (tw + gap)
        y = FY + pad + (k // 2) * (th + gap)
        parts.append(
            f'<g class="tile" style="animation-delay:{delay + 0.15 + k * 0.12:.2f}s">'
            f'<clipPath id="t{k}"><rect x="{x:.1f}" y="{y:.1f}" width="{tw:.1f}" height="{ih:.1f}" rx="9"/></clipPath>'
            f'<rect x="{x - 4:.1f}" y="{y - 4:.1f}" width="{tw + 8:.1f}" height="{th + 4:.1f}" rx="12" fill="{tile_bg}" stroke="{edge}"/>'
            f'<image href="{data(f"{d}/{img}.jpg")}" x="{x:.1f}" y="{y:.1f}" width="{tw:.1f}" height="{ih:.1f}" clip-path="url(#t{k})" preserveAspectRatio="xMidYMin slice"/>'
            f'<text x="{x + 4:.1f}" y="{y + ih + 25:.1f}" class="app">{name}</text></g>'
        )
    return f'<g class="shot first" style="animation-delay:{delay}s">' + "".join(parts) + "</g>"


shots, labels = [], []
for i, (img, name, lines) in enumerate(SCENES):
    delay = i * T
    if img == "grid":
        shots.append(grid(delay))
    else:
        shots.append(
            f'<image href="{data(f"{d}/{img}.jpg")}" x="{FX}" y="{FY}" width="{FW}" height="{FW * 1490 / 1752:.1f}" '
            f'clip-path="url(#c)" class="shot" style="animation-delay:{delay}s"/>'
        )
    y = 120 + i * 112
    labels.append(
        f'<g class="lab" style="animation-delay:{delay}s">'
        f'<text x="24" y="{y}" class="name">{name}</text>'
        + "".join(f'<text x="24" y="{y + 28 + k * 22}" class="says">{t}</text>' for k, t in enumerate(lines))
        + "</g>"
        f'<rect x="0" y="{y - 22}" width="2" height="78" class="track"/>'
        f'<rect x="0" y="{y - 22}" width="2" height="78" class="bar" style="animation-delay:{delay}s"/>'
    )

fade_in, fade_out = 3, on + 4      # the next is fully in before this one leaves
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="What Lemma teammates make: apps the team works in, a workflow that asks a person before it acts, and a reply drafted for a person to review.">
<defs>
  <clipPath id="c"><rect x="{FX}" y="{FY}" width="{FW}" height="{FH}" rx="12"/></clipPath>
  <filter id="s" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="18" stdDeviation="20" flood-color="{'#000' if dark else '#2b1a8a'}" flood-opacity="{.55 if dark else .14}"/></filter>
</defs>
<style>
  text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  .name {{ font-size: 22px; font-weight: 500; fill: {ink}; }}
  .says {{ font-size: 15.5px; fill: {ink2}; }}
  .app {{ font-size: 14px; font-weight: 500; fill: {ink}; }}
  .tile {{ animation: tile {P}s ease-out infinite backwards; }}
  @keyframes tile {{ 0% {{ opacity: 0; transform: translateY(10px); }} 4%, 100% {{ opacity: 1; transform: none; }} }}
  .track {{ fill: {track}; }}
  .bar {{ fill: {violet}; transform-box: fill-box; transform-origin: top; transform: scaleY(0); animation: fill {P}s linear infinite backwards; }}
  .lab {{ opacity: .38; animation: lit {P}s ease-in-out infinite backwards; }}
  .shot {{ opacity: 0; animation: show {P}s ease-in-out infinite backwards; }}
  @keyframes show {{ 0% {{ opacity: 0; transform: translateY(10px); }} {fade_in}% {{ opacity: 1; transform: none; }} {on}% {{ opacity: 1; transform: translateY(-8px); }} {fade_out:.1f}% {{ opacity: 1; }} {fade_out + 2:.1f}%, 100% {{ opacity: 0; }} }}
  @keyframes lit {{ 0% {{ opacity: .38; }} {fade_in}%, {on - 2:.1f}% {{ opacity: 1; }} {on + 1:.1f}%, 100% {{ opacity: .38; }} }}
  @keyframes fill {{ 0% {{ transform: scaleY(0); opacity: 1; }} {on:.1f}% {{ transform: scaleY(1); opacity: 1; }} {on + 2:.1f}%, 100% {{ transform: scaleY(1); opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{
    .shot, .lab, .bar, .tile {{ animation: none; }}
    .first, .lab {{ opacity: 1; }}
  }}
</style>
<g filter="url(#s)"><rect x="{FX - 6}" y="{FY - 6}" width="{FW + 12}" height="{FH + 12}" rx="17" fill="{frame}" stroke="{edge}"/></g>
<rect x="{FX}" y="{FY}" width="{FW}" height="{FH}" rx="12" fill="{grid_bg}"/>
{"".join(shots)}
{"".join(labels)}
</svg>
'''
open(out, "w").write(svg)
print(out, W, H, len(svg) // 1024, "KB")
