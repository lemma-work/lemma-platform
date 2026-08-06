"""Sandbox error vocabulary.

Two axes matter, and only two. Whether the caller may retry, and whether the
operation may have taken effect. Everything else is a message.

The retryable/definitive split is what stops a caller hammering a provider that
has definitively said no. The ambiguity split is what stops a caller replaying
an operation that may already have run -- replaying a create leaks a container
nobody owns, and replaying a python execution runs a user's side effects twice.

Deterministic resource naming removes most sources of ambiguity: a create that
may or may not have landed is resolved by the name either existing or not. What
survives is the genuinely unresolvable case, which is why the type remains.
"""

from __future__ import annotations


class SandboxError(RuntimeError):
    """Base for every sandbox failure."""


class SandboxUnavailable(SandboxError):
    """Retry later. Nothing was consumed and nothing definitive was learned."""

    def __init__(self, message: str, *, retry_after_ms: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class SandboxNotReady(SandboxUnavailable):
    """The exact sandbox exists but is not usable before the deadline."""


class SandboxRejected(SandboxError):
    """Definitive. Retrying the same request cannot succeed."""


class SandboxNotFound(SandboxRejected):
    """No sandbox with this identity exists."""


class SandboxCapabilityUnsupported(SandboxRejected):
    """The operation is not something this kind of sandbox can do."""

    def __init__(self, capability: str, *, kind: str) -> None:
        super().__init__(
            f"{kind} sandboxes do not support {capability}",
        )
        self.capability = capability
        self.kind = kind


class SandboxOperationAmbiguous(SandboxError):
    """The operation may have taken effect. It must never be replayed."""


class SandboxPathNotFound(SandboxRejected):
    """The path definitively does not exist inside the sandbox."""


class SandboxPathConflict(SandboxRejected):
    """A filesystem precondition or destination constraint was not satisfied."""
