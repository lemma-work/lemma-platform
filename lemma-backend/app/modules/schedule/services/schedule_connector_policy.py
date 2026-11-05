"""May this person make a schedule listen through this pod connector?

A WEBHOOK schedule on a connector trigger or an MCP server's event names a pod
connector, never an account. Binding it is the person's own use of that
connector, so it is asked exactly as a connector operation is asked
(`resolve_pod_connector`): ``connector.use`` on the connector; for a SHARED one
the account half too; for a PER_MEMBER one the person's own account, which the
pod picks up on this first use. Its typed refusals -- CONNECTION_REQUIRED with
its connect link, CONNECTOR_NEEDS_ACCOUNT, ... -- reach the caller as they are.

Asked at creation, and again of whoever takes a schedule over by changing what
it does (`schedule_ownership.py`). What keeps it running afterwards is the
connector's standing, re-read as the owner on every use (`resolve_as_owner`):
subscribing, matching a delivery, reconciling a subscription.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.modules.connectors.contracts.pod_connectors import (
    ConnectorDomainError,
    PodConnectorAccount,
    resolve_pod_connector,
    resolve_standing_pod_connector,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
)

logger = get_logger(__name__)

#: ``(uow, ctx, *, pod_id, slug, pod_account_id)``: resolves as ``ctx`` or
#: raises; returns what it resolved (``PodConnectorAccount``).
ConnectorResolve = Callable[..., Awaitable[object]]

_Bindable = TypeVar("_Bindable", ScheduleCreateEntity, ScheduleEntity)


async def require_connector_use(
    schedule: _Bindable,
    ctx: Context | None,
    uow: object,
    *,
    resolve: ConnectorResolve | None = None,
) -> _Bindable:
    """Refuse a binding ``ctx`` could not make by using the connector itself.

    Returns ``schedule`` pinned to what it resolved to: on a PER_MEMBER
    connector the account it will listen through -- the one it already named
    (a deprecated ``account_id``'s), else the person's default now -- so a
    later change of their default does not move it. A SHARED one pins
    nothing: it follows its connector.
    """
    slug = schedule.pod_connector_slug
    if slug is None:
        return schedule
    if schedule.schedule_type is not ScheduleType.WEBHOOK:
        raise ScheduleValidationError(
            "Only a webhook schedule listens through a pod connector."
        )
    if schedule.pod_id is None:
        raise ScheduleValidationError(
            "A schedule listens through a pod connector of its own pod."
        )
    if ctx is None:
        raise RuntimeError("Context is required to bind a schedule to a connector")
    resolved = await (resolve or resolve_pod_connector)(
        uow,
        ctx,
        pod_id=schedule.pod_id,
        slug=slug,
        pod_account_id=schedule.pod_account_id,
    )
    pinned = (
        resolved.pinned_pod_account_id
        if isinstance(resolved, PodConnectorAccount)
        else schedule.pod_account_id
    )
    return schedule.model_copy(update={"pod_account_id": pinned})


async def resolve_as_owner(
    uow: object, schedule: ScheduleEntity, *, record_use: bool = True
) -> PodConnectorAccount:
    """What ``schedule``'s pod connector means right now, as its owner (§4.6),
    on the account it pinned while that is still theirs.

    Raises the connector's typed refusal when it no longer resolves.
    """
    if schedule.pod_id is None or schedule.pod_connector_slug is None:
        raise ScheduleValidationError(
            "A schedule listens through a pod connector of its own pod."
        )
    return await resolve_standing_pod_connector(
        uow,
        pod_id=schedule.pod_id,
        slug=schedule.pod_connector_slug,
        on_behalf_of=schedule.user_id,
        pod_account_id=schedule.pod_account_id,
        record_use=record_use,
    )


async def standing_account(uow: object, schedule: ScheduleEntity) -> UUID | None:
    """The account ``schedule`` listens through now, or ``None`` if none.

    For the paths that decide whether to act at all -- a delivery to match, a
    subscription to keep -- where a connector that stopped resolving is an
    answer, not an error. A read with no write: it runs for every matched
    schedule of every delivery. Inside ``standing_resolutions_once`` each
    (connector, owner, pinned account) is resolved once per delivery.
    """
    try:
        return (await resolve_as_owner(uow, schedule, record_use=False)).account_id
    except ConnectorDomainError as exc:
        logger.info(
            "schedule.connector.unresolved",
            schedule_id=str(schedule.id),
            code=getattr(exc, "code", type(exc).__name__),
        )
        return None


__all__ = [
    "ConnectorResolve",
    "require_connector_use",
    "resolve_as_owner",
    "standing_account",
]
