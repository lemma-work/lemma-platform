"""A machine a person joined to Lemma with ``lemma machine connect``.

See docs/architecture/connected-machines.md. A connected machine is a laptop
or VM that runs the same workspace runtime a sandbox does, reached over the
machine's own outbound link instead of a provider's network. It belongs to the
person who approved it; ``--pod`` shares it into one pod.

Pairing is two halves. The machine asks for a code and keeps a *claim key*
nobody else sees; the person approves the code in the browser, which is where
the connection's name, owner and pod are decided; then the machine presents
the code *and* the claim key on its link and is given its secret, once. A code
seen over someone's shoulder therefore approves nothing and claims nothing.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


#: The pairing code's alphabet: Crockford base32 without the letters people
#: misread (I, L, O, U), so a code read off a terminal is typed right.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
#: 12 symbols, 60 bits. A code lives ten minutes and approving one needs a
#: signed-in person, so guessing one in that window is not a practical attack.
CODE_LENGTH = 12

#: Names a person's own machines already have, which a connection cannot take.
RESERVED_NAMES = frozenset({"me", "default"})

#: A connection's name, as refs spell it: ``mine:<name>``.
NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class MachineConnectionError(Exception):
    """Something about a connection was refused. The message is for a person."""


class PairingNotFound(MachineConnectionError):
    """The code is unknown or expired, or the claim key is not its own."""


class PairingAlreadyApproved(MachineConnectionError):
    pass


class ConnectionLimitReached(MachineConnectionError):
    pass


class ConnectionNameTaken(MachineConnectionError):
    pass


class ConnectionNotFound(MachineConnectionError):
    pass


class PodShareRefused(MachineConnectionError):
    pass


class InvalidMachineName(MachineConnectionError):
    pass


#: The first bytes of every connected machine's id. A connected machine's
#: connection and its machine row share one id, and the tag is how a provider
#: call is routed to the connected fabric without a lookup -- the way a host
#: sandbox's id is (``domain/host_execution``).
_CONNECTED_TAG = b"lmconn"


def connected_machine_id() -> UUID:
    """A fresh id for a connection and its machine row: tagged, version 8."""
    raw = bytearray(_CONNECTED_TAG + secrets.token_bytes(16 - len(_CONNECTED_TAG)))
    raw[6] = (raw[6] & 0x0F) | 0x80  # version 8
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 9562 variant
    return UUID(bytes=bytes(raw))


def is_connected_machine_id(value: UUID) -> bool:
    return value.version == 8 and value.bytes[: len(_CONNECTED_TAG)] == _CONNECTED_TAG


def generate_pairing_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[index : index + 4] for index in range(0, CODE_LENGTH, 4))


def normalize_pairing_code(code: str) -> str:
    """The canonical spelling of a code as a person may have typed it."""
    cleaned = re.sub(r"[\s-]", "", code).upper()
    cleaned = cleaned.translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))
    return "-".join(cleaned[index : index + 4] for index in range(0, len(cleaned), 4))


def secret_hash(value: str) -> str:
    """SHA-256 of a code, claim key or machine secret.

    Unsalted, as ``agent_host_auth`` is: the secrets and claim keys are 256-bit
    and the code is useless without its claim key, so there is no small keyspace
    for a table to cover.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_machine_secret() -> str:
    return secrets.token_urlsafe(32)


def default_name(display_name: str) -> str:
    """A ref-safe name from what the machine called itself."""
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")[:63]
    return slug.strip("-") or "machine"


@dataclass(frozen=True, slots=True)
class PendingPairing:
    """A code the machine asked for, as the approving person is shown it."""

    pairing_id: UUID
    display_name: str
    mode: str
    platform: str
    expires_at: datetime
    approved: bool


@dataclass(frozen=True, slots=True)
class MachineConnection:
    id: UUID
    user_id: UUID
    name: str
    display_name: str
    mode: str
    pod_id: UUID | None
    platform: str | None
    home: str | None
    capabilities: dict[str, object] = field(default_factory=dict)
    runtime_version: str | None = None
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    #: Whether a link holds it right now (from Redis, not the row).
    online: bool = False
