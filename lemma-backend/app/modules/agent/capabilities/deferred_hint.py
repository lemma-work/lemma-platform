"""Awareness hint for deferred (search-loaded) tools.

The extra toolsets (pod, subagents) are hidden from the prompt via
``defer_loading`` + ``ToolSearch`` to keep context small. Without a hint the model
doesn't know they exist, so it never thinks to call ``search_tools``. This
capability adds a compact, static instruction block that lists the deferred tool
*names* (grouped) and tells the model to load them on demand — names only, never
the full schemas, so the context cost is tiny and the cached prefix stays stable.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.capabilities import AbstractCapability

from app.modules.agent.tools.registry import (
    connectors_toolset,
    messaging_toolset,
    pod_toolset,
    snooze_toolset,
    subagents_toolset,
)

# Identity → human label for the deferred toolset groups. Every deferred
# toolset needs an entry: the fallback below is not a default so much as a
# symptom, and "Additional tools" is the one label that tells the model nothing
# about when to go looking. CONNECTORS sat unlabelled here for exactly that
# reason and its tools were correspondingly hard to discover.
_GROUP_LABELS: dict[int, str] = {
    id(pod_toolset): "Pod datastore & files",
    id(subagents_toolset): "Sub-agent delegation",
    id(connectors_toolset): "Connected third-party apps",
    id(messaging_toolset): "Reaching pod members",
    id(snooze_toolset): "Pausing and resuming later",
}


# A summary long enough to say what the tool is for, short enough that 21 of
# them stay a rounding error against the schemas they stand in for.
_MAX_SUMMARY_CHARS = 100


def _summarize(description: object) -> str:
    """First sentence of a tool's docstring, collapsed to one line."""
    if not isinstance(description, str) or not description.strip():
        return ""
    # Docstrings put the one-line summary before the first blank line; the rest
    # is the detail that deferral exists to keep out of the prompt.
    head = description.strip().split("\n\n", 1)[0]
    head = " ".join(head.split())
    for terminator in (". ", "? ", "! "):
        index = head.find(terminator)
        if index != -1:
            head = head[: index + 1]
            break
    head = head.rstrip(".").strip()
    if len(head) > _MAX_SUMMARY_CHARS:
        head = head[: _MAX_SUMMARY_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return head


def _tool_summaries(toolset: object) -> list[tuple[str, str]]:
    """``(name, summary)`` for every tool in a toolset, name-sorted.

    Sorted because this block rides in the cached prompt prefix: a set-ordered
    listing that reshuffles between turns would invalidate the cache on every
    request, which costs far more than the tokens it saves.
    """
    tools = getattr(toolset, "tools", None)
    if not isinstance(tools, dict):
        return []
    summaries: list[tuple[str, str]] = []
    for name in sorted(tools):
        tool = tools[name]
        description = getattr(tool, "description", None)
        if description is None:
            definition = getattr(tool, "tool_def", None)
            description = getattr(definition, "description", None)
        summaries.append((name, _summarize(description)))
    return summaries


def build_deferred_tools_hint(extra_toolsets: Sequence[object]) -> str | None:
    """Build the instruction block listing the deferred tool groups, or None.

    Names alone told the model a tool existed but not when to reach for it —
    `pod_view_document_pages` reads as a filesystem call rather than "look at a
    PDF page". One line of description each costs roughly 300 tokens across the
    whole deferred set, against schemas 10-40x larger that stay withheld.
    """
    sections: list[str] = []
    for toolset in extra_toolsets:
        summaries = _tool_summaries(toolset)
        if not summaries:
            continue
        label = _GROUP_LABELS.get(id(toolset), "Additional tools")
        lines = [f"**{label}**"]
        lines.extend(
            f"- `{name}` — {summary}" if summary else f"- `{name}`"
            for name, summary in summaries
        )
        sections.append("\n".join(lines))
    if not sections:
        return None
    return (
        "# Tools available on demand\n\n"
        "To keep this prompt small, the tools below are not loaded yet. Search "
        "for one by what you need it to do and it becomes callable like any "
        "other tool; the descriptions below are enough to decide which to "
        "reach for.\n\n" + "\n\n".join(sections)
    )


class DeferredToolsHintCapability(AbstractCapability[object]):
    """Inject the deferred-tools awareness block as agent instructions."""

    def __init__(self, hint: str) -> None:
        self._hint = hint

    @classmethod
    def get_serialization_name(cls) -> str | None:  # pragma: no cover - metadata
        return "deferred_tools_hint"

    def get_instructions(self) -> str:
        return self._hint
