"""A dev server on a machine, opened through a preview, end to end.

What a person building an app on their machine does: start a dev server, open
it on its preview host, see the page change as they edit it -- the reload
arriving over the dev server's own WebSocket, the way Vite's HMR does -- and
close the preview, after which the host answers nothing.

The dev server is a few lines of Node with no packages, so the test needs no
network inside the machine: it serves one file and, on a socket, says
`update` whenever that file changes and echoes what it is sent.
"""

from __future__ import annotations

import asyncio
import dataclasses
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import websockets
from fastapi import status

from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers import profiles
from sandbox_runtime.paths import WORKSPACE_ROOT
from app.modules.workspace.tests.e2e.test_machines_api_e2e import _start, _warm

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(900)]

DOMAIN = "preview.test"
PORT = 5173

DEV_SERVER = r"""
const http = require('http'), crypto = require('crypto'), fs = require('fs');
const page = process.argv[2];
const server = http.createServer((req, res) => {
  res.setHeader('content-type', 'text/html');
  res.end(fs.readFileSync(page));
});
server.on('upgrade', (req, sock) => {
  const key = crypto.createHash('sha1')
    .update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64');
  sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n' +
    'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + key + '\r\n\r\n');
  const send = (text) => {
    const body = Buffer.from(text);
    sock.write(Buffer.concat([Buffer.from([0x81, body.length]), body]));
  };
  send('connected');
  const watcher = fs.watch(page, () => send('update'));
  sock.on('close', () => watcher.close());
  sock.on('data', (frame) => {
    if ((frame[0] & 15) !== 1) return;
    const length = frame[1] & 127, mask = frame.subarray(2, 6);
    const body = Buffer.from(frame.subarray(6, 6 + length).map((b, i) => b ^ mask[i % 4]));
    send('echo:' + body);
  });
});
server.listen(Number(process.argv[3]), '0.0.0.0');
"""


@pytest.fixture
async def previews(
    monkeypatch,
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
):
    """Previews on `*.preview.test`, served by the in-process backend."""
    del local_sandbox_server, configure_workspace_api_url
    monkeypatch.setenv("MACHINE_PREVIEW_DOMAIN", DOMAIN)
    monkeypatch.setattr(workspace_settings, "machine_preview_domain", DOMAIN)
    monkeypatch.setattr(workspace_settings, "machine_preview_scheme", "http")
    # Docker on a laptop publishes only the runtime's own ports, so the API
    # reaches no other; a deployment's private network reaches every port.
    # Publishing the dev server's port stands in for that network here.
    standard = profiles.workspace_profile
    monkeypatch.setattr(
        profiles,
        "workspace_profile",
        lambda **kwargs: dataclasses.replace(
            standard(**kwargs),
            published_ports=(*standard(**kwargs).published_ports, PORT),
        ),
    )
    await _warm(authenticated_client)
    folder = f"{WORKSPACE_ROOT}/dev-{uuid4().hex[:6]}"
    await _start(authenticated_client, f"mkdir -p {folder}")
    await asyncio.sleep(1)
    for name, body in (("server.js", DEV_SERVER), ("index.html", "<h1>v1</h1>")):
        written = await authenticated_client.put(
            "/machines/me/files/content",
            params={"path": f"{folder}/{name}"},
            content=body.encode(),
        )
        assert written.status_code == status.HTTP_200_OK, written.text
    await _start(
        authenticated_client,
        f"node {folder}/server.js {folder}/index.html {PORT}",
        cwd=folder,
    )
    async with httpx.AsyncClient(
        base_url=backend_server["host_base_url"], timeout=30
    ) as visitor:
        yield {
            "client": authenticated_client,
            "visitor": visitor,
            "port": int(backend_server["host_base_url"].rsplit(":", 1)[1]),
            "page": f"{folder}/index.html",
        }


async def _open(client, visibility: str) -> tuple[dict, str]:
    created = await client.post(
        "/machines/me/previews", json={"port": PORT, "visibility": visibility}
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    preview = created.json()
    host = urlsplit(preview["url"]).hostname
    assert host is not None and host.startswith("p-") and host.endswith(DOMAIN)
    return preview, host


async def _page(visitor, host: str, cookie: str | None = None) -> httpx.Response:
    headers = {"Host": host}
    if cookie:
        headers["Cookie"] = cookie
    return await visitor.get("/", headers=headers)


async def test_a_link_preview_shows_the_page_and_its_live_reload(previews) -> None:
    preview, host = await _open(previews["client"], "link")

    page = await eventually(
        label="the dev server answering through the preview",
        probe=lambda: _page(previews["visitor"], host),
        done=lambda r: r.status_code == status.HTTP_200_OK,
        timeout_seconds=60,
        interval_seconds=1.0,
    )
    assert "v1" in page.text

    async with websockets.connect(
        f"ws://{host}/hmr",
        host="127.0.0.1",
        port=previews["port"],
    ) as socket:
        assert await asyncio.wait_for(socket.recv(), 10) == "connected"
        await socket.send("ping")
        assert await asyncio.wait_for(socket.recv(), 10) == "echo:ping"

        edited = await previews["client"].put(
            "/machines/me/files/content",
            params={"path": previews["page"]},
            content=b"<h1>v2</h1>",
        )
        assert edited.status_code == status.HTTP_200_OK, edited.text
        assert await asyncio.wait_for(socket.recv(), 15) == "update"

    assert "v2" in (await _page(previews["visitor"], host)).text

    closed = await previews["client"].delete(f"/machines/me/previews/{preview['id']}")
    assert closed.status_code == status.HTTP_200_OK, closed.text
    assert closed.json()["revoked_at"] is not None
    gone = await _page(previews["visitor"], host)
    assert gone.status_code == status.HTTP_404_NOT_FOUND


async def test_a_private_preview_opens_only_through_the_persons_session(
    previews,
) -> None:
    preview, host = await _open(previews["client"], "private")
    label = host.split(".", 1)[0][2:]

    stranger = await _page(previews["visitor"], host)
    assert stranger.status_code == status.HTTP_302_FOUND
    assert stranger.headers["location"].endswith(f"/machine-previews/{label}:open")

    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(
            f"ws://{host}/hmr", host="127.0.0.1", port=previews["port"]
        ):
            pass

    opened = await previews["client"].get(
        f"/machine-previews/{label}:open", follow_redirects=False
    )
    assert opened.status_code == status.HTTP_302_FOUND, opened.text
    ticket_url = urlsplit(opened.headers["location"])
    assert ticket_url.hostname == host

    exchanged = await previews["visitor"].get(
        f"{ticket_url.path}?{ticket_url.query}", headers={"Host": host}
    )
    assert exchanged.status_code in (
        status.HTTP_302_FOUND,
        status.HTTP_303_SEE_OTHER,
    ), exchanged.text
    cookie = exchanged.headers["set-cookie"].split(";", 1)[0]

    page = await eventually(
        label="the private page with its cookie",
        probe=lambda: _page(previews["visitor"], host, cookie),
        done=lambda r: r.status_code == status.HTTP_200_OK,
        timeout_seconds=60,
        interval_seconds=1.0,
    )
    assert "v1" in page.text
    assert "frame-ancestors" in page.headers["content-security-policy"]

    forged = await previews["visitor"].get(
        "/__lemma/auth?ticket=not-a-ticket", headers={"Host": host}
    )
    assert forged.status_code != status.HTTP_200_OK or "v1" not in forged.text

    await previews["client"].delete(f"/machines/me/previews/{preview['id']}")
    assert (await _page(previews["visitor"], host, cookie)).status_code == (
        status.HTTP_404_NOT_FOUND
    )
