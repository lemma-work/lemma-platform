"""A pod's apps, opened inside an AI tool through its connection.

Driven as ChatGPT drives it: tools listed and called on the public mount with
the connection's own token, and the token the app view mints used against the
API the way the framed app uses it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.apps.tests.e2e.test_app_e2e import build_dist_archive
from app.modules.mcp_access.tests.e2e.test_mcp_oauth_flow_e2e import (
    _connect,
    mcp_client,
)

pytestmark = pytest.mark.e2e

__all__ = ["mcp_client"]


async def _deployed_app(client, pod_id: str) -> str:
    slug = f"desk-{uuid4().hex[:6]}"
    created = await client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": slug,
            "public_slug": slug,
            "description": "Orders, by customer.",
            "visibility": "PUBLIC",
        },
    )
    assert created.status_code == 201, created.text
    uploaded = await client.post(
        f"/pods/{pod_id}/apps/{slug}/bundle",
        files={
            "dist_archive": ("dist.zip", build_dist_archive("DESK"), "application/zip")
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    return slug


async def test_a_connection_that_may_change_things_opens_the_pods_apps(
    mcp_client, authenticated_client, test_pod, other_pod, monkeypatch
):
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")
    pod_id = test_pod["id"]
    slug = await _deployed_app(authenticated_client, pod_id)
    await mcp_client.register()
    access = (
        await _connect(mcp_client, authenticated_client, pod_id, "pod:read pod:write")
    )["access_token"]

    listed = await mcp_client.rpc(pod_id, access, "tools/list")
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    opener = tools[f"lemma_open_app_{slug.replace('-', '_')}"]
    assert opener["title"] == f"Open {slug}"
    assert "Orders, by customer." in opener["description"]
    assert opener["_meta"]["ui"] == {"resourceUri": f"ui://lemma/apps/{slug}"}
    assert opener["annotations"]["readOnlyHint"] is True
    for view_only in ("lemma_app_session", "lemma_app_access"):
        assert tools[view_only]["_meta"]["ui"] == {"visibility": ["app"]}

    read = await mcp_client.rpc(
        pod_id, access, "resources/read", {"uri": f"ui://lemma/apps/{slug}"}
    )
    [view] = read.json()["result"]["contents"]
    assert view["mimeType"] == "text/html;profile=mcp-app"
    origin = f"http://{slug}.apps.example.test"
    assert view["_meta"]["ui"]["csp"]["frameDomains"] == [origin]

    opened = await mcp_client.rpc(
        pod_id, access, "tools/call", {"name": opener["name"], "arguments": {}}
    )
    assert opened.json()["result"]["structuredContent"]["app"] == {
        "name": slug,
        "slug": slug,
        "url": origin,
    }

    signed = (
        await mcp_client.rpc(
            pod_id, access, "tools/call", {"name": "lemma_app_session", "arguments": {}}
        )
    ).json()["result"]
    token = signed["_meta"]["lemma/app"]["token"]
    # The view's own call: the token is handed to the view, never put where
    # a host shows the model.
    assert token not in str(signed["structuredContent"])
    assert token not in str(signed["content"])

    as_the_app = {"Authorization": f"Bearer {token}"}
    inside = await mcp_client.http.get(
        f"/pods/{pod_id}/datastore/tables", headers=as_the_app
    )
    assert inside.status_code == 200, inside.text
    elsewhere = await mcp_client.http.get(
        f"/pods/{other_pod['id']}/datastore/tables", headers=as_the_app
    )
    assert elsewhere.status_code == 403

    ticket = (
        await mcp_client.rpc(
            pod_id,
            access,
            "tools/call",
            {"name": "lemma_app_access", "arguments": {"app": slug}},
        )
    ).json()["result"]
    assert ticket["_meta"]["lemma/app"]["ticket"]
    foreign = (
        await mcp_client.rpc(
            pod_id,
            access,
            "tools/call",
            {"name": "lemma_app_access", "arguments": {"app": "not-in-this-pod"}},
        )
    ).json()["result"]
    assert foreign["isError"] is True


async def test_a_read_only_connection_is_offered_no_apps(
    mcp_client, authenticated_client, test_pod, monkeypatch
):
    """An app writes, and the token it would hold is not narrowed by the
    connection's scopes, so reading only means the views and no apps."""
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")
    pod_id = test_pod["id"]
    await _deployed_app(authenticated_client, pod_id)
    await mcp_client.register()
    access = (await _connect(mcp_client, authenticated_client, pod_id, "pod:read"))[
        "access_token"
    ]

    listed = await mcp_client.rpc(pod_id, access, "tools/list")
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert not {name for name in names if name.startswith("lemma_open_app_")}
    assert not names & {"lemma_app_session", "lemma_app_access"}

    refused = (
        await mcp_client.rpc(
            pod_id, access, "tools/call", {"name": "lemma_app_session", "arguments": {}}
        )
    ).json()["result"]
    assert refused["isError"] is True
    assert "_meta" not in refused or not refused["_meta"]
