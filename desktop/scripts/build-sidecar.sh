#!/usr/bin/env bash
# Build the durable control daemon and platform runtime helpers. The native
# backend/frontend and managed guest are verified release artifacts installed
# on demand, so no frozen Python compatibility supervisor is shipped.
#
# Output: desktop/binaries/lemma-locald-<target-triple>,
# lemma-agent-host-<target-triple>, lemma-runtime, and lemma-vz. Platform Tauri
# configs pick them up via externalBin.
set -euo pipefail

cd "$(dirname "$0")/../.."

TRIPLE="${LEMMA_SIDECAR_TRIPLE:-aarch64-apple-darwin}"
OUT_DIR="desktop/binaries"
mkdir -p "$OUT_DIR"
# One invocation, not three. These used to be separate crates with separate
# target directories; now they share one, and asking cargo for them separately
# would resolve features over three different package sets — rebuilding reqwest,
# tokio and hyper from scratch each time, every time.
cargo build --manifest-path desktop/Cargo.toml --release --target "$TRIPLE" \
  -p lemma-locald -p lemma-agent-host -p lemma-runtime
BUILT="desktop/target/$TRIPLE/release"
cp "$BUILT/lemma-locald" "$OUT_DIR/lemma-locald-$TRIPLE"
cp "$BUILT/lemma-agent-host" "$OUT_DIR/lemma-agent-host-$TRIPLE"
cp "$BUILT/lemma-runtime" "$OUT_DIR/lemma-runtime-$TRIPLE"
swift build --package-path desktop/local-runtime/macos-vz -c release --arch arm64
cp "desktop/local-runtime/macos-vz/.build/arm64-apple-macosx/release/lemma-vz" \
  "$OUT_DIR/lemma-vz-$TRIPLE"
# Prefer a real Developer ID over an ad-hoc signature when the machine has one.
#
# This is about locald's access to the OS credential vault, not about Gatekeeper.
# macOS ties each stored secret to the code identity of the program that created
# it, and an ad-hoc signature has no stable identity to tie to: its designated
# requirement pins the exact code hash, so every rebuild reads as a different
# program and the user is asked to authorise access again on the next launch.
# A Developer ID requirement names the identifier and the team instead, which is
# why locald carries an embedded Info.plist to keep that identifier fixed.
#
# Explicit beats discovered: CI sets APPLE_SIGNING_IDENTITY, including to "-" for
# builds that are deliberately untrusted, and that choice is honoured as given.
SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:-}"
if [[ -z "${SIGNING_IDENTITY}" ]]; then
  SIGNING_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: .*\)".*/\1/p' | head -1)"
fi
SIGNING_IDENTITY="${SIGNING_IDENTITY:--}"
if [[ "${SIGNING_IDENTITY}" == "-" ]]; then
  echo "signing: ad-hoc (no Developer ID identity available)"
  echo "signing: expect a keychain prompt on each rebuild of locald"
else
  echo "signing: ${SIGNING_IDENTITY}"
fi

sign() {
  local target="$1"
  shift
  local args=(--force --options runtime --sign "${SIGNING_IDENTITY}" "$@")
  # A timestamp needs the network and means nothing ad-hoc, so it is only worth
  # paying for on the signatures that can actually be notarised.
  if [[ "${SIGNING_IDENTITY}" != "-" ]]; then
    args+=(--timestamp)
  fi
  codesign "${args[@]}" "${target}"
}

# Re-seal copied Mach-O sidecars before executing smoke tests. Cargo's linker
# signature is valid for the build output inode, but overwriting an existing
# destination can leave macOS's executable-signature cache rejecting that
# copied vnode with SIGKILL until it is explicitly signed again.
sign "$OUT_DIR/lemma-locald-$TRIPLE"
sign "$OUT_DIR/lemma-agent-host-$TRIPLE"
sign "$OUT_DIR/lemma-runtime-$TRIPLE"
# Virtualization.framework checks the code signature of the executable that
# creates the VM. Keep the helper as a pre-signed app resource: Tauri applies
# the app entitlement only to its main executable and re-signs externalBin
# sidecars without helper-specific entitlements. Release CI re-signs this helper
# with Developer ID and its entitlement before bundling.
sign "$OUT_DIR/lemma-vz-$TRIPLE" --entitlements desktop/entitlements.plist
echo "locald: $OUT_DIR/lemma-locald-$TRIPLE"
echo "agent host: $OUT_DIR/lemma-agent-host-$TRIPLE"
echo "runtime bridge: $OUT_DIR/lemma-runtime-$TRIPLE"
echo "VZ helper: $OUT_DIR/lemma-vz-$TRIPLE"
"$OUT_DIR/lemma-locald-$TRIPLE" --version >/dev/null && echo "locald: smoke ok"
# Prove the embedded Info.plist survived the link. Without it codesign invents an
# identifier from the binary's contents, the credential vault treats the next
# build as a different program, and the app re-prompts for access on launch --
# all of which looks like working software until someone opens it.
locald_identifier="$(codesign -dv "$OUT_DIR/lemma-locald-$TRIPLE" 2>&1 \
  | sed -n 's/^Identifier=//p')"
if [[ "${locald_identifier}" != "work.lemma.locald" ]]; then
  echo "locald signed as '${locald_identifier}', expected work.lemma.locald" >&2
  exit 1
fi
echo "locald: identifier ${locald_identifier}"
"$OUT_DIR/lemma-agent-host-$TRIPLE" --version >/dev/null \
  && echo "agent host: smoke ok"
"$OUT_DIR/lemma-runtime-$TRIPLE" --version >/dev/null && echo "runtime bridge: smoke ok"
"$OUT_DIR/lemma-vz-$TRIPLE" --version >/dev/null && echo "VZ helper: smoke ok"
codesign -d --entitlements :- "$OUT_DIR/lemma-vz-$TRIPLE" 2>&1 \
  | grep -F "com.apple.security.virtualization" >/dev/null \
  && echo "VZ helper: virtualization entitlement ok"
