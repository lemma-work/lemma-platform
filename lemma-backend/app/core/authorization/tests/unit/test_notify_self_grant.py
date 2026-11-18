"""``channel.notify_self``: the one channel action a workload can be granted.

A named function or agent tells the person it runs as something through
``POST /members/me/notices``, which asks for this permission on the pod. What
keeps it narrow is pinned here: on a pod only it and running the pod's assistant are
grantable, ``channel.write`` is not; people hold it through ``channel.write`` and never the
other way round; another pod over a link can never hold it; and the pod is
named ``/`` in a grant, by nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.assistant_grants import runs_the_assistant
from app.core.authorization.context import ResourceType
from app.core.authorization.grants import validate_pod_resource_grant_permissions
from app.core.authorization.permissions import (
    POD_USER_PERMISSIONS,
    POD_VIEWER_PERMISSIONS,
    Permissions,
    equivalent_permission_ids,
    pod_principal_may_hold,
)
from app.core.authorization.resource_actions import (
    RESOURCE_ACTIONS,
    owner_actions_for_resource,
)
from app.core.authorization.resource_names import (
    resolve_resource_id_by_name,
    resolve_resource_ids_by_names,
    resolve_resource_names_by_ids,
)
from app.core.domain.errors import BadRequestError
from app.core.registry.assembly import configure_resource_names
from app.core.registry.installed import OSS_MODULES

pytestmark = pytest.mark.unit

NOTIFY_SELF = Permissions.CHANNEL_NOTIFY_SELF


@dataclass(frozen=True)
class _Grant:
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str]


class _NoQueries:
    """A session that fails the test if anything is asked of the database:
    the pod resolves by its own id, with no table behind it."""

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("the pod's name resolved through a query")


def _no_queries() -> AsyncSession:
    return cast(AsyncSession, _NoQueries())


@pytest.fixture(autouse=True)
def _assembled() -> None:
    configure_resource_names(OSS_MODULES)


def test_a_pod_takes_only_it_and_running_its_assistant() -> None:
    assert RESOURCE_ACTIONS[ResourceType.POD] == (
        NOTIFY_SELF,
        Permissions.AGENT_EXECUTE,
    )
    validate_pod_resource_grant_permissions(
        [_Grant(ResourceType.POD, "/", [NOTIFY_SELF])]
    )
    for wide in (
        Permissions.CHANNEL_WRITE,
        Permissions.CHANNEL_MANAGE,
        Permissions.CONTACT_MESSAGE,
    ):
        with pytest.raises(BadRequestError) as refused:
            validate_pod_resource_grant_permissions(
                [_Grant(ResourceType.POD, "/", [wide])]
            )
        assert refused.value.code == "PERMISSION_RESOURCE_TYPE_MISMATCH"


def test_people_hold_it_through_channel_write_and_never_the_reverse() -> None:
    assert {Permissions.CHANNEL_WRITE, Permissions.CHANNEL_MANAGE} <= (
        equivalent_permission_ids(NOTIFY_SELF)
    )
    assert NOTIFY_SELF not in equivalent_permission_ids(Permissions.CHANNEL_WRITE)
    assert bool(equivalent_permission_ids(NOTIFY_SELF) & POD_USER_PERMISSIONS)
    # A viewer writes in no channel, so their software tells them nothing.
    assert not equivalent_permission_ids(NOTIFY_SELF) & POD_VIEWER_PERMISSIONS


def test_another_pod_never_holds_it_and_ownership_confers_nothing() -> None:
    assert not pod_principal_may_hold(NOTIFY_SELF)
    assert owner_actions_for_resource(ResourceType.POD) == ()


async def test_the_pod_is_named_slash_and_by_nothing_else() -> None:
    pod_id = uuid4()
    session = _no_queries()

    assert (
        await resolve_resource_id_by_name(
            session, pod_id=pod_id, resource_type=ResourceType.POD, resource_name="/"
        )
        == pod_id
    )
    assert (
        await resolve_resource_id_by_name(
            session,
            pod_id=pod_id,
            resource_type=ResourceType.POD,
            resource_name=str(pod_id),
        )
        is None
    )
    assert await resolve_resource_ids_by_names(
        session, pod_id=pod_id, refs=[(ResourceType.POD, "/")]
    ) == {(ResourceType.POD, "/"): pod_id}
    with pytest.raises(BadRequestError) as unknown:
        await resolve_resource_ids_by_names(
            session, pod_id=pod_id, refs=[(ResourceType.POD, "another-pod")]
        )
    assert unknown.value.code == "UNKNOWN_RESOURCE_NAME"


async def test_an_export_names_the_pod_slash_and_drops_another_pods_id() -> None:
    pod_id = uuid4()
    names = await resolve_resource_names_by_ids(
        _no_queries(),
        pod_id=pod_id,
        refs=[(ResourceType.POD, pod_id), (ResourceType.POD, uuid4())],
    )
    assert names == {(ResourceType.POD, pod_id): "/"}


def test_a_grant_to_run_the_assistant_by_name_is_the_pods_own() -> None:
    """`agent:pod_default:execute` names the assistant's row, whose id is the
    pod's; it is stored where starting the assistant is checked."""
    pod_id = uuid4()
    run = [Permissions.AGENT_EXECUTE]
    assert runs_the_assistant(ResourceType.AGENT, pod_id, run, pod_id=pod_id)
    assert runs_the_assistant(
        ResourceType.AGENT,
        pod_id,
        [Permissions.AGENT_EXECUTE, Permissions.AGENT_READ],
        pod_id=pod_id,
    )
    # A named agent's row, or anything beyond running it, is left alone.
    assert not runs_the_assistant(ResourceType.AGENT, uuid4(), run, pod_id=pod_id)
    assert not runs_the_assistant(
        ResourceType.AGENT,
        pod_id,
        [Permissions.AGENT_EXECUTE, Permissions.AGENT_UPDATE],
        pod_id=pod_id,
    )
    assert not runs_the_assistant(
        ResourceType.AGENT, pod_id, [Permissions.AGENT_READ], pod_id=pod_id
    )
