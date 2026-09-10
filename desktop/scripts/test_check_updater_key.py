import base64
import json
import tempfile
import unittest
from pathlib import Path

from check_updater_key import check, public_key_id, signature_key_id

# Resolved from this file, not the working directory: `unittest discover` is run
# from the repository root by the Makefile and from here by hand.
SHIPPED_CONFIG = Path(__file__).resolve().parent.parent / "tauri.conf.json"


def key_id_bytes(display: str) -> bytes:
    """The 8 bytes minisign stores for a key printed as `display`."""
    return bytes.fromhex(display)[::-1]


def public_key_file(display: str, algorithm: bytes = b"Ed") -> str:
    raw = algorithm + key_id_bytes(display) + bytes(32)
    body = base64.b64encode(raw).decode()
    text = f"untrusted comment: minisign public key: {display}\n{body}\n"
    return base64.b64encode(text.encode()).decode()


def signature_file(display: str, algorithm: bytes = b"Ed", *, wrapped: bool) -> str:
    raw = algorithm + key_id_bytes(display) + bytes(64)
    body = base64.b64encode(raw).decode()
    text = (
        "untrusted comment: signature from minisign secret key\n"
        f"{body}\n"
        "trusted comment: timestamp:1\n"
        f"{base64.b64encode(bytes(64)).decode()}\n"
    )
    return base64.b64encode(text.encode()).decode() if wrapped else text


class KeyIdentityTests(unittest.TestCase):
    def config(self, pubkey: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "tauri.conf.json"
        path.write_text(json.dumps({"plugins": {"updater": {"pubkey": pubkey}}}))
        return path

    def test_the_shipped_public_key_reads_as_the_id_in_its_own_comment(self) -> None:
        """The comment line and the bytes are written by minisign independently,
        so agreeing with the comment is a real check on the parsing."""
        shipped = json.loads(SHIPPED_CONFIG.read_text())
        pubkey = shipped["plugins"]["updater"]["pubkey"]
        comment = base64.b64decode(pubkey).decode().splitlines()[0]
        self.assertEqual(public_key_id(pubkey), comment.rsplit(": ", 1)[1])

    def test_a_matching_pair_passes_in_both_signature_encodings(self) -> None:
        config = self.config(public_key_file("423ED591D0E5931F"))
        for wrapped in (True, False):
            with self.subTest(wrapped=wrapped):
                self.assertEqual(
                    check(config, signature_file("423ED591D0E5931F", wrapped=wrapped)),
                    "423ED591D0E5931F",
                )

    def test_a_rotated_private_key_is_caught(self) -> None:
        """The failure this exists for: the release is signed, notarized and
        published, and every installed app rejects the payload it offers."""
        config = self.config(public_key_file("423ED591D0E5931F"))
        with self.assertRaisesRegex(ValueError, "would be rejected"):
            check(config, signature_file("00112233445566AA", wrapped=True))

    def test_a_declared_rotation_accepts_the_one_release_that_must_mismatch(
        self,
    ) -> None:
        """Step 2 of the runbook: the new public key is committed so it reaches
        installed apps, and the old private key still signs so those apps will
        accept the release carrying it."""
        config = self.config(public_key_file("00112233445566AA"))
        self.assertEqual(
            check(config, signature_file("423ED591D0E5931F", wrapped=True), rotating=True),
            "423ED591D0E5931F -> 00112233445566AA",
        )

    def test_a_rotation_flag_left_switched_on_is_itself_a_failure(self) -> None:
        """Otherwise the escape hatch becomes the permanent state, and the check
        it exists to bypass never runs again."""
        config = self.config(public_key_file("423ED591D0E5931F"))
        with self.assertRaisesRegex(ValueError, "rotation is finished"):
            check(
                config,
                signature_file("423ED591D0E5931F", wrapped=True),
                rotating=True,
            )

    def test_a_prehashed_signature_is_read_the_same_way(self) -> None:
        config = self.config(public_key_file("423ED591D0E5931F"))
        self.assertEqual(
            check(config, signature_file("423ED591D0E5931F", b"ED", wrapped=True)),
            "423ED591D0E5931F",
        )

    def test_an_empty_pubkey_is_refused_rather_than_matched(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty updater pubkey"):
            check(self.config(""), signature_file("423ED591D0E5931F", wrapped=True))

    def test_a_truncated_or_foreign_key_is_refused_rather_than_guessed(self) -> None:
        for broken in [
            base64.b64encode(b"untrusted comment: x\n" + base64.b64encode(b"Ed" + bytes(4)) + b"\n").decode(),
            base64.b64encode(b"untrusted comment: x\n" + base64.b64encode(b"Rs" + bytes(40)) + b"\n").decode(),
            "not base64 at all!!",
        ]:
            with self.subTest(broken=broken[:24]):
                with self.assertRaises(ValueError):
                    public_key_id(broken)

    def test_a_signature_with_no_payload_line_is_refused(self) -> None:
        empty = base64.b64encode(b"untrusted comment: nothing follows\n").decode()
        with self.assertRaisesRegex(ValueError, "no minisign payload line"):
            signature_key_id(empty)


if __name__ == "__main__":
    unittest.main()
