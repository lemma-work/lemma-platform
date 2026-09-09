import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from check_macos_signing import (
    HELPERS,
    VZ_HELPER,
    Signature,
    check,
    parse_signature,
    validate_entitlements,
    validate_signature,
)


class SigningPolicyTests(unittest.TestCase):
    def signature(
        self,
        *,
        identifier: str = "work.lemma.locald",
        team: str = "TESTTEAM",
        authorities: tuple[str, ...] = ("Developer ID Application: Test",),
        adhoc: bool = False,
    ) -> Signature:
        return Signature(identifier, team, authorities, adhoc)

    def validate(self, signature: Signature, *, development: bool = False) -> None:
        validate_signature(
            signature,
            identifier="work.lemma.locald",
            team="TESTTEAM",
            allow_development=development,
        )

    def test_stable_release_identity_passes(self) -> None:
        self.validate(self.signature())

    def test_adhoc_missing_team_changed_team_and_changed_identifier_fail(self) -> None:
        for signature in (
            self.signature(adhoc=True),
            self.signature(team="not set"),
            self.signature(team="OTHERTEAM"),
            self.signature(identifier="work.lemma.locald-build-2"),
        ):
            with self.subTest(signature=signature), self.assertRaises(ValueError):
                self.validate(signature)

    def test_development_identity_is_only_allowed_in_explicit_qa(self) -> None:
        signature = self.signature(authorities=("Apple Development: Test",))
        with self.assertRaises(ValueError):
            self.validate(signature)
        self.validate(signature, development=True)

    def test_certificate_chain_does_not_hide_wrong_leaf_identity(self) -> None:
        with self.assertRaises(ValueError):
            self.validate(
                self.signature(
                    authorities=("Untrusted: Test", "Developer ID Application: Test")
                )
            )

    def test_parser_retains_leaf_authority_and_absent_identity_fails(self) -> None:
        signature = parse_signature(
            "Executable=/tmp/helper\nIdentifier=work.lemma.locald\nAuthority=Developer ID Application: Test\nAuthority=Apple Root CA\nTeamIdentifier=TESTTEAM\n"
        )
        self.validate(signature)
        self.assertEqual(signature.authorities[-1], "Apple Root CA")
        with self.assertRaises(ValueError):
            self.validate(parse_signature("Executable=/tmp/helper"))


@unittest.skipUnless(
    sys.platform == "darwin", "requires macOS code-signing enforcement"
)
class NativeSigningTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def build(
        self,
        name: str,
        revision: int,
        identity: str,
        entitled: dict[str, bool] | None = None,
    ) -> Path:
        """Build a bundle; `entitled` names paths to sign with virtualization.

        Keys are the same relative paths as `HELPERS`, plus `"."` for the
        bundle itself, which is how a real build gets there: Tauri signs the
        app and each externalBin with one entitlements file.
        """
        entitled = entitled or {}
        plist = self.root / f"{name}-virtualization.plist"
        plist.write_bytes(plistlib.dumps({"com.apple.security.virtualization": True}))
        def entitlement_args(relative: str) -> list[str]:
            return ["--entitlements", str(plist)] if entitled.get(relative) else []

        app = self.root / f"{name}.app"
        binary = app / "Contents/MacOS/lemma-desktop"
        binary.parent.mkdir(parents=True)
        (app / "Contents/Resources").mkdir()
        source = Path(__file__).parent / "fixtures/signing_keychain.c"
        subprocess.run(
            [
                "/usr/bin/clang",
                str(source),
                f"-DREVISION={revision}",
                "-Wno-deprecated-declarations",
                "-framework",
                "Security",
                "-framework",
                "CoreFoundation",
                "-o",
                str(binary),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "work.lemma.desktop",
                    "CFBundleExecutable": "lemma-desktop",
                    "CFBundlePackageType": "APPL",
                }
            )
        )
        for relative, identifier in HELPERS.items():
            helper = app / relative
            shutil.copy2(binary, helper)
            subprocess.run(
                [
                    "/usr/bin/codesign",
                    "--force",
                    "--timestamp=none",
                    "--sign",
                    identity,
                    "--identifier",
                    identifier,
                    *entitlement_args(relative),
                    str(helper),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--timestamp=none",
                "--sign",
                identity,
                *entitlement_args("."),
                str(app),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return app

    def test_the_virtualization_entitlement_belongs_to_lemma_vz_alone(self) -> None:
        """Read off a real bundle, because what could go wrong is a signing step.

        Tauri applies one entitlements file to the app and to every sidecar it
        signs, so a grant meant for one binary reaches four; and it does not
        sign the resource that actually needs one, so the grant can equally go
        missing. Neither shape is visible in a plist.
        """
        validate_entitlements(self.build("split", 1, "-", {VZ_HELPER: True}))

    def test_an_app_that_carries_virtualization_is_refused(self) -> None:
        app = self.build("over", 1, "-", {VZ_HELPER: True, ".": True})
        with self.assertRaisesRegex(ValueError, "cannot use it"):
            validate_entitlements(app)

    def test_a_sidecar_that_carries_virtualization_is_refused(self) -> None:
        app = self.build(
            "sidecar", 1, "-", {VZ_HELPER: True, "Contents/MacOS/lemma-locald": True}
        )
        with self.assertRaisesRegex(ValueError, "lemma-locald"):
            validate_entitlements(app)

    def test_a_helper_signed_without_virtualization_is_refused(self) -> None:
        """The other direction, and the one that ships a broken guest: nothing
        else in the release would notice a helper that cannot start a VM."""
        with self.assertRaisesRegex(ValueError, "never come up"):
            validate_entitlements(self.build("under", 1, "-"))

    def test_a_valid_adhoc_bundle_is_not_a_release_identity(self) -> None:
        app = self.build("adhoc", 1, "-")
        with self.assertRaisesRegex(ValueError, "ad-hoc"):
            check(app, team="TESTTEAM")

    @unittest.skipUnless(
        os.environ.get("LEMMA_SIGNING_TEST_IDENTITY")
        and os.environ.get("LEMMA_SIGNING_TEST_TEAM"),
        "requires an explicitly selected QA signing identity",
    )
    def test_changed_binaries_satisfy_the_previous_identity_but_changed_helpers_fail(
        self,
    ) -> None:
        identity = os.environ["LEMMA_SIGNING_TEST_IDENTITY"]
        team = os.environ["LEMMA_SIGNING_TEST_TEAM"]
        old = self.build("previous", 1, identity)
        new = self.build("candidate", 2, identity)
        self.assertNotEqual(
            (old / "Contents/MacOS/lemma-locald").read_bytes(),
            (new / "Contents/MacOS/lemma-locald").read_bytes(),
        )
        check(new, team=team, previous_app=old, allow_development=True)
        old_helper = old / "Contents/MacOS/lemma-locald"
        new_helper = new / "Contents/MacOS/lemma-locald"
        keychain = self.root / "disposable.keychain"

        def credential(binary: Path, action: str) -> None:
            subprocess.run(
                [str(binary), action, str(keychain)],
                check=True,
                capture_output=True,
                timeout=15,
            )

        credential(old_helper, "create")
        self.addCleanup(credential, old_helper, "delete")
        credential(old_helper, "read")
        credential(new_helper, "read")
        adhoc = self.build("previous-adhoc", 1, "-")
        with self.assertRaisesRegex(ValueError, "ad-hoc"):
            check(new, team=team, previous_app=adhoc, allow_development=True)
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--timestamp=none",
                "--sign",
                identity,
                "--identifier",
                "work.lemma.changed",
                str(new / "Contents/MacOS/lemma-locald"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--timestamp=none",
                "--sign",
                identity,
                str(new),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        with self.assertRaisesRegex(ValueError, "identifier changed"):
            check(new, team=team, previous_app=old, allow_development=True)
        with self.assertRaises(subprocess.CalledProcessError):
            credential(new_helper, "read")


if __name__ == "__main__":
    unittest.main()
