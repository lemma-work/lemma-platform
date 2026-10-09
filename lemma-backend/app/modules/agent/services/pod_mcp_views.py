"""Views a pod tool's result is shown in, by an MCP host that supports MCP Apps.

A tool names its view in ``_meta.ui.resourceUri``. The host reads that ``ui://``
resource, renders the HTML in a sandboxed iframe, and hands it the call's
arguments and result over ``postMessage``. A host without MCP Apps ignores the
key and shows the text result, which carries everything the view shows -- so
nothing is lost where the view is not drawn.

The views are static. No data is written into them and the declared CSP lets
them reach nothing but the host: what they show arrives in the tool result the
host forwards, and what they fetch (the next page) they get by asking the host
to call a tool, so it runs through the same connection, scopes and audit line as
the model's own calls.

Spec: https://github.com/modelcontextprotocol/ext-apps (revision 2026-01-26).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastmcp.apps.config import UI_MIME_TYPE
from fastmcp.resources import TextResource
from pydantic import AnyUrl

from app.modules.agent.domain.value_objects import JsonObject

_ASSET_DIR = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class PodMcpView:
    uri: str
    name: str
    title: str
    description: str
    filename: str

    def tool_meta(self) -> JsonObject:
        """The ``_meta`` entry a tool carries to be shown in this view."""
        return {"ui": {"resourceUri": self.uri}}

    def resource(self) -> TextResource:
        return TextResource(
            uri=AnyUrl(self.uri),
            name=self.name,
            title=self.title,
            description=self.description,
            mime_type=UI_MIME_TYPE,
            text=(_ASSET_DIR / self.filename).read_text(encoding="utf-8"),
            meta={
                "ui": {
                    # Declared empty rather than left out: the view loads
                    # nothing from anywhere and connects to nothing, and saying
                    # so is what a reviewer -- or a host's submission check --
                    # reads. Omitted, a host may assume a default of its own.
                    "csp": {"connectDomains": [], "resourceDomains": []},
                    # The host draws the card's edge and ground, so the table
                    # sits in a conversation the way that host's own cards do.
                    "prefersBorder": True,
                }
            },
        )


TABLE_VIEW = PodMcpView(
    uri="ui://lemma/table",
    name="lemma-table",
    title="Table",
    description=(
        "Records or query results from a Lemma pod, as a table the person can "
        "page through and sort."
    ),
    filename="pod_mcp_table_view.html",
)

POD_MCP_VIEWS: tuple[PodMcpView, ...] = (TABLE_VIEW,)
