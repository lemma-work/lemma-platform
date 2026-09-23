# Teammate artwork

Preview at `/characters`. Generated source PNGs and concept boards are kept here;
layered puppets combine raster bodies/limbs with SVG eyes. These are simulated
character studies, not live agent activity or fully articulated 3D models.

- `loop-v2/`: Loop still references and body/arm layers.
- `pleat-v2/`, `frame-v2/`: base, torso and arm layers; SVG clips provide boots.
- `extended/`: additional torsos and source concept boards.
- `cast/`: derived portraits.

After changing source art, run `python3 scripts/serve-art.py` (requires Pillow)
to regenerate full-size and 512px `-sm.webp` assets. Preserve transparent alpha.
Rig placement and behavior live under `src/app/characters/`; check greetings,
gaze, pause, reduced motion and small-size rendering when changing assets.
