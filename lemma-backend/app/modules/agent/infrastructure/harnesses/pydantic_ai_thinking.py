"""Whether a stored thought can be replayed to the model, and where it goes.

The second subtle rule is reasoning, and it is subtle because getting it wrong
looks like nothing at all. pydantic-ai will only send a stored thought back in
the provider's own reasoning field if it can tell where the thought came from;
a bare ``ThinkingPart(content=...)`` is instead written into the assistant's
**content** as ``<think>…</think>``. That is a valid-looking request which
teaches the model that reasoning belongs in the answer — measured against
Fireworks MiniMax M3, two such turns in the history is enough to flip it, and
from then on it answers in ``<think>`` tags.

So a thought is replayed only when both of these hold, and dropped otherwise:

1. The row carries the credential the *target* provider needs — a signature for
   Anthropic, a reasoning-field id for OpenAI-compatible. They are not
   interchangeable, which is why ``protocol`` has to be passed in.
2. It can be merged into the same ``ModelResponse`` as the message it preceded.
   Merging is not cosmetic — a response holding *only* a thought maps to no
   assistant message at all (pydantic-ai drops a parts-list with no text and no
   tool calls), so a thought in its own response is silently discarded even when
   the credential is right.

Dropping is always the safe direction: it costs the model sight of its own
earlier reasoning, and the alternative costs the user an answer made of it.
"""

from __future__ import annotations

from pydantic_ai.messages import ThinkingPart

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Runtime-profile protocols, as `runtime_model_factory` spells them. Only these
#: two build a pydantic-ai model, and they want different reasoning credentials.
_OPENAI_PROTOCOL = "OPENAI_COMPATIBLE"
_ANTHROPIC_PROTOCOL = "ANTHROPIC_COMPATIBLE"


class PendingThoughts:
    """Thoughts waiting for the response they belong to.

    Held rather than emitted as they are read, because a `ModelResponse`
    containing only a thought reaches the provider as nothing at all -- so a
    thought has to be merged into the response for the message that followed it,
    and that message has not been reached yet when the thought is.
    """

    def __init__(self, protocol: str | None) -> None:
        self._protocol = protocol
        self._parts: list[ThinkingPart] = []

    def offer(self, msg: object) -> None:
        """Keep this message's thought, if it can be replayed at all."""
        part = thinking_part_from_message(msg, self._protocol)
        if part is not None:
            self._parts.append(part)

    def take(self) -> list[ThinkingPart]:
        """The held thoughts, for the response now being built."""
        thoughts = self._parts
        self._parts = []
        return thoughts

    def drop(self) -> None:
        """Forget them: nothing they could ride on is coming."""
        if not self._parts:
            return
        logger.debug(
            "agent.pydantic_ai.dropping_unattached_replayed_thought.diagnostic",
            dropped_count=len(self._parts),
        )
        self._parts = []


def thinking_part_from_message(
    msg: object, protocol: str | None
) -> ThinkingPart | None:
    """A stored thought, or None when it cannot be replayed faithfully.

    The test is not "did we record something" but "will *this* provider take it
    in its reasoning channel", and the two answer differently — measured against
    both, not assumed:

    - **Anthropic** needs the ``signature``. Given a thought without one it
      emits a ``<thinking>`` *text* block: the same leak wearing the other
      spelling. An id does not help it.
    - **OpenAI-compatible** needs an ``id`` naming the field the thought arrived
      in (``reasoning_content``), plus the provider it came from. A signature
      does not help it. An id of ``content`` is the one value that must be
      refused — it means pydantic-ai recovered that thought from ``<think>``
      tags in the first place, so replaying it would put the tags straight back.

    Hence ``protocol``: the credentials are not interchangeable, so the answer
    depends on where the history is going, not only on where the thought came
    from. That matters in practice because an agent's model can be changed
    mid-conversation — thoughts recorded under Anthropic would otherwise be
    replayed into a Fireworks model as tags, re-teaching it the habit this
    change exists to break.

    A thought that fails the test drops out of the replayed history. The model
    then loses sight of its own earlier reasoning — which is what the major
    providers do across turns anyway, and what DeepSeek documents as required —
    while the transcript still shows every word of it to the person.
    """
    # Read directly rather than through `pydantic_ai_history._message_text`:
    # that module imports this one, and a thought's body is a plain string with
    # none of the prompt assembly a user message needs.
    text = getattr(msg, "text", None) or ""
    if not text.strip():
        return None

    metadata = getattr(msg, "metadata", None)
    if not isinstance(metadata, dict):
        return None

    part_id = _identity_string(metadata.get("thinking_part_id"))
    provider_name = _identity_string(metadata.get("thinking_provider_name"))
    signature = _identity_string(metadata.get("thinking_signature"))

    if protocol == _ANTHROPIC_PROTOCOL:
        replayable = signature is not None
    elif protocol == _OPENAI_PROTOCOL:
        replayable = (
            provider_name is not None and part_id is not None and part_id != "content"
        )
    else:
        # An unknown target cannot be reasoned about, so nothing is replayed.
        # Silence is the only option here that cannot leak.
        replayable = False

    if not replayable:
        return None

    return ThinkingPart(
        content=text,
        id=part_id,
        provider_name=provider_name,
        signature=signature,
    )


def _identity_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _identity_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
