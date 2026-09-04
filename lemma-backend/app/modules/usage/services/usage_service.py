"""Usage tracking and limit service."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from uuid import UUID

from opentelemetry import metrics

from app.modules.usage.contracts import AgentRunUsage, ModelPricing
from app.modules.usage.domain.entities import (
    CostSource,
    UsageLimitCounterScope,
    UsageRecord,
    UsageReservation,
)
from app.modules.usage.services.cost_resolver import UsageTokens, resolve_cost
from app.modules.usage.services.limit_windows import (
    counter_scopes,
    limit_scope,
    month_start,
    next_month_start,
    week_start,
)
from app.modules.usage.domain.ports import (
    UsageLimitPort,
    UsageLimitValues,
    normalize_limit_values,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.events import (
    ModelUsageEvent,
    UsageLimitDeniedEvent,
)
from app.modules.usage.infrastructure.repositories import UsageRepository
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.pricing import UsagePricing

meter = metrics.get_meter(__name__)
# Every record is already durable in ``usage_records``; these make the same
# numbers chartable next to latency and error rate instead of only queryable
# per-trace. Labelled by model and token type only -- organization and pod stay
# out of the label set, because spend per tenant is a question for the table,
# not for a metric whose cardinality would then track the customer count.
token_counter = meter.create_counter("lemma.llm.tokens")
cost_counter = meter.create_counter("lemma.llm.cost_usd")


class UsageService(UsagePricing):
    """Service for profile-aware usage recording and system-profile limits."""

    DEFAULT_RESERVATION_USD = 0.01

    # Per-model rates (USD per 1M tokens). Keyed by both the public model name
    # and the provider model id so resolution succeeds on either. Starts empty;
    # provider-specific cloud modules register their pricing at startup via
    # ``register_model_pricing()``. Unpriced models are still metered, with a
    # null cost, and never fail solely because price metadata is absent.
    _SYSTEM_MODEL_PRICING: dict[str, ModelPricing] = {}
    _ENV_METADATA_SOURCE: str | None = None

    @classmethod
    def register_model_pricing(cls, pricing: dict[str, ModelPricing]) -> None:
        """Register additional per-model pricing (e.g. from a cloud module).

        Merges into the class-level pricing table; call at application startup
        before any agent runs. Keying by both the public name and the provider
        model id ensures ``_resolve_pricing`` resolves on either form.
        """
        cls._SYSTEM_MODEL_PRICING.update(pricing)

    def __init__(
        self,
        *,
        usage_repository: UsageRepository,
        usage_limit_port: UsageLimitPort | None = None,
    ):
        self.usage_repository = usage_repository
        self.usage_limit_port = usage_limit_port

    async def reserve_for_profile(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        profile_id: str,
        profile_scope: str,
        model_name: str,
        now: datetime | None = None,
    ) -> UsageReservation | None:
        if not self._is_system_scope(profile_scope):
            return None
        limit_values = await self._resolve_usage_limit_values(
            organization_id=organization_id,
            user_id=user_id,
        )
        if not self._has_applicable_limit(limit_values, organization_id):
            return None
        now = now or datetime.now(timezone.utc)
        amount = self.DEFAULT_RESERVATION_USD
        limits = await self.get_usage_limits(
            organization_id=organization_id,
            user_id=user_id,
            now=now,
            _limit_values=limit_values,
        )
        scopes: list[UsageLimitCounterScope] = []
        org_monthly = limits["org_monthly"]
        if organization_id is not None and org_monthly["limit_usd"] is not None:
            scopes.append(
                UsageLimitCounterScope(
                    organization_id=organization_id,
                    user_id=None,
                    window_kind="org_month",
                    window_start=org_monthly["window_start"],
                    window_end=org_monthly["reset_at"],
                    limit_usd=org_monthly["limit_usd"],
                    initial_used_usd=org_monthly["used_usd"],
                )
            )
        user_weekly = limits["user_weekly"]
        if user_weekly["limit_usd"] is not None:
            scopes.append(
                UsageLimitCounterScope(
                    organization_id=user_weekly["counter_organization_id"],
                    user_id=user_id,
                    window_kind="user_week",
                    window_start=user_weekly["window_start"],
                    window_end=user_weekly["reset_at"],
                    limit_usd=user_weekly["limit_usd"],
                    initial_used_usd=user_weekly["used_usd"],
                )
            )
        user_monthly = limits["user_monthly"]
        if user_monthly["limit_usd"] is not None:
            scopes.append(
                UsageLimitCounterScope(
                    organization_id=user_monthly["counter_organization_id"],
                    user_id=user_id,
                    window_kind="user_month",
                    window_start=user_monthly["window_start"],
                    window_end=user_monthly["reset_at"],
                    limit_usd=user_monthly["limit_usd"],
                    initial_used_usd=user_monthly["used_usd"],
                )
            )

        counter_ids = await self.usage_repository.reserve_limit_scopes(
            scopes=scopes,
            amount_usd=amount,
        )
        if counter_ids is None:
            self._collect_denied_event(
                organization_id=organization_id,
                user_id=user_id,
                profile_id=profile_id,
                model_name=model_name,
                reason="limit_exceeded",
            )
            raise UsageLimitExceededError()
        return UsageReservation(
            organization_id=organization_id,
            user_id=user_id,
            amount_usd=amount,
            counter_ids=counter_ids,
            remaining_usd=_tightest_remaining(limits),
        )

    async def release_reservation(self, reservation: UsageReservation | None) -> None:
        if reservation is None:
            return
        await self.release_reservation_handle(
            counter_ids=reservation.counter_ids,
            amount_usd=reservation.amount_usd,
        )

    async def release_reservation_handle(
        self,
        *,
        counter_ids: list[UUID],
        amount_usd: float,
    ) -> None:
        """Give back a reservation from its stored handle alone.

        For the caller that finds an abandoned reservation rather than holding
        one -- the orphan reconciler reads it off the dead run's row, where the
        surrounding `UsageReservation` (and the user it belonged to) is exactly
        the context that did not survive the crash.
        """
        await self.usage_repository.release_reservation(
            counter_ids=counter_ids,
            amount_usd=amount_usd,
        )

    async def record_agent_run_usage(
        self,
        *,
        ctx: UsageExecutionContext,
        runtime_profile: dict[str, object] | None,
        usage_data: AgentRunUsage,
        status: str | None,
        reservation: UsageReservation | None = None,
    ) -> UsageRecord | None:
        if (
            usage_data.input_tokens <= 0
            and usage_data.output_tokens <= 0
            and usage_data.units <= 0
        ):
            await self.release_reservation(reservation)
            return None

        profile_id = self._profile_value(runtime_profile, "profile_id") or "unknown"
        profile_scope = self._profile_value(runtime_profile, "scope") or "ORGANIZATION"
        model_name = (
            self._profile_value(runtime_profile, "model_name") or usage_data.model_name
        )
        priced = self._record_cost(
            runtime_profile=runtime_profile,
            model_name=model_name,
            usage_data=usage_data,
        )
        cost_usd = priced.cost_usd
        record = UsageRecord(
            organization_id=ctx.organization_id,
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            agent_run_id=ctx.agent_run_id,
            parent_agent_run_id=ctx.parent_agent_run_id,
            source_type=ctx.source_type,
            source_id=ctx.source_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
            usage_kind=usage_data.usage_kind,
            input_tokens=usage_data.input_tokens,
            output_tokens=usage_data.output_tokens,
            cached_input_tokens=priced.cache_read_tokens,
            cache_write_tokens=priced.cache_write_tokens,
            units=usage_data.units,
            cost_usd=cost_usd,
            cost_source=priced.cost_source,
            status=status,
            metadata=priced.metadata,
        )
        saved = await self.usage_repository.create(record)
        self._record_usage_metrics(
            model_name=model_name,
            usage_kind=usage_data.usage_kind,
            input_tokens=usage_data.input_tokens,
            output_tokens=usage_data.output_tokens,
            cost_usd=cost_usd,
        )
        if reservation is not None:
            actual_cost = cost_usd or 0.0
            await self.usage_repository.consume_reservation(
                counter_ids=reservation.counter_ids,
                reserved_usd=reservation.amount_usd,
                actual_usd=actual_cost,
            )
        self._collect_recorded_event(saved)
        return saved

    @staticmethod
    def _record_usage_metrics(
        *,
        model_name: str,
        usage_kind: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
    ) -> None:
        """Mirror one usage record onto the metrics pipeline.

        Split by token type rather than summed: input and output are priced
        differently and move independently, so one series for both hides the
        thing you would actually want to see. Cost is emitted separately
        because an unpriced model still meters tokens with a null cost, and
        adding zero there would understate spend rather than leave a gap.
        """
        labels = {
            "gen_ai.request.model": model_name,
            "operation": usage_kind,
        }
        if input_tokens > 0:
            token_counter.add(input_tokens, {**labels, "gen_ai.token.type": "input"})
        if output_tokens > 0:
            token_counter.add(output_tokens, {**labels, "gen_ai.token.type": "output"})
        if cost_usd:
            cost_counter.add(cost_usd, labels)

    async def record_pydantic_ai_result_usage(
        self,
        *,
        ctx: UsageExecutionContext,
        runtime_profile: dict[str, object] | None,
        result: object,
        status: str | None,
        usage_kind: str = "llm",
        reservation: UsageReservation | None = None,
        metadata: dict[str, object] | None = None,
    ) -> UsageRecord | None:
        usage_data = self.usage_from_pydantic_ai_result(
            result=result,
            runtime_profile=runtime_profile,
            usage_kind=usage_kind,
            metadata=metadata,
        )
        if usage_data is None:
            await self.release_reservation(reservation)
            return None
        return await self.record_agent_run_usage(
            ctx=ctx,
            runtime_profile=runtime_profile,
            usage_data=usage_data,
            status=status,
            reservation=reservation,
        )

    def usage_from_pydantic_ai_result(
        self,
        *,
        result: object,
        runtime_profile: dict[str, object] | None,
        usage_kind: str = "llm",
        metadata: dict[str, object] | None = None,
    ) -> AgentRunUsage | None:
        usage_method = getattr(result, "usage", None)
        if not callable(usage_method):
            return None
        run_usage = usage_method()
        input_tokens = self._usage_value(
            run_usage,
            "input_tokens",
            "request_tokens",
            "prompt_tokens",
        )
        output_tokens = self._usage_value(
            run_usage,
            "output_tokens",
            "response_tokens",
            "completion_tokens",
        )
        units = float(self._usage_value(run_usage, "units"))
        if input_tokens <= 0 and output_tokens <= 0 and units <= 0:
            return None
        model_name = (
            self._profile_value(runtime_profile, "model_name")
            or self._profile_value(runtime_profile, "provider_model_name")
            or "unknown"
        )
        # Read off `run_usage`, not off the caller's metadata dict. Every helper
        # path here (title, schedule filter, README) passed only a `helper` tag,
        # so cache reads were invisible and each of those calls was priced as if
        # nothing had been cached -- an overcharge on every one of them, on data
        # the object in hand was already carrying.
        return AgentRunUsage(
            model_name=model_name,
            usage_kind=usage_kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            units=units,
            request_count=self._usage_value(run_usage, "requests"),
            tool_call_count=self._usage_value(run_usage, "tool_calls"),
            metadata={
                **(metadata or {}),
                "cache_read_tokens": self._usage_value(run_usage, "cache_read_tokens"),
                "cache_write_tokens": self._usage_value(
                    run_usage, "cache_write_tokens"
                ),
            },
        )

    async def get_organization_usage_summary(
        self,
        organization_id: UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
    ):
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=30))
        return await self.usage_repository.get_usage_summary(
            organization_id=organization_id,
            start=start,
            end=end,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
            usage_kind=usage_kind,
            source_type=source_type,
            status=status,
        )

    async def get_usage_events(
        self,
        organization_id: UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        days: int = 30,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ):
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=days))
        return list(
            await self.usage_repository.list_usage(
                organization_id=organization_id,
                start=start,
                end=end,
                pod_id=pod_id,
                user_id=user_id,
                agent_id=agent_id,
                profile_id=profile_id,
                profile_scope=profile_scope,
                model_name=model_name,
                usage_kind=usage_kind,
                source_type=source_type,
                status=status,
                limit=limit,
            )
        )

    async def get_usage_stats(self, organization_id: UUID, **kwargs):
        return list(
            await self.usage_repository.get_usage_stats(
                organization_id=organization_id,
                **kwargs,
            )
        )

    async def get_usage_limits(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        now: datetime | None = None,
        _limit_values: UsageLimitValues | None = None,
    ) -> dict[str, object]:
        now = now or datetime.now(timezone.utc)
        window_month = month_start(now)
        window_week = week_start(now)
        limit_values = _limit_values or await self._resolve_usage_limit_values(
            organization_id=organization_id, user_id=user_id
        )
        user_limit_organization_id = (
            organization_id if limit_values.user_limit_scope == "organization" else None
        )
        excluded_organization_ids = (
            limit_values.excluded_organization_ids
            if limit_values.user_limit_scope == "global"
            else ()
        )
        # Six serial aggregates became three. The two user windows are one scan
        # with a FILTER apiece, and the reserved counters are one grouped read
        # of all three scopes; only the organization total keeps a statement of
        # its own, because its predicate is a whole org rather than one user in
        # it and folding it in would widen the scan to every user.
        #
        # Not cached, and not gathered. These numbers gate spending, so a cached
        # answer lets a caller overspend by the TTL; and six concurrent
        # checkouts from a pool_size=10, max_overflow=0 pool is a worse trade
        # than three sequential statements.
        org_used = 0.0
        if organization_id is not None:
            org_used = await self.usage_repository.get_system_cost(
                organization_id=organization_id,
                user_id=None,
                start=window_month,
                end=now,
            )
        user_used = await self.usage_repository.get_system_cost_by_window(
            organization_id=user_limit_organization_id,
            user_id=user_id,
            window_starts={"user_week": window_week, "user_month": window_month},
            end=now,
            exclude_organization_ids=excluded_organization_ids,
        )
        user_weekly_used = user_used["user_week"]
        user_monthly_used = user_used["user_month"]

        reserved = await self.usage_repository.get_reserved_costs(
            scopes=counter_scopes(
                organization_id=organization_id,
                user_limit_organization_id=user_limit_organization_id,
                user_id=user_id,
                now=now,
            )
        )
        org_reserved = reserved.get("org_month", 0.0)
        user_weekly_reserved = reserved["user_week"]
        user_monthly_reserved = reserved["user_month"]
        org_scope = limit_scope(
            limit_usd=limit_values.org_monthly_limit_usd,
            used_usd=org_used,
            reserved_usd=org_reserved,
            reset_at=next_month_start(now),
            window_start_at=window_month,
            scope="organization",
            counter_organization_id=organization_id,
        )
        user_weekly_scope = limit_scope(
            limit_usd=limit_values.user_weekly_limit_usd,
            used_usd=user_weekly_used,
            reserved_usd=user_weekly_reserved,
            reset_at=window_week + timedelta(days=7),
            window_start_at=window_week,
            scope=limit_values.user_limit_scope,
            counter_organization_id=user_limit_organization_id,
        )
        user_monthly_scope = limit_scope(
            limit_usd=limit_values.user_monthly_limit_usd,
            used_usd=user_monthly_used,
            reserved_usd=user_monthly_reserved,
            reset_at=next_month_start(now),
            window_start_at=window_month,
            scope=limit_values.user_limit_scope,
            counter_organization_id=user_limit_organization_id,
        )
        return {
            "organization_id": organization_id,
            "user_id": user_id,
            "org_monthly": org_scope,
            "user_weekly": user_weekly_scope,
            "user_monthly": user_monthly_scope,
            "allowed": bool(
                org_scope["allowed"]
                and user_weekly_scope["allowed"]
                and user_monthly_scope["allowed"]
            ),
        }

    async def _resolve_usage_limit_values(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> UsageLimitValues:
        # OSS metering is unlimited. A separately composed billing/plan module
        # opts into admission by implementing the usage-owned limit port.
        if self.usage_limit_port is None:
            return UsageLimitValues()
        return normalize_limit_values(
            await self.usage_limit_port.resolve_limits(
                organization_id=organization_id,
                user_id=user_id,
            )
        )

    @staticmethod
    def _has_applicable_limit(
        values: UsageLimitValues,
        organization_id: UUID | None,
    ) -> bool:
        return bool(
            (organization_id is not None and values.org_monthly_limit_usd is not None)
            or values.user_weekly_limit_usd is not None
            or values.user_monthly_limit_usd is not None
        )

    def _collect_recorded_event(self, record: UsageRecord) -> None:
        usage_kind = (
            record.usage_kind.value
            if hasattr(record.usage_kind, "value")
            else str(record.usage_kind)
        )
        profile_scope = (
            record.profile_scope.value
            if hasattr(record.profile_scope, "value")
            else str(record.profile_scope)
        )
        self.usage_repository.uow.collect_events(
            [
                ModelUsageEvent(
                    usage_id=record.id,
                    organization_id=record.organization_id,
                    pod_id=record.pod_id,
                    user_id=record.user_id,
                    agent_id=record.agent_id,
                    conversation_id=record.conversation_id,
                    agent_run_id=record.agent_run_id,
                    parent_agent_run_id=record.parent_agent_run_id,
                    source_type=record.source_type,
                    source_id=record.source_id,
                    profile_id=record.profile_id,
                    profile_scope=profile_scope,
                    model_name=record.model_name,
                    usage_kind=usage_kind,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    units=record.units,
                    cost_usd=record.cost_usd,
                    status=record.status,
                    metadata=record.metadata,
                    occurred_at=record.occurred_at,
                )
            ]
        )

    def _collect_denied_event(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        profile_id: str,
        model_name: str,
        reason: str,
    ) -> None:
        self.usage_repository.uow.collect_events(
            [
                UsageLimitDeniedEvent(
                    organization_id=organization_id,
                    user_id=user_id,
                    profile_id=profile_id,
                    model_name=model_name,
                    reason=reason,
                )
            ]
        )


def _tightest_remaining(limits: dict[str, object]) -> float | None:
    """The smallest remaining allowance across the windows that apply.

    The binding constraint is whichever window runs out first, so a run must
    bound itself by the minimum -- not by the organization's monthly figure that
    a weekly per-user cap will stop it reaching.
    """
    remaining = [
        scope["remaining_usd"]
        for key in ("org_monthly", "user_weekly", "user_monthly")
        for scope in [limits[key]]
        if isinstance(scope, dict) and scope.get("remaining_usd") is not None
    ]
    return min(remaining) if remaining else None


def assert_system_pricing_covers_catalog(
    model_names: Iterable[tuple[str, str | None]],
    *,
    pricing: Mapping[str, ModelPricing] | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Return the system models nothing can price (empty == all priceable).

    "Covered" now means *either* layer answers: a registered entry under the
    public name or the provider id, or the public dataset recognising the model.
    A model reaches this list only when both miss, and that is the case worth an
    operator's attention -- it meters with a null cost, so it counts toward no
    spend limit and is effectively free to run.

    Missing prices still never prevent metering or refuse a run (`PS-OPS-011`);
    this only reports completeness.
    """
    table = pricing if pricing is not None else UsageService._SYSTEM_MODEL_PRICING
    probe = UsageTokens(input_tokens=1)
    uncovered: list[str] = []
    for public_name, provider_name in model_names:
        resolved = resolve_cost(
            model_name=public_name,
            provider_model_name=provider_name,
            base_url=base_url,
            tokens=probe,
            pricing_table=dict(table),
        )
        if resolved.source is CostSource.UNKNOWN:
            uncovered.append(public_name or provider_name or "<unknown>")
    return uncovered
