#!/usr/bin/env python3
"""Every macOS entitlement is granted to the thing that needs it, and nothing else.

Tauri 2 has one entitlements setting per build and applies it to every target it
signs: the frameworks, each `externalBin`, and then the app bundle. So a grant in
the app's file is a grant to the process that hosts a WebView *and* to
lemma-locald, lemma-agent-host and lemma-runtime -- four processes for a
capability that, today, exactly one binary in Lemma can use.

That one binary is lemma-vz, and it is not signed by Tauri at all. It ships as a
bundle *resource*, and Tauri's sign list holds only frameworks and external
binaries; the bundle itself is signed with `--force -s <identity> --options
runtime --entitlements <path>` and no `--deep`, so a resource keeps whatever
signature it arrived with. lemma-vz arrives already signed, by
`scripts/build-sidecar.sh` locally and by the release workflows' own step, with
its own entitlements file.

Which is why the app's file can be empty and the guest still boots -- and why
this check exists rather than a comment: the arrangement is invisible in the
diff that would break it.
"""

from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent
REPO = DESKTOP.parent

APP_ENTITLEMENTS = DESKTOP / "app.entitlements.plist"
HELPER_ENTITLEMENTS = DESKTOP / "local-runtime/macos-vz/lemma-vz.entitlements.plist"

# The configurations that build a signed macOS app.
SIGNED_CONFIGS = ("tauri.online.conf.json", "tauri.dist.conf.json", "tauri.qa.conf.json")

VIRTUALIZATION = "com.apple.security.virtualization"

# What may reach the app bundle and the sidecars Tauri signs with it. Empty:
# add to this list only alongside the reason the *app* needs the grant, not the
# reason some binary inside it does.
APP_MAY_GRANT: frozenset[str] = frozenset()

# Files that sign lemma-vz. Each must name the helper's own entitlements, not
# the app's -- signing it with the app's file is how the entitlement was lost
# the first time.
HELPER_SIGNING_SITES = (
    REPO / "Makefile",
    DESKTOP / "scripts/build-sidecar.sh",
    REPO / ".github/workflows/release-desktop.yml",
    REPO / ".github/workflows/release-local-images.yml",
    REPO / ".github/workflows/ci.yml",
)


def failures() -> list[str]:
    problems: list[str] = []

    app = plistlib.loads(APP_ENTITLEMENTS.read_bytes())
    granted = {key for key, value in app.items() if value}
    for extra in sorted(granted - APP_MAY_GRANT):
        problems.append(
            f"app.entitlements.plist grants {extra} to Lemma.app and to "
            f"lemma-locald, lemma-agent-host and lemma-runtime. Sign the binary "
            f"that needs it with its own entitlements file instead."
        )

    helper = plistlib.loads(HELPER_ENTITLEMENTS.read_bytes())
    if not helper.get(VIRTUALIZATION):
        problems.append(
            f"{HELPER_ENTITLEMENTS.name} must grant {VIRTUALIZATION}: without it "
            f"lemma-vz cannot start a guest, and the macOS runtime never comes up."
        )

    for name in SIGNED_CONFIGS:
        config = json.loads((DESKTOP / name).read_text())
        configured = config.get("bundle", {}).get("macOS", {}).get("entitlements")
        if configured != APP_ENTITLEMENTS.name:
            problems.append(
                f"{name} signs the app with {configured!r}; it must be "
                f"{APP_ENTITLEMENTS.name!r}, which is the file this check reads."
            )

    # The suffix, not the whole path: the Makefile reaches it through
    # $(DESKTOP_DIR) and the workflows through a repo-relative path, and both
    # are correct. What matters is which file they name.
    relative = HELPER_ENTITLEMENTS.relative_to(DESKTOP).as_posix()
    for site in HELPER_SIGNING_SITES:
        text = site.read_text()
        if relative not in text:
            problems.append(
                f"{site.relative_to(REPO)} signs lemma-vz but does not name "
                f"{relative}. Signing it with the app's entitlements takes "
                f"{VIRTUALIZATION} away from the only binary that uses it."
            )

    # lemma-vz has to stay a resource. As an externalBin it would join Tauri's
    # sign list and be re-signed with the app's entitlements, which is the one
    # arrangement in which an empty app file breaks the guest.
    for name in ("tauri.macos.conf.json",):
        config = json.loads((DESKTOP / name).read_text())
        bundle = config.get("bundle", {})
        external = " ".join(bundle.get("externalBin", []))
        if "lemma-vz" in external:
            problems.append(
                f"{name} lists lemma-vz as an externalBin. Tauri re-signs those "
                f"with the app's entitlements, so it must stay a resource."
            )
        if "lemma-vz" not in json.dumps(bundle.get("resources", {})):
            problems.append(f"{name} no longer ships lemma-vz as a resource.")

    return problems


def main() -> int:
    problems = failures()
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    if problems:
        return 1
    print("✓ Entitlements: granted only to the binary that uses them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
