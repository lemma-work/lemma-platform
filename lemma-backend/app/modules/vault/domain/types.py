"""Values that cross the vault's boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
type SecretValue = str | JsonObject


class SecretKind(StrEnum):
    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class SecretScope:
    """Who a secret belongs to, as the *caller* understands it.

    Every read passes the scope it expects, taken from the row that points at
    the secret (the account, the surface, ...), never from the secret itself.
    The scope is part of the ciphertext's associated data, so a row pointed at
    another tenant's secret gets a failed decryption, not that tenant's value.
    """

    organization_id: UUID | None
    pod_id: UUID | None = None
    user_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.organization_id is None and (self.pod_id or self.user_id):
            raise ValueError("a pod- or user-scoped secret needs an organization")

    @classmethod
    def system(cls) -> SecretScope:
        """Deployment-wide: belongs to no organization (the WhatsApp pool)."""
        return cls(organization_id=None)


@dataclass(frozen=True, slots=True)
class SecretRef:
    id: UUID
    version: int


@dataclass(frozen=True, slots=True)
class SecretMeta:
    """What can be known about a secret without decrypting it."""

    id: UUID
    version: int
    expires_at: datetime | None
    lease_until: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class LeaseToken:
    """Proof of holding a secret's refresh lease, for the write that ends it."""

    secret_id: UUID
    holder: str
    version: int


class ActorKind(StrEnum):
    SYSTEM = "system"
    USER = "user"
    WORKLOAD = "workload"
    JOB = "job"


@dataclass(frozen=True, slots=True)
class AuditActor:
    kind: ActorKind
    id: str | None = None

    @classmethod
    def system(cls) -> AuditActor:
        return cls(ActorKind.SYSTEM)


@dataclass(frozen=True, slots=True)
class Revealed:
    """A decrypted secret. Its ``repr`` never shows the value."""

    secret_id: UUID
    version: int
    kind: SecretKind
    expires_at: datetime | None
    _payload: bytes = field(repr=False)

    def text(self) -> str:
        if self.kind is not SecretKind.TEXT:
            raise TypeError(f"secret {self.secret_id} holds JSON, not text")
        return self._payload.decode("utf-8")

    def json(self) -> JsonObject:
        if self.kind is not SecretKind.JSON:
            raise TypeError(f"secret {self.secret_id} holds text, not JSON")
        value = json.loads(self._payload)
        if not isinstance(value, dict):
            raise TypeError(f"secret {self.secret_id} does not hold a JSON object")
        return value

    def value(self) -> SecretValue:
        return self.text() if self.kind is SecretKind.TEXT else self.json()

    def __repr__(self) -> str:
        return (
            f"Revealed(secret_id={self.secret_id}, version={self.version}, value=***)"
        )

    __str__ = __repr__


def encode_value(value: SecretValue) -> tuple[SecretKind, bytes]:
    if isinstance(value, str):
        return SecretKind.TEXT, value.encode("utf-8")
    return SecretKind.JSON, json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
