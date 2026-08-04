"""Prompt-caching capability, per provider protocol.

Two different levers, because the two provider families cache differently.

OpenAI-compatible providers reuse the request prefix automatically; the lever we
control is *session affinity* — routing a conversation's turns to the same
replica so the cached prefix is reused. We key affinity on the conversation id
(stable across turns), NOT the agent-run id (which changes every turn and would
scatter routing, defeating cross-turn reuse).

Anthropic caches nothing without an explicit breakpoint, so we ask pydantic-ai
to mark one after the static instruction blocks. That only pays off because the
current-time note is a ``UserPromptPart`` rather than a ``SystemPromptPart``:
Anthropic hoists system parts into the top-level ``system`` parameter ahead of
the instruction blocks, so a per-turn timestamp there would change the prefix on
every request and the breakpoint would never hit. See ``current_time.py``.

Provider-specific extensions (e.g. Fireworks ``x-session-affinity`` header) can
be layered in subclasses via ``configure_caching_capability()`` in assembler.py.
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
    ) -> None:
        self._conversation_id = str(conversation_id)
        self._protocol = protocol
        self._id = id

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return self._id

    def get_model_settings(self) -> dict[str, object]:
        if self._protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE:
            # Marks a cache breakpoint after the last static instruction block —
            # for us, the block ending in the runtime context brief.
            return {"anthropic_cache_instructions": _ANTHROPIC_CACHE_TTL}
        affinity = self._conversation_id
        return {
            # OpenAI `user` field — used by compatible providers for sticky
            # replica routing so the cached prefix is hit across turns.
            "openai_user": affinity,
            # OpenAI prompt-cache key; honored by OpenAI and compatible providers.
            "openai_prompt_cache_key": affinity,
        }
