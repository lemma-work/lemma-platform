"""Applying the outreach rules (``domain.outreach``) to stored facts.

Two halves, because they read two stores and only one of them may be read
while a database connection is held:

* ``may_send`` reads the database -- the contact's handles, the opt-outs --
  and never counts anything, so a member can be told up front whether a line
  could go, inside whatever unit of work they are in.
* ``count_against_today`` counts a member's line against the contact's day in
  Redis, once per line however often its delivery is retried, and is called
  with no unit of work open.

And how a contact opts out and back in (``opt_out``, ``heard_from``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.contacts.config import contacts_settings
from app.modules.contacts.domain.entities import ContactHandle
from app.modules.contacts.domain.handles import handle_hash, normalize_handle
from app.modules.contacts.domain.kinds import IdentityKind
from app.modules.contacts.domain.outreach import (
    RATE_LIMITED_REASON,
    UNAVAILABLE_REASON,
    Address,
    OutreachKind,
    RefusalCode,
    SendWindow,
    Verdict,
    decide,
)
from app.modules.contacts.infrastructure.repository import ContactRepository
from app.modules.contacts.infrastructure.suppressions import (
    SuppressionReason,
    SuppressionRepository,
)
from app.modules.contacts.services.unsubscribe import (
    UnsubscribeTarget,
    unsubscribe_footer,
    unsubscribe_token,
)

logger = get_logger(__name__)

DAY = 86_400

#: Count one line into a contact's day, unless the day is full. A line already
#: counted is let through again: a retry is the same line. Atomic, so two lines
#: at once cannot both take the day's last place, and the window's expiry is
#: set with the first line and repaired if it was ever lost.
_COUNT_LINE_LUA = """
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then return 1 end
if redis.call('SCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
redis.call('SADD', KEYS[1], ARGV[1])
if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ARGV[3]) end
return 1
"""


async def may_send(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    contact_id: UUID,
    to: Address,
    kind: OutreachKind,
    at: datetime,
    window: SendWindow | None = None,
) -> Verdict:
    """Whether a line of ``kind`` may go to this contact at ``to`` now.

    Opt-out first, then a handle, then the platform's window (``domain``).
    A contact since merged answers as the one it became; one forgotten has no
    handles, and the place's own address is still checked for an opt-out.
    """
    del kind  # Every kind is held to the same rules; only the count differs.
    contacts = ContactRepository(uow.session)
    contact = await contacts.get(pod_id=pod_id, contact_id=contact_id)
    if contact is not None and contact.merged_into_id is not None:
        contact = await contacts.get(pod_id=pod_id, contact_id=contact.merged_into_id)
    handles = list(contact.identities) if contact is not None else []
    values = {handle.value for handle in handles if handle.kind is to.kind}
    if to.value is not None:
        values.add(normalize_handle(to.kind, to.value))
    suppressed = await SuppressionRepository(uow.session).suppressed(
        pod_id=pod_id, kind=to.kind, values=values
    )
    verdict = decide(
        to=to, handles=handles, suppressed=suppressed, at=at, window=window
    )
    if not verdict.allowed or verdict.handle is None:
        return verdict
    if to.kind is not IdentityKind.EMAIL:
        return verdict
    address = normalize_handle(to.kind, to.value) if to.value else verdict.handle.value
    token = unsubscribe_token(pod_id, to.kind, address)
    return verdict.model_copy(
        update={"footer": unsubscribe_footer(token), "unsubscribe_token": token}
    )


async def opted_out_handles(
    uow: SqlAlchemyUnitOfWork, handles: Sequence[ContactHandle], *, pod_id: UUID
) -> set[UUID]:
    """Which of a contact's handles asked the pod to stop: the ids, one read
    per kind of handle."""
    by_kind: dict[IdentityKind, list[ContactHandle]] = {}
    for handle in handles:
        by_kind.setdefault(handle.kind, []).append(handle)
    suppressions = SuppressionRepository(uow.session)
    stopped: set[UUID] = set()
    for kind, of_kind in by_kind.items():
        found = await suppressions.suppressed(
            pod_id=pod_id, kind=kind, values=[handle.value for handle in of_kind]
        )
        stopped |= {handle.id for handle in of_kind if handle.value in found}
    return stopped


async def count_against_today(
    *,
    contact_id: UUID,
    line_id: UUID,
    limit: int | None = None,
    redis: Redis | None = None,
) -> Verdict:
    """Count a member's line against the contact's day; refused once it is full.

    Redis away is ``unavailable`` and retryable: the line is not lost, and
    not let through uncounted either.
    """
    cap = (
        contacts_settings.contacts_member_lines_per_contact_per_day
        if limit is None
        else limit
    )
    try:
        counted = await (redis or get_redis()).eval(
            _COUNT_LINE_LUA, 1, f"contact:lines:{contact_id}", str(line_id), cap, DAY
        )
    except (RedisError, OSError) as exc:
        logger.warning(
            "contacts.outreach.day_count_unavailable.degraded",
            error_type=type(exc).__name__,
        )
        return Verdict.refused(
            RefusalCode.UNAVAILABLE, UNAVAILABLE_REASON, retryable=True
        )
    if not int(counted):
        return Verdict.refused(RefusalCode.RATE_LIMITED, RATE_LIMITED_REASON)
    return Verdict(allowed=True)


async def opt_out(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    kind: IdentityKind,
    value: str,
    reason: SuppressionReason,
) -> bool:
    """Nobody in the pod writes to this handle again until it opts back in.
    Whether it was opted in until now. Needs no contact: a stranger who says
    STOP is not written to either."""
    if not normalize_handle(kind, value):
        return False
    return await SuppressionRepository(uow.session).suppress(
        pod_id=pod_id, kind=kind, value_hash=handle_hash(kind, value), reason=reason
    )


async def opt_out_by_token(
    uow: SqlAlchemyUnitOfWork, target: UnsubscribeTarget
) -> bool | None:
    """Opt out the handle a signed link names; whether it was opted in.

    None when the link's pod is gone: it writes to nobody now, and there is
    nothing to keep the opt-out against.
    """
    suppressions = SuppressionRepository(uow.session)
    if not await suppressions.pod_exists(target.pod_id):
        return None
    return await suppressions.suppress(
        pod_id=target.pod_id,
        kind=target.kind,
        value_hash=target.value_hash,
        reason=SuppressionReason.UNSUBSCRIBE,
    )


async def heard_from(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    kind: IdentityKind,
    value: str,
    typed: bool,
) -> bool:
    """The person wrote from this handle. Whether that opted them back in.

    ``typed`` is the caller's word that a person wrote it -- a platform vouched
    for the sender and no header marks it automatic. Only that lifts an
    opt-out: an out-of-office answering our mail, or a forged sender, is not
    the person changing their mind, and does not count as hearing from them.
    """
    if not typed:
        return False
    await ContactRepository(uow.session).note_inbound(
        pod_id=pod_id, kind=kind, value=value
    )
    lifted = await SuppressionRepository(uow.session).lift(
        pod_id=pod_id, kind=kind, value=value
    )
    if lifted:
        logger.info("contacts.outreach.opted_back_in.observed", kind=kind.value)
    return lifted
