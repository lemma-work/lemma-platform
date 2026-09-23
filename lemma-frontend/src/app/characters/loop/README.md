# Loop study

`/characters/loop` uses `loop-puppet.tsx`: layered raster artwork with SVG eyes,
clipped limbs, pointer gaze, blinks and selectable poses. Assets are in
`public/teammates/loop-v2/`. `loop-scene.tsx` is the earlier procedural 3D study.

Behavior is simulated. Preserve pause, reduced-motion handling, hidden/offscreen
suspension and unmount cleanup. Check greetings, gaze and limb placement visually;
unit tests do not validate animation quality.
