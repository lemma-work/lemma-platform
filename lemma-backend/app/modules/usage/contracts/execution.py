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

from app.modules.usage.services.pydantic_ai_tracking import (
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
    "current_usage_context",
    "UsageService",
    "assert_system_pricing_covers_catalog",
    "build_usage_service",
    "record_pydantic_ai_result_usage",
    "reserve_usage_for_runtime",
    "usage_context_from_agent_context",
    "usage_execution_context",
]
