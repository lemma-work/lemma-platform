"""A trigger that sees only the rows its owner can read (``rows: OWNER_READABLE``).

Watching every change to a table takes ``datastore.table.update``, which a
member with the default role does not hold -- so they could not turn on work
that reacts to their own rows. A trigger that is only ever handed rows its
owner could read through the record API, and only ever runs as that owner,
can observe nothing they could not and act with no authority but theirs: it
takes ``datastore.record.read`` and nothing more.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.datastore.contracts.changes import Change, ChangeOp, ChangePosition
from app.modules.datastore.contracts.schedule_tables import ScheduleTableTarget
from app.modules.schedule.domain.schedule import (
    DatastoreScheduleConfig,
    ScheduleEntity,
    ScheduleType,
)
from app.modules.schedule.infrastructure.adapters import datastore_table_policy
from app.modules.schedule.infrastructure.adapters.datastore_table_policy import (
    DatastoreTableSchedulePolicy,
)
from app.modules.schedule.services.datastore_event_handler import trigger_change
from app.modules.schedule.services.schedule_ownership import (
    require_target_edit_for_row_owner_runs,
)
from app.modules.schedule.services.trigger_delivery import owner_can_see
from app.modules.schedule.services.trigger_fire import run_person

OWNER = UUID(int=1)
SOMEONE_ELSE = UUID(int=2)
POD = UUID(int=3)
TABLE = UUID(int=4)


def _schedule(rows: str | None = "OWNER_READABLE") -> ScheduleEntity:
    config: dict[str, object] = {"table_name": "tasks", "operations": ["INSERT"]}
    if rows is not None:
        config["rows"] = rows
    return ScheduleEntity(
        id=uuid4(),
        user_id=OWNER,
        pod_id=POD,
        agent_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config=config,
        instruction="Triage the task.",
        visibility="POD",
    )


def _change(*, owner: UUID | None, outside_actor: str | None = None) -> Change:
    return Change(
        position=ChangePosition(7, 1),
        table_id=TABLE,
        op=ChangeOp.INSERT,
        record_id="rec-1",
        intent="normal",
        owner_user_id=owner,
        outside_actor=outside_actor,
        new_row={"id": "rec-1"},
        old_values=None,
        at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def _sees(schedule: ScheduleEntity, change: Change) -> bool:
    config = schedule.datastore_config
    assert config is not None
    return owner_can_see(schedule, config, trigger_change(change, table_name="tasks"))


def test_another_persons_own_row_never_reaches_it():
    assert not _sees(_schedule(), _change(owner=SOMEONE_ELSE))


def test_the_owners_own_row_and_a_shared_row_reach_it():
    assert _sees(_schedule(), _change(owner=OWNER))
    assert _sees(_schedule(), _change(owner=None))


def test_an_outside_row_reaches_only_the_member_whose_row_it_is():
    """A visitor's row on a per-person table is stamped with the member who
    opened it to them: theirs to read, nobody else's."""
    assert _sees(_schedule(), _change(owner=OWNER, outside_actor="visitor:s1"))
    assert not _sees(
        _schedule(), _change(owner=SOMEONE_ELSE, outside_actor="contact:c1")
    )


def test_a_trigger_that_sees_every_change_is_not_narrowed():
    assert _sees(_schedule(rows=None), _change(owner=SOMEONE_ELSE))
    assert _sees(_schedule(rows="ALL"), _change(owner=SOMEONE_ELSE))


def test_every_change_it_is_handed_runs_as_its_owner():
    assert run_person(_schedule(), OWNER) == OWNER
    assert run_person(_schedule(), None) == OWNER
    # Unreachable through delivery, and still never another person's authority.
    assert run_person(_schedule(), SOMEONE_ELSE) == OWNER


class _Ctx:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def require(self, permission_id: str, resource: object = None) -> None:
        self.asked.append(permission_id)


@pytest.mark.asyncio
async def test_writing_one_takes_no_edit_access_to_the_target():
    """Its instruction never runs with anyone's access but its owner's, so the
    H16 rule for row-owner runs does not apply."""
    ctx = _Ctx()

    await require_target_edit_for_row_owner_runs(_schedule(), ctx)  # type: ignore[arg-type]

    assert ctx.asked == []


@pytest.fixture
def table_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _target(
        uow: object, *, pod_id: UUID, table_name: str, ctx: object
    ) -> ScheduleTableTarget:
        return ScheduleTableTarget(
            ref=ResourceRef.table(pod_id, TABLE), enable_rls=True
        )

    monkeypatch.setattr(datastore_table_policy, "schedule_table_target", _target)


@pytest.mark.asyncio
@pytest.mark.usefixtures("table_found")
@pytest.mark.parametrize(
    ("owner_readable_rows", "permission"),
    [
        (True, Permissions.DATASTORE_RECORD_READ),
        (False, Permissions.DATASTORE_TABLE_UPDATE),
    ],
)
async def test_watching_asks_reading_the_rows_or_changing_the_table(
    owner_readable_rows: bool, permission: str
):
    ctx = _Ctx()

    await DatastoreTableSchedulePolicy(uow=None).require_table_watch(  # type: ignore[arg-type]
        pod_id=POD,
        table_name="tasks",
        owner_readable_rows=owner_readable_rows,
        ctx=ctx,  # type: ignore[arg-type]
    )

    assert ctx.asked == [permission]


def test_an_unknown_rows_value_is_refused():
    config: Mapping[str, object] = {
        "table_name": "tasks",
        "operations": ["INSERT"],
        "rows": "MINE",
    }
    with pytest.raises(ValidationError):
        DatastoreScheduleConfig(**config)  # type: ignore[arg-type]
