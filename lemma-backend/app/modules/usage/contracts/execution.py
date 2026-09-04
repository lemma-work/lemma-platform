"""What a model runtime needs from usage: admit a run, then account for it.

`contracts/__init__.py` is a leaf — its own domain, stdlib and pydantic, nothing
else — because everything that imports any contract pays for whatever it pulls
in. Operations reach this module's services, so they live here instead, which is
the same reason `connectors/contracts/retirement.py` is a submodule.

This exists because `agent` was reaching
`usage.services.{pydantic_ai_tracking,usage_context,usage_service,usage_service_factory}`
through `app/composition/agent_usage.py`. Nothing about that was usage's
decision: a re-export in a third place made four service module paths part of
the agent module's build, so moving a function between them broke agent. The
surface below is the part usage means to keep stable.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai import UsageLimits

from app.modules.usage.contracts import UsageReservation
from app.modules.usage.services.pydantic_ai_tracking import (
    record_agent_run_usage,
    record_pydantic_ai_result_usage,
    reserve_usage_for_runtime,
)
from app.modules.usage.services.usage_context import (
    UsageExecutionContext,
    current_usage_context,
    usage_context_from_agent_context,
    usage_execution_context,
)
from app.modules.usage.services.usage_service import (
    UsageService,
    assert_system_pricing_covers_catalog,
)
from app.modules.usage.services.usage_service_factory import build_usage_service

__all__ = [
    "UsageExecutionContext",
    "UsageService",
    "assert_system_pricing_covers_catalog",
    "build_usage_service",
    "current_usage_context",
    "record_agent_run_usage",
    "record_pydantic_ai_result_usage",
    "reserve_usage_for_runtime",
    "spend_bounded_usage_limits",
    "usage_context_from_agent_context",
    "usage_execution_context",
]


def spend_bounded_usage_limits(
    base: "UsageLimits",
    *,
    reservation: UsageReservation | None,
    runtime_profile: dict[str, object | None] | None,
) -> "UsageLimits":
    """``base``, additionally capped at what the caller can still afford.

    Admission alone cannot bound spend: it reserves a fixed few cents and then
    the run is free to spend whatever it likes, so somebody one cent inside their
    limit could start a run that costs hundreds. This turns the remaining
    allowance into a token cap the model runtime enforces itself.

    Returned unchanged -- i.e. the run stays unbounded -- when no limit applies,
    when the profile is not the deployment's own, or when the model cannot be
    priced. The last one is `PS-OPS-011`: a gap in the pricing table must never
    become a refusal.

    Enforcement is after each request, not before it, because an
    OpenAI-compatible model cannot count tokens up front (see
    ``usage_limits_for``). So the guarantee is "stops at the first request that
    crosses the line", not "never crosses it": the overshoot is one request
    rather than one whole run.
    """
    from app.modules.usage.services.cost_resolver import tokens_for_budget

    if reservation is None or reservation.remaining_usd is None:
        return base
    profile = runtime_profile if isinstance(runtime_profile, dict) else {}
    budget = tokens_for_budget(
        model_name=str(profile.get("model_name") or ""),
        provider_model_name=_optional_str(profile.get("provider_model_name")),
        base_url=_profile_base_url(profile),
        budget_usd=reservation.remaining_usd,
        pricing_table=UsageService._SYSTEM_MODEL_PRICING,
    )
    if budget is None:
        return base
    input_limit, output_limit = budget
    return replace(
        base,
        input_tokens_limit=_tighter(base.input_tokens_limit, input_limit),
        output_tokens_limit=_tighter(base.output_tokens_limit, output_limit),
    )


def _tighter(existing: int | None, candidate: int) -> int:
    return candidate if existing is None else min(existing, candidate)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _profile_base_url(profile: dict[str, object | None]) -> str | None:
    config = profile.get("config")
    if not isinstance(config, dict):
        return None
    return _optional_str(config.get("base_url"))
