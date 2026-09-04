"""Six digits from a stored seed, generated here and never in the sandbox.

RFC 6238, on the standard library. A dependency would be a fair choice too, but
the algorithm is twenty lines and it comes with published test vectors — so this
is verified against the RFC itself rather than against a package's own tests.

**The seed never leaves the backend.** A TOTP seed is not a second factor if the
thing holding your password also holds it: whoever has both can mint codes
forever. So the vault stores the seed, this generates the code, and what reaches
the sandbox is six digits that stop working in half a minute.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

#: The interval every authenticator app uses, and what a site expects.
DEFAULT_PERIOD_SECONDS = 30
DEFAULT_DIGITS = 6


class InvalidTotpSeed(ValueError):
    """The stored seed is not usable base32."""


def normalize_seed(seed: str) -> str:
    """Strip the formatting people paste in with a seed.

    Authenticator setup pages group the secret into blocks and lower-case it,
    and a person copying one brings the spaces along. Refusing that would be
    refusing the ordinary case.
    """
    return seed.replace(" ", "").replace("-", "").upper()


def _decode_seed(seed: str) -> bytes:
    normalized = normalize_seed(seed)
    # Base32 needs its padding; a seed quoted without it is normal.
    padded = normalized + "=" * (-len(normalized) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except (ValueError, TypeError) as exc:
        # `binascii.Error` is a ValueError; a non-string seed is the TypeError.
        raise InvalidTotpSeed("TOTP seed is not valid base32") from exc


def hotp(key: bytes, counter: int, *, digits: int = DEFAULT_DIGITS) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # Dynamic truncation, RFC 4226 §5.3: the low nibble of the last byte picks
    # where to read the four bytes from.
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def totp(
    seed: str,
    *,
    at: float | None = None,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD_SECONDS,
) -> str:
    """The current code for a seed.

    Raises :class:`InvalidTotpSeed` rather than returning something wrong: a
    silently bad code fails at the site as a wrong password, which is the most
    confusing way this could break.
    """
    moment = time.time() if at is None else at
    return hotp(_decode_seed(seed), int(moment // period), digits=digits)


def seconds_remaining(
    *, at: float | None = None, period: int = DEFAULT_PERIOD_SECONDS
) -> float:
    """How long the current code lasts.

    The caller needs this to decide whether to wait: injecting a code with two
    seconds left produces a failed login that looks like a wrong secret.
    """
    moment = time.time() if at is None else at
    return period - (moment % period)
