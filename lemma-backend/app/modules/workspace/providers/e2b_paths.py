"""Where a path in an E2B sandbox really is.

Split out of `e2b_ops` because it answers a different question from the rest of
that file -- what a path *is*, rather than what to do inside a sandbox -- and
because the operations file is at the size the architecture ratchet allows.

The reason it exists at all: E2B's file SDK reports a type that is only ever
"file" or "dir" and echoes back the path it was handed. A containment check
written against those two fields is therefore structurally inert here, and the
files API relies on exactly that check. It was correct on Docker and a comment
on E2B, so a symlink out of `/workspace` was served. `realpath` is what this
fabric does give us, so the boundary is enforced with that.
"""

from __future__ import annotations

import shlex

from app.core.log.log import get_logger
from sandbox_runtime.errors import SandboxPathNotFound, SandboxUnavailable
from app.modules.workspace.providers.base import (
    ProviderFailed,
    ProviderGone,
    ProviderRejected,
)
from app.modules.workspace.providers.e2b_common import sdk_errors

logger = get_logger(__name__)

#: Long enough for a filesystem call in a healthy sandbox, short enough that a
#: wedged one fails the read rather than holding the request open.
_RESOLVE_TIMEOUT_SECONDS = 15


async def resolve_real_path(sandbox, path: str) -> tuple[str, bool]:
    """The real path, and whether the last component was a link.

    Both from one command, quoted, so a path is data rather than shell. The two
    answers are separate on purpose: an *ancestor* being a link is not a
    caller's business to refuse -- the resolved path says where it landed --
    while the final component being one is, because where it points is not the
    files API's decision to make.
    """
    quoted = shlex.quote(path)
    script = (
        f"readlink -f -- {quoted} 2>/dev/null || true; "
        f"printf '\\n'; "
        f"[ -L {quoted} ] && printf link || printf plain"
    )
    try:
        # `sdk_errors` is this provider's one broad catch, on purpose: the SDK's
        # exception classes cannot be imported without the optional extra, so
        # failures are classified by shape in a single place. Catching its
        # vocabulary here keeps this narrow -- a `NameError` in our own code
        # still propagates rather than being read as "cannot resolve".
        with sdk_errors(path):
            outcome = await sandbox.commands.run(
                script, timeout=_RESOLVE_TIMEOUT_SECONDS
            )
    except (
        SandboxPathNotFound,
        SandboxUnavailable,
        ProviderFailed,
        ProviderGone,
        ProviderRejected,
    ) as exc:
        # Cannot answer, so do not pretend to. Reported as a link, which every
        # caller that asks this question refuses: a containment check that
        # cannot see has to fail closed.
        logger.warning(
            "workspace.e2b.path_not_resolved.degraded",
            path=path,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return path, True
    raw = str(getattr(outcome, "stdout", "") or "")
    resolved, _, marker = raw.partition("\n")
    return (resolved.strip() or path), marker.strip() == "link"


__all__ = ["resolve_real_path"]
