"""Prompt-caching capability, per provider protocol.

Two different levers, because the two provider families cache differently.

OpenAI-compatible providers reuse the request prefix automatically; the lever we
control is *session affinity* — routing a conversation's turns to the same
replica so the cached prefix is reused. The assembler keys affinity on the pod
and agent, so a new conversation lands where that agent's long, shared prompt
start is already cached, and a conversation's turns still land together; the
conversation id is the fallback. Never the agent-run id, which changes every
turn and would scatter routing, defeating cross-turn reuse.

Anthropic caches nothing without an explicit breakpoint, so we ask pydantic-ai
to mark them: after the tool definitions, after the system prompt, and on the
last message block, so a multi-step run reads its own history from cache rather
than paying for all of it on every step. The run's context part adds one more,
in front of itself (``run_context.py``). All of it only pays off because nothing
per-run is a ``SystemPromptPart``: Anthropic hoists system parts into the
top-level ``system`` parameter ahead of the instruction blocks, so per-run text
there would change the prefix on every request and no breakpoint would hit.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.capabilities import AbstractCapability

from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol

# Anthropic's cache TTL for the instruction prefix. The short window is the right
# default: it covers a user's active back-and-forth, which is when the same
# prefix is replayed, without paying the premium of the long-lived tier.
_ANTHROPIC_CACHE_TTL = "5m"


class PromptCachingCapability(AbstractCapability[object]):
    """Apply the caching lever that this run's provider protocol understands."""

    def __init__(
        self,
        *,
        conversation_id: UUID,
        protocol: RuntimeProfileProtocol = RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        id: str | None = "prompt_caching",
        affinity_key: str | None = None,
    ) -> None:
        self._conversation_id = str(conversation_id)
        # What routes requests to the same replica. Wider than the conversation
        # on purpose when the caller knows more: every conversation with one
        # agent in one pod shares the long start of its prompt, and a new
        # conversation keyed on its own id lands on a replica that has never
        # seen it -- a cold first request, every time.
        self._affinity_key = affinity_key or self._conversation_id
        self._protocol = protocol
        self._id = id

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return self._id

    def get_model_settings(self) -> dict[str, object]:
        if self._protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE:
            return {
                # A breakpoint after the last system block, which ends with the
                # conversation's own section -- working directory and brief --
                # since everything that changes run to run is in the run's
                # context part instead.
                "anthropic_cache_instructions": _ANTHROPIC_CACHE_TTL,
                # And one after the tool definitions. Anthropic's cache is a
                # prefix over [tools, system, messages], so a changed tool array
                # invalidates every breakpoint behind it — and the tool array
                # *does* change mid-run: `search_tools` reveals deferred tools on
                # demand, which is the whole point of deferring them. Without
                # this, the first search costs a full re-read of the system
                # prompt for the rest of the run.
                "anthropic_cache_tool_definitions": _ANTHROPIC_CACHE_TTL,
                # And one on the last message block. Without it the history was
                # never cached, so every step of a long run paid full price for
                # every message before it. `anthropic_cache_messages` rather
                # than the automatic `anthropic_cache`: it is plain per-block
                # `cache_control`, which an Anthropic-compatible gateway accepts
                # where the top-level parameter is not supported.
                "anthropic_cache_messages": _ANTHROPIC_CACHE_TTL,
            }
        affinity = self._affinity_key
        return {
            # OpenAI `user` field — used by compatible providers for sticky
            # replica routing so the cached prefix is hit across turns. The only
            # affinity lever in the baseline Chat Completions schema, and so the
            # only one this protocol can send: OPENAI_COMPATIBLE says what the
            # wire format is, not who answers on it.
            #
            # `prompt_cache_key` does the same job marginally better and is
            # deliberately absent. It is a recent OpenAI-only addition, and a
            # strict shim rejects an unknown field rather than ignoring it —
            # Google's answers `400 Unknown name "prompt_cache_key"`, failing
            # every turn of every Gemini run. Nothing real is lost: caching
            # applies automatically to prefixes over 1024 tokens, keyed or not.
            "openai_user": affinity,
        }
