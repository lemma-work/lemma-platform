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
    Signature,
    check,
    parse_signature,
    validate_bundle_metadata,
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

    def build(self, name: str, revision: int, identity: str) -> Path:
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
                    # `check` validates this too, and the QA-identity test below
                    # runs the whole of it -- only in an environment that has an
                    # identity, which is the worst place to discover a fixture
                    # is missing a key.
                    "NSLocalNetworkUsageDescription": "Lemma runs its services here.",
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
                str(app),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return app

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


class BundleMetadataTests(unittest.TestCase):
    """The keys a shipped bundle cannot work without.

    Only one of the two DMG pipelines asked for the local-network description,
    and it was the nightly -- not the release that reaches users. Moving the
    assertion here is what makes both pipelines carry it, since both call this
    file.
    """

    def bundle(self, info: dict | None) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = Path(directory.name) / "Lemma.app"
        (app / "Contents").mkdir(parents=True)
        if info is not None:
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        return app

    def test_a_present_description_passes(self) -> None:
        app = self.bundle({
            "CFBundleIdentifier": "work.lemma.desktop",
            "NSLocalNetworkUsageDescription": "Lemma runs its services on this Mac.",
        })

        validate_bundle_metadata(app)

    def test_a_missing_description_is_refused(self) -> None:
        """The failure it prevents: macOS never asks, so the app cannot reach
        its own backend, and it looks like a startup that never finishes."""
        app = self.bundle({"CFBundleIdentifier": "work.lemma.desktop"})

        with self.assertRaisesRegex(ValueError, "NSLocalNetworkUsageDescription"):
            validate_bundle_metadata(app)

    def test_an_empty_or_non_string_description_does_not_count(self) -> None:
        for value in ["", "   ", True, 1, ["a reason"]]:
            app = self.bundle({"NSLocalNetworkUsageDescription": value})

            with self.assertRaises(ValueError, msg=repr(value)):
                validate_bundle_metadata(app)

    def test_an_unreadable_plist_is_refused_rather_than_skipped(self) -> None:
        app = self.bundle(None)

        with self.assertRaisesRegex(ValueError, "could not be read"):
            validate_bundle_metadata(app)
