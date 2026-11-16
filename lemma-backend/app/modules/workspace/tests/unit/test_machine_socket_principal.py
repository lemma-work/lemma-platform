"""A machine socket's caller is the token's principal, not just its user id.

Sockets are exempt from ``verify_auth``, and the process-stream and browser
sockets used to build a USER caller from the token's user id alone: an agent
token from a schedule or channel run -- or another pod's agent -- attached to
the person's ttys and drove their browser as the person. These hold the
socket to what HTTP does: the delegation resolved and refused the same way,
the caller built by the same function, and a function-run token refused.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_DELEGATION_VERSION,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_POD_ID,
    CLAIM_SESSION_ID,
)
from app.core.config import settings
from app.core.security import PrincipalLookups
from app.modules.workspace.api.controllers.browser_view_session import (
    remember_principal,
    resolve_user_id,
)
from app.modules.workspace.api.controllers.machine_socket_auth import _caller
from app.modules.workspace.domain.machine_access import CallerKind
from app.modules.workspace.domain.machine_errors import MachineNotFound

pytestmark = pytest.mark.asyncio

PERSON = uuid4()
POD = uuid4()
AGENT = uuid4()


def _socket(**headers: str) -> SimpleNamespace:
    return SimpleNamespace(
        headers=headers, cookies={}, query_params={}, state=SimpleNamespace()
    )


def _lookups(*, revoked: bool = False) -> PrincipalLookups:
    async def standing(_user_id):
        return SimpleNamespace(is_active=True, is_deleted=False, is_verified=True)

    async def delegation_revoked(_actor_id) -> bool:
        return revoked

    return PrincipalLookups(
        account_standing=standing, delegation_revoked=delegation_revoked
    )


def _agent_payload(*, invoked_by: UUID = PERSON) -> dict[str, object]:
    return {
        "isImpersonation": True,
        CLAIM_ACTOR_TYPE: "AGENT",
        CLAIM_ACTOR_ID: str(AGENT),
        CLAIM_ACTOR_NAME: "scheduled-digest",
        CLAIM_POD_ID: str(POD),
        CLAIM_SESSION_ID: str(uuid4()),
        CLAIM_INVOKED_BY_USER_ID: str(invoked_by),
        CLAIM_DELEGATION_VERSION: 1,
    }


@pytest.fixture(autouse=True)
def _delegation_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "authz_delegated_tokens_enabled", True)


async def test_an_agent_token_is_the_agent_on_the_socket_too() -> None:
    socket = _socket()
    await remember_principal(socket, str(PERSON), _agent_payload(), lookups=_lookups())

    caller = await _caller(socket, PERSON)

    assert caller.kind is CallerKind.AGENT, "never the person it acts for"
    assert caller.user_id == PERSON
    assert caller.workload_id == AGENT
    assert caller.pod_id == POD


@pytest.mark.parametrize(
    ("payload", "revoked"),
    [
        (_agent_payload(), True),
        (_agent_payload(invoked_by=uuid4()), False),
        ({**_agent_payload(), CLAIM_POD_ID: None}, False),
    ],
    ids=["revoked", "someone-else's-delegation", "malformed-claims"],
)
async def test_a_refused_delegation_never_becomes_the_person(payload, revoked) -> None:
    socket = _socket()
    with pytest.raises(PermissionError):
        await remember_principal(
            socket, str(PERSON), payload, lookups=_lookups(revoked=revoked)
        )

    with pytest.raises(MachineNotFound):
        await _caller(socket, PERSON)


async def test_a_socket_whose_principal_was_not_resolved_is_refused() -> None:
    with pytest.raises(MachineNotFound):
        await _caller(_socket(), PERSON)


async def test_a_principal_for_another_user_is_not_used() -> None:
    socket = _socket()
    await remember_principal(socket, str(PERSON), _agent_payload(), lookups=_lookups())
    with pytest.raises(MachineNotFound):
        await _caller(socket, uuid4())


async def test_a_function_run_token_may_not_open_a_machine_socket() -> None:
    with pytest.raises(PermissionError):
        await resolve_user_id(_socket(authorization="Bearer lfr_anything"))
