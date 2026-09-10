"""Parts a one-reply surface has collected but has not sent yet.

A chat surface delivers each envelope as it is ready. Email cannot: the person
gets one composed reply, so anything the run wants to show has to become part of
it. Until now that meant ``display_resource`` on an email surface returned
``success=True, "FILE resource ready for display."`` and delivered nothing --
the model believed it had shown the file, and the recipient never saw one.

This is where those parts wait. The agent's reply tool drains them when it
sends, so a file the agent displayed arrives attached to the reply it was
displayed alongside.

**Run-scoped, in this process, on purpose.** One agent run is one asyncio task
in one worker, and every writer here -- the ``display_resource`` tool, the reply
tool, the run observer -- is inside it. Redis would buy nothing a dict does not
already give, and would have to carry file paths across a boundary they never
cross. What it costs is that a run which dies mid-turn drops what it collected,
which is exactly what happened before this existed: no regression, just no
improvement in that one case.
"""

from __future__ import annotations

from uuid import UUID

from app.core.bounded import BoundedDict
from app.core.log.log import get_logger

logger = get_logger(__name__)

# conversation_id -> pod file paths displayed but not yet sent, in the order the
# agent showed them.
#
# Bounded on the outer key too: the inner lists are capped, but an entry is only
# removed when the run that owns it finishes or fails. An observer that never
# fires leaves its conversation behind permanently.
_MAX_PENDING_CONVERSATIONS = 2048
_pending_paths: BoundedDict[UUID, list[str]] = BoundedDict(_MAX_PENDING_CONVERSATIONS)

# A run that shows a hundred files is a runaway, and the reply would be refused
# by the provider anyway. Bound it rather than letting the dict grow.
_MAX_PENDING_PATHS = 20


def remember_display_path(conversation_id: UUID, path: str) -> bool:
    """Hold a displayed pod file until the one reply goes out.

    Returns whether it was taken: a duplicate or an overflowing run is declined
    rather than silently dropped, so the caller can tell the model the truth.
    """
    if not path:
        return False
    pending = _pending_paths.setdefault(conversation_id, [])
    if path in pending:
        # Showing the same file twice is one attachment, not two.
        return True
    if len(pending) >= _MAX_PENDING_PATHS:
        logger.warning(
            "agent_surfaces.pending_envelope.display_paths_overflowed.degraded",
            conversation_id=str(conversation_id),
            limit=_MAX_PENDING_PATHS,
        )
        return False
    pending.append(path)
    return True


def take_display_paths(conversation_id: UUID) -> list[str]:
    """Drain what was collected, so a second reply does not re-attach it."""
    return _pending_paths.pop(conversation_id, [])


def discard_display_paths(conversation_id: UUID) -> None:
    """Forget what was collected -- the run ended without a reply going out."""
    _pending_paths.pop(conversation_id, None)
