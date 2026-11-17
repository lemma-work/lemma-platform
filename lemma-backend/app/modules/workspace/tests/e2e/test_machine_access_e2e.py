"""Who may use which machine, decided against real roles, grants and rows.

A pod machine is a pod resource, so the core authorizer decides it: members by
their pod role, agents and functions by a ``machine.use`` grant intersected
with the person they act for, the pod's default agent as its person, an app's
page as its viewer in its own pod. A person's own machine is theirs, and every
run of theirs that answers a member -- a schedule's or a channel's as much as
a chat's; a run answering a contact never reaches it.

Nothing here starts a machine: the resolver decides from rows.
"""

from __future__ import annotations

from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import insert

from app.core.api.dependencies import get_uow_factory
from app.core.authorization.context import ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.workspace.domain.machine import Machine
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.domain.machine_errors import MachineNotFound
from app.modules.workspace.infrastructure.machine_repository import MachineRepository
from app.modules.workspace.services.machine_resolver import MachineResolver

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

NONE, STATUS, USE, MANAGE = (
    MachineAccess.NONE,
    MachineAccess.STATUS,
    MachineAccess.USE,
    MachineAccess.MANAGE,
)


@pytest.fixture
async def pod(scenario):
    await scenario.create_org_with_pod(name_prefix="Machine access")
    pod_id = scenario.pod_id
    people = {"owner": scenario.owner_user}
    for role in ("POD_EDITOR", "POD_USER", "POD_VIEWER"):
        user = await scenario.create_user(role.lower().replace("_", "-"))
        await scenario.add_user_to_pod(user=user, role=role)
        people[role] = user
    people["outsider"] = await scenario.create_user("outsider")
    created = await scenario.owner_client.post(
        f"/pods/{pod_id}/machines", json={"name": "qa"}
    )
    assert created.status_code == 201, created.text
    # Naming the machine on an agent grants it the machine.
    granted = await scenario.owner_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "qa-agent", "instruction": "Test.", "machine": "pod:qa"},
    )
    assert granted.status_code == 201, granted.text
    other = await scenario.create_agent(name="bystander")
    return {
        "pod_id": UUID(pod_id),
        "machine_id": UUID(created.json()["id"]),
        "people": {name: UUID(user["id"]) for name, user in people.items()},
        "granted": UUID(granted.json()["id"]),
        "ungranted": UUID(other["id"]),
    }


async def _decide(caller: MachineCaller, machine_id: UUID) -> MachineAccess:
    async with get_uow_factory()() as uow:
        machine = await MachineRepository(uow).get(machine_id)
        assert machine is not None
        return await MachineResolver(uow).decide(caller, machine)


def _agent(pod, agent_id: UUID, person: str, conversation_id: UUID | None = None):
    return MachineCaller(
        CallerKind.AGENT,
        user_id=pod["people"][person],
        pod_id=pod["pod_id"],
        workload_id=agent_id,
        conversation_id=conversation_id,
    )


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        ("owner", MANAGE),
        ("POD_EDITOR", MANAGE),
        ("POD_USER", STATUS),
        ("POD_VIEWER", STATUS),
        ("outsider", NONE),
    ],
)
async def test_members_by_their_pod_role(pod, person, expected):
    caller = MachineCaller(CallerKind.USER, user_id=pod["people"][person])

    assert await _decide(caller, pod["machine_id"]) is expected


async def test_an_agent_by_its_grant_and_no_further_than_its_person(pod):
    granted, ungranted = pod["granted"], pod["ungranted"]

    assert await _decide(_agent(pod, granted, "owner"), pod["machine_id"]) is USE
    assert await _decide(_agent(pod, ungranted, "owner"), pod["machine_id"]) is NONE
    # The grant is a ceiling on the agent, never a promotion for its person.
    assert await _decide(_agent(pod, granted, "POD_VIEWER"), pod["machine_id"]) is NONE
    # An agent of another pod, whatever it holds here.
    elsewhere = MachineCaller(
        CallerKind.AGENT,
        user_id=pod["people"]["owner"],
        pod_id=uuid4(),
        workload_id=granted,
    )
    assert await _decide(elsewhere, pod["machine_id"]) is NONE


async def test_the_pods_default_agent_is_its_person(pod):
    assistant = pod["pod_id"]  # the assistant's row id is its pod's

    by_editor = await _decide(_agent(pod, assistant, "POD_EDITOR"), pod["machine_id"])
    by_viewer = await _decide(_agent(pod, assistant, "POD_VIEWER"), pod["machine_id"])

    assert by_editor >= USE
    assert by_viewer is NONE


async def test_a_function_by_its_grant(pod):
    function_id = uuid4()
    caller = MachineCaller(
        CallerKind.FUNCTION, user_id=None, pod_id=pod["pod_id"], workload_id=function_id
    )
    assert await _decide(caller, pod["machine_id"]) is NONE

    async with get_uow_factory()() as uow:
        await uow.session.execute(
            insert(ResourcePermissionGrantModel).values(
                pod_id=pod["pod_id"],
                resource_type=ResourceType.MACHINE.value,
                resource_id=pod["machine_id"],
                grantee_type="FUNCTION",
                grantee_id=function_id,
                permission_id=Permissions.MACHINE_USE,
                created_by_user_id=pod["people"]["owner"],
            )
        )
        await uow.commit()

    assert await _decide(caller, pod["machine_id"]) is USE


async def test_an_app_uses_its_own_pods_machine_as_its_viewer(pod):
    def app(person: str, pod_id: UUID) -> MachineCaller:
        return MachineCaller(
            CallerKind.APP, user_id=pod["people"][person], pod_id=pod_id, app_id=uuid4()
        )

    assert await _decide(app("owner", pod["pod_id"]), pod["machine_id"]) is USE
    assert await _decide(app("POD_VIEWER", pod["pod_id"]), pod["machine_id"]) is STATUS
    assert await _decide(app("owner", uuid4()), pod["machine_id"]) is NONE


# --- the person's own machine ------------------------------------------------


async def _own_machine(owner: UUID) -> Machine:
    async with get_uow_factory()() as uow:
        resolved = await MachineResolver(uow).resolve(
            "me", MachineCaller(CallerKind.USER, user_id=owner)
        )
        await uow.commit()
    return resolved.machine


async def _conversation(pod, *, contact: bool = False, shape: str = "chat"):
    owner = pod["people"]["owner"]
    async with get_uow_factory()() as uow:
        contact_id = None
        if contact:
            customer = await open_contact(
                uow,
                pod_id=pod["pod_id"],
                kind=IdentityKind.EMAIL,
                value=f"customer-{uuid4().hex[:6]}@client.example",
                strength=IdentityStrength.CHANNEL,
                display_name="Dana",
            )
            contact_id = customer.id
        channel_id = None
        if shape == "channel":
            channel = ChannelModel(
                id=uuid7(), pod_id=pod["pod_id"], kind="GROUP", visibility="PRIVATE"
            )
            uow.session.add(channel)
            await uow.session.flush()
            channel_id = channel.id
        conversation = ConversationModel(
            user_id=owner,
            pod_id=pod["pod_id"],
            contact_id=contact_id,
            # A group on Slack or WhatsApp the person's run answers in.
            channel_id=channel_id,
            # A sub-agent's: spawned by the person's run, not typed into.
            parent_run_id=uuid4() if shape == "subagent" else None,
        )
        uow.session.add(conversation)
        await uow.commit()
    return conversation.id


@pytest.mark.parametrize("shape", ["chat", "channel", "subagent"])
async def test_every_run_a_member_owns_uses_their_own_machine(pod, shape):
    owner = pod["people"]["owner"]
    machine = await _own_machine(owner)
    conversation = await _conversation(pod, shape=shape)

    async with get_uow_factory()() as uow:
        access = await MachineResolver(uow).decide(
            _agent(pod, pod["granted"], "owner", conversation), machine
        )

    assert access is USE


async def test_a_run_answering_a_contact_gets_no_machine(pod):
    owner = pod["people"]["owner"]
    machine = await _own_machine(owner)
    conversation = await _conversation(pod, contact=True)

    async with get_uow_factory()() as uow:
        access = await MachineResolver(uow).decide(
            _agent(pod, pod["granted"], "owner", conversation), machine
        )
        with pytest.raises(MachineNotFound):
            await MachineResolver(uow).resolve(
                "me", _agent(pod, pod["granted"], "owner", conversation)
            )

    assert access is NONE


async def test_an_app_never_reaches_the_persons_own_machine(pod):
    owner = pod["people"]["owner"]
    machine = await _own_machine(owner)
    app = MachineCaller(
        CallerKind.APP, user_id=owner, pod_id=pod["pod_id"], app_id=uuid4()
    )

    async with get_uow_factory()() as uow:
        assert await MachineResolver(uow).decide(app, machine) is NONE
