"""A contact's function run acts for no member, and only its opener may allow it.

The dispatcher half: a run with no user is sent with a function-run token of
its own (never a member's session) and an identity naming the contact, while a
member's run reaches the runtime exactly as before. The runtime half: that
token fetches its own revision and reports its own run, and nothing else. The
policy half: opening a function to contacts takes pod settings permission and
either owning the function or administering the pod.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.authorization.context import (
    ActorType,
    AuthorizationDecision,
    Context,
)
from app.core.authorization.function_run import parse_function_run_token
from app.core.authorization.permissions import (
    POD_ADMIN_PERMISSIONS,
    POD_EDITOR_PERMISSIONS,
    POD_VIEWER_PERMISSIONS,
)
from app.core.domain.errors import DomainError
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionEntity,
    FunctionExecutionDispatch,
    FunctionRunRuntimeContext,
    FunctionSessionPrincipal,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.services.contact_access import (
    require_contact_input,
    require_contacts_opener,
)
from sandbox_runtime.function.runtime_models import RuntimeInvocation

pytestmark = pytest.mark.unit

_REVISION = f"sha256:{'a' * 64}"


class _UowFactory:
    @asynccontextmanager
    async def __call__(self):
        yield object()


async def _no_session_minter(**_kwargs):
    raise AssertionError("a contact's run must never mint a member's session")


async def _organization(_pod_id):
    return str(uuid4())


def _dispatch(*, user_id: UUID | None, contact_id: UUID | None):
    return FunctionExecutionDispatch(
        run_id=uuid4(),
        pod_id=uuid4(),
        function_id=uuid4(),
        function_name="book_slot",
        user_id=user_id,
        user_email=None,
        contact_id=contact_id,
        config=None,
        mode=FunctionDispatchMode.ASYNCHRONOUS,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=120),
        revision_hash=_REVISION,
        input_data={"contact_id": str(contact_id) if contact_id else None},
    )


def _context(dispatch: FunctionExecutionDispatch) -> FunctionRunRuntimeContext:
    return FunctionRunRuntimeContext(
        run_id=dispatch.run_id,
        deadline_at=dispatch.deadline_at,
        revision_hash=dispatch.revision_hash,
        artifact_path=f"artifacts/{'a' * 64}.zip",
        input_data=dispatch.input_data,
        config=None,
        user_id=dispatch.user_id,
        user_email=None,
        contact_id=dispatch.contact_id,
        pod_id=dispatch.pod_id,
        function_id=dispatch.function_id,
        function_name=dispatch.function_name,
    )


async def _send(dispatch: FunctionExecutionDispatch, minter):
    """Choose the credential and send the invocation, as ``execute`` does.

    The two steps are driven directly, with the runtime and the session
    minter injected, so nothing inside the dispatcher is replaced.
    """
    observed: dict = {}

    class _Runtime:
        async def post(self, url, *, headers, json, timeout):
            observed.update(headers=headers, json=json)
            return httpx.Response(
                202,
                request=httpx.Request("POST", url),
                json={"accepted": True, "run_id": str(dispatch.run_id)},
            )

    dispatcher = FunctionDispatcher(
        uow_factory=_UowFactory(),
        sandbox_client_factory=AsyncMock,
        token_minter=minter,
        token_cache=FunctionSessionTokenCache(),
        endpoint_cache=FunctionRuntimeEndpointCache(),
        runtime_http_client_factory=_Runtime,
        organization_resolver=_organization,
        delegated_tokens_enabled=True,
    )
    endpoint = FunctionRuntimeEndpoint(
        url="https://sandbox.test/runtime/",
        request_headers=(),
        allocation_id=uuid4(),
        allocation_epoch=1,
        profile_digest=f"sha256:{'2' * 64}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    token = await dispatcher._function_session_token(dispatch)
    await dispatcher._invoke_runtime(
        dispatch,
        context=_context(dispatch),
        endpoint=endpoint,
        function_token=token.value,
        organization_id=None,
    )
    return observed


async def test_a_contacts_run_calls_back_with_its_own_token():
    contact = uuid4()
    dispatch = _dispatch(user_id=None, contact_id=contact)

    observed = await _send(dispatch, _no_session_minter)

    bearer = observed["headers"]["Authorization"].removeprefix("Bearer ")
    claims = parse_function_run_token(bearer)
    assert claims.run_id == dispatch.run_id
    assert claims.function_id == dispatch.function_id
    assert claims.pod_id == dispatch.pod_id
    assert claims.revision_hash == dispatch.revision_hash
    assert claims.contact_id == contact
    identity = observed["json"]["identity"]
    assert identity["user_id"] is None
    assert identity["user_email"] is None
    assert identity["contact_id"] == str(contact)
    # The current runtime accepts what was sent.
    RuntimeInvocation.model_validate(observed["json"])


async def test_a_members_run_reaches_the_runtime_unchanged():
    member = uuid4()
    dispatch = _dispatch(user_id=member, contact_id=None)

    async def _member_session(**kwargs):
        assert kwargs["user_id"] == member
        return FunctionSessionToken(
            value="delegated-function-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    observed = await _send(dispatch, _member_session)

    assert observed["headers"]["Authorization"] == "Bearer delegated-function-token"
    # No ``contact_id`` key at all: a runtime that predates contacts forbids it.
    assert "contact_id" not in observed["json"]["identity"]
    assert observed["json"]["identity"]["user_id"] == str(member)


def _matches(principal, *, user_id, run_id, revision=_REVISION, ids=None):
    pod_id, function_id = ids
    return FunctionExecutionRepository._principal_matches(
        principal,
        user_id=user_id,
        pod_id=pod_id,
        function_id=function_id,
        function_name="book_slot",
        revision_hash=revision,
        delegated_tokens_enabled=True,
        run_id=run_id,
    )


def test_a_run_token_reports_its_own_run_and_nothing_else():
    ids = (uuid4(), uuid4())
    run_id = uuid4()
    principal = FunctionSessionPrincipal(
        pod_id=ids[0], function_id=ids[1], run_id=run_id, revision_hash=_REVISION
    )

    assert _matches(principal, user_id=None, run_id=run_id, ids=ids)
    # Its own revision's code, for the artifact fetch.
    assert _matches(principal, user_id=None, run_id=None, ids=ids)
    assert not _matches(principal, user_id=None, run_id=uuid4(), ids=ids)
    assert not _matches(
        principal, user_id=None, run_id=run_id, revision=f"sha256:{'b' * 64}", ids=ids
    )
    # A member's run of the same function is not a person-less run's to report.
    assert not _matches(principal, user_id=uuid4(), run_id=run_id, ids=ids)
    assert not _matches(principal, user_id=None, run_id=run_id, ids=(uuid4(), ids[1]))


class _RolePermissions:
    """Answers from the context's own permissions, as a role grant would."""

    async def authorize(self, ctx, permission_id, resource=None):
        return AuthorizationDecision(
            permission_id in ctx.permission_ids,
            "PERMISSION_MATCH"
            if permission_id in ctx.permission_ids
            else "INSUFFICIENT_PERMISSION",
            permission_id,
            resource,
        )

    async def accessible_resource_ids(self, ctx, permission_id, resource_type, pod_id):
        return frozenset()


def _member(permissions, user_id: UUID) -> Context:
    return Context(
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        user_id=user_id,
        authorizer=_RolePermissions(),
        permission_ids=frozenset(permissions),
    )


def _function(owner: UUID, *, input_schema=None) -> FunctionEntity:
    return FunctionEntity(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=owner,
        name="book_slot",
        input_schema=input_schema or {},
    )


async def test_an_editor_opens_their_own_function_but_not_a_colleagues():
    editor = uuid4()

    await require_contacts_opener(
        _member(POD_EDITOR_PERMISSIONS, editor), _function(editor)
    )
    with pytest.raises(DomainError) as raised:
        await require_contacts_opener(
            _member(POD_EDITOR_PERMISSIONS, editor), _function(uuid4())
        )
    assert raised.value.status_code == 403
    assert raised.value.code == "FUNCTION_CONTACTS_OWNER_OR_ADMIN"


async def test_an_admin_opens_anybodys_function_and_a_viewer_nobodys():
    await require_contacts_opener(
        _member(POD_ADMIN_PERMISSIONS, uuid4()), _function(uuid4())
    )
    viewer = uuid4()
    with pytest.raises(DomainError) as raised:
        await require_contacts_opener(
            _member(POD_VIEWER_PERMISSIONS, viewer), _function(viewer)
        )
    assert raised.value.status_code == 403


def test_a_function_must_declare_the_contact_it_is_told():
    require_contact_input(
        _function(uuid4(), input_schema={"properties": {"contact_id": {}}})
    )
    for schema in ({}, {"properties": {"customer": {}}}, {"properties": []}):
        with pytest.raises(DomainError) as raised:
            require_contact_input(_function(uuid4(), input_schema=schema))
        assert raised.value.status_code == 422
