"""Usage repository implementations."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import func, or_, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.domain.entities import (
    UsageLimitCounterScope,
    UsageRecord,
    UsageSummary,
)
from app.modules.usage.domain.ports import UsageRepositoryPort
from app.modules.usage.infrastructure.limit_counters import (
    consume_reservation,
    release_reservation,
    reserve_limit_scopes,
)
from app.modules.usage.infrastructure.usage_aggregates import (
    aggregate_row_to_dict,
    token_and_cost_columns,
)
from app.modules.usage.infrastructure.usage_limit_reads import (
    reserved_costs,
    system_cost_by_window,
)
from app.modules.usage.infrastructure.models import (
    UsageLimitCounter,
    UsageRecord as UsageRecordModel,
)

#: Hard ceiling on a usage listing, applied whatever the caller asks for.
#: ``usage_records`` gains a row per model call and is never pruned, so an
#: unbounded listing is a table export wearing an endpoint's clothes.
MAX_USAGE_PAGE_SIZE = 1_000


class UsageRepository(UsageRepositoryPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create(self, entity: UsageRecord) -> UsageRecord:
        record = UsageRecordModel.from_entity(entity)
        self.session.add(record)
        await self.session.flush()
        return record.to_entity()

    def _apply_filters(
        self,
        stmt,
        *,
        organization_id: UUID | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        system_cost_only: bool = False,
    ):
        if organization_id is not None:
            stmt = stmt.where(UsageRecordModel.organization_id == organization_id)
        if pod_id is not None:
            stmt = stmt.where(UsageRecordModel.pod_id == pod_id)
        if user_id is not None:
            stmt = stmt.where(UsageRecordModel.user_id == user_id)
        if agent_id is not None:
            stmt = stmt.where(UsageRecordModel.agent_id == agent_id)
        if profile_id:
            stmt = stmt.where(UsageRecordModel.profile_id == profile_id)
        if profile_scope:
            stmt = stmt.where(UsageRecordModel.profile_scope == profile_scope)
        if model_name:
            stmt = stmt.where(UsageRecordModel.model_name == model_name)
        if usage_kind:
            # Case-insensitive: usage_kind is stored lowercase ("llm") by every
            # writer, but the UsageKind enum value is "LLM" — a consumer filtering
            # by the enum value must still match. Normalize both sides.
            stmt = stmt.where(
                func.lower(UsageRecordModel.usage_kind) == usage_kind.lower()
            )
        if source_type:
            stmt = stmt.where(UsageRecordModel.source_type == source_type)
        if status:
            stmt = stmt.where(UsageRecordModel.status == status)
        if system_cost_only:
            stmt = stmt.where(
                UsageRecordModel.profile_scope == "SYSTEM",
                UsageRecordModel.cost_usd.is_not(None),
            )
        return stmt

    async def list_usage(
        self,
        *,
        organization_id: UUID,
        start: datetime,
        end: datetime,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> Sequence[UsageRecord]:
        stmt = select(UsageRecordModel).where(
            UsageRecordModel.occurred_at >= start,
            UsageRecordModel.occurred_at <= end,
        )
        stmt = self._apply_filters(
            stmt,
            organization_id=organization_id,
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
        # Always bounded. ``limit`` was optional, so a caller passing None or 0
        # asked for every record in the window — on the table that gains a row
        # per model call, that is unbounded by construction. The ceiling applies
        # even when a caller names a larger one; a listing endpoint is not the
        # way to export the whole table.
        stmt = stmt.order_by(
            UsageRecordModel.occurred_at.desc(), UsageRecordModel.id.desc()
        ).limit(min(limit or MAX_USAGE_PAGE_SIZE, MAX_USAGE_PAGE_SIZE))
        result = await self.session.execute(stmt)
        return [record.to_entity() for record in result.scalars().all()]

    async def get_usage_summary(
        self,
        *,
        organization_id: UUID | None,
        start: datetime,
        end: datetime,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
    ) -> UsageSummary:
        """Totals for a window, aggregated by the database.

        This used to select every matching row, hydrate each into a Pydantic
        entity and add it up in Python. One row per model call means a busy
        organization's 90-day summary pulled hundreds of thousands of rows
        across the wire to produce a few dozen numbers, and it got slower every
        day the table grew.

        Three grouped aggregates answer the same question in a fixed number of
        rows — one per profile, model and kind actually used — so the cost now
        tracks how many distinct things were used, not how often.
        """
        filters = {
            "organization_id": organization_id,
            "pod_id": pod_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "profile_id": profile_id,
            "profile_scope": profile_scope,
            "model_name": model_name,
            "usage_kind": usage_kind,
            "source_type": source_type,
            "status": status,
        }
        summary = UsageSummary(
            organization_id=organization_id,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            start_date=start,
            end_date=end,
            period_days=(end - start).days,
        )
        for target, column in (
            (summary.total_by_profile, UsageRecordModel.profile_id),
            (summary.total_by_model, UsageRecordModel.model_name),
            (summary.total_by_kind, UsageRecordModel.usage_kind),
        ):
            for row in await self._grouped_totals(
                column, start=start, end=end, filters=filters
            ):
                target[row.key] = aggregate_row_to_dict(row, with_record_count=True)
        # Overall totals come from one of the groupings rather than a fourth
        # query: every record lands in exactly one model bucket, so the bucket
        # sums are the window sums.
        for bucket in summary.total_by_model.values():
            summary.total_input_tokens += int(bucket["input_tokens"])
            summary.total_output_tokens += int(bucket["output_tokens"])
            summary.total_cached_input_tokens += int(bucket["cached_input_tokens"])
            summary.total_cache_write_tokens += int(bucket["cache_write_tokens"])
            summary.total_units += float(bucket["units"])
            summary.system_cost_usd += float(bucket["system_cost_usd"])
            summary.total_cost_usd += float(bucket["total_cost_usd"])
        return summary

    async def _grouped_totals(
        self,
        group_column,
        *,
        start: datetime,
        end: datetime,
        filters: dict,
    ):
        stmt = select(
            group_column.label("key"),
            *token_and_cost_columns(with_record_count=True),
        ).where(
            UsageRecordModel.occurred_at >= start,
            UsageRecordModel.occurred_at <= end,
        )
        stmt = self._apply_filters(stmt, **filters).group_by(group_column)
        return (await self.session.execute(stmt)).all()

    async def get_system_cost(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        start: datetime,
        end: datetime,
        exclude_organization_ids: Sequence[UUID] = (),
    ) -> float:
        stmt = select(func.coalesce(func.sum(UsageRecordModel.cost_usd), 0.0)).where(
            UsageRecordModel.occurred_at >= start,
            UsageRecordModel.occurred_at <= end,
        )
        stmt = self._apply_filters(
            stmt,
            organization_id=organization_id,
            user_id=user_id,
            system_cost_only=True,
        )
        if exclude_organization_ids:
            stmt = stmt.where(
                or_(
                    UsageRecordModel.organization_id.is_(None),
                    UsageRecordModel.organization_id.notin_(
                        tuple(exclude_organization_ids)
                    ),
                )
            )
        result = await self.session.execute(stmt)
        return float(result.scalar_one() or 0.0)

    async def get_system_cost_by_window(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        window_starts: Mapping[str, datetime],
        end: datetime,
        exclude_organization_ids: Sequence[UUID] = (),
    ) -> dict[str, float]:
        """``get_system_cost`` for several windows over the same rows."""
        return await system_cost_by_window(
            self.session,
            organization_id=organization_id,
            user_id=user_id,
            window_starts=window_starts,
            end=end,
            exclude_organization_ids=exclude_organization_ids,
            apply_filters=self._apply_filters,
        )

    async def get_reserved_costs(
        self,
        *,
        scopes: Sequence[tuple[UUID | None, UUID | None, str, datetime]],
    ) -> dict[str, float]:
        """``get_reserved_cost`` for several scopes, keyed by window kind."""
        return await reserved_costs(self.session, scopes=scopes)

    async def get_reserved_cost(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        window_kind: str,
        window_start: datetime,
    ) -> float:
        stmt = select(
            func.coalesce(func.sum(UsageLimitCounter.reserved_usd), 0.0)
        ).where(
            UsageLimitCounter.window_kind == window_kind,
            UsageLimitCounter.window_start == window_start,
        )
        if organization_id is not None:
            stmt = stmt.where(UsageLimitCounter.organization_id == organization_id)
        else:
            stmt = stmt.where(UsageLimitCounter.organization_id.is_(None))
        if user_id is not None:
            stmt = stmt.where(UsageLimitCounter.user_id == user_id)
        else:
            stmt = stmt.where(UsageLimitCounter.user_id.is_(None))
        result = await self.session.execute(stmt)
        return float(result.scalar_one() or 0.0)

    async def reserve_limit_scopes(
        self,
        *,
        scopes: list[UsageLimitCounterScope],
        amount_usd: float,
    ) -> list[UUID] | None:
        return await reserve_limit_scopes(
            self.session, scopes=scopes, amount_usd=amount_usd
        )

    async def release_reservation(
        self,
        *,
        counter_ids: list[UUID],
        amount_usd: float,
    ) -> None:
        await release_reservation(
            self.session, counter_ids=counter_ids, amount_usd=amount_usd
        )

    async def consume_reservation(
        self,
        *,
        counter_ids: list[UUID],
        reserved_usd: float,
        actual_usd: float,
    ) -> None:
        await consume_reservation(
            self.session,
            counter_ids=counter_ids,
            reserved_usd=reserved_usd,
            actual_usd=actual_usd,
        )

    async def get_usage_stats(
        self,
        *,
        organization_id: UUID,
        start: datetime,
        end: datetime,
        granularity: str = "day",
        group_by: str | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
    ) -> Sequence[dict[str, object]]:
        if granularity not in {"hour", "day", "week"}:
            granularity = "day"
        bucket = func.date_trunc(granularity, UsageRecordModel.occurred_at).label(
            "bucket"
        )
        group_column = None
        if group_by == "profile":
            group_column = UsageRecordModel.profile_id.label("group")
        elif group_by == "model":
            group_column = UsageRecordModel.model_name.label("group")
        elif group_by == "user":
            group_column = UsageRecordModel.user_id.label("group")
        elif group_by == "pod":
            group_column = UsageRecordModel.pod_id.label("group")
        elif group_by == "agent":
            group_column = UsageRecordModel.agent_id.label("group")
        elif group_by == "kind":
            group_column = UsageRecordModel.usage_kind.label("group")
        elif group_by == "source":
            group_column = UsageRecordModel.source_type.label("group")

        columns = [bucket, *token_and_cost_columns()]
        if group_column is not None:
            columns.insert(1, group_column)
        stmt = select(*columns).where(
            UsageRecordModel.organization_id == organization_id,
            UsageRecordModel.occurred_at >= start,
            UsageRecordModel.occurred_at <= end,
        )
        stmt = self._apply_filters(
            stmt,
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
        stmt = stmt.group_by(bucket)
        if group_column is not None:
            stmt = stmt.group_by(bucket, group_column)
        stmt = stmt.order_by(bucket.desc())
        result = await self.session.execute(stmt)
        rows = []
        for row in result.all():
            item = {"bucket": row.bucket, **aggregate_row_to_dict(row)}
            if group_column is not None:
                item["group"] = str(row.group) if row.group is not None else None
            rows.append(item)
        return rows
