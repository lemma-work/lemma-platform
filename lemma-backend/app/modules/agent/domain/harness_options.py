"""What one harness execution is given, apart from the conversation itself.

Its own module because ``value_objects`` sits against the architecture ratchet's
file-size limit and this is the piece that grows: every capability a run can be
given — a usage ceiling, a stop signal, a spend budget — arrives here first.

Imports from ``value_objects`` and is never imported back by it: the dependency
has to run one way, or the two form a cycle the module graph check would reject.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic_ai import UsageLimits
from pydantic_ai.capabilities import AgentCapability
from pydantic_ai.output import OutputSpec
from pydantic_ai.toolsets import AbstractToolset

from app.modules.agent.domain.run_budget import RunSpend
from app.modules.agent.domain.run_notices import RunNotices
from app.modules.agent.domain.value_objects import (
    DEFAULT_HISTORY_HARD_TOKEN_CEILING,
    DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES,
    DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT,
    JsonObject,
)


@dataclass(slots=True)
class HarnessOptions[DepsT = object]:
    """Dependency-injected options for one harness execution."""

    model_name: str
    toolsets: list[AbstractToolset[DepsT]] = field(default_factory=list)
    # Remote harnesses use MCP; only the in-process harness consumes capabilities.
    capabilities: list[AgentCapability[DepsT]] = field(default_factory=list)
    usage_limits: UsageLimits | None = None
    output_type: OutputSpec[object] | None = None
    model_settings: JsonObject | None = None
    history_summarization_enabled: bool = True
    history_summarization_token_limit: int = DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT
    history_summarization_keep_messages: int = (
        DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES
    )
    history_hard_token_ceiling: int = DEFAULT_HISTORY_HARD_TOKEN_CEILING
    should_stop: Callable[[], Awaitable[bool]] | None = None
    # What this run may spend before it pauses to ask whether to carry on. None
    # leaves it uncapped, which is what every caller did before budgets existed
    # and what a test that is not about budgets still wants.
    spend: RunSpend | None = None
    #: Where a threshold posts what the run should be told. Always present: the
    #: things that post to it are optional, delivering nothing is free, and an
    #: optional mailbox would put a None check at every posting site.
    notices: RunNotices = field(default_factory=RunNotices)
    extra: JsonObject = field(default_factory=dict)
