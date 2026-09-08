"""A failed settlement must finish before another provider request starts."""

from collections.abc import Iterator
from datetime import datetime
from types import ModuleType
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.usage.domain.accounting import RequestReceipt, TokenCounts
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.services.request_accounting_gateway import (
    PostgresRequestAccountingGateway,
)
from app.modules.usage.services.request_meter import RequestMeter


class Accounting:
    def __init__(self) -> None:
        self.starts = 0
        self.used = Decimal(0)
        self.in_flight_flags: list[bool] = []
        self.receipts: dict[UUID, RequestReceipt] = {}
        self.lose_ack = False
        self.ack_error: Exception = ConnectionError("Commit acknowledgement lost")

    async def begin(
        self,
        request_id: UUID,
        now: datetime,
        *,
        priceable: bool = True,
        in_flight: bool = False,
    ) -> bool:
        self.in_flight_flags.append(in_flight)
        if self.used >= Decimal(1):
            raise UsageLimitExceededError()
        self.starts += 1
        return True

    async def record(self, receipt: RequestReceipt) -> bool:
        if receipt.request_id not in self.receipts:
            self.receipts[receipt.request_id] = receipt
            self.used += receipt.cost or Decimal(0)
        else:
            assert self.receipts[receipt.request_id] == receipt
        if self.lose_ack:
            self.lose_ack = False
            raise self.ack_error
        return self.used >= Decimal(1)


async def test_each_request_commits_usage_before_another_budget_check() -> None:
    gateway = Accounting()
    meter = RequestMeter(gateway)
    request_id, occurred_at, limited = await meter.before(priceable=True)
    assert limited
    await meter.after(
        RequestReceipt(
            request_id=request_id,
            occurred_at=occurred_at,
            counts=TokenCounts(request_count=1),
            cost=Decimal("1.2"),
        )
    )
    assert gateway.used == Decimal("1.2")
    with pytest.raises(UsageLimitExceededError):
        await meter.before(priceable=True)
    assert gateway.starts == 1
    await meter.close()


async def test_lost_ack_replays_the_same_receipt_before_next_dispatch() -> None:
    gateway = Accounting()
    meter = RequestMeter(gateway)
    request_id, occurred_at, _ = await meter.before(priceable=True)
    gateway.lose_ack = True
    with pytest.raises(ConnectionError):
        await meter.after(
            RequestReceipt(
                request_id=request_id,
                occurred_at=occurred_at,
                counts=TokenCounts(request_count=1),
                cost=Decimal("1.2"),
            )
        )
    with pytest.raises(UsageLimitExceededError):
        await meter.before(priceable=True)
    assert gateway.starts == 1
    assert gateway.used == Decimal("1.2")
    assert not meter.pending
    await meter.close()


@pytest.mark.parametrize("provider_status", [None, 400], ids=["success", "rejection"])
async def test_settlement_timeout_does_not_repeat_provider_request(
    provider_status: int | None,
) -> None:
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from pydantic_ai.usage import RequestUsage

    from app.modules.usage.infrastructure.metered_model import MeteredModel
    from app.modules.usage.services.metering_scope import metering_execution
    from app.modules.usage.services.usage_context import UsageExecutionContext
    from app.modules.usage.services.usage_service import ModelPricing, UsageService

    provider_calls = 0

    async def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal provider_calls
        provider_calls += 1
        if provider_status is not None:
            raise ModelHTTPError(provider_status, "test")
        return ModelResponse(
            parts=[TextPart("successful response")],
            usage=RequestUsage(input_tokens=10, output_tokens=1),
        )

    model_name = f"accounting-timeout-{uuid4()}"
    profile: dict[str, object] = {
        "profile_id": "system:timeout-test",
        "scope": "SYSTEM",
        "model_name": model_name,
    }
    gateway = Accounting()
    gateway.lose_ack = True
    gateway.ack_error = TimeoutError("Accounting commit acknowledgement timed out")
    UsageService.register_model_pricing({model_name: ModelPricing(1000, 0)})
    try:
        async with metering_execution(
            UsageExecutionContext(user_id=uuid4(), organization_id=None, pod_id=None)
        ) as scope:
            meter, _ = scope.meter(profile, None)
            meter.gateway = gateway
            model = MeteredModel(FunctionModel(provider), profile)
            with pytest.raises(TimeoutError, match="Accounting commit"):
                await model.request([], None, ModelRequestParameters())
            assert provider_calls == 1
            assert gateway.starts == 1
            assert len(meter.pending) == 1
        assert not meter.pending
        assert len(gateway.receipts) == 1
        assert gateway.used == (
            Decimal(".01") if provider_status is None else Decimal(0)
        )
    finally:
        UsageService._SYSTEM_MODEL_PRICING.pop(model_name, None)


@pytest.mark.asyncio
async def test_a_run_already_under_way_is_not_refused_mid_answer() -> None:
    """The first request of a run may be refused. A continuation may not.

    Whether a request is priceable depends on the shape of its messages, and
    that changes mid-run: the opening prompt is priceable, the continuation
    carrying a tool's non-JSON result is not. Refusing the continuation ended
    the run partway through, after the earlier requests had already been
    spent and billed.
    """
    gateway = Accounting()
    meter = RequestMeter(gateway)

    await meter.before(priceable=True)
    await meter.before(priceable=False)

    assert gateway.in_flight_flags == [False, True]


class TestUnpricedLimitPolicy:
    """A limit a deployment cannot measure is not automatically a refusal.

    `enforceable` is only true when the price was matched through the model
    provider's own base URL, so every model served through an
    OpenAI-compatible gateway resolves the *vendor's* list price and is
    unenforceable -- `gpt-4o` included. A self-hoster who set any USD limit
    against such a gateway had every request refused, mid-run, by a message
    that named neither the model nor a fix.
    """

    def test_allow_is_the_default(self) -> None:
        """Refusing is the wrong answer for the deployment that hits this.

        A model behind an OpenAI-compatible gateway is never enforceable, so
        `refuse` turned a spend cap into a total outage for anyone self-hosting
        behind one. A deployment billing somebody else for the usage sets
        `refuse` deliberately, and is told at startup if it has not.
        """
        from app.modules.usage.config import UsageSettings

        assert UsageSettings().usage_unpriced_limit_policy == "allow"

    def test_the_policy_decides_whether_an_unpriced_request_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.usage.services import request_accounting_gateway as module

        gateway = module.PostgresRequestAccountingGateway

        monkeypatch.setattr(
            module.usage_settings, "usage_unpriced_limit_policy", "refuse"
        )
        assert gateway._refuses_unpriced()

        monkeypatch.setattr(
            module.usage_settings, "usage_unpriced_limit_policy", "allow"
        )
        assert not gateway._refuses_unpriced()


class TestLimitsArePossible:
    """Whether any monetary limit can apply is knowable before a request runs.

    That is the whole point: the condition that refused the request is
    deployment configuration, so it belongs in a startup report rather than in
    a 429 halfway through a conversation.
    """

    @pytest.fixture
    def unlimited(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
        """A deployment with no provider registered and no limit in settings."""
        from app.modules.usage.services import usage_limit_provider as module

        module.configure_usage_limit_provider(None)
        for field in (
            "usage_org_monthly_limit_usd",
            "usage_user_weekly_limit_usd",
            "usage_user_monthly_limit_usd",
        ):
            monkeypatch.setattr(module.usage_settings, field, None)
        yield module
        module.configure_usage_limit_provider(None)

    def test_no_provider_and_no_configured_limit_means_no_limit(
        self, unlimited: ModuleType
    ) -> None:
        assert not unlimited.usage_limits_are_possible()

    def test_a_configured_limit_is_enough(
        self, unlimited: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            unlimited.usage_settings, "usage_user_weekly_limit_usd", 10.0
        )

        assert unlimited.usage_limits_are_possible()

    def test_a_registered_billing_provider_is_enough(
        self, unlimited: ModuleType
    ) -> None:
        # Registered the way `lemma-cloud` registers one, not by reaching into
        # the module for the name it keeps the factory under.
        unlimited.configure_usage_limit_provider(lambda _uow: None)

        assert unlimited.usage_limits_are_possible()


class TestUnpriceableIsAlwaysReported:
    """An admitted request is a limit that is not binding, and says nothing.

    The report lived inside the refusal branch, so the moment `allow` became
    the default the case that most needs telling — a spend cap quietly not
    applying — was the one case that logged nothing at all.
    """

    @pytest.fixture
    def gateway(self) -> PostgresRequestAccountingGateway:
        """A gateway built for reporting alone.

        `_report_unpriceable` reads the rate card and its own once-flag and
        nothing else, so the three collaborators it never reaches are not stood
        up. Passing them as None keeps the real constructor in the test rather
        than skipping it.
        """
        return PostgresRequestAccountingGateway(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            RateCard(model="deepseek-v4-flash", provider="deepseek"),
            None,  # type: ignore[arg-type]
        )

    @staticmethod
    def _reports(caplog: pytest.LogCaptureFixture) -> list[dict]:
        return [
            record.msg
            for record in caplog.records
            if isinstance(record.msg, dict)
            and record.msg["event"]
            == "usage.request_accounting_gateway.request_not_priceable.degraded"
        ]

    def test_an_admitted_request_is_reported(
        self,
        gateway: PostgresRequestAccountingGateway,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        with caplog.at_level(logging.DEBUG):
            gateway._report_unpriceable(priceable=True, refused=False)

        reported = self._reports(caplog)
        assert len(reported) == 1
        assert reported[0]["refused"] is False
        assert reported[0]["model"] == "deepseek-v4-flash"

    def test_it_reports_once_per_gateway_not_once_per_request(
        self,
        gateway: PostgresRequestAccountingGateway,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One line per model per run, not a wall of them under a live agent."""
        import logging

        with caplog.at_level(logging.DEBUG):
            for _ in range(5):
                gateway._report_unpriceable(priceable=True, refused=False)

        assert len(self._reports(caplog)) == 1
