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

from app.modules.agent.domain.run_budget import RunSpend
from app.modules.agent.domain.run_log import RunLogReplay
from app.modules.agent.domain.run_notices import RunNotices
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.domain.tools import ToolSpec
from app.modules.agent.domain.value_objects import (
    DEFAULT_HISTORY_HARD_TOKEN_CEILING,
    DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES,
    DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT,
    JsonObject,
)


@dataclass(slots=True)
class HarnessOptions:
    """Dependency-injected options for one harness execution, in Lemma's types."""

    model_name: str
    #: The run's tools (``services/tool_catalog``): the in-process harness
    #: builds its toolsets from them, a remote one names them in its run spec.
    tools: list[ToolSpec] = field(default_factory=list)
    model_settings: JsonObject | None = None
    #: Request parameters for the history-compaction call: the run's own,
    #: turned down to low effort and a summary-sized output cap. See
    #: ``services/generation_settings.compaction_generation_settings``. None
    #: when nothing resolved them; the summary then gets only its cap.
    compaction_model_settings: JsonObject | None = None
    #: A light reasoning effort as this model takes one, laid over the run's
    #: settings when an over-long answer is asked for again: empty unless the
    #: model declares REASONING. See ``run_input_settings.shorter_answer_settings``.
    lighter_reasoning_settings: JsonObject | None = None
    history_summarization_enabled: bool = True
    history_summarization_token_limit: int = DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT
    history_summarization_keep_messages: int = (
        DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES
    )
    history_hard_token_ceiling: int = DEFAULT_HISTORY_HARD_TOKEN_CEILING
    #: The model's whole window, which a request's reply budget is fitted into
    #: once its instructions, tools and history are known. 0 leaves the reply
    #: budget as the settings give it.
    context_window: int = 0
    should_stop: Callable[[], Awaitable[bool]] | None = None
    #: Whether to claim what the person sent to the run now: signalled to it,
    #: or overdue (``services/run_signals``). None claims before every node.
    steer_due: Callable[[], bool] | None = None
    # What this run may spend before it pauses to ask whether to carry on. None
    # leaves it uncapped, which is what every caller did before budgets existed
    # and what a test that is not about budgets still wants.
    spend: RunSpend | None = None
    #: Where a threshold posts what the run should be told. Always present: the
    #: things that post to it are optional, delivering nothing is free, and an
    #: optional mailbox would put a None check at every posting site.
    notices: RunNotices = field(default_factory=RunNotices)
    #: The runtime this run resolved to. Typed here rather than carried in
    #: ``extra``, where every reader re-checked a dict and compared its
    #: protocol as a string.
    runtime_profile: RuntimeSnapshot | None = None
    #: Whether the worker running this turn still holds it, asked before each
    #: tool call; a run on the work runtime stops when it says no. None for a
    #: run nothing leases (a test, the API's own).
    lease_alive: Callable[[], bool] | None = None
    #: The run's place in its conversation, 1 for the first: what each model
    #: request's prompt-cache report is read against. None when unknown.
    run_index: int | None = None
    #: The conversation's run log and what to take in from outside it. None
    #: rebuilds the history from ``messages`` alone, as a test or a caller
    #: with no database does.
    run_log: RunLogReplay | None = None
    extra: JsonObject = field(default_factory=dict)
