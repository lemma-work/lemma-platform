"""What a machine call can be refused with, each a sentence a person can act on."""

from __future__ import annotations

from app.core.domain.errors import DomainError


class MachineNotFound(DomainError):
    """No such machine -- or one this caller may not know about."""

    def __init__(self, ref: str) -> None:
        super().__init__(
            f"There is no machine '{ref}' you can use.",
            code="MACHINE_NOT_FOUND",
            status_code=404,
        )


class MachineAccessDenied(DomainError):
    """The caller can see the machine but not do this to it."""

    def __init__(self, ref: str, needs: str) -> None:
        super().__init__(
            f"You can see '{ref}' but not {needs} it. Ask a pod editor.",
            code="MACHINE_ACCESS_DENIED",
            status_code=403,
        )


class MachineNotConnected(DomainError):
    """A connected machine whose link is down. Never answered by another one."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} is not connected.",
            code="MACHINE_NOT_CONNECTED",
            status_code=409,
        )


class MachinePolicyRefused(DomainError):
    """The plan does not allow this machine, size, template or count."""

    def __init__(self, message: str, *, code: str = "MACHINE_POLICY") -> None:
        super().__init__(message, code=code, status_code=403)


class MachineCapabilityMissing(DomainError):
    """The machine cannot do this at all -- no Docker, no browser, offline files."""

    def __init__(self, ref: str, capability: str) -> None:
        super().__init__(
            f"'{ref}' has no {capability}.",
            code="MACHINE_CAPABILITY_MISSING",
            status_code=409,
        )


class MachineConflict(DomainError):
    def __init__(self, message: str, *, code: str = "MACHINE_CONFLICT") -> None:
        super().__init__(message, code=code, status_code=409)
