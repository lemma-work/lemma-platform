#!/usr/bin/env bash
set -euo pipefail

# Resize the sandbox display to a given size, so what a person is watching
# matches the shape of the pane they are watching it in.
#
# Usage: set-display-size WIDTH HEIGHT
#
# Three things make this less obvious than `xrandr -s`:
#
# **Xvfb offers exactly one mode** -- the one it was started with. `xrandr -s
# 1440x960` answers "Size 1440x960 not found in available modes", so a mode has
# to be created before it can be chosen. The timings are invented: there is no
# hardware here to satisfy, and a virtual display accepts a modeline whose
# porches are simply the next few integers.
#
# **`--fb` on its own is not enough.** It resizes the framebuffer while leaving
# the output's CRTC covering the old, larger area, which X refuses with
# BadValue. Setting a mode moves both.
#
# **The maximum is fixed at startup.** RandR cannot grow the framebuffer Xvfb
# allocated, so `WORKSPACE_XVFB_MAX_SCREEN` is a ceiling and a request above it
# is clamped rather than failed -- a viewer with a big window should get the
# biggest display available, not an error.
#
# Chrome follows the new size on its own because a window manager is running
# (see `start-browser`); without one it would keep the geometry it launched
# with and the resize would only change how much empty desktop was on screen.

if [ "$#" -ne 2 ]; then
  echo "usage: set-display-size WIDTH HEIGHT" >&2
  exit 64
fi

want_width="$1"
want_height="$2"
case "$want_width$want_height" in
  *[!0-9]*)
    echo "set-display-size: width and height must be whole numbers" >&2
    exit 64
    ;;
esac

export DISPLAY="${DISPLAY:-:99}"
max_screen="${WORKSPACE_XVFB_MAX_SCREEN:-1920x1200x24}"
max_width="${max_screen%%x*}"
max_rest="${max_screen#*x}"
max_height="${max_rest%%x*}"

# Clamped, and to something even: odd widths upset some X servers and no
# viewer cares about one pixel.
[ "$want_width" -gt "$max_width" ] && want_width="$max_width"
[ "$want_height" -gt "$max_height" ] && want_height="$max_height"
[ "$want_width" -lt 320 ] && want_width=320
[ "$want_height" -lt 240 ] && want_height=240
want_width=$((want_width - want_width % 2))
want_height=$((want_height - want_height % 2))

# Wait for the X server to actually answer, rather than for its socket to
# exist. `start-browser` calls this the moment Xvfb's socket appears, and a
# server that has bound but is not yet serving RandR answers nothing: the
# output name comes back empty, every `xrandr` below fails, and the display
# silently stays at the framebuffer's full size. That is how the initial
# resize came to be a no-op while the identical command worked by hand a
# second later. Bounded, because a server that is never going to answer must
# not hold the browser's start.
waited=0
while [ "$waited" -lt 60 ] && ! xrandr --current >/dev/null 2>&1; do
  sleep 0.05
  waited=$((waited + 1))
done

current="$(xrandr --current 2>/dev/null | awk '/^Screen 0:/ {print $8 "x" $10}' | tr -d ',')"
if [ "$current" = "${want_width}x${want_height}" ]; then
  echo "${want_width}x${want_height}"
  exit 0
fi

output="$(xrandr --current 2>/dev/null | awk '/ connected/ {print $1; exit}')"
output="${output:-screen}"
mode="${want_width}x${want_height}_lemma"
clock="$(awk -v w="$want_width" -v h="$want_height" \
  'BEGIN { printf "%.2f", (w * h * 60) / 1000000 }')"

# Attempted, then *checked*, then attempted again.
#
# `xrandr --newmode` against an Xvfb that x11vnc has not attached to yet exits
# 0 and creates nothing -- measured by running the identical command either
# side of starting x11vnc on one display. A script that trusted its own exit
# codes therefore reported a resize it had not performed, and the display sat
# at its full size while every caller believed otherwise. So the postcondition
# is read back from the server, and the only success is the size actually
# being what was asked for.
attempt=0
while [ "$attempt" -lt 30 ]; do
  if ! xrandr --current 2>/dev/null | grep -q "[[:space:]]${mode}[[:space:]]"; then
    # The timings are invented; there is no hardware here to satisfy.
    xrandr --newmode "$mode" "$clock" \
      "$want_width" "$((want_width + 1))" "$((want_width + 2))" "$((want_width + 3))" \
      "$want_height" "$((want_height + 1))" "$((want_height + 2))" "$((want_height + 3))" \
      >/dev/null 2>&1 || true
    xrandr --addmode "$output" "$mode" >/dev/null 2>&1 || true
  fi
  xrandr --output "$output" --mode "$mode" >/dev/null 2>&1 || true

  now="$(xrandr --current 2>/dev/null | awk '/^Screen 0:/ {print $8 "x" $10}' | tr -d ',')"
  if [ "$now" = "${want_width}x${want_height}" ]; then
    echo "$now"
    exit 0
  fi
  sleep 0.1
  attempt=$((attempt + 1))
done

echo "set-display-size: the display stayed at ${now:-unknown}" >&2
exit 1
