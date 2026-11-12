"""A model call outside a turn, in Lemma's own terms.

Titles, the memory pass and the vision delegate each ask a model one thing and
read the answer. What they ask is described here, without a vendor or a
framework type, so the call sites stay on Lemma's side of the provider port
(``domain/ports.ModelProvider``) and the pydantic-ai specifics stay in
``infrastructure/providers``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.modules.agent.domain.tools import ImagePart, ToolSpec
from app.modules.agent.domain.value_objects import JsonObject

if TYPE_CHECKING:
    from app.modules.agent.domain.context import ToolContext


#: One element of a prompt: text, or an image.
type PromptPart = str | ImagePart


@dataclass(frozen=True, slots=True)
class BackgroundLimits:
    """A runaway guard for one background call, not a budget."""

    request_limit: int
    input_tokens_limit: int | None = None
    output_tokens_limit: int | None = None
    total_tokens_limit: int | None = None
    count_tokens_before_request: bool = False


@dataclass(frozen=True, slots=True)
class BackgroundCall:
    """What a background call sends, and how."""

    prompt: str | Sequence[PromptPart]
    limits: BackgroundLimits
    #: The agent's standing instructions (re-sent on every request).
    instructions: str | None = None
    #: A system prompt (sent once, at the head of the history).
    system_prompt: str | None = None
    #: The settings asked for first, as the provider's wire settings.
    model_settings: JsonObject | None = None
    #: Asked once more with these when a strict endpoint refuses a field it
    #: does not know (400/422) -- only when they differ from the first ask.
    plain_model_settings: JsonObject | None = None
    #: Called with the refusal's status code before that second ask.
    on_plain_retry: Callable[[int], None] | None = None
    #: Where the provider should route it to find its prefix cached:
    #: ``<pod_id>:<purpose>``, like an agent request's.
    affinity_key: str | None = None
    #: The tools the call may use, and what they run against.
    tools: Sequence[ToolSpec] = ()
    deps: ToolContext | None = None


@dataclass(frozen=True, slots=True)
class ToolReturnRecord:
    """One tool's return during a background call."""

    tool_name: str
    content: object


@dataclass(frozen=True, slots=True)
class BackgroundAnswer:
    """What came back: the final text and every tool return along the way."""

    output: str
    tool_returns: list[ToolReturnRecord] = field(default_factory=list)


class BackgroundLimitReached(RuntimeError):
    """The call ran into its ``BackgroundLimits`` before it answered.

    Carries the tool returns made before the limit: a call that writes (the
    memory pass) may have kept something already.
    """

    def __init__(self, message: str, *, tool_returns: list[ToolReturnRecord]) -> None:
        super().__init__(message)
        self.tool_returns = tool_returns
