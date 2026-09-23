"""What a sandbox can do to you, as types.

Beside the protocol rather than inside the workspace module because both
the workspace module and the function module have to catch these -- a
function's sandbox fails in exactly the ways a workspace's does. Putting
them here is what stops the function module reaching across a boundary to
name an exception.

The hierarchy carries the only distinction a caller acts on: whether
waiting could help. `SandboxUnavailable` means try again;
`SandboxRejected` means do not.
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


class SandboxProcessNotFound(SandboxRejected):
    """The sandbox has no process with this id.

    Its own type because the nearest words were both wrong. `ProviderGone`
    says the *sandbox* is gone, and callers act on that by forgetting their
    handle to it and re-ensuring -- so a stale process id cost a lost
    workspace handle. `SandboxUnavailable` says wait, and waiting for a
    process that does not exist ends only at the deadline.
    """


class SandboxUnauthorized(SandboxRejected):
    """The sandbox refused this caller's credential.

    Definitive -- a credential does not become valid by waiting -- and kept
    apart from the other refusals because they mean "your request was wrong",
    which is what a reader tells a user, and this means "Lemma could not
    authenticate to its own sandbox", which is not the user's to fix.
    """
