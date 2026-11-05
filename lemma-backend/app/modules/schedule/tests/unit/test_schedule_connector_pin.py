"""A schedule bound to a pod connector pins the account it resolved to.

Review of #1039: a schedule on its owner's second account (Telegram bot B) was
migrated to the connector's slug, and every later resolution meant the owner's
member default (bot A) -- B's schedule went quiet and A's events fired twice.
Binding now keeps what binding resolved: on a PER_MEMBER connector the pod
account, so the owner changing their default does not move it; on a SHARED one
nothing, since it follows its connector.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.connectors.contracts.pod_connectors import (
    PodConnectorAccount,
    PodConnectorMode,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.schedule.domain.schedule import ScheduleCreateEntity, ScheduleType
from app.modules.schedule.services.schedule_connector_policy import (
    require_connector_use,
)


def _schedule(**extra) -> ScheduleCreateEntity:
    return ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        pod_connector_slug="telegram",
        **extra,
    )


def _resolved(mode: PodConnectorMode) -> PodConnectorAccount:
    owner = uuid4()
    return PodConnectorAccount(
        pod_id=uuid4(),
        slug="telegram",
        mode=mode,
        pod_account_id=uuid4(),
        owner_user_id=owner,
        auth_config_id=uuid4(),
        connector_id="telegram",
        account=AccountEntity(
            user_id=owner,
            organization_id=uuid4(),
            auth_config_id=uuid4(),
            connector_id="telegram",
        ),
    )


class _Resolve:
    def __init__(self, answer: PodConnectorAccount) -> None:
        self.answer = answer
        self.pins: list[object] = []

    async def __call__(self, _uow, _ctx, *, pod_id, slug, pod_account_id):
        del pod_id, slug
        self.pins.append(pod_account_id)
        return self.answer


async def test_a_per_member_binding_pins_the_account_it_resolved_to():
    resolve = _Resolve(_resolved(PodConnectorMode.PER_MEMBER))

    bound = await require_connector_use(_schedule(), object(), None, resolve=resolve)

    assert bound.pod_account_id == resolve.answer.pod_account_id
    assert resolve.pins == [None]


async def test_an_account_it_already_named_is_the_one_asked_for():
    """A deprecated ``account_id`` translated to bot B is resolved as bot B,
    not re-chosen as the owner's default."""
    named = uuid4()
    resolve = _Resolve(_resolved(PodConnectorMode.PER_MEMBER))

    await require_connector_use(
        _schedule(pod_account_id=named), object(), None, resolve=resolve
    )

    assert resolve.pins == [named]


async def test_a_shared_binding_pins_nothing():
    resolve = _Resolve(_resolved(PodConnectorMode.SHARED))

    bound = await require_connector_use(
        _schedule(pod_account_id=uuid4()), object(), None, resolve=resolve
    )

    assert bound.pod_account_id is None
