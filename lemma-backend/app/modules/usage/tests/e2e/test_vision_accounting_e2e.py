"""Looking at an image is metered work, not a run the budget has to refuse.

Two paths reach a provider with an image in the request, and a monetary limit
broke both. A vision-capable model got the image in its own history, and the
request after it died reconciling spend that had been recorded unpriced. A
text-only model's delegate opened a metering scope of its own, so its single
image request looked like a run *starting* unpriceable, which `begin` refuses
before anything is spent. Neither failure named an image; both were reported
as a usage limit the account was nowhere near.
"""

from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import select

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.usage.config import usage_settings
from app.modules.usage.infrastructure.metered_model import MeteredModel
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import ModelPricing, UsageService

pytestmark = pytest.mark.e2e

IMAGE = BinaryContent(data=b"\x89PNG\r\n\x1a\n", media_type="image/png")
TEXT: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(["hello"])])]
# What `view_image` hands back to a model that can read it itself.
LOOKING: list[ModelMessage] = [
    ModelRequest(parts=[ToolReturnPart("view_image", [IMAGE])])
]


@pytest.fixture
def vision_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A budget with room in it, and a card that prices input and output."""
    name = f"vision-{uuid4()}"
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 100.0)
    UsageService.register_model_pricing({name: ModelPricing(1000.0, 1000.0)})
    try:
        yield name
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop(name, None)


async def _provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart("what the image says")],
        usage=RequestUsage(input_tokens=1000, output_tokens=100),
    )


def _model(name: str) -> MeteredModel:
    return MeteredModel(
        FunctionModel(_provider),
        {"profile_id": "system:vision", "scope": "SYSTEM", "model_name": name},
    )


async def _costs(db_manager: DatabaseManager, user_id: UUID) -> list[Decimal | None]:
    async with db_manager.session_factory() as session:
        return [
            record.cost_amount
            for record in await session.scalars(
                select(UsageRecord)
                .where(UsageRecord.user_id == user_id)
                .order_by(UsageRecord.occurred_at)
            )
        ]


async def test_a_run_carries_on_after_the_model_looks_at_an_image(
    db_manager: DatabaseManager, vision_model: str
) -> None:
    model, user_id = _model(vision_model), uuid4()
    async with metering_execution(
        UsageExecutionContext(user_id=user_id, organization_id=None, pod_id=None),
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    ):
        await model.request(TEXT, None, ModelRequestParameters())
        await model.request(LOOKING, None, ModelRequestParameters())
        await model.request(TEXT, None, ModelRequestParameters())

    # Every request priced, the image one included: the provider counted its
    # pixels into `input_tokens` and this card charges those at `input_mtok`.
    assert await _costs(db_manager, user_id) == [Decimal("1.1")] * 3


async def test_the_vision_delegate_is_admitted_and_billed_to_the_same_budget(
    db_manager: DatabaseManager, vision_model: str
) -> None:
    """`describe_images` opens its own scope so the spend is sourced to vision."""
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    model, user_id = _model(vision_model), uuid4()
    parent = UsageExecutionContext(
        user_id=user_id, organization_id=None, pod_id=None, agent_run_id=uuid4()
    )
    async with metering_execution(parent, factory=factory):
        await model.request(TEXT, None, ModelRequestParameters())
        async with metering_execution(
            UsageExecutionContext(
                user_id=user_id,
                organization_id=None,
                pod_id=None,
                agent_run_id=parent.agent_run_id,
                source_type="vision",
            ),
            factory=factory,
        ):
            await model.request(LOOKING, None, ModelRequestParameters())
        await model.request(TEXT, None, ModelRequestParameters())

    assert await _costs(db_manager, user_id) == [Decimal("1.1")] * 3
    async with db_manager.session_factory() as session:
        sources = set(
            await session.scalars(
                select(UsageRecord.source_type).where(UsageRecord.user_id == user_id)
            )
        )
    assert sources == {"agent_run", "vision"}
