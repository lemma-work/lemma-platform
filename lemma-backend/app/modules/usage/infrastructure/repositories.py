"""Usage repository implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.domain.entities import UsageRecord, UsageSummary
from app.modules.usage.domain.ports import UsageRepositoryPort
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord as UsageRecordModel


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
        stmt = stmt.order_by(UsageRecordModel.occurred_at.desc(), UsageRecordModel.id.desc())
        if limit:
            stmt = stmt.limit(limit)
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
        result = await self.session.execute(stmt)
        summary = UsageSummary(
            organization_id=organization_id,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            start_date=start,
            end_date=end,
            period_days=(end - start).days,
        )
        for record in result.scalars().all():
            summary.add_usage(record.to_entity())
        return summary

    async def get_system_cost(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        start: datetime,
        end: datetime,
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
        result = await self.session.execute(stmt)
        return float(result.scalar_one() or 0.0)

    async def get_reserved_cost(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        window_kind: str,
        window_start: datetime,
    ) -> float:
        stmt = select(func.coalesce(func.sum(UsageLimitCounter.reserved_usd), 0.0)).where(
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

    async def reserve_counter(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        window_kind: str,
        window_start: datetime,
        window_end: datetime,
        amount_usd: float,
    ) -> UUID:
        stmt = select(UsageLimitCounter).where(
            UsageLimitCounter.window_kind == window_kind,
            UsageLimitCounter.window_start == window_start,
        )
        if organization_id is None:
            stmt = stmt.where(UsageLimitCounter.organization_id.is_(None))
        else:
            stmt = stmt.where(UsageLimitCounter.organization_id == organization_id)
        if user_id is None:
            stmt = stmt.where(UsageLimitCounter.user_id.is_(None))
        else:
            stmt = stmt.where(UsageLimitCounter.user_id == user_id)
        result = await self.session.execute(stmt.with_for_update())
        counter = result.scalars().first()
        if counter is None:
            counter = UsageLimitCounter(
                organization_id=organization_id,
                user_id=user_id,
                window_kind=window_kind,
                window_start=window_start,
                window_end=window_end,
                used_usd=0.0,
                reserved_usd=0.0,
            )
            self.session.add(counter)
            await self.session.flush()
        counter.reserved_usd = max(0.0, float(counter.reserved_usd or 0.0) + amount_usd)
        await self.session.flush()
        return counter.id

    async def release_reservation(
        self,
        *,
        counter_ids: list[UUID],
        amount_usd: float,
    ) -> None:
        if not counter_ids:
            return
        stmt = select(UsageLimitCounter).where(UsageLimitCounter.id.in_(counter_ids))
        result = await self.session.execute(stmt.with_for_update())
        for counter in result.scalars().all():
            counter.reserved_usd = max(
                0.0,
                float(counter.reserved_usd or 0.0) - amount_usd,
            )
        await self.session.flush()

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
        bucket = func.date_trunc(granularity, UsageRecordModel.occurred_at).label("bucket")
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

        columns = [
            bucket,
            func.sum(UsageRecordModel.input_tokens).label("input_tokens"),
            func.sum(UsageRecordModel.output_tokens).label("output_tokens"),
            func.sum(UsageRecordModel.units).label("units"),
            func.coalesce(func.sum(UsageRecordModel.cost_usd), 0.0).label("system_cost_usd"),
        ]
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
            input_tokens = int(row.input_tokens or 0)
            output_tokens = int(row.output_tokens or 0)
            item = {
                "bucket": row.bucket,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "units": float(row.units or 0.0),
                "system_cost_usd": float(row.system_cost_usd or 0.0),
            }
            if group_column is not None:
                item["group"] = str(row.group) if row.group is not None else None
            rows.append(item)
        return rows
