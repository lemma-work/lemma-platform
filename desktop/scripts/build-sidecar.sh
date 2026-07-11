#!/usr/bin/env bash
# Build the compiled lemma-supervisor sidecar (PyInstaller, single file) from
# the lemma-stack package. The sidecar is self-contained: it runs
# `lemma-stack supervise`, which pulls the released images and brings the
# stack up — no runtime checkout or download needed.
#
# Output: desktop/binaries/lemma-supervisor-<target-triple> plus the uv binary
# used to install/update lemma-terminal for Finder-launched desktop sessions.
# tauri.dist.conf.json picks up both via externalBin.
set -euo pipefail

cd "$(dirname "$0")/../.."

TRIPLE="${LEMMA_SIDECAR_TRIPLE:-aarch64-apple-darwin}"
OUT_DIR="desktop/binaries"
WORK_DIR="$(mktemp -d /tmp/lemma-sidecar.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$OUT_DIR"
# Build inside lemma-stack's environment so its deps (typer/rich/tomlkit) and
# package data are discoverable.
( cd lemma-stack && uv run --with pyinstaller pyinstaller \
    --onefile --noconfirm \
    --name lemma-supervisor \
    --collect-data lemma_stack \
    --distpath "$OLDPWD/$OUT_DIR" \
    --workpath "$WORK_DIR/build" \
    --specpath "$WORK_DIR" \
    lemma_stack/sidecar_main.py )

mv "$OUT_DIR/lemma-supervisor" "$OUT_DIR/lemma-supervisor-$TRIPLE"
UV_BIN="$(command -v uv)"
cp "$UV_BIN" "$OUT_DIR/uv-$TRIPLE"
chmod 0755 "$OUT_DIR/uv-$TRIPLE"
echo "sidecar: $OUT_DIR/lemma-supervisor-$TRIPLE"
echo "uv: $OUT_DIR/uv-$TRIPLE"
"$OUT_DIR/lemma-supervisor-$TRIPLE" --help >/dev/null && echo "sidecar: smoke ok"
"$OUT_DIR/uv-$TRIPLE" --version >/dev/null && echo "uv: smoke ok"
