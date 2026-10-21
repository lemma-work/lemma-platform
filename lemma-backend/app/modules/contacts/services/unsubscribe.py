"""The signed link, and the headers, that let a contact stop a pod's email.

A token names a handle by its hash, inside the pod -- not the contact, not the
handle's row -- so a link in an email sent before the contact was forgotten,
or merged, still stops that address. It is signed, so nobody can mint one for
somebody else's address, and it never expires: a person must be able to stop
mail from an old message.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.core.crypto.factory import get_secret_signer
from app.modules.contacts.domain.handles import handle_hash
from app.modules.contacts.domain.kinds import IdentityKind

#: The signing purpose of an unsubscribe link: one handle in one pod, forever.
UNSUBSCRIBE_PURPOSE = "contact-unsubscribe:v2"
UNSUBSCRIBE_PATH = "/public/contacts/unsubscribe"
_SEPARATOR = "~"


@dataclass(frozen=True, slots=True)
class UnsubscribeTarget:
    """What a token stops: one handle, by its hash, in one pod."""

    pod_id: UUID
    kind: IdentityKind
    value_hash: bytes


def unsubscribe_token(pod_id: UUID, kind: IdentityKind, value: str) -> str:
    """A link token that unsubscribes exactly one handle, and can't be forged."""
    digest = base64.urlsafe_b64encode(handle_hash(kind, value)).rstrip(b"=")
    payload = _SEPARATOR.join((str(pod_id), kind.value, digest.decode()))
    return (
        f"{payload}.{get_secret_signer().sign(UNSUBSCRIBE_PURPOSE, payload.encode())}"
    )


def read_unsubscribe_token(token: str) -> UnsubscribeTarget | None:
    """The handle a genuine token names, or ``None``."""
    payload, _, signature = token.partition(".")
    if not signature or not get_secret_signer().verify(
        UNSUBSCRIBE_PURPOSE, payload.encode(), signature
    ):
        return None
    pod, _, rest = payload.partition(_SEPARATOR)
    kind, _, digest = rest.partition(_SEPARATOR)
    try:
        value_hash = base64.urlsafe_b64decode(digest + "=" * (-len(digest) % 4))
        if len(value_hash) != 32:
            return None
        return UnsubscribeTarget(
            pod_id=UUID(pod), kind=IdentityKind(kind), value_hash=value_hash
        )
    except ValueError, binascii.Error:
        return None


def unsubscribe_link(token: str) -> str:
    base = str(settings.api_url).rstrip("/")
    return f"{base}{UNSUBSCRIBE_PATH}?token={quote(token)}"


def unsubscribe_footer(token: str) -> str:
    """The line every email to a contact ends with, saying how to stop."""
    return f"\n\n--\nTo stop these emails: {unsubscribe_link(token)}"


def unsubscribe_headers(token: str, *, mailto: str | None = None) -> dict[str, str]:
    """``List-Unsubscribe`` with one-click (RFC 8058) for an email to a contact.

    The https link takes the mail client's one-click ``POST`` -- the
    unsubscribe controller answers it with no page in between. ``mailto`` is
    the address that receives a reply, when there is one: a client that
    prefers mail sends "unsubscribe" there, which inbound reads as a stop.
    """
    targets = [f"<{unsubscribe_link(token)}>"]
    if mailto:
        targets.append(f"<mailto:{mailto}?subject=unsubscribe>")
    return {
        "List-Unsubscribe": ", ".join(targets),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
