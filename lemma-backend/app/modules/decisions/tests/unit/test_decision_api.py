"""The route's own work: body bounds, who is asking, and how refusals look."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api.exception_handlers import register_exception_handlers
from app.core.authorization.context import ActorType, Context, PrincipalRef
from app.core.authorization.dependencies import get_pod_context
from app.modules.decisions.api.controllers.decision_controller import router
from app.modules.decisions.api.dependencies import (
    MAX_BODY_BYTES,
    get_decision_service,
)
from app.modules.decisions.domain.answers import Answer, DecisionResult
from app.modules.decisions.domain.errors import (
    DecisionLimitedError,
    DecisionUnavailableError,
)
from app.modules.decisions.domain.request import DecisionCaller, DecisionRequest

pytestmark = pytest.mark.unit

POD = uuid4()
ORG = uuid4()
USER = uuid4()
FUNCTION = uuid4()
BODY = {
    "instruction": "Is it urgent?",
    "evidence": {"subject": "Server down"},
    "schema": {
        "type": "object",
        "properties": {"urgent": {"type": "boolean", "description": "Urgent?"}},
    },
}


@dataclass
class _Service:
    raises: BaseException | None = None
    asked: list[tuple[DecisionRequest, DecisionCaller]] = field(default_factory=list)

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        self.asked.append((request, caller))
        if self.raises is not None:
            raise self.raises
        return DecisionResult(
            answers={"urgent": Answer(True, 0.8)}, provider="fake", model="m"
        )


def _context(*, actor_type: ActorType, actor_id: str, member: bool) -> Context:
    return Context(
        actor_type=actor_type,
        actor_id=actor_id,
        authorizer=object(),  # type: ignore[arg-type]
        user_id=USER,
        organization_id=ORG,
        pod_id=POD,
        principal_refs=frozenset(
            {PrincipalRef(type="POD_MEMBER", id=uuid4())} if member else set()
        ),
    )


@pytest.fixture
def service() -> _Service:
    return _Service()


def _client(service: _Service, context: Context) -> AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_pod_context] = lambda: context
    app.dependency_overrides[get_decision_service] = lambda: service
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def member(service: _Service) -> AsyncIterator[AsyncClient]:
    context = _context(actor_type=ActorType.USER, actor_id=f"user:{USER}", member=True)
    async with _client(service, context) as client:
        yield client


async def test_a_member_gets_the_answers(
    member: AsyncClient, service: _Service
) -> None:
    response = await member.post(f"/pods/{POD}/decisions", json=BODY)

    assert response.status_code == 200
    assert response.json() == {
        "answers": {"urgent": {"value": True, "confidence": 0.8}},
        "provider": "fake",
        "model": "m",
        "usage": {"input_tokens": None, "output_tokens": None},
    }
    request, caller = service.asked[0]
    assert request.evidence == {"subject": "Server down"}
    assert (caller.user_id, caller.organization_id, caller.pod_id) == (USER, ORG, POD)
    assert caller.workload_type is None


async def test_a_function_is_attributed_to_its_person_and_tagged(
    service: _Service,
) -> None:
    context = _context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{FUNCTION}",
        member=False,
    )
    async with _client(service, context) as client:
        response = await client.post(f"/pods/{POD}/decisions", json=BODY)

    assert response.status_code == 200
    caller = service.asked[0][1]
    assert caller.user_id == USER
    assert (caller.workload_type, caller.workload_id) == ("function", FUNCTION)
    assert caller.source_id == f"function:{FUNCTION}"


async def test_a_person_outside_the_pod_is_refused(service: _Service) -> None:
    context = _context(actor_type=ActorType.USER, actor_id=f"user:{USER}", member=False)
    async with _client(service, context) as client:
        response = await client.post(f"/pods/{POD}/decisions", json=BODY)

    assert response.status_code == 403
    assert response.json()["code"] == "POD_MEMBERSHIP_REQUIRED"
    assert service.asked == []


async def test_a_rate_limit_says_when_to_retry(
    member: AsyncClient, service: _Service
) -> None:
    service.raises = DecisionLimitedError(23)

    response = await member.post(f"/pods/{POD}/decisions", json=BODY)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "23"
    assert response.json()["code"] == "DECISION_RATE_LIMITED"


async def test_an_unavailable_provider_is_a_503_not_an_answer(
    member: AsyncClient, service: _Service
) -> None:
    service.raises = DecisionUnavailableError("timeout")

    response = await member.post(f"/pods/{POD}/decisions", json=BODY)

    assert response.status_code == 503
    assert response.json()["code"] == "DECISION_PROVIDER_UNAVAILABLE"
    assert response.json()["details"] == {"reason": "timeout"}


async def test_an_oversized_body_is_refused_unread(
    member: AsyncClient, service: _Service
) -> None:
    response = await member.post(
        f"/pods/{POD}/decisions",
        json={**BODY, "evidence": "x" * (MAX_BODY_BYTES + 1)},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "DECISION_INPUT_TOO_LARGE"
    assert service.asked == []


async def test_unknown_fields_and_strictness(member: AsyncClient) -> None:
    unknown = await member.post(f"/pods/{POD}/decisions", json={**BODY, "subject": "x"})
    no_schema = await member.post(
        f"/pods/{POD}/decisions",
        json={key: value for key, value in BODY.items() if key != "schema"},
    )

    assert unknown.status_code == 422
    assert no_schema.status_code == 422
