"""A schedule's triage is checked against the decider it names, when it is saved.

The shape is the domain's to check; whether the routes cover the options a
person will be asked about needs the decider, which the policy reads through a
seam. The decider here is built in place, as the decisions contract returns
one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.decisions.contracts.deciders import DeciderEntity
from app.modules.decisions.contracts.shapes import DeciderDefinition
from app.modules.schedule.api.schemas.schedule_schemas import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from app.modules.schedule.domain.errors import (
    ScheduleTooFrequentError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
)
from app.modules.schedule.domain.triage import (
    TriageConfig,
    TriageRoute,
    next_digest_at,
    rearmed_digest_at,
)
from app.modules.schedule.services.triage_policy import (
    apply_triage_update,
    validate_create_policies,
    validated_triage,
)

pytestmark = pytest.mark.unit

POD = uuid4()
ROUTES = {"act": "act", "digest": "digest", "ask": "ask", "ignore": "ignore"}
DIGEST = {"cron": "0 9 * * *", "timezone": "Europe/Berlin"}
EMAIL_TRIAGE = {
    "description": "What happens to each new email.",
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should happen with this email?",
            "options": {
                "act": "A customer is waiting.",
                "digest": "Worth knowing, not urgent.",
                "ask": "Needs a person.",
                "ignore": "Newsletters.",
            },
            "fallback": "ask",
        }
    },
}


def _decider(definition: dict[str, object] = EMAIL_TRIAGE) -> DeciderEntity:
    return DeciderEntity(
        pod_id=POD,
        name="email-triage",
        definition=DeciderDefinition.model_validate(definition),
    )


class _Deciders:
    """The decisions contract's `get_decider`, over one pod's deciders."""

    def __init__(self, *deciders: DeciderEntity) -> None:
        self.by_name = {decider.name: decider for decider in deciders}
        self.asked: list[tuple[UUID, str]] = []

    async def __call__(self, *, pod_id: UUID, name: str) -> DeciderEntity | None:
        self.asked.append((pod_id, name))
        return self.by_name.get(name) if pod_id == POD else None


def _triage(**updates: object) -> TriageConfig:
    values: dict[str, object] = {
        "decider": "email-triage",
        "routes": ROUTES,
        "digest": DIGEST,
    }
    values.update(updates)
    return TriageConfig.model_validate(values)


async def _validated(triage: TriageConfig, deciders: _Deciders | None = None):
    return await validated_triage(
        triage,
        pod_id=POD,
        ctx=None,
        session=None,
        decider_of=deciders or _Deciders(_decider()),
    )


def test_a_digest_route_needs_a_digest_cadence():
    with pytest.raises(ValidationError, match="needs a `digest`"):
        _triage(digest=None)


def test_routes_are_one_of_the_four_outcomes_and_nothing_else_is_accepted():
    with pytest.raises(ValidationError):
        _triage(routes={**ROUTES, "act": "fire"})
    with pytest.raises(ValidationError):
        _triage(act_per_hour=0)
    with pytest.raises(ValidationError):
        _triage(notify="everyone")


def test_the_ceiling_sends_act_to_the_digest_or_else_to_a_person():
    assert _triage().over_ceiling is TriageRoute.DIGEST
    assert (
        _triage(routes={**ROUTES, "digest": "ignore"}, digest=None).over_ceiling
        is TriageRoute.ASK
    )


async def test_the_question_is_resolved_and_saved_as_the_one_it_routes_on():
    validated = await _validated(_triage())

    assert validated.question == "action"
    assert validated.routes["digest"] is TriageRoute.DIGEST


async def test_a_decider_that_does_not_exist_is_refused():
    with pytest.raises(ScheduleValidationError, match="No decider named 'nope'"):
        await _validated(_triage(decider="nope"))


async def test_every_declared_option_must_be_routed():
    with pytest.raises(ScheduleValidationError, match=r"missing \['ignore'\]"):
        await _validated(
            _triage(routes={"act": "act", "digest": "digest", "ask": "ask"})
        )


async def test_a_route_for_an_option_the_question_lacks_is_refused():
    with pytest.raises(ScheduleValidationError, match=r"does not declare: \['spam'\]"):
        await _validated(_triage(routes={**ROUTES, "spam": "ignore"}))


async def test_only_a_choice_question_can_be_routed():
    yes_no = {
        "description": "Is it urgent?",
        "questions": {"urgent": {"type": "yes_no", "prompt": "Urgent?"}},
    }
    with pytest.raises(ScheduleValidationError, match="must be a choice"):
        await _validated(
            _triage(routes={"yes": "act"}, digest=None),
            _Deciders(_decider(yes_no)),
        )


async def test_a_decider_with_several_questions_needs_the_one_named():
    two_questions = {
        **EMAIL_TRIAGE,
        "questions": {
            **EMAIL_TRIAGE["questions"],
            "tone": {"type": "yes_no", "prompt": "Is it angry?"},
        },
    }
    deciders = _Deciders(_decider(two_questions))
    with pytest.raises(ScheduleValidationError, match="name the one to route on"):
        await _validated(_triage(), deciders)
    with pytest.raises(ScheduleValidationError, match="has no question 'topic'"):
        await _validated(_triage(question="topic"), deciders)

    assert (await _validated(_triage(question="action"), deciders)).question == (
        "action"
    )


async def test_an_answer_must_be_able_to_settle_the_event():
    every_option_asks = dict.fromkeys(ROUTES, "ask")
    with pytest.raises(ScheduleValidationError, match="act, digest or ignore"):
        await _validated(_triage(routes=every_option_asks, digest=None))


async def test_the_digest_keeps_the_time_schedules_frequency_floor():
    with pytest.raises(ScheduleTooFrequentError):
        await _validated(_triage(digest={"cron": "*/5 * * * *"}))
    with pytest.raises(ScheduleValidationError):
        await _validated(_triage(digest={"cron": "0 9 * * *", "timezone": "Mars/Base"}))


def _create(**updates: object) -> ScheduleCreateEntity:
    values: dict[str, object] = {
        "user_id": uuid4(),
        "pod_id": POD,
        "schedule_type": ScheduleType.WEBHOOK,
        "config": {"source": "custom"},
        "triage": _triage(),
    }
    values.update(updates)
    return ScheduleCreateEntity.model_validate(values)


async def test_create_returns_the_schedule_with_its_question_resolved():
    created = await validate_create_policies(
        _create(),
        ctx=None,
        session=None,
        webhook_sources=None,
        decider_of=_Deciders(_decider()),
    )

    assert created.triage is not None
    assert created.triage.question == "action"


async def test_create_refuses_triage_on_a_time_schedule_and_beside_a_filter():
    """The service says it too: a bundle import never sees the request schema."""
    deciders = _Deciders(_decider())
    with pytest.raises(ScheduleValidationError, match="no event to sort"):
        await validate_create_policies(
            _create(schedule_type=ScheduleType.TIME, config={"cron": "0 9 * * *"}),
            ctx=None,
            session=None,
            webhook_sources=None,
            decider_of=deciders,
        )
    with pytest.raises(
        ScheduleValidationError, match="instead of `filter_instruction`"
    ):
        await validate_create_policies(
            _create(
                schedule_type=ScheduleType.DATASTORE,
                config={"table_name": "tickets", "operations": ["INSERT"]},
                filter_instruction="Only urgent ones.",
            ),
            ctx=None,
            session=None,
            webhook_sources=None,
            decider_of=deciders,
        )
    assert deciders.asked == []


def test_the_request_schema_refuses_triage_where_it_cannot_apply():
    base = {"workflow_name": "triage-flow", "triage": _triage().to_json()}
    with pytest.raises(ValidationError, match="no event to sort"):
        CreateScheduleRequest.model_validate(
            {**base, "schedule_type": "TIME", "config": {"cron": "0 9 * * *"}}
        )
    with pytest.raises(ValidationError, match="instead of `filter_instruction`"):
        CreateScheduleRequest.model_validate(
            {
                **base,
                "schedule_type": "WEBHOOK",
                "config": {"source": "custom"},
                "filter_instruction": "Only urgent ones.",
            }
        )


def test_an_explicit_null_clears_the_triage_and_leaving_it_out_does_not():
    assert UpdateScheduleRequest.model_validate({"triage": None}).clears_triage
    assert not UpdateScheduleRequest.model_validate({"name": "renamed"}).clears_triage


def _existing(**updates: object) -> ScheduleEntity:
    values: dict[str, object] = {
        "user_id": uuid4(),
        "pod_id": POD,
        "schedule_type": ScheduleType.WEBHOOK,
        "config": {"source": "custom"},
    }
    values.update(updates)
    return ScheduleEntity.model_validate(values)


async def test_an_update_stores_the_triage_and_arms_its_digest():
    update: dict[str, object] = {
        "triage": _triage().model_dump(),
        "clear_triage": False,
    }

    await apply_triage_update(
        _existing(),
        update,
        ctx=None,
        session=None,
        decider_of=_Deciders(_decider()),
    )

    assert "clear_triage" not in update
    assert update["triage"] == _triage(question="action").to_json()
    digest_at = update["next_digest_at"]
    assert isinstance(digest_at, datetime) and digest_at > datetime.now(timezone.utc)


async def test_removing_a_digest_sends_what_it_held_once_more():
    update: dict[str, object] = {"clear_triage": True}

    await apply_triage_update(
        _existing(triage=_triage()),
        update,
        ctx=None,
        session=None,
        decider_of=_Deciders(),
    )

    assert update["triage"] is None
    digest_at = update["next_digest_at"]
    assert isinstance(digest_at, datetime)
    assert digest_at <= datetime.now(timezone.utc)


async def test_a_filter_cannot_be_added_beside_a_kept_triage():
    with pytest.raises(
        ScheduleValidationError, match="instead of `filter_instruction`"
    ):
        await apply_triage_update(
            _existing(triage=_triage()),
            {"filter_instruction": "Only urgent ones.", "clear_triage": False},
            ctx=None,
            session=None,
            decider_of=_Deciders(),
        )


async def test_an_update_that_leaves_triage_alone_changes_nothing_about_it():
    update: dict[str, object] = {"name": "renamed", "clear_triage": False}

    await apply_triage_update(
        _existing(triage=_triage()),
        update,
        ctx=None,
        session=None,
        decider_of=_Deciders(),
    )

    assert update == {"name": "renamed"}


async def test_a_time_schedule_refuses_triage_on_update():
    with pytest.raises(ScheduleValidationError, match="no event to sort"):
        await apply_triage_update(
            _existing(schedule_type=ScheduleType.TIME, config={"cron": "0 9 * * *"}),
            {"triage": _triage().model_dump()},
            ctx=None,
            session=None,
            decider_of=_Deciders(_decider()),
        )


def test_the_digest_cursor_follows_the_cron_in_its_zone():
    after = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    # 09:00 in Berlin is 07:00Z in summer: the next one is tomorrow's.
    assert next_digest_at(_triage(), after=after) == datetime(
        2026, 7, 2, 7, 0, tzinfo=timezone.utc
    )
    assert next_digest_at(None, after=after) is None
    assert rearmed_digest_at(None, None, now=after) is None
