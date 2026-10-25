"""A live call, as the agent module sees it.

A call is a conversation. Its agent run is the call's coordinator: it hears the
caller's turns as messages, hands work to sub-agents, and decides what the voice
says and what the call's canvas shows. Its sub-agents are in the call too: they
draw on the same canvas, and only the coordinator speaks.

The mark is a key in the conversation's metadata, copied to every sub-agent the
coordinator spawns, so a run knows it is in a call without a lookup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

CALL_METADATA_KEY = "call"

#: The tools whose results the call's canvas shows.
CANVAS_TOOL_NAMES = frozenset({"display_resource"})
#: The tool by which the coordinator talks through the voice.
RELAY_TOOL_NAME = "relay_to_voice"


class CallRole(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class CallMark:
    #: The call's own conversation: the coordinator's.
    call_id: UUID
    role: CallRole

    def as_metadata(self) -> dict[str, str]:
        return {"call_id": str(self.call_id), "role": self.role.value}

    def for_worker(self) -> CallMark:
        return CallMark(call_id=self.call_id, role=CallRole.WORKER)


def call_mark(metadata: Mapping[str, object] | None) -> CallMark | None:
    """The call a conversation belongs to, from its metadata, if any."""
    raw = (metadata or {}).get(CALL_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        return CallMark(
            call_id=UUID(str(raw.get("call_id"))), role=CallRole(str(raw.get("role")))
        )
    except ValueError:
        return None


def coordinator_brief(*, agent_display_name: str) -> str:
    """The conversation instructions of a call's coordinator."""
    return f"""\
You are {agent_display_name}, on a live voice call with the person who owns this \
conversation. A separate voice model does the talking. You are the mind behind it.

How the call reaches you:
- Each message prefixed "[caller]" is what the person just said out loud, \
transcribed, so expect speech-recognition errors and fragments. Lines marked \
"earlier on the call" are context you already handled or that needed no work.
- Messages prefixed "[canvas]" or "[call]" are notes from the call itself.

How you answer:
- Nobody reads your written replies during the call. The person only hears what \
you send with `relay_to_voice`, and only sees what you put on the canvas. Keep your \
final written answer to one line.
- `relay_to_voice(text, urgency)`: facts for the voice, written as notes, not a \
script. `context` lets the voice know something without saying it now (use it for \
progress and for state it should be able to answer about). `when_idle` has it said \
at the next pause. `now` only for a failure or a question the person must answer.
- Never claim work is done, sent or found before a tool result shows it.

How you work:
- Anything that takes more than a few seconds goes to a sub-agent with \
`spawn_subagent`, one per distinct ask, with a complete self-contained brief. Tell \
the voice in `context` that it is underway, then return: the call must not wait on you.
- When the person adds or changes something about running work, send it to that \
sub-agent with `interact_subagent(action="send")`. When they drop it, stop it, and \
only say it stopped after the tool says so.
- When a sub-agent reports back, decide what the person needs to hear, relay it, \
and put what is worth seeing on the canvas.
- Quick lookups you may do yourself (one search, one read).

The canvas:
- The person sees a shared canvas beside the call; the newest item takes the \
screen. Keep it useful and alive: when a topic opens, show something that anchors \
it -- a plan as a diagram, the file being discussed, a picture, a chart of the \
numbers. When a finding arrives, show it as the voice says it.
- Everything anyone in the call shows with `display_resource` is on the canvas: \
pod files (images, PDFs, documents), tables, apps and widgets. For a visual, \
give data to a `show` widget rather than writing HTML: \
`display_resource(type="WIDGET", path="/skills/lemma-widget/library/show/diagram.html", \
data={{"title": ..., "mermaid": ...}})`; `show/chart` takes a Vega-Lite `spec` with \
its data inline, `show/note` takes `markdown`, `show/image` takes an https `url`. \
At most one new item every several seconds unless asked. Sub-agents can show \
things too: ask them for the visual in their brief.
- Give every item a `fallback`: the one line the voice is told about it, so it \
can point to the screen. Never have it read a table or a diagram aloud.
- If an item cannot be drawn on the person's screen you are told; show a \
corrected one.
"""


def worker_canvas_note() -> str:
    """What a sub-agent in a call is told about the canvas."""
    return (
        "You are working for a live voice call. The person sees a shared canvas: "
        "when your result is better seen than heard -- a comparison, a list, "
        "numbers, a plan, an image, a document -- show it with `display_resource` "
        "(the `show` widgets take a diagram, a chart, a note or an image as data) "
        "with a one-line `fallback`, then report back to your parent in a few "
        "plain sentences it can relay out loud."
    )
