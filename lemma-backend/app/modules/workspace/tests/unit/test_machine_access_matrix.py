"""Every cell of the machine authorization matrix, one row each.

Rows are (caller, machine, facts) -> the access the caller gets. The matrix is
the plan's: a person's default machine is theirs, their in-Lemma runs' and
their consented apps'; a pod machine follows pod roles, visibility and grants;
a connected machine is its owner's, and a pod's only where shared.
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
    DenialReason,
    MachineAccess,
    MachineCaller,
    MachineFacts,
    PodStanding,
    decide,
)

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
POD_WIDE = _machine(MachineKind.POD, MachineVisibility.POD)
RESTRICTED = _machine(MachineKind.POD, MachineVisibility.RESTRICTED)
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


def function(pod=POD):
    return MachineCaller(
        CallerKind.FUNCTION, user_id=None, pod_id=pod, workload_id=FUNCTION
    )


def facts(**values) -> MachineFacts:
    return MachineFacts(**values)


VIEWER = facts(standing=PodStanding.VIEWER)
EDITOR = facts(standing=PodStanding.EDITOR)
ADMIN = facts(standing=PodStanding.ADMIN)
STRANGER = facts()

MATRIX = [
    # --- default machine (`me`) -----------------------------------------------
    ("owner on own machine", user(), DEFAULT, STRANGER, MANAGE),
    ("someone else's default machine", user(OTHER), DEFAULT, ADMIN, NONE),
    ("pod viewer on a default machine", user(OTHER), DEFAULT, VIEWER, NONE),
    ("app before consent", app(), DEFAULT, STRANGER, NONE),
    ("app after consent", app(), DEFAULT, facts(app_consented=True), USE),
    ("app as another viewer", app(OTHER), DEFAULT, facts(app_consented=True), NONE),
    (
        "agent run started in Lemma",
        agent(),
        DEFAULT,
        facts(run_started_in_lemma=True),
        USE,
    ),
    ("channel-triggered run", agent(), DEFAULT, STRANGER, NONE),
    (
        "run for somebody else",
        agent(OTHER),
        DEFAULT,
        facts(run_started_in_lemma=True),
        NONE,
    ),
    ("function on a default machine", function(), DEFAULT, STRANGER, NONE),
    # --- pod machine, POD visibility -------------------------------------------
    ("pod viewer sees status", user(OTHER), POD_WIDE, VIEWER, STATUS),
    ("pod editor has it all", user(OTHER), POD_WIDE, EDITOR, MANAGE),
    ("pod admin has it all", user(OTHER), POD_WIDE, ADMIN, MANAGE),
    ("not in the pod", user(OTHER), POD_WIDE, STRANGER, NONE),
    ("agent of the pod uses a POD machine", agent(), POD_WIDE, STRANGER, USE),
    ("agent with a grant", agent(), POD_WIDE, facts(workload_granted=True), USE),
    (
        "agent of another pod",
        agent(pod=OTHER_POD),
        POD_WIDE,
        facts(workload_granted=True),
        NONE,
    ),
    ("function without a grant", function(), POD_WIDE, STRANGER, NONE),
    ("function with a grant", function(), POD_WIDE, facts(workload_granted=True), USE),
    ("app as a viewer", app(OTHER), POD_WIDE, VIEWER, STATUS),
    ("app as an editor", app(OTHER), POD_WIDE, EDITOR, MANAGE),
    ("app of another pod", app(OTHER, pod=OTHER_POD), POD_WIDE, ADMIN, NONE),
    ("channel run of the pod's agent", agent(), POD_WIDE, STRANGER, USE),
    # --- pod machine, RESTRICTED -----------------------------------------------
    ("viewer cannot see it", user(OTHER), RESTRICTED, VIEWER, NONE),
    (
        "granted viewer sees status",
        user(OTHER),
        RESTRICTED,
        facts(standing=PodStanding.VIEWER, member_granted=True),
        STATUS,
    ),
    ("editor without a grant", user(OTHER), RESTRICTED, EDITOR, NONE),
    (
        "editor with a grant",
        user(OTHER),
        RESTRICTED,
        facts(standing=PodStanding.EDITOR, member_granted=True),
        MANAGE,
    ),
    ("admins see all", user(OTHER), RESTRICTED, ADMIN, MANAGE),
    ("agent without a grant", agent(), RESTRICTED, STRANGER, NONE),
    ("agent with a grant", agent(), RESTRICTED, facts(workload_granted=True), USE),
    (
        "function with a grant",
        function(),
        RESTRICTED,
        facts(workload_granted=True),
        USE,
    ),
    ("function without a grant", function(), RESTRICTED, STRANGER, NONE),
    ("app as an ungranted editor", app(OTHER), RESTRICTED, EDITOR, NONE),
    # --- connected -------------------------------------------------------------
    ("owner on their laptop", user(), CONNECTED, STRANGER, MANAGE),
    ("someone else on an unshared laptop", user(OTHER), CONNECTED, ADMIN, NONE),
    (
        "agent on an unshared laptop",
        agent(),
        CONNECTED,
        facts(workload_granted=True),
        NONE,
    ),
    ("app never reaches a laptop", app(), CONNECTED, facts(app_consented=True), NONE),
    ("pod viewer on a shared laptop", user(OTHER), SHARED_CONNECTED, VIEWER, STATUS),
    ("pod editor on a shared laptop", user(OTHER), SHARED_CONNECTED, EDITOR, USE),
    (
        "agent with a grant on a shared laptop",
        agent(OTHER),
        SHARED_CONNECTED,
        facts(workload_granted=True),
        USE,
    ),
    (
        "agent without a grant on a shared laptop",
        agent(OTHER),
        SHARED_CONNECTED,
        STRANGER,
        NONE,
    ),
    (
        "function with a grant on a shared laptop",
        function(),
        SHARED_CONNECTED,
        facts(workload_granted=True),
        USE,
    ),
]


@pytest.mark.parametrize(
    ("caller", "machine", "known", "expected"),
    [row[1:] for row in MATRIX],
    ids=[row[0] for row in MATRIX],
)
def test_matrix(caller, machine, known, expected):
    assert decide(caller, machine, known).access is expected


def test_an_app_that_has_not_asked_is_told_to_ask():
    decision = decide(app(), DEFAULT, STRANGER)

    assert decision.reason is DenialReason.CONSENT_REQUIRED


def test_every_other_refusal_reads_as_not_found():
    decision = decide(user(OTHER), RESTRICTED, VIEWER)

    assert decision.reason is DenialReason.NOT_FOUND
