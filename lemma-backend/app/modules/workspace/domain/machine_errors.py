"""What a machine call can be refused with, each a sentence a person can act on."""

from __future__ import annotations

from app.core.authorization.outside_text import OUTSIDE_TEXT_REQUIRES_APPROVAL
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError


class MachineNotFound(DomainError):
    """No such machine -- or one this caller may not know about."""

    def __init__(self, ref: str, *, hint: str | None = None) -> None:
        message = f"There is no machine '{ref}' you can use."
        super().__init__(
            f"{message} {hint}" if hint else message,
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


class MachineHeldForApproval(MachineAccessDenied):
    """A run reading what people outside the pod wrote, asking to use a
    machine: its person approves first, as for anything else such a run does
    beyond the pod's own records and files (``outside_text_gate``).

    The authorizer's own refusal code and the permission it is for, so an
    approval card -- the agent's ``request_approval``, or the one a delegated
    token's refusal is recorded for -- lends exactly ``machine.use``.
    """

    def __init__(self, ref: str) -> None:
        DomainError.__init__(
            self,
            f"This run is reading what people outside the pod wrote, so the "
            f"person it acts for approves using '{ref}' first. Ask them with "
            "request_approval for this same call.",
            code=OUTSIDE_TEXT_REQUIRES_APPROVAL,
            status_code=403,
            details={"permission_ids": [Permissions.MACHINE_USE]},
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


class MachineMoving(DomainError):
    """The machine is being moved to a new size; using it now would lose work."""

    def __init__(self, ref: str) -> None:
        super().__init__(
            f"'{ref}' is being moved to a new size and cannot be used until the "
            "move ends. Try again later.",
            code="MACHINE_MOVING",
            status_code=409,
        )


class MachineConflict(DomainError):
    def __init__(self, message: str, *, code: str = "MACHINE_CONFLICT") -> None:
        super().__init__(message, code=code, status_code=409)
