"""A pod's deployed apps, opened inside an MCP host such as ChatGPT.

Each app the person may open becomes a tool -- "Open Customers" -- and calling
it shows the app itself in the conversation: the deployed app, framed inside a
thin Lemma view, not a copy rebuilt as one. See
``docs/architecture/mcp-plugin.md`` §b.

The frame sits in someone else's page, where Lemma's cookies never arrive
(they are ``SameSite=Lax``). So the view asks this server, through two tools
only a view can call, for what the app needs instead:

* ``lemma_app_session`` -- a token with which the pod's own agent acts as the
  person, in this pod: their roles and row-level security, nothing outside the
  pod, destructive actions gated. It never enters the transcript: a view's own
  calls are answered to the view.
* ``lemma_app_access`` -- for a private app, a one-minute ticket to its files,
  bound to this connection so the access ends when the connection does.

Offered only to a connection allowed to change things. An app is a surface
that writes, and a token minted for the pod's agent is not narrowed by the
connection's scopes, so a read-only connection is shown the views but no apps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastmcp.apps.config import UI_MIME_TYPE
from fastmcp.resources import ResourceContent, ResourceResult, ResourceTemplate
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.apps.contracts import AppNotFoundError, AppStatus
from app.modules.apps.contracts.embedded_access import (
    app_host_origin,
    mint_embedded_app_ticket,
)
from app.modules.apps.contracts.pod_summaries import list_readable_app_summaries
from app.modules.identity.contracts.delegated_tokens import mint_pod_agent_session
from app.modules.mcp_access.contracts import McpPrincipal, Scope

APP_VIEW_URI_TEMPLATE = "ui://lemma/apps/{slug}"
OPEN_APP_PREFIX = "lemma_open_app_"
APP_SESSION = "lemma_app_session"
APP_ACCESS = "lemma_app_access"
VIEW_ONLY_TOOLS = frozenset({APP_SESSION, APP_ACCESS})

# More than this, and the tool list is a directory rather than a set of tools;
# the rest stay one "Open in Lemma" away.
MAX_APP_TOOLS = 20
# MCP tool names are at most 64 characters.
_MAX_TOOL_NAME = 64
_NOT_A_TOOL_NAME = re.compile(r"[^A-Za-z0-9_]")

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
# The two tools the view calls for itself. MCP Apps hosts keep them from the
# model (`visibility: ["app"]`) and let only a view call them.
_VIEW_ONLY_META = {"ui": {"visibility": ["app"]}}


@dataclass(frozen=True, slots=True)
class OpenableApp:
    name: str
    description: str | None
    slug: str
    url: str

    @property
    def tool_name(self) -> str:
        return (OPEN_APP_PREFIX + _NOT_A_TOOL_NAME.sub("_", self.slug))[:_MAX_TOOL_NAME]

    @property
    def view_uri(self) -> str:
        return APP_VIEW_URI_TEMPLATE.format(slug=self.slug)


def offers_apps(principal: McpPrincipal | None) -> bool:
    """Apps are for an outside client's connection that may change things."""
    return principal is not None and Scope.WRITE in principal.scopes


async def openable_apps(
    uow_factory: UnitOfWorkFactory, *, pod_id: UUID, user_id: UUID
) -> list[OpenableApp]:
    """The pod's live apps this person may open, on a host this deployment serves."""
    async with uow_factory() as uow:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        summaries = await list_readable_app_summaries(
            session=uow.session, pod_id=pod_id, ctx=ctx, limit=MAX_APP_TOOLS
        )
    apps: list[OpenableApp] = []
    seen: set[str] = set()
    for summary in summaries:
        if summary.url is None or summary.status != AppStatus.READY.value:
            continue
        app = OpenableApp(
            name=summary.name,
            description=summary.description,
            slug=summary.public_slug,
            url=summary.url,
        )
        # Two slugs that differ only in a character a tool name cannot hold
        # would be one tool; the first keeps it.
        if app.tool_name in seen:
            continue
        seen.add(app.tool_name)
        apps.append(app)
    return apps


def app_tools(apps: list[OpenableApp]) -> list[Tool]:
    tools = [
        Tool(
            name=app.tool_name,
            title=f"Open {app.name}",
            description=" ".join(
                part
                for part in (
                    (
                        f"Open the {app.name} app from this pod in the "
                        "conversation, so the person can use it here, as themselves."
                    ),
                    app.description,
                )
                if part
            ),
            input_schema={"type": "object", "properties": {}},
            annotations=_READ_ONLY,
            _meta={"ui": {"resourceUri": app.view_uri}},
        )
        for app in apps
    ]
    if not tools:
        return []
    return [
        *tools,
        Tool(
            name=APP_SESSION,
            title="Sign an open app in",
            description="Used by a Lemma app view: a short-lived token for the app.",
            input_schema={"type": "object", "properties": {}},
            annotations=_READ_ONLY,
            _meta=_VIEW_ONLY_META,
        ),
        Tool(
            name=APP_ACCESS,
            title="Open a private app's files",
            description="Used by a Lemma app view: a ticket to a private app's files.",
            input_schema={
                "type": "object",
                "properties": {"app": {"type": "string"}},
                "required": ["app"],
            },
            annotations=_READ_ONLY,
            _meta=_VIEW_ONLY_META,
        ),
    ]


def is_app_tool(name: str) -> bool:
    return name.startswith(OPEN_APP_PREFIX) or name in VIEW_ONLY_TOOLS


async def call_app_tool(
    uow_factory: UnitOfWorkFactory,
    name: str,
    arguments: dict[str, object],
    *,
    pod_id: UUID,
    principal: McpPrincipal,
) -> CallToolResult:
    """Answer one of the app tools, for a connection ``offers_apps`` allows."""
    if name == APP_SESSION:
        session = await mint_pod_agent_session(
            user_id=principal.user_id,
            pod_id=pod_id,
            session_id=f"mcp:{principal.grant_id}",
        )
        return _result(
            {"success": True},
            text="Signed the app in.",
            secret={
                "token": session.value,
                "expires_at": session.expires_at.isoformat(),
            },
        )
    apps = await openable_apps(uow_factory, pod_id=pod_id, user_id=principal.user_id)
    if name == APP_ACCESS:
        slug = arguments.get("app")
        app = next((app for app in apps if app.slug == slug), None)
        if app is None:
            return _refusal("No app by that name can be opened here.")
        session = await mint_pod_agent_session(
            user_id=principal.user_id,
            pod_id=pod_id,
            session_id=f"mcp:{principal.grant_id}",
        )
        try:
            ticket = await mint_embedded_app_ticket(
                uow_factory,
                slug=app.slug,
                user_id=principal.user_id,
                session_handle=session.session_handle,
                grant_id=principal.grant_id,
            )
        except AppNotFoundError:
            return _refusal("No app by that name can be opened here.")
        return _result(
            {"success": True},
            text="Opened the app's files.",
            secret={
                "ticket": ticket.ticket,
                "expires_in_seconds": ticket.expires_in_seconds,
            },
        )
    app = next((app for app in apps if app.tool_name == name), None)
    if app is None:
        return _refusal("That app is not in this pod, or is not shared with you.")
    return _result(
        {
            "success": True,
            "app": {"name": app.name, "slug": app.slug, "url": app.url},
        },
        # Listed for every host, and not every host draws an app: the text has
        # to be true where nothing is shown.
        text=(
            f"Opened the {app.name} app. Where this tool shows apps it is shown "
            f"here; it is also at {app.url}"
        ),
    )


def app_view_template() -> ResourceTemplate:
    """The view an app is shown in: the same page for every app, with a CSP
    that lets it frame exactly that app's host and nothing else."""
    html = (Path(__file__).parent / "pod_mcp_app_view.html").read_text(encoding="utf-8")

    def view(slug: str) -> ResourceResult:
        origin = app_host_origin(slug)
        return ResourceResult(
            contents=[
                ResourceContent(
                    html,
                    mime_type=UI_MIME_TYPE,
                    meta={
                        "ui": {
                            "csp": {
                                "connectDomains": [],
                                "resourceDomains": [],
                                "frameDomains": [origin] if origin else [],
                            },
                            # The app draws its own ground, edge to edge.
                            "prefersBorder": False,
                        }
                    },
                )
            ]
        )

    return ResourceTemplate.from_function(
        view,
        uri_template=APP_VIEW_URI_TEMPLATE,
        name="lemma-app",
        title="App",
        description="A pod's app, opened in the conversation.",
        mime_type=UI_MIME_TYPE,
    )


SECRET_META_KEY = "lemma/app"


def _result(
    payload: dict[str, object],
    *,
    text: str,
    secret: dict[str, object] | None = None,
) -> CallToolResult:
    """``secret`` rides in ``_meta``, which hosts hand the view and keep from the
    model -- unlike ``structuredContent``, which ChatGPT shows the model too."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        _meta={SECRET_META_KEY: secret} if secret is not None else None,
    )


def _refusal(message: str) -> CallToolResult:
    payload = {"success": False, "error": message}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=True,
    )
