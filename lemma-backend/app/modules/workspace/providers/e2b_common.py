"""Vocabulary shared by the E2B lifecycle and ops halves.

Metadata keys are namespaced rather than hardcoded because one E2B account is
shared: a conformance run must be able to label its sandboxes so that neither
its queries nor its sweeps can ever see production's, and vice versa.
"""

from __future__ import annotations

from contextlib import contextmanager

from sandbox_runtime.errors import (
    SandboxPathNotFound,
    SandboxUnavailable,
)
from app.modules.workspace.providers.base import ProviderGone, ProviderRejected

DEFAULT_METADATA_NAMESPACE = "lemma"


def meta_sandbox_id(namespace: str) -> str:
    return f"{namespace}-sandbox-id"


def meta_sandbox_kind(namespace: str) -> str:
    return f"{namespace}-sandbox-kind"


def meta_epoch(namespace: str) -> str:
    return f"{namespace}-epoch"


def meta_profile_digest(namespace: str) -> str:
    return f"{namespace}-profile-digest"


META_SANDBOX_ID = meta_sandbox_id(DEFAULT_METADATA_NAMESPACE)
META_SANDBOX_KIND = meta_sandbox_kind(DEFAULT_METADATA_NAMESPACE)
META_EPOCH = meta_epoch(DEFAULT_METADATA_NAMESPACE)
META_PROFILE_DIGEST = meta_profile_digest(DEFAULT_METADATA_NAMESPACE)



def classify(exc: Exception) -> Exception:
    """Turn an SDK failure into this module's two-axis vocabulary.

    Deliberately no retry loop. Whether waiting could help is a question about
    the caller's deadline, which the service owns; the provider's job is to say
    what happened.
    """
    name = type(exc).__name__
    message = str(exc)

    if "NotFound" in name or "not found" in message.lower():
        return ProviderGone(message)
    if "RateLimit" in name or "429" in message:
        return SandboxUnavailable(message, retry_after_ms=2000)
    if "Timeout" in name or "timeout" in message.lower():
        return SandboxUnavailable(message, retry_after_ms=1000)
    if "Authentication" in name or "401" in message or "403" in message:
        return ProviderRejected(f"e2b rejected the credentials: {message}")
    if "Invalid" in name or "400" in message:
        return ProviderRejected(message)
    # Unknown failures are treated as worth retrying: E2B is a network service,
    # and a permanent failure will simply fail again with the same message.
    return SandboxUnavailable(message, retry_after_ms=1000)


def classify_path(exc: Exception, path: str) -> Exception:
    name = type(exc).__name__
    if "NotFound" in name or "not found" in str(exc).lower():
        return SandboxPathNotFound(f"{path} does not exist")
    return classify(exc)


@contextmanager
def sdk_errors(path: str | None = None):
    """Translate whatever one E2B SDK call raises into this module's vocabulary.

    The catch is deliberately broad, and it is broad in exactly one place. The
    SDK's exception classes cannot be imported without the optional extra
    installed, so failures are classified by shape rather than caught by type
    -- and doing that at every call site meant thirty copies of the same three
    lines, each of which could quietly drift.

    Pass `path` for filesystem calls, where "not found" is a missing file
    rather than a missing sandbox.
    """

    try:
        yield
    except Exception as exc:
        raise (classify_path(exc, path) if path is not None else classify(exc)) from exc


@contextmanager
def sdk_best_effort(path: str | None = None):
    """A call whose failure is already the outcome it was asking for.

    Killing a process that has exited, or deleting a file that is not there,
    has achieved what it set out to do. Classifying first and then catching
    only this module's own types keeps that intent narrow: a `NameError` in our
    own code still propagates, where the bare `except Exception` these replaced
    would have swallowed it and reported success.
    """

    try:
        with sdk_errors(path):
            yield
    except (
        ProviderGone,
        ProviderRejected,
        SandboxPathNotFound,
        SandboxUnavailable,
    ):
        return
