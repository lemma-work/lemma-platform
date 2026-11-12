"""Agent-written content in a harness report, read without trusting its shape.

Split out of ``agent_host``, the wire contract, because it answers a different
question: not what the host and Lemma agreed to say, but what to do with the
part of a report the *agent* wrote and may change in any release. See
"Resilience to agent updates" in docs/architecture/agent-host.md.
"""

from __future__ import annotations


# The configuration option types an Agent Host publishes and Lemma can offer a
# choice for. An option is the agent's own -- its ACP adapter defines it, and a
# newer one may define a type nobody here has seen. Such an option is kept off
# the snapshot (see ``AgentHostHarnessSnapshot``), not offered for selection,
# and never the reason the rest of the snapshot is refused.
AGENT_HOST_SELECTABLE_OPTION_TYPES = frozenset({"select", "boolean"})


def agent_host_option_type(option: object) -> str | None:
    """The ACP type a host reported for an option, or ``None`` if it reported none.

    A host that predates reporting it sent no ``metadata.type``; its options
    are selects and toggles, so absent reads as one Lemma can offer.
    """
    if not isinstance(option, dict):
        return None
    metadata = option.get("metadata")
    kind = metadata.get("type") if isinstance(metadata, dict) else None
    return kind if isinstance(kind, str) else None


def agent_host_option_is_selectable(option: object) -> bool:
    kind = agent_host_option_type(option)
    return kind is None or kind in AGENT_HOST_SELECTABLE_OPTION_TYPES


def bounded_agent_text(value: object, limit: int) -> object:
    """Agent-written display text, cut to the column it is stored in.

    Cut, not refused: the text is the agent's (a version banner, a model's
    label), and refusing it refused the whole harness report it was part of.
    """
    if isinstance(value, str):
        return value.strip()[:limit]
    return value
