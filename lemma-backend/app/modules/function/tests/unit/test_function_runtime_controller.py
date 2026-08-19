"""Behavior of the internal function-runtime callback controller.

``test_function_runtime_auth_boundary.py`` only checks that these routes are
wired into the global auth middleware (they are not in ``EXCLUDED_PATHS``).
These tests call the router's handler functions directly -- the same pattern
``app/modules/agent/tests/unit/test_agent_tool_controller.py`` uses for an
internal, delegation-authenticated controller -- with a ``SimpleNamespace``
standing in for ``request.state`` and a mocked ``FunctionRuntimeGateway``, to
lock down the actual 401/422/503/409 behavior instead of just the routing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.authorization.delegation import WorkloadPrincipalType
from app.modules.function.api.controllers.function_runtime_controller import (
    _principal,
    download_definition_artifact,
    report_terminal,
)
from app.modules.function.application.function_runtime_gateway import (
    RuntimeArtifactCorrupt,
    RuntimeCredentialRejected,
    RuntimeStateRejected,
)
from app.modules.function.contracts.runtime import (
    RuntimeEventResponse,
    RuntimeTerminalRequest,
)

pytestmark = pytest.mark.unit

_VALID_REVISION_HASH = f"sha256:{'a' * 64}"


def _authenticated_request(*, function_id=None):
    return SimpleNamespace(
        state=SimpleNamespace(
            user=SimpleNamespace(id=uuid4()),
            delegation_claims=SimpleNamespace(
                actor_type=WorkloadPrincipalType.FUNCTION,
                actor_id=function_id or uuid4(),
                pod_id=uuid4(),
                session_id="function-session:test",
                actor_name="my_function",
                scope=["execute"],
            ),
        )
    )


# -- _principal -----------------------------------------------------------


def test_principal_rejects_when_request_has_no_authenticated_user() -> None:
    request = SimpleNamespace(
        state=SimpleNamespace(
            user=None,
            delegation_claims=SimpleNamespace(
                actor_type=WorkloadPrincipalType.FUNCTION,
                actor_id=uuid4(),
                pod_id=uuid4(),
                session_id="s",
                actor_name=None,
                scope=[],
            ),
        )
    )

    with pytest.raises(HTTPException) as excinfo:
        _principal(request)
    assert excinfo.value.status_code == 401


def test_principal_rejects_when_delegation_claims_are_missing() -> None:
    request = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id=uuid4()), delegation_claims=None)
    )

    with pytest.raises(HTTPException) as excinfo:
        _principal(request)
    assert excinfo.value.status_code == 401


def test_principal_rejects_a_non_function_delegated_actor() -> None:
    """A delegated AGENT token (or a user token, in principle) must not be
    accepted on the function-runtime callback surface -- only the sandbox's
    own minted FUNCTION token may call it."""
    request = SimpleNamespace(
        state=SimpleNamespace(
            user=SimpleNamespace(id=uuid4()),
            delegation_claims=SimpleNamespace(
                actor_type=WorkloadPrincipalType.AGENT,
                actor_id=uuid4(),
                pod_id=uuid4(),
                session_id="s",
                actor_name=None,
                scope=[],
            ),
        )
    )

    with pytest.raises(HTTPException) as excinfo:
        _principal(request)
    assert excinfo.value.status_code == 401


def test_principal_builds_the_session_principal_from_valid_function_claims() -> None:
    function_id = uuid4()
    request = _authenticated_request(function_id=function_id)

    principal = _principal(request)

    assert principal.user_id == request.state.user.id
    assert principal.function_id == function_id
    assert principal.pod_id == request.state.delegation_claims.pod_id
    assert principal.session_id == "function-session:test"
    assert principal.scope == ("execute",)


# -- download_definition_artifact ------------------------------------------


@pytest.mark.asyncio
async def test_download_definition_artifact_rejects_a_malformed_revision_hash() -> None:
    # The format check runs before authentication, so a garbage request/gateway
    # is fine here -- neither is ever touched.
    with pytest.raises(HTTPException) as excinfo:
        await download_definition_artifact(
            uuid4(), "not-a-sha256-digest", SimpleNamespace(), SimpleNamespace()
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_download_definition_artifact_maps_credential_rejected_to_401() -> None:
    gateway = SimpleNamespace(
        definition_artifact=AsyncMock(side_effect=RuntimeCredentialRejected())
    )

    with pytest.raises(HTTPException) as excinfo:
        await download_definition_artifact(
            uuid4(), _VALID_REVISION_HASH, _authenticated_request(), gateway
        )
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_download_definition_artifact_maps_missing_artifact_to_503() -> None:
    gateway = SimpleNamespace(
        definition_artifact=AsyncMock(side_effect=FileNotFoundError())
    )

    with pytest.raises(HTTPException) as excinfo:
        await download_definition_artifact(
            uuid4(), _VALID_REVISION_HASH, _authenticated_request(), gateway
        )
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_download_definition_artifact_maps_corrupt_artifact_to_503() -> None:
    gateway = SimpleNamespace(
        definition_artifact=AsyncMock(side_effect=RuntimeArtifactCorrupt())
    )

    with pytest.raises(HTTPException) as excinfo:
        await download_definition_artifact(
            uuid4(), _VALID_REVISION_HASH, _authenticated_request(), gateway
        )
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_download_definition_artifact_returns_the_gateway_bytes_on_success() -> (
    None
):
    gateway = SimpleNamespace(definition_artifact=AsyncMock(return_value=b"zip-bytes"))

    response = await download_definition_artifact(
        uuid4(), _VALID_REVISION_HASH, _authenticated_request(), gateway
    )

    assert response.body == b"zip-bytes"
    assert response.media_type == "application/zip"


# -- report_terminal --------------------------------------------------------


def _terminal_request() -> RuntimeTerminalRequest:
    return RuntimeTerminalRequest(
        status="completed", output_data={"ok": True}, stdout="done", stderr=""
    )


@pytest.mark.asyncio
async def test_report_terminal_maps_credential_rejected_to_401() -> None:
    gateway = SimpleNamespace(terminal=AsyncMock(side_effect=RuntimeCredentialRejected()))

    with pytest.raises(HTTPException) as excinfo:
        await report_terminal(
            uuid4(), _terminal_request(), _authenticated_request(), gateway
        )
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_report_terminal_maps_state_rejected_to_409() -> None:
    """An already-terminal (or otherwise conflicting) run must not be silently
    accepted a second time -- the runtime's retry needs a definitive answer."""
    gateway = SimpleNamespace(terminal=AsyncMock(side_effect=RuntimeStateRejected()))

    with pytest.raises(HTTPException) as excinfo:
        await report_terminal(
            uuid4(), _terminal_request(), _authenticated_request(), gateway
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_report_terminal_returns_the_gateway_response_on_success() -> None:
    gateway = SimpleNamespace(
        terminal=AsyncMock(return_value=RuntimeEventResponse(accepted=True, duplicate=False))
    )

    response = await report_terminal(
        uuid4(), _terminal_request(), _authenticated_request(), gateway
    )

    assert response.accepted is True
    assert response.duplicate is False
