#!/usr/bin/env bash
# Regenerate app/fonts/inter-latin-wght-normal.subset.woff2.
#
# Inter is the only face preloaded on every route, so its bytes sit on the
# critical path for every visitor. Fontsource ships the file Google publishes,
# but Google *serves* a tighter cut of it: its `latin` @font-face declares the
# unicode-range below and ships only those glyphs, 47.1 KB against Fontsource's
# whole-latin file. Self-hosting is meant to change where the bytes come from,
# not how many of them land on the critical path, so the file is cut to the same
# range here and the bundle budget stays where it was.
#
# The range is Google's own, copied from the `/* latin */` block of
#   https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap
# It is pasted rather than fetched because a build that reaches for
# fonts.googleapis.com is the thing this whole change exists to remove. Re-run
# this by hand when the Fontsource package is upgraded; the output is committed.
#
# Usage:  ./scripts/subset-inter.sh
set -Eeuo pipefail

cd "$(dirname "$0")/.."

SOURCE="node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2"
OUTPUT="app/fonts/inter-latin-wght-normal.subset.woff2"

# Exactly Google's `latin` subset for Inter.
RANGE="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"

if [[ ! -f "$SOURCE" ]]; then
  echo "error: $SOURCE is missing — run 'npm ci' first" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

# Two steps, and the first is what keeps this at parity.
#
# Fontsource's file carries the whole 100-900 weight axis. The design has only
# ever had 300-600: that is what the Google loader was asked for, so a
# `font-weight: 700` in the stylesheets — and there are several — has always
# been clamped to 600 by the browser. Narrowing the axis here reproduces exactly
# that, and shipping 100-900 instead would silently start rendering those rules
# at a real 700, which is a typography change nobody asked for.
#
# uv rather than a project dependency: fonttools is needed to *produce* this
# file, never to build or run the app, so it does not belong in package.json.
TRIMMED="$(mktemp -t inter-wght).woff2"
trap 'rm -f "$TRIMMED"' EXIT

uv run --quiet --with "fonttools[woff]==4.60.1" \
  fonttools varLib.instancer "$SOURCE" \
  wght=300:600 \
  --output="$TRIMMED"

uv run --quiet --with "fonttools[woff]==4.60.1" \
  pyftsubset "$TRIMMED" \
  --output-file="$OUTPUT" \
  --flavor=woff2 \
  --layout-features='*' \
  --unicodes="$RANGE"

before=$(wc -c <"$SOURCE" | tr -d ' ')
after=$(wc -c <"$OUTPUT" | tr -d ' ')
printf 'inter subset: %s -> %s bytes (%d%% of the full latin file)\n' \
  "$before" "$after" $((after * 100 / before))
