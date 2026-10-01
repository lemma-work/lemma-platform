"""Checking a schedule's triage against the decider it names, when it is saved.

The request schema can only see the triage block. Whether it routes every
option a person will be asked about needs the decider itself, which is a pod
resource, so it is checked here, on create and on update alike -- and on the
create that a bundle import makes, which never sees the schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.decisions.contracts.shapes import ChoiceQuestion, DeciderDefinition
from app.modules.schedule.contracts.webhook_source import WebhookSourceRegistry
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
)
from app.modules.schedule.domain.triage import (
    TIME_SCHEDULE_TRIAGE_REFUSED,
    TRIAGE_REPLACES_FILTER,
    TriageConfig,
    TriageRoute,
    rearmed_digest_at,
    triage_json,
)
from app.modules.schedule.services.time_schedule_policy import (
    validate_cron_expression,
    validated_time_schedule_config,
)
from app.modules.schedule.services.webhook_source_policy import validate_webhook_source

if TYPE_CHECKING:
    from app.modules.decisions.contracts.deciders import DeciderEntity


class DeciderLookup(Protocol):
    """The decisions contract's `get_decider`, as much of it as a triage reads."""

    async def __call__(self, *, pod_id: UUID, name: str) -> DeciderEntity | None: ...


async def _get_decider(*, pod_id: UUID, name: str) -> DeciderEntity | None:
    # Here rather than at the top: the contract reaches the decisions service
    # and its engines, which only a schedule saved with a triage needs.
    from app.modules.decisions.contracts.deciders import get_decider

    return await get_decider(pod_id=pod_id, name=name)


async def validate_create_policies(
    schedule_create: ScheduleCreateEntity,
    *,
    ctx: Context | None,
    session: object | None,
    webhook_sources: WebhookSourceRegistry | None,
    decider_of: DeciderLookup = _get_decider,
) -> ScheduleCreateEntity:
    """What a new schedule's type and triage must satisfy; the schedule to write.

    The triage comes back with its question resolved, so a decider that gains
    a second question later does not change which one this schedule routes on.
    """
    if schedule_create.schedule_type == ScheduleType.TIME:
        if schedule_create.triage is not None:
            raise ScheduleValidationError(TIME_SCHEDULE_TRIAGE_REFUSED)
        await validated_time_schedule_config(schedule_create.config, session=session)
    elif schedule_create.schedule_type == ScheduleType.WEBHOOK:
        validate_webhook_source(schedule_create, webhook_sources)
    if schedule_create.triage is None:
        return schedule_create
    if schedule_create.filter_instruction or schedule_create.filter_output_schema:
        raise ScheduleValidationError(TRIAGE_REPLACES_FILTER)
    triage = await validated_triage(
        schedule_create.triage,
        pod_id=schedule_create.pod_id,
        ctx=ctx,
        session=session,
        decider_of=decider_of,
    )
    return schedule_create.model_copy(update={"triage": triage})


async def apply_triage_update(
    existing: ScheduleEntity,
    update_data: dict[str, object],
    *,
    ctx: Context | None,
    session: object | None,
    decider_of: DeciderLookup = _get_decider,
) -> None:
    """Check the triage an update leaves the schedule with, and re-arm its digest.

    Rewrites `update_data` in place into the columns it stands for: `triage`
    as stored JSON, or None when `clear_triage` asked for that, and
    `next_digest_at` whenever the triage changed.
    """
    clearing = bool(update_data.pop("clear_triage", False))
    given = update_data.pop("triage", None)
    if clearing:
        after: TriageConfig | None = None
    elif given is not None:
        after = TriageConfig.model_validate(given)
    else:
        after = existing.triage
    filtered = update_data.get(
        "filter_instruction", existing.filter_instruction
    ) or update_data.get("filter_output_schema", existing.filter_output_schema)
    if after is not None and filtered:
        raise ScheduleValidationError(TRIAGE_REPLACES_FILTER)
    if given is None and not clearing:
        return
    if after is not None:
        if existing.schedule_type == ScheduleType.TIME:
            raise ScheduleValidationError(TIME_SCHEDULE_TRIAGE_REFUSED)
        after = await validated_triage(
            after,
            pod_id=existing.pod_id,
            ctx=ctx,
            session=session,
            decider_of=decider_of,
        )
    update_data["triage"] = triage_json(after)
    update_data["next_digest_at"] = rearmed_digest_at(
        existing.triage, after, now=datetime.now(timezone.utc)
    )


async def validated_triage(
    triage: TriageConfig,
    *,
    pod_id: UUID | None,
    ctx: Context | None,
    session: object | None,
    decider_of: DeciderLookup = _get_decider,
) -> TriageConfig:
    """`triage`, once its decider backs every route; its question resolved.

    The pooled connection is handed back across the decider read, which opens
    its own, and across the digest's cron walk, which is CPU work. Asking the
    decider about events is the schedule owner's to do, so whoever saves the
    triage must be allowed to execute it.
    """
    if pod_id is None:
        raise ScheduleValidationError(
            "A triage asks one of the pod's deciders, so only a pod schedule can "
            "carry one."
        )
    async with connection_released(session):
        decider = await decider_of(pod_id=pod_id, name=triage.decider)
        if triage.digest is not None:
            await run_blocking(
                validate_cron_expression,
                triage.digest.cron,
                zone=triage.digest.timezone,
                limiter="cpu_bound",
            )
    if decider is None:
        raise ScheduleValidationError(
            f"No decider named {triage.decider!r} in this pod. Define it first, "
            "then point the triage at it."
        )
    if ctx is not None:
        await ctx.require(
            Permissions.DECIDER_EXECUTE,
            ResourceRef(
                resource_type=ResourceType.DECIDER,
                resource_id=decider.id,
                pod_id=pod_id,
            ),
        )
    key = routed_question(decider.definition, triage)
    check_routes(decider.definition.questions[key], triage.routes, key=key)
    return triage.model_copy(update={"question": key})


def routed_question(definition: DeciderDefinition, triage: TriageConfig) -> str:
    """The question a triage routes on: the one it names, or the decider's only one."""
    if triage.question is not None:
        if triage.question not in definition.questions:
            raise ScheduleValidationError(
                f"The decider {triage.decider!r} has no question {triage.question!r}."
            )
        return triage.question
    if len(definition.questions) != 1:
        raise ScheduleValidationError(
            f"The decider {triage.decider!r} asks {len(definition.questions)} "
            "questions; name the one to route on in `question`."
        )
    return next(iter(definition.questions))


def check_routes(
    question: object, routes: Mapping[str, TriageRoute], *, key: str
) -> None:
    """Every declared option routed, nothing else, and one way to settle an event.

    A person asked about an event is offered the options that settle it -- act,
    digest or ignore -- so a triage that routes every option to ask would put
    a question nobody could answer.
    """
    if not isinstance(question, ChoiceQuestion) or not question.options:
        raise ScheduleValidationError(
            f"Question {key!r} must be a choice with declared options for a "
            "triage to route on."
        )
    declared = set(question.options)
    missing = sorted(declared - set(routes))
    if missing:
        raise ScheduleValidationError(
            f"routes must cover every option of {key!r}; missing {missing}."
        )
    unknown = sorted(set(routes) - declared)
    if unknown:
        raise ScheduleValidationError(
            f"routes name options {key!r} does not declare: {unknown}."
        )
    if all(route is TriageRoute.ASK for route in routes.values()):
        raise ScheduleValidationError(
            "At least one option must route to act, digest or ignore: a person's "
            "answer has to be able to settle the event."
        )
