from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.identity.api.controllers.auth_controller import verify_token
from app.modules.identity.domain.user_entities import AuthUserEntity, UserEntity
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
)
from app.core.authorization.delegation import WorkloadPrincipalType


class _FakeUserService:
    def __init__(self, user: UserEntity):
        self.user = user
        self.requested_user_ids: list = []

    async def get_user(self, user_id):
        self.requested_user_ids.append(user_id)
        return self.user


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, organization_id=None):
        self.organization_id = organization_id
        self.execute_calls = []

    async def execute(self, statement):
        self.execute_calls.append(statement)
        return _ScalarResult(self.organization_id)


class _FakeUoW:
    def __init__(self, organization_id=None):
        self.session = _FakeSession(organization_id)


@pytest.mark.asyncio
async def test_verify_token_returns_user_context():
    user_id = uuid4()
    request = SimpleNamespace(state=SimpleNamespace(user=AuthUserEntity(id=user_id)))
    service = _FakeUserService(
        UserEntity(
            id=user_id,
            email="lemma@lemma.work",
        )
    )

    uow = _FakeUoW()

    response = await verify_token(request=request, user_service=service, uow=uow)

    assert response.user_id == user_id
    assert response.email == "lemma@lemma.work"
    assert response.pod_id is None
    assert response.organization_id is None
    assert response.function_id is None
    assert response.function_name is None
    assert response.scopes == []
    assert service.requested_user_ids == [user_id]
    assert uow.session.execute_calls == []


@pytest.mark.asyncio
async def test_verify_token_returns_function_delegation_claims():
    user_id = uuid4()
    pod_id = uuid4()
    function_id = uuid4()
    organization_id = uuid4()
    request = SimpleNamespace(
        state=SimpleNamespace(
            user=AuthUserEntity(id=user_id),
            auth_claims={
                CLAIM_ACTOR_TYPE: WorkloadPrincipalType.FUNCTION.value,
                CLAIM_ACTOR_ID: str(function_id),
                CLAIM_ACTOR_NAME: "sync_expense",
                CLAIM_POD_ID: str(pod_id),
                CLAIM_SCOPE: ["function:execute"],
            },
        )
    )
    service = _FakeUserService(
        UserEntity(
            id=user_id,
            email="lemma@lemma.work",
        )
    )

    uow = _FakeUoW(organization_id)

    response = await verify_token(request=request, user_service=service, uow=uow)

    assert response.user_id == user_id
    assert response.pod_id == pod_id
    assert response.organization_id == organization_id
    assert response.function_id == function_id
    assert response.function_name == "sync_expense"
    assert response.scopes == ["function:execute"]
    assert len(uow.session.execute_calls) == 1
