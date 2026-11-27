from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional
from uuid import UUID

from app.core.authorization.scope import uow_scope
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.contracts.webhook_source import NormalizedWebhook
from app.modules.schedule.domain.errors import ScheduleSourceEventIdRequiredError
from app.modules.schedule.services.trigger_fire import (
    FireTrigger,
    TriggerEvent,
    fire_trigger,
    jsonable,
)
from app.modules.connectors.contracts.pod_connectors import (
    standing_resolutions_once,
)
from app.modules.schedule.services.schedule_connector_policy import (
    StandingIdentity,
    standing_identity,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.webhook_event_mapper import WebhookEventMapper
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


#: The account a schedule's pod connector resolves to right now, as its owner
#: -- or ``APP_IDENTITY`` for an APP connector -- or ``None`` when it no longer
#: resolves at all.
BoundAccount = Callable[[Any, ScheduleEntity], Awaitable[StandingIdentity | None]]


def _narrowed(
    schedules: list[ScheduleEntity], normalized: NormalizedWebhook
) -> list[ScheduleEntity]:
    """What the source says about the delivery beyond its routing key."""
    if normalized.refine is not None:
        # The routing key is deliberately coarse -- see `match_criteria`.
        # Schedules that scoped themselves further are filtered here.
        schedules = [s for s in schedules if normalized.refine(s.config or {})]
    return schedules


async def _still_bound(
    uow: Any,
    schedules: list[ScheduleEntity],
    verified_by: str | None,
    bound_account: BoundAccount,
) -> list[ScheduleEntity]:
    """Only the schedules whose pod connector still resolves, as their owner.

    A schedule names a connector, and what it listens through is whatever
    that connector means *now*: an owner who left the pod, or a shared
    account its owner withdrew, stops the schedule here, on the next delivery,
    whatever its routing key still says.

    ``verified_by`` is the account a source proved the delivery with its own
    secret: only a schedule resolving to that very account may claim it -- so
    never one on an APP connector, which has no account.
    """
    kept: list[ScheduleEntity] = []
    # Several schedules on one connector and owner share one resolution.
    with standing_resolutions_once():
        for schedule in schedules:
            if schedule.pod_connector_slug is None:
                if verified_by is None:
                    kept.append(schedule)
                continue
            account_id = await bound_account(uow, schedule)
            if account_id is None:
                continue
            if verified_by is not None and str(account_id) != verified_by:
                continue
            kept.append(schedule)
    return kept


async def _in_organizations(
    uow: Any, schedules: list[ScheduleEntity], organization_ids: frozenset[str]
) -> list[ScheduleEntity]:
    """Only the schedules in pods of the organizations the delivery belongs to.

    The routing key names a tenant the source bound to organizations, each on
    its own proof (a GitHub installation, audit H4). A schedule in any other
    organization that carries the same key -- written before the binding
    existed, or by any path that skipped it -- is somebody else's, and does
    not run on this event.
    """
    from app.modules.pod.contracts.members import pod_organization_id

    owners: dict[UUID, UUID | None] = {}
    kept: list[ScheduleEntity] = []
    for schedule in schedules:
        if schedule.pod_id is None:
            continue
        if schedule.pod_id not in owners:
            owners[schedule.pod_id] = await pod_organization_id(uow, schedule.pod_id)
        if str(owners[schedule.pod_id]) in organization_ids:
            kept.append(schedule)
    if len(kept) != len(schedules):
        logger.warning(
            "schedule.webhook_handler.foreign_tenant_schedules_skipped.denied",
            skipped=len(schedules) - len(kept),
        )
    return kept


def _in_pods(
    schedules: list[ScheduleEntity], pod_ids: frozenset[str]
) -> list[ScheduleEntity]:
    """Only the schedules in the pods the delivery is for.

    Matching is containment across every tenant's schedules, and a Slack
    channel id is something anyone can type into a config. What makes a
    channel's messages a pod's is that the pod brought the channel in -- which
    the module that heard the message knows, and states as these pods.
    """
    kept = [s for s in schedules if s.pod_id is not None and str(s.pod_id) in pod_ids]
    if len(kept) != len(schedules):
        logger.warning(
            "schedule.webhook_handler.foreign_pod_schedules_skipped.denied",
            skipped=len(schedules) - len(kept),
        )
    return kept


async def _eligible(
    uow: Any,
    schedules: list[ScheduleEntity],
    normalized: NormalizedWebhook | None,
    bound_account: BoundAccount,
) -> list[ScheduleEntity]:
    """The matched schedules this delivery may actually fire.

    The tenancy scopes a source stated go first, so a schedule that is not the
    delivery's never costs a connector resolution in `_still_bound`.
    """
    if normalized is not None:
        schedules = _narrowed(schedules, normalized)
        if normalized.pod_ids is not None:
            schedules = _in_pods(schedules, normalized.pod_ids)
    schedules = await _still_bound(
        uow,
        schedules,
        normalized.account_id if normalized is not None else None,
        bound_account,
    )
    if normalized is not None and normalized.organization_ids is not None:
        schedules = await _in_organizations(uow, schedules, normalized.organization_ids)
    return schedules


def _plugin_metadata(normalized: NormalizedWebhook) -> Dict[str, Any]:
    """What a run is told about a delivery that stated its own routing key."""
    metadata: Dict[str, Any] = {**(normalized.match or {}), **normalized.context}
    metadata.setdefault("source_event_id", normalized.source_event_id)
    return metadata


async def fire_heard_event(
    uow: Any,
    normalized: NormalizedWebhook,
    *,
    matcher: WebhookScheduleMatcher | None = None,
    fire: FireTrigger = fire_trigger,
    bound_account: BoundAccount = standing_identity,
) -> list[UUID]:
    """Fire the WEBHOOK schedules an event another module heard matches.

    For an event that never reaches `POST /webhooks/{source}`: a Slack message
    arrives on the agent surfaces' own endpoint, signed with the secret of the
    app that heard it, and that module verified it, logged it and knows which
    pods brought its channel in. It states that as `pod_ids`, which is required
    -- a caller that could not say whose event this is must not reach every
    tenant's schedules by containment.

    Everything else is the delivery path's own: the same matching, the same
    connector check, the same fire. All of it is writes on the caller's open
    transaction, so the runs commit with whatever the caller wrote beside them,
    and a repeat of the same `source_event_id` is a no-op per schedule.
    """
    if normalized.match is None or normalized.pod_ids is None:
        raise ValueError("A heard event states its routing key and its pods.")
    if not normalized.source_event_id:
        raise ScheduleSourceEventIdRequiredError()
    matcher = matcher or WebhookScheduleMatcher(
        schedule_repository=ScheduleRepository(uow=uow)
    )
    schedules = await _eligible(
        uow,
        await matcher.match_criteria(normalized.match),
        normalized,
        bound_account,
    )
    event = TriggerEvent(
        source_event_id=normalized.source_event_id,
        payload=jsonable(normalized.payload),
        metadata=jsonable(_plugin_metadata(normalized)),
    )
    for schedule in schedules:
        await fire(uow, schedule, event)
    return [schedule.id for schedule in schedules]


class WebhookHandler:
    """Service for handling webhooks and matching them to schedules."""

    def __init__(
        self,
        matcher_factory: Callable[[Any], WebhookScheduleMatcher] | None = None,
        uow_factory: Any = None,
        event_mapper: WebhookEventMapper | None = None,
        fire: FireTrigger = fire_trigger,
        bound_account: BoundAccount = standing_identity,
    ):
        """Take a way to *open* a unit of work, not an open one.

        The match and every fire it causes run in one short transaction of
        their own: run rows and the work items that start their targets (or
        ask their filters) commit together, and nothing slow happens inside it.
        """
        self.matcher_factory = matcher_factory
        self.uow_factory = uow_factory
        if self.matcher_factory is None or self.uow_factory is None:
            raise ValueError("matcher_factory and uow_factory are required")
        self.event_mapper = event_mapper or WebhookEventMapper()
        self._fire = fire
        self._bound_account = bound_account

    async def handle_webhook(
        self,
        source: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        normalized: NormalizedWebhook | None = None,
    ) -> list[UUID]:
        """Handle incoming webhook and find matching schedules.

        A source plugin that states its own routing key passes `normalized` and
        the mapper is not consulted. The mapper remains the path for the sources
        that predate the plugins -- Composio's branch in particular, whose exact
        output is pinned by tests, and which was moved onto a plugin on the
        explicit condition that its behaviour did not change with it.
        """

        if normalized is not None and normalized.match is not None:
            metadata: Dict[str, Any] = _plugin_metadata(normalized)
            normalized_payload = normalized.payload
        else:
            normalized_payload = self.event_mapper.normalize_payload(
                source=source,
                payload=payload,
            )
            metadata = self.event_mapper.extract_metadata(
                source, normalized_payload, headers
            )
        source_event_id = metadata.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id:
            logger.warning(
                "schedule.webhook_handler.quarantined_webhook_without_stable_provider.degraded"
            )
            raise ScheduleSourceEventIdRequiredError()
        if normalized is not None and normalized.match is not None:
            publish_payload = normalized.payload
        else:
            publish_payload = self.event_mapper.event_payload_for_source(
                source, normalized_payload
            )
        event = TriggerEvent(
            source_event_id=source_event_id,
            payload=jsonable(publish_payload),
            metadata=jsonable(metadata),
        )
        schedule_ids: list[UUID] = []
        async with uow_scope(self.uow_factory) as uow:
            matcher = self.matcher_factory(uow)
            if normalized is not None and normalized.match is not None:
                schedules = await matcher.match_criteria(normalized.match)
            else:
                schedules = await matcher.match(source, metadata)
            schedules = await _eligible(uow, schedules, normalized, self._bound_account)
            for schedule in schedules:
                # A webhook fire has no row owner; the schedule owner runs it.
                await self._fire(uow, schedule, event)
                schedule_ids.append(schedule.id)
        return schedule_ids
