"""The credential a process started over HTTP carries, per caller kind.

`POST /machines/{ref}/processes` used to mint a full session for whoever
called: an app page got its viewer's, an agent's delegated token became the
person's unscoped one, and a person-less function run got the machine
creator's. Each case here is one of those, held to "the caller's own
authority, never more".
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.delegation import DelegationClaims, WorkloadPrincipalType
from app.modules.workspace.domain.machine_access import CallerKind, MachineCaller
from app.modules.workspace.domain.machine_errors import MachineAccessDenied
from app.modules.workspace.services.machine_process_credentials import (
    process_env_for,
)
from app.modules.workspace.services.machine_services import (
    TokenClaims,
)

pytestmark = pytest.mark.asyncio

POD = uuid4()
PERSON = uuid4()
AGENT = uuid4()


class _Minter:
    """Records what it was asked to mint; the token is a function of it."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, user_id, pod_id, **kwargs) -> dict[str, str]:
        self.calls.append(((user_id, pod_id), kwargs))
        env = {"LEMMA_USER_ID": str(user_id), "LEMMA_BASE_URL": "http://api"}
        if kwargs.get("with_token", True):
            env["LEMMA_TOKEN"] = f"minted-for-{kwargs.get('workload_type')}"
        return env


def _claims(
    actor_type: WorkloadPrincipalType, *, scope=("pod:read",)
) -> DelegationClaims:
    session = uuid4()
    return DelegationClaims(
        actor_type=actor_type,
        actor_id=AGENT,
        actor_name="qa",
        pod_id=POD,
        session_id=str(session),
        scope=list(scope),
        invoked_by_user_id=PERSON,
        delegation_version=1,
    )


async def _env(caller: MachineCaller, minter: _Minter, **overrides):
    arguments = {
        "machine_pod_id": None,
        "get_env_vars": minter,
        "delegation_claims": None,
        "function_run_claims": None,
        "function_run_token": None,
        "delegated_tokens_enabled": True,
    }
    arguments.update(overrides)
    env, _claims_minted = await process_env_for(caller, **arguments)
    return env


async def _claims_for(caller: MachineCaller, minter: _Minter, **overrides):
    arguments = {
        "machine_pod_id": None,
        "get_env_vars": minter,
        "delegation_claims": None,
        "function_run_claims": None,
        "function_run_token": None,
        "delegated_tokens_enabled": True,
    }
    arguments.update(overrides)
    _env_minted, claims = await process_env_for(caller, **arguments)
    return claims


async def test_an_apps_page_gets_no_lemma_token() -> None:
    minter = _Minter()
    env = await _env(
        MachineCaller(CallerKind.APP, user_id=PERSON, pod_id=POD, app_id=uuid4()),
        minter,
    )

    assert "LEMMA_TOKEN" not in env
    assert minter.calls[0][1] == {"with_token": False}, "nothing was minted"


async def test_a_person_gets_their_own_token() -> None:
    minter = _Minter()
    env = await _env(MachineCaller(CallerKind.USER, user_id=PERSON), minter)

    assert env["LEMMA_TOKEN"] == "minted-for-None"
    assert minter.calls[0][0] == (PERSON, None)


async def test_an_agent_gets_the_same_delegation_claims_and_no_wider() -> None:
    minter = _Minter()
    claims = _claims(WorkloadPrincipalType.AGENT)
    caller = MachineCaller(
        CallerKind.AGENT,
        user_id=PERSON,
        pod_id=POD,
        workload_id=AGENT,
        conversation_id=UUID(claims.session_id),
    )

    env = await _env(caller, minter, delegation_claims=claims)

    assert env["LEMMA_TOKEN"] == "minted-for-AGENT"
    (user_id, pod_id), kwargs = minter.calls[0]
    assert (user_id, pod_id) == (PERSON, POD)
    assert kwargs == {
        "workload_type": "AGENT",
        "workload_id": AGENT,
        "workload_name": "qa",
        "scope": ["pod:read"],
        "session_id": claims.session_id,
        "conversation_id": UUID(claims.session_id),
    }


async def test_a_delegated_function_keeps_its_function_claims() -> None:
    minter = _Minter()
    claims = _claims(WorkloadPrincipalType.FUNCTION)
    caller = MachineCaller(
        CallerKind.FUNCTION, user_id=PERSON, pod_id=POD, workload_id=AGENT
    )

    env = await _env(caller, minter, delegation_claims=claims)

    assert env["LEMMA_TOKEN"] == "minted-for-FUNCTION"


async def test_a_personless_function_run_carries_its_own_run_token() -> None:
    minter = _Minter()
    run = SimpleNamespace(pod_id=POD, function_id=AGENT)
    caller = MachineCaller(
        CallerKind.FUNCTION, user_id=None, pod_id=POD, workload_id=AGENT
    )

    env = await _env(
        caller, minter, function_run_claims=run, function_run_token="lfr_run-token"
    )

    assert env["LEMMA_TOKEN"] == "lfr_run-token"
    assert "LEMMA_USER_ID" not in env, "the run acts for nobody"
    assert minter.calls[0][1] == {"with_token": False}, "no session was minted"


@pytest.mark.parametrize(
    ("caller", "overrides"),
    [
        # A workload whose claims cannot be read is refused, not upgraded.
        (
            MachineCaller(CallerKind.AGENT, user_id=PERSON, workload_id=AGENT),
            {},
        ),
        # Delegation switched off: minting would be an unscoped session.
        (
            MachineCaller(CallerKind.AGENT, user_id=PERSON, workload_id=AGENT),
            {
                "delegation_claims": _claims(WorkloadPrincipalType.AGENT),
                "delegated_tokens_enabled": False,
            },
        ),
        # A person-less run without its token.
        (MachineCaller(CallerKind.FUNCTION, user_id=None), {}),
        # A person-less caller that is not a function.
        (
            MachineCaller(CallerKind.AGENT, user_id=None),
            {"function_run_token": "lfr_x"},
        ),
        # A "person" whose request carried a workload's claims.
        (
            MachineCaller(CallerKind.USER, user_id=PERSON),
            {"delegation_claims": _claims(WorkloadPrincipalType.AGENT)},
        ),
    ],
)
async def test_a_caller_whose_claims_cannot_be_built_is_refused(
    caller, overrides
) -> None:
    minter = _Minter()
    with pytest.raises(MachineAccessDenied):
        await _env(caller, minter, **overrides)
    assert minter.calls == [], "nothing was minted for a refused caller"


async def test_a_service_restart_is_minted_exactly_what_its_start_was() -> None:
    """A service started by an agent comes back with the agent's claims, and one
    started by a page or a person-less run comes back with no token at all --
    never the person's whole session."""
    claims = _claims(WorkloadPrincipalType.AGENT)
    agent = MachineCaller(
        CallerKind.AGENT,
        user_id=PERSON,
        pod_id=POD,
        workload_id=AGENT,
        conversation_id=UUID(claims.session_id),
    )

    stored = await _claims_for(agent, _Minter(), delegation_claims=claims)

    assert stored is not None
    assert (stored.user_id, stored.pod_id) == (PERSON, POD)
    assert (stored.workload_type, stored.workload_id) == ("AGENT", AGENT)
    assert stored.scope == ("pod:read",)
    assert stored.session_id == claims.session_id
    assert TokenClaims.from_json(stored.to_json()) == stored

    page = MachineCaller(CallerKind.APP, user_id=PERSON, pod_id=POD, app_id=uuid4())
    assert await _claims_for(page, _Minter()) is None
    run = SimpleNamespace(pod_id=POD, function_id=AGENT)
    headless = MachineCaller(
        CallerKind.FUNCTION, user_id=None, pod_id=POD, workload_id=AGENT
    )
    assert (
        await _claims_for(
            headless, _Minter(), function_run_claims=run, function_run_token="lfr_x"
        )
        is None
    )
