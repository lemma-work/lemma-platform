"""What the sandbox service is allowed to remember between calls.

Two memoizations, both narrow, both existing because a single shell tool call
asks the same questions several times over: it acquires a workspace session,
starts a process, then reads that process's output, and the sandbox client
re-ensures on every one of those.

The rule they follow is the one that makes caching safe here. A stale entry must
produce a *loud* failure, never a plausible answer:

* A remembered handle names a container by its epoch, so once that container is
  gone the operation fails definitively with ``ProviderGone`` -- and the process
  operations call ``forget`` when they see it, because the documented recovery
  is to re-ensure and a remembered handle would answer that with the dead one.
* A remembered running-check is keyed by the exact instance and epoch it was
  observed for, so a recreate cannot inherit it.

What is deliberately *not* remembered is the sandbox row: the epoch and storage
generation are read every time, because the directory key and the
workspace-recreated notice come from them, and a stale one of those is a silent
wrong answer rather than a retryable failure.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.modules.workspace.domain.sandbox import SandboxHandle

#: Spans one tool call's sequential operations. Not a warmth mechanism: that is
#: the idle release window, two orders of magnitude longer.
ENSURE_REUSE_SECONDS = 5.0
#: Ceiling on remembered entries, so a worker serving many users cannot grow
#: these without bound. They live for seconds, not for a session.
REUSE_CACHE_MAX = 512


class SandboxReuseMixin:
    """The service's short-lived memory, mixed in."""

    # Keyed by (event loop, sandbox id).
    _recent: dict[tuple[int, UUID], tuple[float, SandboxHandle]] = {}
    # Instances observed running, by (loop, sandbox, provider id, epoch).
    _verified_running: dict[tuple[int, UUID, str, int], float] = {}

    @classmethod
    def _remember_handle(cls, key: tuple[int, UUID], handle: SandboxHandle) -> None:
        cls._evict(cls._recent)
        cls._recent[key] = (asyncio.get_running_loop().time(), handle)

    @classmethod
    def _remember_verified(cls, key: tuple[int, UUID, str, int]) -> None:
        cls._evict(cls._verified_running)
        cls._verified_running[key] = asyncio.get_running_loop().time()

    @staticmethod
    def _evict(entries: dict) -> None:
        if len(entries) < REUSE_CACHE_MAX:
            return
        stale = sorted(entries, key=lambda key: _timestamp(entries[key]))
        for key in stale[: len(entries) - REUSE_CACHE_MAX + 1]:
            entries.pop(key, None)

    @staticmethod
    def _is_fresh(recorded_at: float) -> bool:
        return (asyncio.get_running_loop().time() - recorded_at) < ENSURE_REUSE_SECONDS

    def forget(self, sandbox_id: UUID) -> None:
        """Drop everything remembered about a sandbox that may be gone.

        ``ProviderGone`` is documented as definitive rather than retryable --
        "the caller must re-ensure to get a current handle" -- and a remembered
        handle would answer that re-ensure with the dead one. A sandbox can die
        without passing through ``release`` or ``destroy``: the sweeper destroys
        through the provider, E2B times sandboxes out server-side, and another
        replica's sweep is invisible here. So whatever discovers it says so.
        """
        for key in [key for key in self._recent if key[1] == sandbox_id]:
            self._recent.pop(key, None)
        for key in [key for key in self._verified_running if key[1] == sandbox_id]:
            self._verified_running.pop(key, None)

    #: The private spelling used by release/destroy inside the service.
    _forget_recent = forget


def _timestamp(value) -> float:
    """Both caches store a time; one stores it alongside a handle."""
    return value[0] if isinstance(value, tuple) else value
