"""The shared webhook signature schemes.

These were five implementations across two modules before they were one. The
tests are written against published vectors and against the failures that
actually happened, not against the implementation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest

from app.core.webhooks.signatures import (
    MAX_TIMESTAMP_SKEW_SECONDS,
    hex_digest_signature_matches,
    shared_secret_matches,
    slack_signature_matches,
    svix_signature_matches,
    timestamp_within_skew,
    usable_secrets,
)

pytestmark = pytest.mark.unit

BODY = b'{"action":"opened","number":42}'


def _github_signature(secret: str, body: bytes = BODY) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestHexDigestScheme:
    """GitHub's `X-Hub-Signature-256`, which is Meta's byte for byte."""

    def test_a_correct_signature_is_accepted(self):
        assert hex_digest_signature_matches(
            _github_signature("s3cret"), BODY, ["s3cret"]
        )

    def test_a_signature_over_different_bytes_is_rejected(self):
        # Re-serialized JSON is the failure this catches: same object, different
        # whitespace and key order, different digest.
        assert not hex_digest_signature_matches(
            _github_signature("s3cret", b'{"number": 42, "action": "opened"}'),
            BODY,
            ["s3cret"],
        )

    def test_the_wrong_secret_is_rejected(self):
        assert not hex_digest_signature_matches(
            _github_signature("other"), BODY, ["s3cret"]
        )

    def test_a_missing_or_unprefixed_header_is_rejected(self):
        raw = hmac.new(b"s3cret", BODY, hashlib.sha256).hexdigest()
        assert not hex_digest_signature_matches(None, BODY, ["s3cret"])
        assert not hex_digest_signature_matches("", BODY, ["s3cret"])
        assert not hex_digest_signature_matches(raw, BODY, ["s3cret"])
        assert not hex_digest_signature_matches("sha1=" + raw, BODY, ["s3cret"])

    def test_either_candidate_secret_is_accepted(self):
        """A rotation has both secrets live, and so does a mis-set environment.

        Without candidates this is a 403 that is indistinguishable from an
        attack, and the provider responds by disabling the hook.
        """
        secrets = ["old-secret", "new-secret"]
        assert hex_digest_signature_matches(
            _github_signature("old-secret"), BODY, secrets
        )
        assert hex_digest_signature_matches(
            _github_signature("new-secret"), BODY, secrets
        )

    def test_an_unset_secret_never_matches(self):
        """An empty key is a key anyone can compute with."""
        empty_key_signature = (
            "sha256=" + hmac.new(b"", BODY, hashlib.sha256).hexdigest()
        )
        assert not hex_digest_signature_matches(empty_key_signature, BODY, [None, ""])
        assert not hex_digest_signature_matches(empty_key_signature, BODY, [])


class TestSlackScheme:
    def test_it_agrees_with_the_verifier_it_was_extracted_from(self):
        """The extraction must not change what Slack deliveries are accepted.

        Checked against the shipping implementation rather than a re-derivation
        of the same formula, which would only prove the test and the code were
        written by the same hand.
        """
        from app.modules.agent_surfaces.services.webhook_security_service import (
            SurfaceWebhookAuthenticationError,
            SurfaceWebhookSecurityService,
        )

        secret = "8f742231b10e8888abcd99yyyzzz85a5"
        timestamp = int(time.time())
        body = b"token=xyz&team_id=T1DC2JH3J&command=%2Fwebhook&text="

        def shipping_accepts(signature: str) -> bool:
            try:
                SurfaceWebhookSecurityService()._verify_slack_signature(
                    headers={
                        "x-slack-signature": signature,
                        "x-slack-request-timestamp": str(timestamp),
                    },
                    raw_body=body,
                    signing_secret=secret,
                )
            except SurfaceWebhookAuthenticationError:
                return False
            return True

        good = (
            "v0="
            + hmac.new(
                secret.encode(), b"v0:%d:" % timestamp + body, hashlib.sha256
            ).hexdigest()
        )
        for signature in (good, good[:-1] + "0", "v0=short", "garbage"):
            assert slack_signature_matches(signature, timestamp, body, [secret]) == (
                shipping_accepts(signature)
            ), signature

    def test_the_timestamp_is_inside_the_signed_material(self):
        secret = "s"
        good = slack_signature_matches
        basestring = b"v0:1700000000:" + BODY
        signature = (
            "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
        )
        assert good(signature, 1700000000, BODY, [secret])
        # Same signature, a different claimed timestamp: a replay with the clock
        # moved on cannot reuse it.
        assert not good(signature, 1700000001, BODY, [secret])

    def test_a_non_numeric_timestamp_is_rejected(self):
        assert not slack_signature_matches("v0=deadbeef", "not-a-number", BODY, ["s"])
        assert not slack_signature_matches("v0=deadbeef", None, BODY, ["s"])


class TestSvixScheme:
    def _signature(
        self, secret_b64: str, message_id: str, timestamp: int, body: bytes
    ) -> str:
        key = base64.b64decode(secret_b64)
        signed = f"{message_id}.{timestamp}.".encode() + body
        digest = base64.b64encode(
            hmac.new(key, signed, hashlib.sha256).digest()
        ).decode()
        return f"v1,{digest}"

    SECRET_B64 = base64.b64encode(b"resend-signing-key").decode()

    def test_a_correct_signature_is_accepted_with_or_without_the_prefix(self):
        sig = self._signature(self.SECRET_B64, "msg_1", 1700000000, BODY)
        for secret in (self.SECRET_B64, "whsec_" + self.SECRET_B64):
            assert svix_signature_matches(sig, "msg_1", 1700000000, BODY, [secret])

    def test_the_message_id_is_inside_the_signed_material(self):
        sig = self._signature(self.SECRET_B64, "msg_1", 1700000000, BODY)
        assert not svix_signature_matches(
            sig, "msg_2", 1700000000, BODY, [self.SECRET_B64]
        )

    def test_several_versions_in_one_header_are_tried(self):
        """Svix sends space-separated entries during a rotation."""
        good = self._signature(self.SECRET_B64, "msg_1", 1700000000, BODY)
        header = f"v1,notthisone {good}"
        assert svix_signature_matches(
            header, "msg_1", 1700000000, BODY, [self.SECRET_B64]
        )

    def test_an_unknown_version_alone_is_rejected(self):
        assert not svix_signature_matches(
            "v2,anything", "msg_1", 1700000000, BODY, [self.SECRET_B64]
        )

    def test_a_secret_that_is_not_base64_does_not_take_a_good_one_down(self):
        """One bad candidate must not disable the rest."""
        sig = self._signature(self.SECRET_B64, "msg_1", 1700000000, BODY)
        assert svix_signature_matches(
            sig, "msg_1", 1700000000, BODY, ["whsec_not!base64!", self.SECRET_B64]
        )


class TestSharedSecret:
    def test_it_compares_the_whole_secret(self):
        assert shared_secret_matches("tok", ["tok"])
        assert not shared_secret_matches("to", ["tok"])
        assert not shared_secret_matches("tok", ["token"])

    def test_an_absent_header_or_secret_never_matches(self):
        assert not shared_secret_matches(None, ["tok"])
        assert not shared_secret_matches("", ["tok"])
        assert not shared_secret_matches("tok", [None, ""])


class TestSupportingParts:
    def test_usable_secrets_drops_blanks_and_duplicates_in_order(self):
        assert usable_secrets(["a", None, "", "b", "a"]) == ["a", "b"]

    def test_the_skew_window_is_symmetric(self):
        now = int(time.time())
        assert timestamp_within_skew(now, now=now)
        assert timestamp_within_skew(now - MAX_TIMESTAMP_SKEW_SECONDS, now=now)
        # A clock ahead of ours is as suspicious as one behind.
        assert timestamp_within_skew(now + MAX_TIMESTAMP_SKEW_SECONDS, now=now)
        assert not timestamp_within_skew(now - MAX_TIMESTAMP_SKEW_SECONDS - 1, now=now)
        assert not timestamp_within_skew(now + MAX_TIMESTAMP_SKEW_SECONDS + 1, now=now)

    def test_a_missing_timestamp_is_not_within_the_window(self):
        assert not timestamp_within_skew(None)
        assert not timestamp_within_skew("")
