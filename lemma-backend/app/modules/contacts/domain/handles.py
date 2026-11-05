"""How a handle is spelled, and the words a contact uses to stop or start.

One spelling per handle is what makes one person one contact, and what lets
an opt-out keyed by the handle find them again: ``+1 (415) 555-0100``, typed
by a member, and ``14155550100``, as WhatsApp names the sender, are one number.

* **Phones** are E.164 (``+14155550100``), read by ``phonenumbers``. A number
  written without ``+`` -- typed by a member -- is tried in the pod's region
  first (no pod carries one yet: ``contacts_default_phone_region`` stands in)
  and then as international. A number a platform vouches for is international
  already, so its caller writes the ``+`` (WhatsApp sends none) and it is never
  read in the pod's region. One that parses as neither keeps its digits behind
  a ``+`` -- a platform vouched for it, so it still names somebody.
* **Email** addresses are trimmed and case-folded.
* **Host** identities are ``host:{issuer}:{subject}``: a signed-in user of a
  customer's product, as named by the product (``issuer``) that signed for
  them. Two products' user 42 are two people.
"""

from __future__ import annotations

import hashlib
import re

import phonenumbers

from app.modules.contacts.config import contacts_settings
from app.modules.contacts.domain.kinds import IdentityKind

_NOT_DIGITS = re.compile(r"\D")

#: The issuer of a host identity whose widget declared none: every such widget
#: of a pod speaks for the same product.
DEFAULT_HOST_ISSUER = "default"
#: The longest an issuer may be: it is part of a handle of at most 320.
MAX_HOST_ISSUER = 64
_ISSUER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def normalize_handle(
    kind: IdentityKind, value: str, *, region: str | None = None
) -> str:
    """The one spelling a handle is stored, looked up and suppressed under.

    Empty when nothing is left of it: a "number" of punctuation names nobody.
    """
    cleaned = value.strip()
    if kind is IdentityKind.PHONE:
        return e164(cleaned, region=region)
    if kind is IdentityKind.EMAIL:
        return cleaned.lower()
    return cleaned


def e164(value: str, *, region: str | None = None) -> str:
    """A phone number as ``+{country}{number}``, or ``""`` with no digits."""
    digits = _NOT_DIGITS.sub("", value)
    if not digits:
        return ""
    region = region or contacts_settings.contacts_default_phone_region
    candidates: list[tuple[str, str | None]] = []
    if value.lstrip().startswith("+"):
        candidates.append((f"+{digits}", None))
    elif digits.startswith("00"):
        candidates.append((f"+{digits[2:]}", None))
    else:
        if region:
            candidates.append((value, region.upper()))
        candidates.append((f"+{digits}", None))
    parsed = [number for number in map(_parse, candidates) if number is not None]
    for check in (phonenumbers.is_valid_number, phonenumbers.is_possible_number):
        for number in parsed:
            if check(number):
                return phonenumbers.format_number(
                    number, phonenumbers.PhoneNumberFormat.E164
                )
    return f"+{digits[2:] if digits.startswith('00') else digits}"


def _parse(candidate: tuple[str, str | None]) -> phonenumbers.PhoneNumber | None:
    text, region = candidate
    try:
        return phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None


def host_issuer(declared: str | None) -> str:
    """The issuer a widget speaks for: what it declared, lower-cased, or the
    pod's default product. Raises ``ValueError`` for one that is not a name."""
    if declared is None or not declared.strip():
        return DEFAULT_HOST_ISSUER
    issuer = declared.strip().lower()
    if not _ISSUER.match(issuer):
        raise ValueError(
            "An issuer is up to 64 letters, digits, dots, dashes or underscores"
        )
    return issuer


def host_handle(issuer: str, subject: str) -> str:
    """How a customer's user is known in a pod: by the product that named them."""
    return f"host:{issuer}:{subject}"


def issuer_of(kind: IdentityKind, value: str) -> str | None:
    """The product a host identity's value names; ``None`` for other kinds."""
    if kind is not IdentityKind.HOST:
        return None
    prefix, _, rest = value.partition(":")
    issuer, separator, _ = rest.partition(":")
    return issuer if prefix == "host" and separator else None


def handle_hash(kind: IdentityKind, value: str) -> bytes:
    """What an opt-out is keyed by: the sha256 of the normalised handle.

    Not the handle itself, so a suppression outlives the contact being
    forgotten without the pod keeping who they were.
    """
    return hashlib.sha256(normalize_handle(kind, value).encode()).digest()


#: What a contact writes to stop hearing from the pod, as carriers and mail
#: senders have long taught people to: ``STOP`` on a text channel, and the
#: words people use for the same thing by email.
STOP_WORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "OPTOUT", "OPT OUT"})
#: What a contact writes to hear from the pod again after a stop. Not "YES":
#: that is an answer to a question far more often than an opt-in.
START_WORDS = frozenset({"START", "UNSTOP", "SUBSCRIBE", "RESUBSCRIBE"})


def _whole_message(text: str) -> str:
    return " ".join(text.strip().rstrip(".!").split()).upper()


def is_stop_request(text: str) -> bool:
    """Whether a whole message is a request to stop, and nothing else.

    The whole message only: "stop sending me the wrong size" is a question for
    the bot, not an unsubscribe. Trailing punctuation is forgiven, case and
    surrounding space are ignored.
    """
    return _whole_message(text) in STOP_WORDS


def is_start_request(text: str) -> bool:
    """Whether a whole message asks to hear from the pod again."""
    return _whole_message(text) in START_WORDS
