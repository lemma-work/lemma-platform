"""Read-only usage reports, shared by administrative and self-service views."""

from datetime import datetime, timedelta, timezone
from typing import Unpack
from uuid import UUID

from app.modules.usage.domain.entities import UsageRecord, UsageSummary
from app.modules.usage.domain.query_types import UsageStatsQuery, UsageStatsBucket
from app.modules.usage.infrastructure.repositories import UsageRepository


class UsageReporting:
    usage_repository: UsageRepository

    async def get_organization_usage_summary(
        self,
        organization_id: UUID | None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        conversation_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        exclude_organization_ids: tuple[UUID, ...] = (),
    ) -> UsageSummary:
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=30))
        return await self.usage_repository.get_usage_summary(
            organization_id=organization_id,
            start=start,
            end=end,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
            usage_kind=usage_kind,
            source_type=source_type,
            status=status,
            exclude_organization_ids=exclude_organization_ids,
        )

    async def get_usage_events(
        self,
        organization_id: UUID | None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        days: int = 30,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        conversation_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        exclude_organization_ids: tuple[UUID, ...] = (),
        limit: int = 100,
    ) -> list[UsageRecord]:
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
                agent_run_id=agent_run_id,
                conversation_id=conversation_id,
                profile_id=profile_id,
                profile_scope=profile_scope,
                model_name=model_name,
                usage_kind=usage_kind,
                source_type=source_type,
                status=status,
                exclude_organization_ids=exclude_organization_ids,
                limit=limit,
            )
        )

    async def get_usage_stats(
        self, organization_id: UUID | None, **kwargs: Unpack[UsageStatsQuery]
    ) -> list[UsageStatsBucket]:
        return list(
            await self.usage_repository.get_usage_stats(
                organization_id=organization_id,
                **kwargs,
            )
        )
