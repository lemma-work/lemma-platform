"""How many model tokens one agent run used, read back from the ledger.

For a reader that meters work per item -- a plugin that runs an agent once per
record and wants to know what that record cost in tokens. Tokens only: what
they cost depends on whose provider profile paid, and a pod's builder is not
the person who sees the organization's bill.

Summed over every model call recorded against the run: the in-process harness
writes one row per request as it streams, an Agent Host one row when the run
ends. A run still working reads what it has spent so far.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.infrastructure.models import UsageRecord


class RunTokenUsage(BaseModel):
    """Model tokens one run used, summed over its model calls."""

    input_tokens: int = Field(
        default=0,
        description=(
            "Tokens sent to the model, cached ones included "
            "(cached_input_tokens says how many of these were cache reads)."
        ),
    )
    output_tokens: int = Field(default=0, description="Tokens the model wrote.")
    cached_input_tokens: int = Field(
        default=0, description="Input tokens read from the provider's prompt cache."
    )
    cache_write_tokens: int = Field(
        default=0, description="Input tokens written to the provider's prompt cache."
    )
    models: list[str] = Field(
        default_factory=list,
        description="Every model the run's calls were recorded against, by name.",
    )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


async def run_token_usage(
    session: AsyncSession,
    agent_run_id: UUID,
    *,
    exclude_sources: Collection[str] = (),
) -> RunTokenUsage | None:
    """The run's tokens, or ``None`` when nothing was recorded against it.

    ``exclude_sources`` leaves out work done on the run's behalf that is not
    the run answering -- a conversation's title, the memory kept after it --
    which lands later and would make the same run read differently twice.
    """
    statement = select(
        UsageRecord.model_name,
        func.sum(UsageRecord.input_tokens),
        func.sum(UsageRecord.output_tokens),
        func.sum(func.coalesce(UsageRecord.cached_input_tokens, 0)),
        func.sum(func.coalesce(UsageRecord.cache_write_tokens, 0)),
    ).where(
        UsageRecord.agent_run_id == agent_run_id,
        func.lower(UsageRecord.usage_kind) == "llm",
    )
    if exclude_sources:
        statement = statement.where(UsageRecord.source_type.not_in(exclude_sources))
    rows = (
        await session.execute(
            statement.group_by(UsageRecord.model_name).order_by(UsageRecord.model_name)
        )
    ).all()
    if not rows:
        return None
    usage = RunTokenUsage()
    for model_name, input_tokens, output_tokens, cached, cache_writes in rows:
        usage.models.append(str(model_name))
        usage.input_tokens += int(input_tokens or 0)
        usage.output_tokens += int(output_tokens or 0)
        usage.cached_input_tokens += int(cached or 0)
        usage.cache_write_tokens += int(cache_writes or 0)
    return usage


__all__ = ["RunTokenUsage", "run_token_usage"]
