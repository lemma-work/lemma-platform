"""A pod's apps as tools, and the view that frames them.

The server's half: what each app is offered as, to whom, and where the view's
secrets travel. The view's half, run under node against a host and a framed
app the test plays: it answers only the frame it made, at that app's origin.
The whole exchange over the real endpoint is in the module e2e suites.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastmcp import Client

from app.core.config import settings
from app.mcp_server import build_pod_mcp_server
from app.modules.agent.services.pod_mcp_apps import (
    APP_ACCESS,
    APP_SESSION,
    SECRET_META_KEY,
    OpenableApp,
    _result,
    app_tools,
    is_app_tool,
    offers_apps,
)
from app.modules.agent.tests.unit.test_pod_mcp_views import _FAKE_PAGE, _node, _Page
from app.modules.mcp_access.contracts import McpPrincipal, Scope

pytestmark = pytest.mark.unit

_HTML = (
    Path(__file__).resolve().parents[2] / "services" / "pod_mcp_app_view.html"
).read_text(encoding="utf-8")
_PAGE = _Page(_HTML)
_MODEL_SCRIPT, _BRIDGE_SCRIPT = _PAGE.scripts

_DESK = OpenableApp(
    name="Desk",
    description="Orders, by customer.",
    slug="order-desk",
    url="https://order-desk.apps.example.test",
)


def _principal(*scopes: Scope) -> McpPrincipal:
    return McpPrincipal(
        user_id=uuid4(),
        pod_id=uuid4(),
        grant_id=uuid4(),
        client_id="client",
        client_name="ChatGPT",
        scopes=frozenset(scopes),
    )


# --- The server's half --------------------------------------------------------


def test_each_app_is_its_own_tool_named_for_it():
    opener, session, access = app_tools([_DESK])
    assert opener.name == "lemma_open_app_order_desk"
    assert opener.title == "Open Desk"
    assert opener.description.endswith("Orders, by customer.")
    assert opener.meta == {"ui": {"resourceUri": "ui://lemma/apps/order-desk"}}
    assert opener.annotations.read_only_hint is True
    # The view's own two, kept from the model by the host.
    assert (session.name, access.name) == (APP_SESSION, APP_ACCESS)
    assert session.meta == access.meta == {"ui": {"visibility": ["app"]}}
    assert all(is_app_tool(tool.name) for tool in (opener, session, access))
    assert not is_app_tool("lemma_pod_query")


def test_a_tool_name_fits_what_mcp_allows():
    long = OpenableApp(name="L", description=None, slug="a-" * 40, url="https://x")
    assert len(long.tool_name) == 64
    assert set(long.tool_name) <= set("abcdefghijklmnopqrstuvwxyz_")


def test_no_apps_means_no_app_tools_at_all():
    assert app_tools([]) == []


def test_apps_are_offered_only_to_a_connection_that_may_change_things():
    """An app writes, and its token is not narrowed by the connection's scopes."""
    assert offers_apps(_principal(Scope.READ, Scope.WRITE))
    assert not offers_apps(_principal(Scope.READ))
    assert not offers_apps(None)


def test_a_view_only_secret_rides_in_meta_and_nowhere_the_model_reads():
    """ChatGPT shows the model `structuredContent` as well as `content`."""
    result = _result(
        {"success": True}, text="Signed the app in.", secret={"token": "t-1"}
    )
    assert result.meta == {SECRET_META_KEY: {"token": "t-1"}}
    assert "t-1" not in json.dumps(result.structured_content)
    assert all("t-1" not in block.text for block in result.content)


async def test_an_apps_view_may_frame_that_app_and_nothing_else(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")
    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    async with Client(build_pod_mcp_server()) as client:
        [content] = await client.read_resource("ui://lemma/apps/order-desk")
    assert content.mime_type == "text/html;profile=mcp-app"
    assert content.meta["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
        "frameDomains": ["https://order-desk.apps.example.test"],
    }


def test_the_view_loads_nothing_and_writes_no_markup():
    assert _PAGE.loads == []
    assert "innerHTML" not in _HTML
    assert "fetch(" not in _HTML


def test_the_views_protocol_names_are_the_sdks():
    """Two files speak this protocol; a renamed message silently ends it."""
    sdk = (
        Path(__file__).resolve().parents[6] / "lemma-typescript" / "src" / "embedded.ts"
    ).read_text(encoding="utf-8")
    for name in (
        "lemma_embed",
        '"mcp"',
        "lemma:token-request",
        "lemma:token",
        "lemma:app-access-request",
        "lemma:app-access",
    ):
        assert name in sdk, name
        assert name in _MODEL_SCRIPT, name


# --- The view's half, under node -----------------------------------------------

_APP_ORIGIN = "https://order-desk.apps.example.test"

# A framed app's window: what the view posts to it, and with which origin.
_FRAMED_APP = r"""
const toFrame = [];
const framedWindow = { postMessage: (message, targetOrigin) => toFrame.push({ message, targetOrigin }) };
element("frame").contentWindow = framedWindow;
const fromFrame = (data, origin) => deliver({ source: framedWindow, origin, data });
const answer = (call, result) => fromHost({ id: call.id, result });
const calls = (name) => posted.filter((m) => m.method === "tools/call" && m.params.name === name);
"""


def _run_view(harness: str) -> object:
    result = subprocess.run(
        [
            _node(),
            "-e",
            f"{_FAKE_PAGE}\n{_FRAMED_APP}\n{_MODEL_SCRIPT}\n{_BRIDGE_SCRIPT}\n{harness}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


_OPENED = json.dumps(
    {
        "structuredContent": {
            "success": True,
            "app": {"name": "Desk", "slug": "order-desk", "url": _APP_ORIGIN},
        }
    }
)

_START = f"""
  fromHost({{ id: posted[0].id, result: {{
    protocolVersion: "2026-01-26",
    hostCapabilities: {{ serverTools: {{}}, openLinks: {{}} }},
    hostContext: {{ displayMode: "inline", availableDisplayModes: ["inline", "fullscreen"] }},
  }} }});
  await settle();
  fromHost({{ method: "ui/notifications/tool-result", params: {_OPENED} }});
"""


def test_the_view_frames_the_app_marked_as_embedded():
    seen = _run_view(
        f"""(async () => {{
          {_START}
          console.log(JSON.stringify({{ src: elements.frame.src, name: elements.name.textContent }}));
          process.exit(0);
        }})();"""
    )
    assert seen == {"src": f"{_APP_ORIGIN}/?lemma_embed=mcp", "name": "Desk"}


def test_the_view_hands_its_frame_a_token_at_that_apps_origin_only():
    seen = _run_view(
        f"""(async () => {{
          {_START}
          fromFrame({{ type: "lemma:token-request", id: "q1" }}, "{_APP_ORIGIN}");
          await settle();
          const [call] = calls("lemma_app_session");
          answer(call, {{ structuredContent: {{ success: true }},
                          _meta: {{ "lemma/app": {{ token: "t-1", expires_at: "2026-10-07T10:00:00+00:00" }} }} }});
          await settle();
          // The same question from anywhere but the app's own origin is not answered.
          fromFrame({{ type: "lemma:token-request", id: "q2" }}, "https://elsewhere.example.test");
          await settle();
          console.log(JSON.stringify({{ toFrame, asked: calls("lemma_app_session").length }}));
          process.exit(0);
        }})();"""
    )
    assert seen == {
        "toFrame": [
            {
                "message": {
                    "id": "q1",
                    "type": "lemma:token",
                    "token": "t-1",
                    "expiresAt": "2026-10-07T10:00:00+00:00",
                },
                "targetOrigin": _APP_ORIGIN,
            }
        ],
        "asked": 1,
    }


def test_the_view_fetches_a_private_apps_ticket_and_passes_on_a_refusal():
    seen = _run_view(
        f"""(async () => {{
          {_START}
          fromFrame({{ type: "lemma:app-access-request", id: "a1" }}, "{_APP_ORIGIN}");
          await settle();
          const [first] = calls("lemma_app_access");
          answer(first, {{ structuredContent: {{ success: true }}, _meta: {{ "lemma/app": {{ ticket: "tk-1" }} }} }});
          await settle();
          fromFrame({{ type: "lemma:app-access-request", id: "a2" }}, "{_APP_ORIGIN}");
          await settle();
          const [, second] = calls("lemma_app_access");
          answer(second, {{ isError: true, structuredContent: {{ success: false, error: "No app by that name can be opened here." }} }});
          await settle();
          console.log(JSON.stringify({{ args: first.params.arguments, replies: toFrame.map((t) => t.message) }}));
          process.exit(0);
        }})();"""
    )
    assert seen == {
        "args": {"app": "order-desk"},
        "replies": [
            {"id": "a1", "type": "lemma:app-access", "ticket": "tk-1"},
            {
                "id": "a2",
                "type": "lemma:app-access",
                "error": "No app by that name can be opened here.",
            },
        ],
    }


def test_a_refused_open_is_shown_not_framed():
    refused = json.dumps(
        {
            "isError": True,
            "structuredContent": {
                "success": False,
                "error": "That app is not in this pod, or is not shared with you.",
            },
        }
    )
    seen = _run_view(
        f"""(async () => {{
          fromHost({{ id: posted[0].id, result: {{ protocolVersion: "2026-01-26", hostCapabilities: {{}}, hostContext: {{}} }} }});
          await settle();
          fromHost({{ method: "ui/notifications/tool-result", params: {refused} }});
          console.log(JSON.stringify({{ state: elements.state.textContent, src: typeof elements.frame.src === "string" ? elements.frame.src : null }}));
          process.exit(0);
        }})();"""
    )
    assert seen == {
        "state": "That app is not in this pod, or is not shared with you.",
        "src": None,
    }
