"""Whether a conversation's run has read what somebody outside the pod wrote.

A member's run in a group is shown the whole group -- what people outside the
pod said there included, marked as theirs -- because a run that cannot see what
the room said answers the room wrongly. What it reads that way is information,
never a request: the person the run acts for asked it something, and nobody
else in the room may spend that person's access. The prompt says so; this is
the part that does not depend on the model listening.

The run's turn writes here whether its prompt carries such lines, and the
authorizer reads it back for every check software makes on the person's behalf
in that conversation (``Authorizer._outside_text_decision``): in-process tools,
the MCP bridge, the sandbox's ``lemma`` CLI and an approved re-run all reach
the same question. Kept by conversation -- the delegation's ``session_id`` --
rather than on a run's context or in a token, because a sandbox token and an
MCP context were minted before the run read anything.

Redis, like the session approvals beside it. Unavailable, it answers "not
read": the mark is a second line behind the prompt's, and an outage of it must
not stop every member's run from writing.
"""

from __future__ import annotations

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
_store_incident = DependencyIncident("outside_text_store", logger=logger)

#: The refusal an agent meets for acting while it reads strangers' words. An
#: approval lifts it, like the destructive gate's.
OUTSIDE_TEXT_REQUIRES_APPROVAL = "OUTSIDE_TEXT_REQUIRES_APPROVAL"

#: Outlives any one turn: a turn sets or clears it as it starts, so the only
#: thing this bounds is a mark left by a conversation nobody comes back to.
_MARK_TTL_SECONDS = 24 * 3600

#: The store's own failures: the server or the network, or a value not JSON.
_STORE_ERRORS = (RedisError, OSError, ValueError)

_marks: RedisJsonCache[object] | None = None


def _store() -> RedisJsonCache[object]:
    global _marks
    if _marks is None:
        _marks = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:outside-text",
            ttl_seconds=_MARK_TTL_SECONDS,
        )
    return _marks


async def note_outside_text(session_id: str) -> None:
    """This conversation's run has read lines from outside the pod."""
    try:
        await _store().set_json(session_id, True)
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def forget_outside_text(session_id: str) -> None:
    """A turn whose prompt carries nothing from outside the pod."""
    try:
        await _store().delete(session_id)
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
    else:
        _store_incident.record_success()


async def has_read_outside_text(session_id: str | None) -> bool:
    """Whether this conversation's run is reading lines from outside the pod."""
    if not session_id:
        return False
    try:
        marked = await _store().get_json(session_id)
    except _STORE_ERRORS as exc:
        _store_incident.record_failure(error_type=type(exc).__name__)
        return False
    _store_incident.record_success()
    return marked is True
