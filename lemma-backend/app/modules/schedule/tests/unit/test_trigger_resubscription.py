"""Editing a webhook schedule's config keeps its remote subscription in step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest

from app.modules.schedule.domain.errors import (
    ScheduleAccessDeniedError,
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import (
    ExternalScheduleWriter,
    ProvisionedTrigger,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.trigger_resubscription import (
    update_schedule_resubscribing,
)


class _Writer(ExternalScheduleWriter):
    def __init__(self, provisioned: ProvisionedTrigger) -> None:
        self.provisioned = provisioned
        self.created: list[ScheduleEntity] = []
        self.deleted: list[str | None] = []
        self.fail_delete = False

    async def create_provider_trigger(
        self, schedule: ScheduleEntity
    ) -> ProvisionedTrigger:
        self.created.append(schedule)
        return self.provisioned

    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        if self.fail_delete:
            raise ScheduleInfrastructureError("provider down")
        self.deleted.append(schedule.config.get("provider_trigger_id"))


class _Rows:
    def __init__(self, existing: ScheduleEntity, *, found: bool = True) -> None:
        self.existing = existing
        self.found = found
        self.written: dict[str, object] | None = None

    async def update(
        self, schedule_id: UUID, **kwargs: object
    ) -> ScheduleEntity | None:
        self.written = kwargs
        if not self.found:
            return None
        return self.existing.model_copy(update=kwargs)


class _Uow:
    session = None

    def __init__(self) -> None:
        self.callbacks: list[Callable[[], Awaitable[object]]] = []

    def after_commit(self, callback: Callable[[], Awaitable[object]]) -> None:
        self.callbacks.append(callback)

    async def commit(self) -> None:
        for callback in self.callbacks:
            await callback()


class _Service:
    def __init__(self, rows: _Rows, writer: _Writer, uow: _Uow) -> None:
        self.schedule_repository = rows
        self.external_schedule_writer = writer
        self.uow = uow


async def write_update_resubscribing(
    existing: ScheduleEntity,
    update_data: dict[str, object],
    *,
    repository: _Rows,
    writer: _Writer,
    uow: _Uow,
    caller_id: UUID | None = None,
) -> ScheduleEntity | None:
    return await update_schedule_resubscribing(
        existing, update_data, _Service(repository, writer, uow), caller_id=caller_id
    )


def _schedule(config: dict[str, object]) -> ScheduleEntity:
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id="github_issue_labeled",
        account_id=uuid4(),
        config=config,
    )


@pytest.mark.asyncio
async def test_a_new_filter_makes_a_new_subscription_and_drops_the_old_after_commit() -> (
    None
):
    existing = _schedule({"label": "bug", "provider_trigger_id": "old"})
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="new"))
    rows, uow = _Rows(existing), _Uow()
    update: dict[str, object] = {"config": {"label": "urgent"}}

    await write_update_resubscribing(
        existing, update, repository=rows, writer=writer, uow=uow
    )

    assert writer.created[0].config == {"label": "urgent"}
    assert rows.written == {"config": {"label": "urgent", "provider_trigger_id": "new"}}
    assert writer.deleted == [], "the old subscription outlives an uncommitted row"
    await uow.commit()
    assert writer.deleted == ["old"]


@pytest.mark.asyncio
async def test_an_unchanged_filter_keeps_the_provider_id_the_author_never_sends() -> (
    None
):
    existing = _schedule({"label": "bug", "provider_trigger_id": "keep"})
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="unused"))
    rows = _Rows(existing)
    update: dict[str, object] = {"config": {"label": "bug"}}

    await write_update_resubscribing(
        existing, update, repository=rows, writer=writer, uow=_Uow()
    )

    assert writer.created == []
    assert rows.written == {"config": {"label": "bug", "provider_trigger_id": "keep"}}


@pytest.mark.asyncio
async def test_a_local_routing_key_survives_an_edit_that_left_it_out() -> None:
    routing = {"source": "github", "installation_id": "42", "event": "issues"}
    existing = _schedule({"actions": ["opened"], **routing})
    writer = _Writer(ProvisionedTrigger(bound_config=routing))
    rows, uow = _Rows(existing), _Uow()
    update: dict[str, object] = {"config": {"actions": ["closed"]}}

    await write_update_resubscribing(
        existing, update, repository=rows, writer=writer, uow=uow
    )

    assert rows.written == {"config": {"actions": ["closed"], **routing}}
    assert uow.callbacks == [], "nothing remote to drop for a local binding"


@pytest.mark.asyncio
async def test_a_row_that_vanished_drops_the_subscription_just_made() -> None:
    existing = _schedule({"label": "bug", "provider_trigger_id": "old"})
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="new"))
    rows = _Rows(existing, found=False)

    result = await write_update_resubscribing(
        existing,
        {"config": {"label": "urgent"}},
        repository=rows,
        writer=writer,
        uow=_Uow(),
    )

    assert result is None
    assert writer.deleted == ["new"]


@pytest.mark.asyncio
async def test_a_failed_drop_of_the_old_subscription_does_not_fail_the_commit() -> None:
    existing = _schedule({"label": "bug", "provider_trigger_id": "old"})
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="new"))
    rows, uow = _Rows(existing), _Uow()

    await write_update_resubscribing(
        existing,
        {"config": {"label": "urgent"}},
        repository=rows,
        writer=writer,
        uow=uow,
    )
    writer.fail_delete = True

    await uow.commit()


@pytest.mark.asyncio
async def test_other_schedule_types_are_left_alone() -> None:
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "deals", "operations": ["INSERT"]},
    )
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="never"))
    update: dict[str, object] = {
        "config": {"table_name": "deals", "operations": ["UPDATE"]}
    }

    await write_update_resubscribing(
        existing, update, repository=_Rows(existing), writer=writer, uow=_Uow()
    )

    assert writer.created == []
    assert update["config"] == {"table_name": "deals", "operations": ["UPDATE"]}


@pytest.mark.asyncio
async def test_only_the_author_may_change_what_their_account_listens_for() -> None:
    existing = _schedule({"label": "bug", "provider_trigger_id": "old"})
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="new"))
    rows = _Rows(existing)

    with pytest.raises(ScheduleAccessDeniedError):
        await write_update_resubscribing(
            existing,
            {"config": {"label": "anything"}},
            repository=rows,
            writer=writer,
            uow=_Uow(),
            caller_id=uuid4(),
        )

    assert writer.created == [], "nothing is subscribed on the author's account"
    assert rows.written is None

    await write_update_resubscribing(
        existing,
        {"config": {"label": "urgent"}},
        repository=rows,
        writer=writer,
        uow=_Uow(),
        caller_id=existing.user_id,
    )
    assert writer.created[0].config == {"label": "urgent"}


@pytest.mark.asyncio
async def test_someone_else_may_edit_around_an_unchanged_filter() -> None:
    existing = _schedule({"label": "bug", "provider_trigger_id": "keep"})
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="unused"))
    rows = _Rows(existing)

    await write_update_resubscribing(
        existing,
        {"config": {"label": "bug"}, "name": "renamed"},
        repository=rows,
        writer=writer,
        uow=_Uow(),
        caller_id=uuid4(),
    )

    assert writer.created == []
    assert rows.written == {
        "config": {"label": "bug", "provider_trigger_id": "keep"},
        "name": "renamed",
    }


@pytest.mark.asyncio
async def test_an_mcp_schedule_cannot_stop_listening_through_its_account() -> None:
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        account_id=uuid4(),
        config={
            "source": "mcp",
            "event": "issue.created",
            "arguments": {},
            "provider_trigger_id": "sub",
        },
    )
    writer = _Writer(ProvisionedTrigger(provider_trigger_id="never"))
    rows = _Rows(existing)

    with pytest.raises(ScheduleValidationError):
        await write_update_resubscribing(
            existing,
            {"config": {"source": "github"}},
            repository=rows,
            writer=writer,
            uow=_Uow(),
        )

    assert writer.created == []
    assert rows.written is None, "the old subscription stays routed and deletable"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["provider_trigger_id", "installation_id"])
async def test_an_edit_cannot_type_in_a_routing_key_only_provisioning_writes(
    key: str,
) -> None:
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        config={"source": "github", "event": "push"},
    )
    rows = _Rows(existing)
    update: dict[str, object] = {
        "config": {"source": "github", "event": "push", key: "someone-elses"}
    }

    await write_update_resubscribing(
        existing,
        update,
        repository=rows,
        writer=_Writer(ProvisionedTrigger()),
        uow=_Uow(),
    )

    assert rows.written == {"config": {"source": "github", "event": "push"}}
