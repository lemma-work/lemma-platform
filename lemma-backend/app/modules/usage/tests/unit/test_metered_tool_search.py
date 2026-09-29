"""Under a monetary limit, tool search runs locally so the request has a price.

Provider-native tool search is billed in a category the normalized receipt does
not report, so a request carrying it cannot be priced, and a monetary limit
refuses what it cannot price. An agent with deferred tools therefore could not
start a run under a spend limit on Anthropic or OpenAI. The local `search_tools`
function costs ordinary tokens, so that is what a limited request sends; an
unlimited one keeps the native search.
"""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.native_tools import AbstractNativeTool

from app.modules.usage.domain.accounting import RequestReceipt
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure.metered_model import (
    MeteredModel,
    _with_local_tool_search,
)
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import ModelPricing, UsageService

pytestmark = pytest.mark.unit


class Accounting:
    """The gateway's contract, with or without a monetary limit in force.

    Mirrors `PostgresRequestAccountingGateway.begin`: under a limit, a request
    that starts a run and cannot be priced is refused.
    """

    def __init__(self, *, limited: bool) -> None:
        self.limited = limited
        self.receipts: list[RequestReceipt] = []

    async def begin(
        self,
        request_id: UUID,
        now: datetime,
        *,
        priceable: bool = True,
        in_flight: bool = False,
    ) -> bool:
        if self.limited and not priceable and not in_flight:
            raise UsageLimitExceededError(reason="configuration")
        return self.limited

    async def record(self, receipt: RequestReceipt) -> bool:
        self.receipts.append(receipt)
        return False


def get_weather(city: str) -> str:
    """Today's weather in a city."""
    return f"sunny in {city}"


@pytest.fixture
def profile() -> Iterator[dict[str, object]]:
    model_name = f"tool-search-{uuid4()}"
    UsageService.register_model_pricing({model_name: ModelPricing(1000, 0)})
    try:
        yield {
            "profile_id": "system:tool-search-test",
            "scope": "SYSTEM",
            "model_name": model_name,
        }
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop(model_name, None)


async def _run(
    profile: dict[str, object], gateway: Accounting
) -> list[ModelRequestParameters]:
    """Run an agent whose only tool is deferred; return what each request sent."""
    sent: list[ModelRequestParameters] = []

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(info.model_request_parameters)
        offered = {tool.name for tool in info.function_tools}
        if len(sent) == 1 and "search_tools" in offered:
            return ModelResponse(
                parts=[ToolCallPart("search_tools", {"queries": ["weather"]})]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        MeteredModel(FunctionModel(provider), profile),
        tools=[Tool(get_weather, defer_loading=True)],
        capabilities=[ToolSearch()],
    )
    async with metering_execution(
        UsageExecutionContext(user_id=uuid4(), organization_id=None, pod_id=None)
    ) as scope:
        meter, _ = scope.meter(profile, None)
        meter.gateway = gateway
        result = await agent.run("what is the weather in Lisbon?")
    assert result.output == "done"
    return sent


def _native_tools_of(capability: ToolSearch[object]) -> list[AbstractNativeTool]:
    return [
        tool
        for tool in capability.get_native_tools()
        if isinstance(tool, AbstractNativeTool)
    ]


def _native_kinds(parameters: ModelRequestParameters) -> list[str]:
    return [tool.kind for tool in parameters.native_tools]


async def test_a_limited_run_searches_locally_and_every_request_is_priced(
    profile: dict[str, object],
) -> None:
    gateway = Accounting(limited=True)

    sent = await _run(profile, gateway)

    assert len(sent) == 2, "the model searched, then answered"
    for parameters in sent:
        assert "tool_search" not in _native_kinds(parameters), parameters
        assert "search_tools" in {t.name for t in parameters.function_tools}
    assert gateway.receipts
    assert all(receipt.cost is not None for receipt in gateway.receipts), (
        "a request under a monetary limit must carry a price"
    )
    assert sum(receipt.cost or Decimal(0) for receipt in gateway.receipts) > 0


async def test_an_unlimited_run_keeps_the_providers_native_search(
    profile: dict[str, object],
) -> None:
    gateway = Accounting(limited=False)

    sent = await _run(profile, gateway)

    assert "tool_search" in _native_kinds(sent[0])
    assert "search_tools" not in {t.name for t in sent[0].function_tools}, (
        "the local fallback is dropped where the native search is sent"
    )


def test_only_the_optional_tool_search_is_left_to_the_local_fallback() -> None:
    (optional,) = _native_tools_of(ToolSearch())
    (named,) = _native_tools_of(ToolSearch(strategy="bm25"))

    swapped = _with_local_tool_search(ModelRequestParameters(native_tools=[optional]))
    assert swapped is not None
    assert swapped.native_tools == []

    # A named strategy has no local equivalent: it stays, and stays unpriceable.
    assert _with_local_tool_search(ModelRequestParameters(native_tools=[named])) is None
    assert _with_local_tool_search(ModelRequestParameters()) is None
