"""Every cell of the machine access rules that are not the authorizer's.

A person's default machine is theirs and every run they own that answers a
member; a connected machine is its owner's, and a pod's resource -- never
past use -- where shared. A pod machine is the core authorizer's to decide
(``test_pod_machine_authorization_e2e`` holds that against real roles and
grants); what is pure about it is the two limits on an app's page, and how
the resolver maps the authorizer's three answers onto a level.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.workspace.domain.machine import (
    Capabilities,
    DEFAULT_MACHINE_SIZE,
    Machine,
    MachineBillingScope,
    MachineFabric,
    MachineKind,
    MachineTemplate,
    MachineVisibility,
)
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
    PodAccess,
    connected_machine_access,
    default_machine_access,
    pod_machine_access,
)
from app.modules.workspace.domain.machine_errors import MachineNotFound
from app.modules.workspace.services.machine_resolver import MachineResolver

pytestmark = pytest.mark.unit

OWNER = uuid4()
OTHER = uuid4()
POD = uuid4()
OTHER_POD = uuid4()
AGENT = uuid4()
FUNCTION = uuid4()
APP = uuid4()

NONE, STATUS, USE, MANAGE = (
    MachineAccess.NONE,
    MachineAccess.STATUS,
    MachineAccess.USE,
    MachineAccess.MANAGE,
)


def _machine(kind: MachineKind, visibility: MachineVisibility) -> Machine:
    pod_machine = kind is MachineKind.POD
    return Machine(
        id=uuid4(),
        kind=kind,
        name="me" if kind is MachineKind.DEFAULT else "qa",
        display_name="machine",
        template=MachineTemplate.WORKSPACE,
        size=DEFAULT_MACHINE_SIZE,
        fabric=MachineFabric.CONNECTED
        if kind is MachineKind.CONNECTED
        else MachineFabric.E2B,
        capabilities=Capabilities(),
        visibility=visibility,
        billing_scope=MachineBillingScope.ORG
        if pod_machine
        else MachineBillingScope.USER,
        owner_id=POD if pod_machine else OWNER,
        pod_id=POD if pod_machine else None,
    )


DEFAULT = _machine(MachineKind.DEFAULT, MachineVisibility.PRIVATE)
POD_MACHINE = _machine(MachineKind.POD, MachineVisibility.POD)
CONNECTED = _machine(MachineKind.CONNECTED, MachineVisibility.PRIVATE)
SHARED_CONNECTED = replace(
    _machine(MachineKind.CONNECTED, MachineVisibility.POD), pod_id=POD
)


def user(who=OWNER, pod=None):
    return MachineCaller(CallerKind.USER, user_id=who, pod_id=pod)


def app(who=OWNER, pod=POD):
    return MachineCaller(CallerKind.APP, user_id=who, pod_id=pod, app_id=APP)


def agent(who=OWNER, pod=POD):
    return MachineCaller(
        CallerKind.AGENT,
        user_id=who,
        pod_id=pod,
        workload_id=AGENT,
        conversation_id=uuid4(),
    )


def function(pod=POD, who=None):
    return MachineCaller(
        CallerKind.FUNCTION, user_id=who, pod_id=pod, workload_id=FUNCTION
    )


# --- the default machine (`me`) ------------------------------------------------

DEFAULT_MATRIX = [
    ("the person on their own machine", user(), True, MANAGE),
    ("someone else", user(OTHER), True, NONE),
    ("a run of theirs answering a member", agent(), True, USE),
    ("a run of theirs answering a contact or a pod", agent(), False, NONE),
    ("somebody else's run", agent(OTHER), True, NONE),
    ("a function, with a person behind it", function(who=OWNER), True, NONE),
    ("a function with nobody behind it", function(), True, NONE),
    ("an app's page, as its own viewer", app(), True, NONE),
    ("an app's page, as another viewer", app(OTHER), True, NONE),
]


@pytest.mark.parametrize(
    ("caller", "answers_member", "expected"),
    [row[1:] for row in DEFAULT_MATRIX],
    ids=[row[0] for row in DEFAULT_MATRIX],
)
def test_default_machine(caller, answers_member, expected):
    assert (
        default_machine_access(caller, DEFAULT, answers_member=answers_member)
        is expected
    )


# --- a pod machine: the authorizer's answer, and the app's limits ------------

EDITOR = PodAccess(manage=True, use=True, see=True)
USER_ROLE = PodAccess(use=True, see=True)
VIEWER = PodAccess(see=True)
OUTSIDER = PodAccess()

POD_MATRIX = [
    ("an editor manages", user(OTHER), EDITOR, MANAGE),
    ("a member who may use", user(OTHER), USER_ROLE, USE),
    ("a viewer sees status", user(OTHER), VIEWER, STATUS),
    ("not in the pod", user(OTHER), OUTSIDER, NONE),
    ("a granted agent uses", agent(), USER_ROLE, USE),
    ("an ungranted agent", agent(), OUTSIDER, NONE),
    ("an app as an editor only uses", app(OTHER), EDITOR, USE),
    ("an app as a viewer sees", app(OTHER), VIEWER, STATUS),
    ("an app of another pod", app(OTHER, pod=OTHER_POD), EDITOR, NONE),
    ("an app with no pod", app(OTHER, pod=None), EDITOR, NONE),
]


@pytest.mark.parametrize(
    ("caller", "pod", "expected"),
    [row[1:] for row in POD_MATRIX],
    ids=[row[0] for row in POD_MATRIX],
)
def test_pod_machine(caller, pod, expected):
    assert pod_machine_access(caller, POD_MACHINE, pod) is expected


# --- a connected machine ---------------------------------------------------------

CONNECTED_MATRIX = [
    ("its owner", user(), CONNECTED, OUTSIDER, MANAGE),
    ("someone else, unshared", user(OTHER), CONNECTED, EDITOR, NONE),
    ("an agent, unshared", agent(), CONNECTED, USER_ROLE, NONE),
    ("an app never", app(), CONNECTED, EDITOR, NONE),
    ("an app never, shared", app(OTHER), SHARED_CONNECTED, EDITOR, NONE),
    (
        "a pod editor on a shared laptop only uses",
        user(OTHER),
        SHARED_CONNECTED,
        EDITOR,
        USE,
    ),
    ("a pod viewer on a shared laptop", user(OTHER), SHARED_CONNECTED, VIEWER, STATUS),
    (
        "a granted agent on a shared laptop",
        agent(OTHER),
        SHARED_CONNECTED,
        USER_ROLE,
        USE,
    ),
    (
        "an ungranted agent on a shared laptop",
        agent(OTHER),
        SHARED_CONNECTED,
        OUTSIDER,
        NONE,
    ),
    (
        "a granted function on a shared laptop",
        function(),
        SHARED_CONNECTED,
        USER_ROLE,
        USE,
    ),
]


@pytest.mark.parametrize(
    ("caller", "machine", "pod", "expected"),
    [row[1:] for row in CONNECTED_MATRIX],
    ids=[row[0] for row in CONNECTED_MATRIX],
)
def test_connected_machine(caller, machine, pod, expected):
    assert connected_machine_access(caller, machine, pod) is expected


# --- the resolver puts them together -----------------------------------------


class _Facts:
    def __init__(self, *, member: bool = True, pod: PodAccess = OUTSIDER) -> None:
        self.member = member
        self.pod = pod
        self.asked: list[str] = []

    async def answers_member(self, caller: MachineCaller) -> bool:
        self.asked.append("audience")
        return self.member

    async def pod_access(self, caller: MachineCaller, machine: Machine) -> PodAccess:
        self.asked.append("authorizer")
        return self.pod

    async def pods_named(self, caller: MachineCaller, name: str) -> list:
        self.asked.append("pod names")
        return []


class _NoDatabase:
    """``decide`` reads no rows: anything touching the session fails the test."""

    session = None


def _resolver(facts: _Facts) -> MachineResolver:
    return MachineResolver(_NoDatabase(), facts=facts)  # type: ignore[arg-type]


async def test_a_pod_machine_is_the_authorizers_to_decide():
    facts = _Facts(pod=USER_ROLE)

    assert await _resolver(facts).decide(agent(), POD_MACHINE) is USE
    assert facts.asked == ["authorizer"]


async def test_the_persons_own_machine_never_asks_the_authorizer():
    facts = _Facts(member=True, pod=EDITOR)

    assert await _resolver(facts).decide(user(), DEFAULT) is MANAGE
    assert await _resolver(facts).decide(agent(), DEFAULT) is USE
    assert "authorizer" not in facts.asked


async def test_an_outsider_run_is_refused_the_persons_own_machine():
    facts = _Facts(member=False)

    assert await _resolver(facts).decide(agent(), DEFAULT) is NONE


async def test_somebody_elses_run_is_refused_without_reading_its_conversation():
    facts = _Facts(member=True)

    assert await _resolver(facts).decide(agent(OTHER), DEFAULT) is NONE
    assert facts.asked == []


def test_not_found_is_what_a_refusal_reads_as():
    from app.modules.workspace.services.machine_resolver import _require
    from app.modules.workspace.domain.machine import MachineRef

    with pytest.raises(MachineNotFound):
        _require(NONE, USE, MachineRef.parse("pod:qa"))
