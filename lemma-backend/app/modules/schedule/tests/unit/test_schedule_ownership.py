"""Whoever changes what a schedule does becomes the person it runs as (H15).

The edit is held to what creating that schedule would ask of the editor; a
rename or a pause changes nothing about who it runs as. Driven through
`ScheduleService.update_schedule`, with the authorizer and the target check
recorded, so what is asked and of whom is visible.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.modules.schedule.domain.errors import ScheduleAccessDeniedError
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleType,
    ScheduleUpdateEntity,
)
from app.modules.schedule.services.schedule_ownership import changes_behaviour
from app.modules.schedule.services.schedule_service import ScheduleService

pytestmark = pytest.mark.asyncio

AUTHOR = UUID(int=1)
EDITOR = UUID(int=2)


class _Ctx:
    """A person's context that records what it is asked, refusing ``refuse``."""

    def __init__(self, user_id: UUID, refuse: frozenset[str] = frozenset()) -> None:
        self.user_id = user_id
        self.refuse = refuse
        self.asked: list[str] = []

    async def require(self, permission_id: str, resource=None) -> None:
        self.asked.append(permission_id)
        if permission_id in self.refuse:
            raise DomainError("denied", code="PERMISSION_DENIED", status_code=403)


def _schedule(**overrides: object) -> ScheduleEntity:
    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": AUTHOR,
        "pod_id": uuid4(),
        "agent_id": uuid4(),
        "schedule_type": ScheduleType.TIME,
        "config": {"cron": "0 9 * * *"},
        "instruction": "Summarise the week.",
        "visibility": "POD",
    }
    values.update(overrides)
    return ScheduleEntity(**values)


class _Connectors:
    """The connector check: as whom, on what, refusing when told to."""

    def __init__(self, refuse: Exception | None = None) -> None:
        self.asked: list[tuple[UUID, str]] = []
        self.refuse = refuse

    async def __call__(self, _uow, ctx, *, pod_id, slug, pod_account_id=None):
        self.asked.append((ctx.user_id, slug))
        if self.refuse is not None:
            raise self.refuse


def _service(
    existing: ScheduleEntity, connectors: _Connectors | None = None
) -> tuple[ScheduleService, AsyncMock]:
    repository = AsyncMock()
    repository.get.return_value = existing
    repository.get_by_name.return_value = None
    repository.update.side_effect = lambda _id, **data: existing.model_copy(update=data)
    service = ScheduleService(
        uow=AsyncMock(),
        schedule_repository=repository,
        external_schedule_writer=AsyncMock(),
        target_resolver=AsyncMock(),
        connector_access=connectors or _Connectors(),
    )

    async def _no_resolution(existing, update):
        return update.model_dump(exclude_none=True)

    service._resolve_update_target = _no_resolution  # type: ignore[method-assign]
    return service, repository


def _written(repository: AsyncMock) -> dict[str, object]:
    (call,) = repository.update.await_args_list
    return call.kwargs


async def test_editing_the_instruction_makes_the_editor_the_owner():
    existing = _schedule()
    service, repository = _service(existing)
    ctx = _Ctx(EDITOR)

    await service.update_schedule(
        existing.id, ScheduleUpdateEntity(instruction="Delete the archive."), ctx=ctx
    )

    assert _written(repository)["user_id"] == EDITOR
    # Asked of the editor exactly what creating it would have asked.
    assert ctx.asked == [Permissions.SCHEDULE_CREATE, Permissions.AGENT_EXECUTE]


async def test_an_editor_who_may_not_run_the_target_cannot_edit_what_it_does():
    existing = _schedule()
    service, repository = _service(existing)

    with pytest.raises(DomainError):
        await service.update_schedule(
            existing.id,
            ScheduleUpdateEntity(instruction="Delete the archive."),
            ctx=_Ctx(EDITOR, refuse=frozenset({Permissions.AGENT_EXECUTE})),
        )

    repository.update.assert_not_called()


async def test_an_editor_who_may_not_create_schedules_cannot_edit_what_one_does():
    existing = _schedule()
    service, repository = _service(existing)

    with pytest.raises(DomainError):
        await service.update_schedule(
            existing.id,
            ScheduleUpdateEntity(visibility="PERSONAL"),
            ctx=_Ctx(EDITOR, refuse=frozenset({Permissions.SCHEDULE_CREATE})),
        )

    repository.update.assert_not_called()


@pytest.mark.parametrize(
    "update",
    [
        ScheduleUpdateEntity(name="weekly-summary"),
        ScheduleUpdateEntity(is_active=False),
        # The whole form sent back unchanged is not a change.
        ScheduleUpdateEntity(instruction="Summarise the week.", visibility="POD"),
    ],
    ids=["rename", "pause", "unchanged-resend"],
)
async def test_a_change_that_leaves_the_behaviour_alone_leaves_the_owner_alone(
    update: ScheduleUpdateEntity,
):
    existing = _schedule()
    service, repository = _service(existing)
    ctx = _Ctx(EDITOR, refuse=frozenset({Permissions.SCHEDULE_CREATE}))

    await service.update_schedule(existing.id, update, ctx=ctx)

    assert "user_id" not in _written(repository)
    assert ctx.asked == []


def _listening(**overrides: object) -> ScheduleEntity:
    return _schedule(
        schedule_type=ScheduleType.WEBHOOK,
        pod_connector_slug="gmail",
        connector_trigger_id="GMAIL_NEW_MESSAGE",
        config={"source": "composio", "provider_trigger_id": "ti_1"},
        **overrides,
    )


async def test_an_editor_takes_a_connector_schedule_on_their_own_use_of_it():
    """A per-member connector then means the editor's own account, a shared
    one the account shared with them: never the author's, on their say-so."""
    existing = _listening()
    connectors = _Connectors()
    service, repository = _service(existing, connectors)

    await service.update_schedule(
        existing.id,
        ScheduleUpdateEntity(instruction="Forward everything to me."),
        ctx=_Ctx(EDITOR),
    )

    assert connectors.asked == [(EDITOR, "gmail")]
    assert _written(repository)["user_id"] == EDITOR


async def test_an_editor_who_may_not_use_the_connector_cannot_take_the_schedule():
    existing = _listening()
    service, repository = _service(
        existing,
        _Connectors(refuse=ScheduleAccessDeniedError("no connector.use")),
    )

    with pytest.raises(ScheduleAccessDeniedError):
        await service.update_schedule(
            existing.id,
            ScheduleUpdateEntity(instruction="Forward everything to me."),
            ctx=_Ctx(EDITOR),
        )

    repository.update.assert_not_called()


def test_a_config_is_compared_as_authored_not_with_its_routing_keys():
    existing = _schedule(
        schedule_type=ScheduleType.WEBHOOK,
        config={"source": "composio", "provider_trigger_id": "ti_1"},
    )

    assert not changes_behaviour(existing, {"config": {"source": "composio"}})
    assert changes_behaviour(existing, {"config": {"source": "slack"}})
