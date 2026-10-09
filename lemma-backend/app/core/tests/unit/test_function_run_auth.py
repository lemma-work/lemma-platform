"""A person-less function run authenticates with its own token, and only so far.

The token names one run of one function in one pod, for one contact. It is
recognised before the session provider is asked, admitted only to the pod's
data and the runtime's own callbacks, and leaves the request with no user
anyone could act as.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core import security
from app.core.authorization import dependencies
from app.core.config import settings
from app.core.authorization.function_run import (
    NOBODY_USER_ID,
    FunctionRunClaims,
    InvalidSignedToken,
    allows_path,
    mint_function_run_token,
    parse_function_run_token,
)
from app.core.crypto.tokens import mint_signed_token

pytestmark = pytest.mark.unit

_REVISION = f"sha256:{'a' * 64}"


def _claims(*, contact_id=None) -> FunctionRunClaims:
    return FunctionRunClaims(
        run_id=uuid4(),
        function_id=uuid4(),
        pod_id=uuid4(),
        revision_hash=_REVISION,
        contact_id=contact_id if contact_id is not None else uuid4(),
    )


def _connection(token: str, path: str):
    return SimpleNamespace(
        scope={"type": "http", "method": "GET"},
        url=SimpleNamespace(path=path),
        cookies={},
        headers={"authorization": f"Bearer {token}"},
        state=SimpleNamespace(),
    )


@pytest.fixture
def no_session_provider(monkeypatch):
    """No SuperTokens core and no revocation store here.

    A function-run token that reached ``get_session`` would fail for want of
    the core, so a test that sees the run admitted proves it never got there.
    Revocation needs Redis and is proved in
    ``function/tests/e2e/test_contact_function_runs_e2e.py``.
    """
    monkeypatch.setattr(settings, "delegation_revocation_ttl_seconds", 0)


def test_a_token_carries_the_run_it_was_minted_for():
    claims = _claims()

    token = mint_function_run_token(claims, ttl_seconds=60)

    assert token.startswith("lfr_")
    assert parse_function_run_token(token) == claims
    assert claims.actor_label == f"contact:{claims.contact_id}"


def test_an_anonymous_run_names_no_contact():
    claims = FunctionRunClaims(
        run_id=uuid4(),
        function_id=uuid4(),
        pod_id=uuid4(),
        revision_hash=_REVISION,
        contact_id=None,
    )

    parsed = parse_function_run_token(mint_function_run_token(claims, ttl_seconds=60))

    assert parsed.contact_id is None
    assert parsed.actor_label == "anonymous"


def test_a_token_signed_for_another_purpose_is_refused_under_the_prefix():
    forged = "lfr_" + mint_signed_token(
        "visitor-access",
        {"workload": "function", "run_id": str(uuid4())},
        ttl_seconds=60,
    )

    with pytest.raises(InvalidSignedToken):
        parse_function_run_token(forged)


def test_a_changed_claim_is_refused():
    token = mint_function_run_token(_claims(), ttl_seconds=60)
    payload, rest = token.removeprefix("lfr_").split(".", 1)
    mutated = payload[:-1] + ("A" if payload[-1] != "A" else "B")

    with pytest.raises(InvalidSignedToken):
        parse_function_run_token(f"lfr_{mutated}.{rest}")


def test_only_the_runs_own_pod_data_and_the_runtime_callbacks_are_reachable():
    claims = _claims()
    pod = f"/pods/{claims.pod_id}"

    assert allows_path(claims, f"{pod}/datastore/tables/orders/records")
    assert allows_path(claims, f"{pod}/datastore/query")
    assert allows_path(claims, f"/internal/function-runtime/runs/{claims.run_id}")
    assert not allows_path(claims, f"/pods/{uuid4()}/datastore/tables")
    assert not allows_path(claims, f"{pod}/functions/other/runs")
    assert not allows_path(claims, f"{pod}/members")
    assert not allows_path(claims, "/users/me")
    assert not allows_path(claims, f"{pod}")


async def test_verify_auth_admits_the_run_as_nobody(no_session_provider):
    claims = _claims()
    connection = _connection(
        mint_function_run_token(claims, ttl_seconds=60),
        f"/pods/{claims.pod_id}/datastore/tables",
    )

    await security.verify_auth(connection)

    assert connection.state.function_run_claims == claims
    assert connection.state.user.id == NOBODY_USER_ID
    assert connection.state.delegation_claims is None


async def test_verify_auth_refuses_a_member_route_cleanly(no_session_provider):
    claims = _claims()
    connection = _connection(
        mint_function_run_token(claims, ttl_seconds=60), "/users/me"
    )

    with pytest.raises(HTTPException) as raised:
        await security.verify_auth(connection)

    assert raised.value.status_code == 403
    assert raised.value.detail["code"] == "FUNCTION_RUN_ROUTE_NOT_ALLOWED"
    assert not hasattr(connection.state, "user")


async def test_verify_auth_refuses_an_expired_or_forged_run_token(
    no_session_provider,
):
    claims = _claims()
    path = f"/pods/{claims.pod_id}/datastore/tables"
    expired = mint_function_run_token(claims, ttl_seconds=-1)

    for token in (expired, "lfr_not-a-token"):
        with pytest.raises(HTTPException) as raised:
            await security.verify_auth(_connection(token, path))
        assert raised.value.status_code == 401


async def test_a_run_token_never_resolves_another_pods_context():
    claims = _claims()
    request = SimpleNamespace(
        state=SimpleNamespace(function_run_claims=claims, delegation_claims=None),
        headers={},
    )

    with pytest.raises(HTTPException) as raised:
        await dependencies.resolve_pod_context(
            session=None, request=request, user_id=NOBODY_USER_ID, pod_id=uuid4()
        )

    assert raised.value.status_code == 403
