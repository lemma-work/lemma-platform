"""A saved provider key goes only to the address it was entered for, over HTTP.

The key is write-only: an editor can replace it but never read it. An edit that
moved the profile to a host the editor controls and kept the key would hand it
over on the very next model listing, run or connection test. Two real
`/models` servers on this computer stand in for the two hosts; nothing in the
edit path is replaced.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.config import settings

pytestmark = pytest.mark.e2e

_KEY_FOR_A = "sk-e2e-entered-for-host-a"
_KEY_FOR_B = "sk-e2e-entered-for-host-b"


@contextmanager
def _recording_model_server() -> Iterator[tuple[str, list[str]]]:
    """A `/models` endpoint that remembers every Authorization it was sent."""
    seen: list[str] = []
    body = json.dumps({"data": [{"id": "served-1"}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization") or "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", seen
    finally:
        server.shutdown()
        server.server_close()


async def _fresh_organization(client: AsyncClient) -> str:
    response = await client.post(
        "/organizations", json={"name": f"Key custody {uuid4().hex[:8]}"}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_moving_a_provider_to_another_host_never_sends_it_the_stored_key(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The connection test's completion goes to the deterministic model; its
    # listing still goes to whichever real server the profile names.
    monkeypatch.setattr(settings, "e2e_llm_mode", "mock")
    org_id = await _fresh_organization(authenticated_client)
    with (
        _recording_model_server() as (host_a, seen_by_a),
        _recording_model_server() as (host_b, seen_by_b),
    ):
        created = await authenticated_client.post(
            f"/organizations/{org_id}/agent-runtime/profiles",
            json={
                "source": "OPENAI_COMPATIBLE",
                "name": f"Provider {uuid4().hex[:8]}",
                "base_url": host_a,
                "api_key": _KEY_FOR_A,
            },
        )
        assert created.status_code == status.HTTP_201_CREATED, created.text
        assert f"Bearer {_KEY_FOR_A}" in seen_by_a
        profile_path = (
            f"/organizations/{org_id}/agent-runtime/profiles/{created.json()['id']}"
        )

        moved_without_key = await authenticated_client.patch(
            profile_path,
            json={"source": "OPENAI_COMPATIBLE", "base_url": host_b},
        )
        assert moved_without_key.status_code == status.HTTP_400_BAD_REQUEST
        assert "Re-enter the API key" in moved_without_key.json()["message"]
        assert seen_by_b == []

        tested = await authenticated_client.post(f"{profile_path}/test")
        assert tested.status_code == status.HTTP_200_OK, tested.text
        assert seen_by_b == []

        moved_with_key = await authenticated_client.patch(
            profile_path,
            json={
                "source": "OPENAI_COMPATIBLE",
                "base_url": host_b,
                "api_key": _KEY_FOR_B,
            },
        )
        assert moved_with_key.status_code == status.HTTP_200_OK, moved_with_key.text
        assert seen_by_b
        assert set(seen_by_b) == {f"Bearer {_KEY_FOR_B}"}
