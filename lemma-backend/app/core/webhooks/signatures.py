"""How inbound webhooks prove they are who they say.

Four schemes cover every provider Lemma accepts today, and they were written
five times between two modules -- Slack, WhatsApp, Telegram and Svix in
`agent_surfaces`, and Svix again in `identity`'s bounce controller, with a
different timestamp window and a different error. Signature verification is the
one thing on these paths that is actually load-bearing, so having it in two
places with two behaviours is the worst arrangement available.

Two decisions run through all of them:

**Candidate secrets, not one secret.** Every scheme takes a sequence and matches
against any of them. Rotating a webhook secret means both are briefly live, and
an App per environment means a secret can be pasted into the wrong one; without
candidates that is a silent 403 that looks exactly like an attack.

**Booleans, not exceptions.** Each caller already has its own failure to raise --
a domain error in one module, an `HTTPException` in another -- and each has its
own idea of what status code a bad signature deserves. Deciding that here would
mean one of them lying.

Nothing here logs, and nothing here is timing-dependent: every comparison goes
through `hmac.compare_digest`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Iterable, Sequence

# Slack signs `v0:{timestamp}:{body}` and prefixes the digest with the same
# version. It has never been anything but v0, but the scheme carries it
# explicitly so a v1 would be a visible change rather than a silent mismatch.
SLACK_SIGNATURE_VERSION = "v0"

# Both timestamped schemes allow five minutes either way, which is what Slack
# and Svix each document. Shared so the two cannot drift apart again -- they
# already had.
MAX_TIMESTAMP_SKEW_SECONDS = 300


def usable_secrets(secrets: Iterable[str | None]) -> list[str]:
    """The candidates worth trying: present, non-empty, de-duplicated.

    An unset secret reaching a comparison would otherwise be a real match
    against an empty-key HMAC, which anyone can compute.
    """
    seen: dict[str, None] = {}
    for secret in secrets:
        if secret:
            seen.setdefault(secret, None)
    return list(seen)


def timestamp_within_skew(
    timestamp: str | int | None,
    *,
    max_skew_seconds: int = MAX_TIMESTAMP_SKEW_SECONDS,
    now: int | None = None,
) -> bool:
    """Whether a signed timestamp is recent enough to accept.

    A signature stays valid forever on its own; the timestamp is what closes the
    replay window, which is why it is inside the signed material.
    """
    try:
        signed_at = int(timestamp)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return False
    current = int(time.time()) if now is None else now
    return abs(current - signed_at) <= max_skew_seconds


def _matches_any(expected: Iterable[str], presented: str) -> bool:
    # `any()` over compare_digest rather than a set membership test: the point
    # of compare_digest is that it does not return early on the first differing
    # byte, and `in` does.
    return any(hmac.compare_digest(candidate, presented) for candidate in expected)


def hex_digest_signature_matches(
    signature: str | None,
    raw_body: bytes,
    secrets: Sequence[str | None],
    *,
    prefix: str = "sha256=",
) -> bool:
    """The `sha256=<hexdigest>` scheme, over the body exactly as it arrived.

    GitHub (`X-Hub-Signature-256`) and Meta/WhatsApp use the identical
    construction, down to the header name -- so this is one scheme, not two that
    happen to look alike.

    `raw_body` must be the bytes the client sent. Re-serializing parsed JSON
    changes key order and whitespace and produces a different digest, which
    presents as an authentication failure nobody can explain.
    """
    if not signature or not signature.startswith(prefix):
        return False
    expected = [
        prefix + hmac.new(s.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        for s in usable_secrets(secrets)
    ]
    return _matches_any(expected, signature)


def slack_signature_matches(
    signature: str | None,
    timestamp: str | int | None,
    raw_body: bytes,
    secrets: Sequence[str | None],
) -> bool:
    """Slack's `v0={hexdigest}` over `v0:{timestamp}:{body}`.

    The caller checks the timestamp separately with `timestamp_within_skew`: a
    stale-but-authentic delivery and a forged one are different problems, and a
    caller that wants to say so needs them apart.
    """
    if not signature:
        return False
    try:
        signed_at = int(timestamp)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return False
    basestring = f"{SLACK_SIGNATURE_VERSION}:{signed_at}:".encode("utf-8") + raw_body
    expected = [
        f"{SLACK_SIGNATURE_VERSION}="
        + hmac.new(s.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
        for s in usable_secrets(secrets)
    ]
    return _matches_any(expected, signature)


def svix_signing_key(secret: str | None) -> bytes | None:
    """The key behind Svix's `whsec_`-prefixed base64, or None if it is not one.

    Public because a caller may want to tell "our secret is malformed" -- its own
    configuration, a 503 -- from "this delivery does not verify", a 401. The
    match below cannot draw that line for it: with several candidates, one
    malformed entry must not fail a request the others would have accepted.
    """
    if not secret:
        return None
    try:
        return base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except binascii.Error, ValueError:
        return None


def svix_signature_matches(
    signature: str | None,
    message_id: str,
    timestamp: str | int | None,
    raw_body: bytes,
    secrets: Sequence[str | None],
) -> bool:
    """Svix's scheme, used by Resend: base64 HMAC over `{id}.{timestamp}.{body}`.

    The header carries space-separated `v{n},{sig}` entries so Svix can offer
    several at once during a rotation; only `v1` is defined, and anything else
    is ignored rather than rejected -- a future version arriving alongside a v1
    we can check should not fail the delivery.

    The secret is base64 behind a `whsec_` prefix, and unlike the other schemes
    the key is the decoded bytes. A secret that is not valid base64 is dropped
    rather than raised on: with candidates, one bad entry must not take a good
    one down with it.
    """
    if not signature or not message_id:
        return False
    try:
        signed_at = int(timestamp)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return False
    presented = [
        item.removeprefix("v1,") for item in signature.split() if item.startswith("v1,")
    ]
    if not presented:
        return False
    signed = f"{message_id}.{signed_at}.".encode("utf-8") + raw_body
    expected: list[str] = []
    for secret in usable_secrets(secrets):
        key = svix_signing_key(secret)
        if key is None:
            continue
        expected.append(
            base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        )
    return any(_matches_any(expected, candidate) for candidate in presented)


def shared_secret_matches(
    presented: str | None,
    secrets: Sequence[str | None],
) -> bool:
    """A secret sent verbatim in a header, as Telegram does.

    Not a signature: it proves only that the sender knows the secret, and says
    nothing about the body. Constant-time all the same, because the comparison
    still leaks the secret otherwise.
    """
    if not presented:
        return False
    return _matches_any(usable_secrets(secrets), presented)
