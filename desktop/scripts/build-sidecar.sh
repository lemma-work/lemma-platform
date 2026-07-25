#!/usr/bin/env bash
# Build the durable control daemon and platform runtime helpers. The native
# backend/frontend and managed guest are verified release artifacts installed
# on demand, so no frozen Python compatibility supervisor is shipped.
#
# Output: desktop/binaries/lemma-locald-<target-triple>, lemma-runtime, and
# lemma-vz. Platform Tauri configs pick them up via externalBin.
set -euo pipefail

cd "$(dirname "$0")/../.."

TRIPLE="${LEMMA_SIDECAR_TRIPLE:-aarch64-apple-darwin}"
OUT_DIR="desktop/binaries"
mkdir -p "$OUT_DIR"
cargo build --manifest-path locald/Cargo.toml --release --target "$TRIPLE"
cp "locald/target/$TRIPLE/release/lemma-locald" "$OUT_DIR/lemma-locald-$TRIPLE"
cargo build --manifest-path local-runtime/hostctl/Cargo.toml --release --target "$TRIPLE"
cp "local-runtime/hostctl/target/$TRIPLE/release/lemma-runtime" \
  "$OUT_DIR/lemma-runtime-$TRIPLE"
swift build --package-path local-runtime/macos-vz -c release --arch arm64
cp "local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz" \
  "$OUT_DIR/lemma-vz-$TRIPLE"
# Re-seal copied Mach-O sidecars before executing smoke tests. Cargo's linker
# signature is valid for the build output inode, but overwriting an existing
# destination can leave macOS's executable-signature cache rejecting that
# copied vnode with SIGKILL until it is explicitly signed again.
codesign --force --sign - --options runtime "$OUT_DIR/lemma-locald-$TRIPLE"
codesign --force --sign - --options runtime "$OUT_DIR/lemma-runtime-$TRIPLE"
# Virtualization.framework checks the code signature of the executable that
# creates the VM. Keep the helper as a pre-signed app resource: Tauri applies
# the app entitlement only to its main executable and re-signs externalBin
# sidecars without helper-specific entitlements. Release CI replaces this
# local ad-hoc signature with Developer ID before bundling.
codesign --force --sign - --options runtime \
  --entitlements desktop/entitlements.plist \
  "$OUT_DIR/lemma-vz-$TRIPLE"
echo "locald: $OUT_DIR/lemma-locald-$TRIPLE"
echo "runtime bridge: $OUT_DIR/lemma-runtime-$TRIPLE"
echo "VZ helper: $OUT_DIR/lemma-vz-$TRIPLE"
"$OUT_DIR/lemma-locald-$TRIPLE" --version >/dev/null && echo "locald: smoke ok"
"$OUT_DIR/lemma-runtime-$TRIPLE" --version >/dev/null && echo "runtime bridge: smoke ok"
"$OUT_DIR/lemma-vz-$TRIPLE" --version >/dev/null && echo "VZ helper: smoke ok"
codesign -d --entitlements :- "$OUT_DIR/lemma-vz-$TRIPLE" 2>&1 \
  | grep -F "com.apple.security.virtualization" >/dev/null \
  && echo "VZ helper: virtualization entitlement ok"
