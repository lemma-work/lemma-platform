"""A shared page is live, and carries what it shows.

A public link is no snapshot: an edit is what the next reader sees, and a page
brings the pictures, stylesheets and scripts it embeds — only those, only while
it still embeds them, and only what the person who shared it may still read.
These run against the real app, Redis and Postgres, with the live-answer cache
turned off so each fetch asks the pod as it is at that moment.
"""

from __future__ import annotations

from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.modules.datastore.api import shared_link_files
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


@pytest.fixture(autouse=True)
def _ask_every_time(monkeypatch):
    monkeypatch.setattr(shared_link_files, "LIVE_CACHE_SECONDS", 0)


async def _share(api: DatastoreApi, path: str, body: dict | None = None) -> str:
    resp = await api.request(
        "POST",
        f"/pods/{api.pod_id}/datastore/files/signed-url",
        params={"path": path},
        json=body or {},
    )
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    return resp.json()["signed_url"].rstrip("/").rsplit("/", 1)[-1]


def _embedded(code: str, reference: str) -> str:
    return f"/s/{code}/a?ref={quote(reference, safe='')}"


async def test_an_edit_is_what_the_next_reader_sees(
    pod_api: DatastoreApi, async_client: AsyncClient
):
    await pod_api.create_folder("/pages")
    page = await pod_api.upload_file("Plan.md", b"# First", directory_path="/pages")
    code = await _share(pod_api, page["path"])

    first = await async_client.get(f"/s/{code}")
    assert first.status_code == status.HTTP_200_OK, first.text
    assert first.content == b"# First"
    stale_etag = first.headers["etag"]

    await pod_api.update_file(page["path"], content=b"# Second", filename="Plan.md")

    # The browser holding the first copy asks whether it is still current. It
    # is not, so it gets the edit rather than a 304 that would pin it forever.
    again = await async_client.get(f"/s/{code}", headers={"If-None-Match": stale_etag})
    assert again.status_code == status.HTTP_200_OK, again.text
    assert again.content == b"# Second"
    assert again.headers["etag"] != stale_etag


async def test_a_page_carries_the_pictures_it_shows_without_spending_opens(
    pod_api: DatastoreApi, async_client: AsyncClient
):
    await pod_api.create_folder("/pages")
    await pod_api.create_folder("/pages/Plan-files")
    await pod_api.upload_file(
        "chart.png", PNG, directory_path="/pages/Plan-files", content_type="image/png"
    )
    page = await pod_api.upload_file(
        "Plan.md",
        b"# Plan\n\n![chart](Plan-files/chart.png)\n",
        directory_path="/pages",
    )
    code = await _share(pod_api, page["path"], {"max_hits": 1})

    # Many readers' worth of the picture, and the page's single open is intact.
    for _ in range(5):
        picture = await async_client.get(_embedded(code, "Plan-files/chart.png"))
        assert picture.status_code == status.HTTP_200_OK, picture.text
        assert picture.content == PNG
        assert picture.headers["content-type"].startswith("image/png")

    opened = await async_client.get(f"/s/{code}")
    assert opened.status_code == status.HTTP_200_OK, opened.text

    # The open was the page's last; what it carried goes with it.
    spent = await async_client.get(_embedded(code, "Plan-files/chart.png"))
    assert spent.status_code == status.HTTP_410_GONE


async def test_only_what_the_page_embeds_now(
    pod_api: DatastoreApi, async_client: AsyncClient
):
    await pod_api.create_folder("/pages")
    await pod_api.upload_file(
        "chart.png", PNG, directory_path="/pages", content_type="image/png"
    )
    await pod_api.upload_file("budget.md", b"# Numbers", directory_path="/pages")
    page = await pod_api.upload_file(
        "Plan.md",
        b"![chart](chart.png)\n\nSee [the budget](budget.md).\n",
        directory_path="/pages",
    )
    code = await _share(pod_api, page["path"])

    assert (await async_client.get(_embedded(code, "chart.png"))).status_code == 200

    # Linked, not embedded: another document, which is not what was shared.
    linked = await async_client.get(_embedded(code, "budget.md"))
    assert linked.status_code == status.HTTP_404_NOT_FOUND
    # A path the page never mentions, however real.
    assert (
        await async_client.get(_embedded(code, "/pages/budget.md"))
    ).status_code == status.HTTP_404_NOT_FOUND

    # Take the picture out of the page and it stops travelling with it.
    await pod_api.update_file(page["path"], content=b"No pictures.", filename="Plan.md")
    assert (
        await async_client.get(_embedded(code, "chart.png"))
    ).status_code == status.HTTP_404_NOT_FOUND


async def test_a_pod_page_never_carries_a_private_file(
    pod_api: DatastoreApi, async_client: AsyncClient
):
    """Anyone in the pod can edit a pod page; writing a path into it must not
    publish a file only the person who shared the page can read."""
    await pod_api.create_folder("/me/private")
    await pod_api.upload_file(
        "secret.png", PNG, directory_path="/me/private", content_type="image/png"
    )
    await pod_api.create_folder("/pages")
    pod_page = await pod_api.upload_file(
        "Plan.md", b"![](/me/private/secret.png)\n", directory_path="/pages"
    )
    code = await _share(pod_api, pod_page["path"])
    refused = await async_client.get(_embedded(code, "/me/private/secret.png"))
    assert refused.status_code == status.HTTP_404_NOT_FOUND

    # The same picture in the owner's own page is theirs to share.
    own_page = await pod_api.upload_file(
        "notes.md", b"![](secret.png)\n", directory_path="/me/private"
    )
    own_code = await _share(pod_api, own_page["path"])
    carried = await async_client.get(_embedded(own_code, "secret.png"))
    assert carried.status_code == status.HTTP_200_OK, carried.text
    assert carried.content == PNG


async def test_an_html_report_brings_its_stylesheet_and_script(
    pod_api: DatastoreApi, async_client: AsyncClient
):
    await pod_api.create_folder("/reports")
    await pod_api.create_folder("/reports/assets")
    await pod_api.upload_file(
        "style.css",
        b"body{color:red}",
        directory_path="/reports/assets",
        content_type="text/css",
    )
    await pod_api.upload_file(
        "chart.js",
        b"document.title='drawn'",
        directory_path="/reports/assets",
        content_type="text/javascript",
    )
    page = await pod_api.upload_file(
        "q3.html",
        b"<html><head><link rel='stylesheet' href='assets/style.css'>"
        b"<script src='./assets/chart.js'></script></head>"
        b"<body><a href='q2.html'>Last quarter</a></body></html>",
        directory_path="/reports",
        content_type="text/html",
    )
    code = await _share(pod_api, page["path"])

    css = await async_client.get(_embedded(code, "assets/style.css"))
    assert css.status_code == status.HTTP_200_OK, css.text
    assert css.content == b"body{color:red}"
    script = await async_client.get(_embedded(code, "./assets/chart.js"))
    assert script.status_code == status.HTTP_200_OK, script.text
    assert (
        await async_client.get(_embedded(code, "q2.html"))
    ).status_code == status.HTTP_404_NOT_FOUND


async def test_a_file_that_is_not_a_page_carries_nothing(
    pod_api: DatastoreApi, async_client: AsyncClient
):
    await pod_api.create_folder("/data")
    await pod_api.upload_file(
        "chart.png", PNG, directory_path="/data", content_type="image/png"
    )
    listing = await pod_api.upload_file(
        "list.txt", b"chart.png", directory_path="/data", content_type="text/plain"
    )
    code = await _share(pod_api, listing["path"])
    assert (
        await async_client.get(_embedded(code, "chart.png"))
    ).status_code == status.HTTP_404_NOT_FOUND


async def test_an_agents_page_carries_only_what_the_agent_may_read(
    pod_api: DatastoreApi,
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    test_app,
):
    """PS-ACCESS-020 at fetch time: the person's access intersected with the
    agent's, never the person's alone — or an agent narrower than its person
    could publish what it was never granted by embedding it in a page."""
    pod_id = str(pod_api.pod_id)
    await pod_api.create_folder("/library")
    await pod_api.create_folder("/vault")
    await pod_api.upload_file(
        "cover.png", PNG, directory_path="/library", content_type="image/png"
    )
    await pod_api.upload_file(
        "payroll.png", PNG, directory_path="/vault", content_type="image/png"
    )
    page = await pod_api.upload_file(
        "brief.md",
        b"![](cover.png)\n![](/vault/payroll.png)\n",
        directory_path="/library",
    )

    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": f"narrow-{uuid4().hex[:6]}", "instruction": "Answer briefly."},
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text
    agent_body = agent.json()
    perms = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_body['name']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent_body["name"],
                    "permission_ids": ["agent.read"],
                },
                {
                    "resource_type": "folder",
                    "resource_name": "/library",
                    "permission_ids": ["folder.read"],
                },
            ]
        },
    )
    assert perms.status_code == status.HTTP_200_OK, perms.text
    me = await authenticated_client.get("/users/me")
    user_id = me.json()["id"]

    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(agent_body["id"]),
        pod_id=UUID(pod_id),
        session_id=uuid4().hex,
        invoked_by_user_id=UUID(user_id),
        workload_name=agent_body["name"],
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as agent_client:
        minted = await agent_client.post(
            f"/pods/{pod_id}/datastore/files/signed-url",
            params={"path": page["path"]},
            json={},
        )
    assert minted.status_code == status.HTTP_201_CREATED, minted.text
    agent_code = minted.json()["signed_url"].rsplit("/", 1)[-1]

    assert (
        await async_client.get(_embedded(agent_code, "cover.png"))
    ).status_code == status.HTTP_200_OK
    assert (
        await async_client.get(_embedded(agent_code, "/vault/payroll.png"))
    ).status_code == status.HTTP_404_NOT_FOUND

    # The person, sharing the same page themselves, may carry both.
    person_code = await _share(pod_api, page["path"])
    assert (
        await async_client.get(_embedded(person_code, "/vault/payroll.png"))
    ).status_code == status.HTTP_200_OK
