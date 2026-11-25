"""How many model tokens one agent run used, read back from the ledger.

For a reader that meters work per item -- a plugin that runs an agent once per
record and wants to know what that record cost in tokens. Tokens only: what
they cost depends on whose provider profile paid, and a pod's builder is not
the person who sees the organization's bill.

Summed over every model call recorded against the run: the in-process harness
writes one row per request as it streams, an Agent Host one row when the run
ends. A run still working reads what it has spent so far.

``ledger_totals_by_run`` and ``ledger_totals`` answer the same question with
cost, for a conversation's owner reading their own spend: the rows are held to
the reader's ``user_id``, so they are rows that person's own usage report reads
too, with no date window and no organization left out.
"""

from __future__ import annotations

from collections.abc import Collection
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.infrastructure.cost_expressions import recorded_cost
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


class LedgerTotals(BaseModel):
    """What a set of ledger rows cost, and the model tokens among them."""

    cost_usd: float = Field(
        default=0.0,
        description=(
            "What every recorded call cost, in US dollars, whichever provider "
            "profile paid for it. A call recorded without a price adds nothing."
        ),
    )
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
    model_calls: int = Field(
        default=0,
        description=(
            "Model calls recorded: one per request for a run Lemma answers "
            "itself; an Agent Host reports each turn's calls as one."
        ),
    )


def _is_model_call() -> ColumnElement[bool]:
    return func.lower(UsageRecord.usage_kind) == "llm"


#: Cost, input, output, cached and cache-write tokens, model calls.
type _TotalsColumns = tuple[
    ColumnElement[Decimal],
    ColumnElement[int],
    ColumnElement[int],
    ColumnElement[int],
    ColumnElement[int],
    ColumnElement[int],
]


def _totals_columns() -> _TotalsColumns:
    # Cost over every kind (images, machine time); tokens and calls over model
    # calls only, which is all the token columns are filled for.
    is_model_call = _is_model_call()
    return (
        func.coalesce(func.sum(recorded_cost()), Decimal(0)),
        func.coalesce(func.sum(UsageRecord.input_tokens).filter(is_model_call), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens).filter(is_model_call), 0),
        func.coalesce(
            func.sum(func.coalesce(UsageRecord.cached_input_tokens, 0)).filter(
                is_model_call
            ),
            0,
        ),
        func.coalesce(
            func.sum(func.coalesce(UsageRecord.cache_write_tokens, 0)).filter(
                is_model_call
            ),
            0,
        ),
        func.count().filter(is_model_call),
    )


def _totals_of(
    cost: Decimal | float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached: int | None,
    cache_writes: int | None,
    calls: int | None,
) -> LedgerTotals:
    return LedgerTotals(
        cost_usd=float(cost or 0),
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cached_input_tokens=int(cached or 0),
        cache_write_tokens=int(cache_writes or 0),
        model_calls=int(calls or 0),
    )


async def ledger_totals_by_run(
    session: AsyncSession,
    agent_run_ids: Collection[UUID],
    *,
    user_id: UUID,
) -> dict[UUID, LedgerTotals]:
    """Each run's cost and tokens, as recorded against ``user_id``.

    A run with no row for that person is absent from the result: nothing was
    reported, which a reader must not show as zero.
    """
    if not agent_run_ids:
        return {}
    rows = (
        await session.execute(
            select(UsageRecord.agent_run_id, *_totals_columns())
            .where(
                UsageRecord.agent_run_id.in_(list(agent_run_ids)),
                UsageRecord.user_id == user_id,
            )
            .group_by(UsageRecord.agent_run_id)
        )
    ).all()
    return {
        run_id: _totals_of(*totals) for run_id, *totals in rows if run_id is not None
    }


async def ledger_totals(
    session: AsyncSession,
    agent_run_ids: Select[tuple[UUID]],
    *,
    conversation_id: UUID,
    user_id: UUID,
) -> tuple[LedgerTotals, int]:
    """The summed cost and tokens of a conversation, as recorded against
    ``user_id``, and how many of its runs have any row.

    The runs ``agent_run_ids`` selects, and the rows the conversation holds
    with no run: its title and the memory kept after a run are written after
    the run, against the conversation alone.
    """
    row = (
        await session.execute(
            select(
                *_totals_columns(),
                func.count(func.distinct(UsageRecord.agent_run_id)),
            ).where(
                or_(
                    UsageRecord.agent_run_id.in_(agent_run_ids),
                    UsageRecord.conversation_id == conversation_id,
                ),
                UsageRecord.user_id == user_id,
            )
        )
    ).one()
    *totals, reported_runs = row
    return _totals_of(*totals), int(reported_runs or 0)


__all__ = [
    "LedgerTotals",
    "RunTokenUsage",
    "ledger_totals",
    "ledger_totals_by_run",
    "run_token_usage",
]
