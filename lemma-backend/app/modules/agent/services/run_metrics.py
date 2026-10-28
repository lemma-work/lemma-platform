"""The counters and timings of agent runs, as OpenTelemetry metrics.

Every label here is bounded by the code or the model catalog, never by data:
no run, conversation, pod, agent or user id, and no agent name -- agents are
named per pod, so their names grow with the customer count. That attribution
lives on the ``agent.run`` span, which is sampled and costs storage per run
rather than a series per value.

One object, constructed over a ``Meter``, so a test can hand it a meter that
reads into memory instead of patching the global provider.
"""

from __future__ import annotations

import re
from typing import Literal

from opentelemetry import metrics
from opentelemetry.metrics import Meter

from app.modules.agent.domain.tools import ToolKind
from app.modules.agent.domain.value_objects import ConversationStatus
from app.core.infrastructure.db.transient_errors import is_transient_database_error
from app.core.infrastructure.work import LeaseLost

#: How a run ended. ``interrupted`` is a run whose worker went away before it
#: ended (a deploy, a lost lease): it is resumed by another worker, which
#: counts it again when it does end, so it is not a failure.
RunOutcomeLabel = Literal["completed", "failed", "cancelled", "waiting", "interrupted"]
#: How a tool call ended, in the vocabulary its span already uses.
ToolOutcome = Literal["ok", "error", "cancelled", "control_flow"]

#: From a fast reply to a run that works for hours; the SDK's default buckets
#: are sized for milliseconds.
_RUN_SECONDS_BUCKETS = (
    0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400,
)  # fmt: skip
_FIRST_TOKEN_SECONDS_BUCKETS = (
    0.1, 0.25, 0.5, 1, 2, 3, 5, 7.5, 10, 15, 30, 60, 120, 300,
)  # fmt: skip
_MODEL_LABEL_RE = re.compile(r"[^A-Za-z0-9_.:/-]+")
_MAX_MODEL_LABEL = 96
UNKNOWN = "unknown"


def model_label(model_name: str | None) -> str:
    """A model name as a label: the configured name, cleaned and bounded.

    Bounded by the runtime-profile catalog an operator configures, which is
    small; the cleanup only keeps a hand-typed custom model from carrying
    whitespace or an absurd length into a series name.
    """
    cleaned = _MODEL_LABEL_RE.sub("_", (model_name or "").strip())
    return cleaned[:_MAX_MODEL_LABEL] or UNKNOWN


class AgentRunMetrics:
    """Records agent run starts, ends, first tokens and tool calls."""

    def __init__(self, meter: Meter | None = None) -> None:
        meter = meter or metrics.get_meter("app.modules.agent.run_metrics")
        self._runs = meter.create_counter(
            "lemma.agent.runs",
            unit="{run}",
            description="Agent runs that ended, by outcome, kind and model.",
        )
        self._duration = meter.create_histogram(
            "lemma.agent.run.duration",
            unit="s",
            description="Agent run wall time, from the harness starting to its end.",
            explicit_bucket_boundaries_advisory=_RUN_SECONDS_BUCKETS,
        )
        self._first_token = meter.create_histogram(
            "lemma.agent.first_token",
            unit="s",
            description="From the harness starting to its first streamed token.",
            explicit_bucket_boundaries_advisory=_FIRST_TOKEN_SECONDS_BUCKETS,
        )
        self._tool_calls = meter.create_counter(
            "lemma.agent.tool_calls",
            unit="{call}",
            description="Tool calls made by in-process agent runs.",
        )
        self._active = meter.create_up_down_counter(
            "lemma.agent.runs.active",
            unit="{run}",
            description="Agent runs executing in this process right now.",
        )

    def run_started(self, *, kind: str) -> None:
        self._active.add(1, {"kind": kind})

    def run_ended(
        self,
        *,
        kind: str,
        outcome: RunOutcomeLabel,
        model: str | None,
        seconds: float,
    ) -> None:
        """One run is over -- for this process, if ``interrupted``.

        Always pairs with ``run_started``: the active gauge comes down whatever
        the outcome.
        """
        self._active.add(-1, {"kind": kind})
        self._runs.add(
            1, {"outcome": outcome, "kind": kind, "model": model_label(model)}
        )
        self._duration.record(seconds, {"kind": kind, "outcome": outcome})

    def first_token(self, seconds: float, *, model: str | None) -> None:
        self._first_token.record(seconds, {"model": model_label(model)})

    def tool_call(self, *, tool_kind: ToolKind, outcome: ToolOutcome) -> None:
        self._tool_calls.add(1, {"tool_kind": tool_kind, "outcome": outcome})


def ended_as(status: ConversationStatus | None) -> RunOutcomeLabel:
    """How a run that returned normally ended, from its final status.

    A harness that ends without a terminal status still finished its turn,
    and the run is finalized as completed, so it is counted that way.
    """
    if status is ConversationStatus.WAITING:
        return "waiting"
    if status is ConversationStatus.FAILED:
        return "failed"
    if status in (ConversationStatus.STOPPED, ConversationStatus.STOP_REQUESTED):
        return "cancelled"
    return "completed"


def ended_by(exc: BaseException) -> RunOutcomeLabel:
    """How a run that raised ended: resumed by another worker, or failed.

    A person stopping a run is not this path -- that ends normally, STOPPED,
    and is counted ``cancelled`` by ``ended_as``.
    """
    return "interrupted" if run_is_not_over(exc) else "failed"


#: The process's instance, over the global meter provider.
agent_run_metrics = AgentRunMetrics()


__all__ = [
    "AgentRunMetrics",
    "RunOutcomeLabel",
    "ToolKind",
    "ToolOutcome",
    "agent_run_metrics",
    "ended_as",
    "ended_by",
    "model_label",
]


def run_is_not_over(exc: BaseException) -> bool:
    """Whether ``exc`` interrupts the run rather than ending it.

    A cancellation (a lost lease, a shutdown past its grace), a shutdown yield
    at a step boundary, and a transient database failure all leave the run
    RUNNING for its work item to resume from the last committed step. Anything
    else is the run going wrong, and it fails.
    """
    if isinstance(exc, LeaseLost):
        # Another worker owns the turn now; it finishes the run, not this one.
        return True
    return not isinstance(exc, Exception) or is_transient_database_error(exc)
