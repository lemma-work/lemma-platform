"""Cross-module contract for reading a conversation widget's HTML.

A widget's HTML fragment is authored by the agent's ``display_resource`` tool and
stored in the conversation. Both the widget serving path and "save widget as
app" need that content, but the app module must not depend on agent
internals. This port is the shared contract: the agent module implements it,
and consumers (app service / controllers) depend only on this core interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class WidgetSourceUnavailable(Exception):
    """A path-backed widget's file cannot be read for this viewer.

    On the port rather than beside the implementation, because it is half of
    what ``resolve`` promises: every consumer has to turn it into something the
    person sees, and ``apps`` may not import the agent service to catch it.
    """


@dataclass(frozen=True)
class WidgetArtifact:
    """A widget: where its HTML comes from, plus the pod it belongs to.

    Two sources, and the difference is who owns the bytes. ``content`` is a
    fragment the agent wrote into the tool call, frozen there for as long as the
    conversation exists. ``path`` is a pod file the agent wrote and can edit
    afterwards, so the widget is whatever that file says *now* — which is the
    point of it: correcting a widget is editing its file, not displaying a
    second one underneath the first.

    ``content`` is empty until ``resolve`` has run on a path-backed artifact.
    Reading it before then is reading a widget nobody was authorized for.
    """

    content: str
    pod_id: UUID
    title: str = ""
    path: str | None = None


class WidgetContentReader(Protocol):
    """Resolves a widget's content by ``(conversation_id, tool_call_id)``."""

    async def get_widget(
        self, conversation_id: UUID, tool_call_id: str
    ) -> WidgetArtifact | None: ...

    async def resolve(
        self, artifact: WidgetArtifact, ctx: object
    ) -> WidgetArtifact: ...

    """Fill ``content`` for a path-backed artifact, reading as ``ctx``.

    Separate from ``get_widget`` because the two answer to different people.
    ``get_widget`` reads the tool call to learn which pod the widget belongs to,
    and the viewer cannot be authorized until that is known. The file itself is
    then read as the viewer, under their own grants — so a widget whose source
    they may not read does not render for them, and nothing here is a way around
    the file's permissions.
    """
