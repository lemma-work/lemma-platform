"""The prompt block naming the tools that sit behind tool search.

Deferred toolsets are hidden from the prompt to keep it small. Without a hint
the model does not know they exist, so it never searches for them. This lists
each deferred tool's name and one line of its description, grouped -- never the
schemas -- so the cost is small and the cached prefix stays stable.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.agent.domain.tools import ToolGroup, ToolSpec

# Identity → human label for the deferred toolset groups. Every deferred
# toolset needs an entry: the fallback below is not a default so much as a
# symptom, and "Additional tools" is the one label that tells the model nothing
# about when to go looking. CONNECTORS sat unlabelled here for exactly that
# reason and its tools were correspondingly hard to discover.
_GROUP_LABELS: dict[ToolGroup, str] = {
    ToolGroup.SUBAGENTS: "Sub-agent delegation",
    ToolGroup.CONNECTORS: "Connected third-party apps",
    ToolGroup.MESSAGING: "Reaching pod members and other pods",
    ToolGroup.WAIT: "Pausing and resuming later",
    ToolGroup.BROWSER: "Driving a real browser",
    ToolGroup.SPEECH: "Speaking and transcribing",
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


def _tool_summaries(specs: Sequence[ToolSpec]) -> list[tuple[str, str]]:
    """``(name, summary)`` for every tool in a group, name-sorted.

    Sorted because this block rides in the cached prompt prefix: a listing
    that reshuffled between turns would invalidate the cache on every request.
    """
    return [
        (spec.name, _summarize(spec.description))
        for spec in sorted(specs, key=lambda spec: spec.name)
    ]


def deferred_tools_hint(groups: Sequence[Sequence[ToolSpec]]) -> str | None:
    """Build the instruction block listing the deferred tool groups, or None.

    Names alone told the model a tool existed but not when to reach for it —
    `pod_view_document_pages` reads as a filesystem call rather than "look at a
    PDF page". One line of description each costs roughly 300 tokens across the
    whole deferred set, against schemas 10-40x larger that stay withheld.
    """
    sections: list[str] = []
    for specs in groups:
        summaries = _tool_summaries(specs)
        if not summaries:
            continue
        label = _GROUP_LABELS.get(specs[0].toolset, "Additional tools")
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
