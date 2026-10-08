"""Vault failures. None of them carries secret material in its message.

Domain errors, so one that reaches the API boundary becomes its own status
rather than a 500: a missing secret is a 404, a lost race a 409, keys that
cannot be loaded a 503.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.errors import DomainError


class VaultError(DomainError):
    def __init__(
        self, message: str, *, code: str = "VAULT_ERROR", status_code: int = 500
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class SecretNotFound(VaultError):
    def __init__(self, secret_id: UUID) -> None:
        super().__init__(
            f"secret {secret_id} not found", code="SECRET_NOT_FOUND", status_code=404
        )
        self.secret_id = secret_id


class SecretScopeMismatch(SecretNotFound):
    """The secret exists but not for the scope or purpose the caller expected.

    A subclass of :class:`SecretNotFound` on purpose: to anyone above the vault
    this is indistinguishable from absence, and must not become an oracle for
    which ids exist in other tenants.
    """


class SecretIntegrityError(VaultError):
    """The stored bytes did not authenticate. Tampering or corruption."""

    def __init__(self, secret_id: UUID) -> None:
        super().__init__(
            f"secret {secret_id} failed its integrity check",
            code="SECRET_INTEGRITY_FAILED",
        )
        self.secret_id = secret_id


class SecretVersionConflict(VaultError):
    """Someone else wrote the secret, or took its lease, first."""

    def __init__(self, secret_id: UUID, expected: int | None, actual: int) -> None:
        super().__init__(
            f"secret {secret_id} is at version {actual}, expected {expected}",
            code="SECRET_VERSION_CONFLICT",
            status_code=409,
        )
        self.secret_id = secret_id
        self.actual = actual


class VaultUnavailable(VaultError):
    """The vault's keys could not be loaded. The process cannot read secrets."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="VAULT_UNAVAILABLE", status_code=503)
