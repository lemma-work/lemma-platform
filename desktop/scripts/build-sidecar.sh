#!/usr/bin/env bash
# Build the durable lemma-locald control daemon and the compiled compatibility
# supervisor sidecar (PyInstaller, single file). The supervisor is self-contained: it runs
# `lemma-stack supervise`, which pulls the released images and brings the
# stack up — no runtime checkout or download needed.
#
# Output: desktop/binaries/lemma-locald-<target-triple>, the compatibility
# supervisor, and the uv binary used to install/update lemma-terminal.
# tauri.dist.conf.json picks up both via externalBin.
set -euo pipefail

cd "$(dirname "$0")/../.."

TRIPLE="${LEMMA_SIDECAR_TRIPLE:-aarch64-apple-darwin}"
OUT_DIR="desktop/binaries"
WORK_DIR="$(mktemp -d /tmp/lemma-sidecar.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$OUT_DIR"
cargo build --manifest-path locald/Cargo.toml --release --target "$TRIPLE"
cp "locald/target/$TRIPLE/release/lemma-locald" "$OUT_DIR/lemma-locald-$TRIPLE"
cargo build --manifest-path local-runtime/hostctl/Cargo.toml --release --target "$TRIPLE"
cp "local-runtime/hostctl/target/$TRIPLE/release/lemma-runtime" \
  "$OUT_DIR/lemma-runtime-$TRIPLE"
swift build --package-path local-runtime/macos-vz -c release --arch arm64
cp "local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz" \
  "$OUT_DIR/lemma-vz-$TRIPLE"
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
echo "locald: $OUT_DIR/lemma-locald-$TRIPLE"
echo "runtime bridge: $OUT_DIR/lemma-runtime-$TRIPLE"
echo "VZ helper: $OUT_DIR/lemma-vz-$TRIPLE"
echo "uv: $OUT_DIR/uv-$TRIPLE"
"$OUT_DIR/lemma-supervisor-$TRIPLE" --help >/dev/null && echo "sidecar: smoke ok"
"$OUT_DIR/lemma-locald-$TRIPLE" --version >/dev/null && echo "locald: smoke ok"
"$OUT_DIR/lemma-runtime-$TRIPLE" --version >/dev/null && echo "runtime bridge: smoke ok"
"$OUT_DIR/lemma-vz-$TRIPLE" --version >/dev/null && echo "VZ helper: smoke ok"
"$OUT_DIR/uv-$TRIPLE" --version >/dev/null && echo "uv: smoke ok"
