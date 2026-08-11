"""Awareness hint for deferred (search-loaded) tools.

The extra toolsets (pod, subagents) are hidden from the prompt via
``defer_loading`` + ``ToolSearch`` to keep context small. Without a hint the model
doesn't know they exist, so it never thinks to call ``search_tools``. This
capability adds a compact, static instruction block that lists the deferred tool
*names* (grouped) and tells the model to load them on demand — names only, never
the full schemas, so the context cost is tiny and the cached prefix stays stable.
"""

from __future__ import annotations

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


def _tool_names(toolset: object) -> list[str]:
    tools = getattr(toolset, "tools", None)
    if isinstance(tools, dict):
        return list(tools.keys())
    return []


def build_deferred_tools_hint(extra_toolsets: list[object]) -> str | None:
    """Build the instruction block listing the deferred tool groups, or None."""
    lines: list[str] = []
    for toolset in extra_toolsets:
        names = _tool_names(toolset)
        if not names:
            continue
        label = _GROUP_LABELS.get(id(toolset), "Additional tools")
        lines.append(f"- {label}: {', '.join(names)}")
    if not lines:
        return None
    return (
        "# Tools available on demand\n\n"
        "To keep this prompt small, the tools below are NOT loaded yet. When a "
        "task needs one, call `search_tools` (e.g. search_tools('pod records') or "
        "search_tools('spawn subagent')) to load the matching tools, then call "
        "them normally:\n\n" + "\n".join(lines)
    )


class DeferredToolsHintCapability(AbstractCapability[object]):
    """Inject the deferred-tools awareness block as agent instructions."""

    def __init__(self, hint: str) -> None:
        self._hint = hint

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "deferred_tools_hint"

    def get_instructions(self) -> str:
        return self._hint
