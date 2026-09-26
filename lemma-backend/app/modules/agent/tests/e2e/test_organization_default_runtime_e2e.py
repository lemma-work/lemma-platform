"""The organization's default model, and testing a saved provider, over HTTP.

Each test makes its own organization: the default is organization-wide state,
and the shared test organization would hand it to every other test that starts
a run there.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.organization_default import ORGANIZATION_DEFAULT_KEY
from app.modules.agent.services.pod_runtime_defaults import (
    default_agent_runtime_for_pod,
)
from app.modules.test_support.e2e_authz import (
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

# Nothing listens on the discard port, so discovery finds no models and the
# names given at creation stand -- no stand-in for discovery needed.
_NOTHING_LISTENING = "http://127.0.0.1:9/v1"


@contextmanager
def _model_server(*, reject: list[bool]) -> Iterator[str]:
    """A real `/models` endpoint on this computer; 401 while ``reject[0]``."""
    body = json.dumps({"data": [{"id": "served-1"}, {"id": "served-2"}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if reject[0]:
                self.send_response(401)
                self.end_headers()
                return
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
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


async def _fresh_organization(client: AsyncClient) -> str:
    response = await client.post(
        "/organizations", json={"name": f"Default model {uuid4().hex[:8]}"}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _provider(
    client: AsyncClient,
    org_id: str,
    *,
    base_url: str = _NOTHING_LISTENING,
    model_names: list[str] | None = None,
) -> dict:
    response = await client.post(
        f"/organizations/{org_id}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Provider {uuid4().hex[:8]}",
            "base_url": base_url,
            "api_key": "sk-e2e-not-real",
            **({"model_names": model_names} if model_names else {}),
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _listing(client: AsyncClient, org_id: str) -> dict:
    response = await client.get(
        f"/organizations/{org_id}/agent-runtime/profiles",
        params={"include_disabled": True},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _pod_runtime(pod_id: str):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        return await default_agent_runtime_for_pod(uow, pod_id=UUID(pod_id))


def _marked(listing: dict) -> set[str]:
    return {
        item["id"]
        for item in listing["items"]
        if ORGANIZATION_DEFAULT_KEY in (item.get("metadata") or {})
    }


@pytest.mark.asyncio
async def test_an_owner_chooses_the_model_every_teammate_runs_on(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
) -> None:
    org_id = await _fresh_organization(authenticated_client)
    alpha = await _provider(
        authenticated_client, org_id, model_names=["alpha-1", "alpha-2"]
    )
    beta = await _provider(
        authenticated_client, org_id, model_names=["beta-1", "beta-2"]
    )
    pod = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Default model pod {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
    )
    assert pod.status_code == status.HTTP_201_CREATED, pod.text
    pod_id = pod.json()["id"]
    default_path = f"/organizations/{org_id}/agent-runtime/default"

    assert (await _listing(authenticated_client, org_id))[
        "organization_default_runtime"
    ] is None

    chosen = await authenticated_client.put(
        default_path, json={"profile_id": beta["id"], "model_name": "beta-2"}
    )
    assert chosen.status_code == status.HTTP_200_OK, chosen.text
    assert chosen.json() == {"profile_id": beta["id"], "model_name": "beta-2"}

    listing = await _listing(authenticated_client, org_id)
    expected = {"profile_id": beta["id"], "model_name": "beta-2"}
    assert listing["organization_default_runtime"] == expected
    # What the picker labels "Organization default" is what a run gets.
    assert listing["default_runtime"] == expected
    runtime = await _pod_runtime(pod_id)
    assert (runtime.profile_id, runtime.model_name) == (beta["id"], "beta-2")

    # Moving the default moves the mark: only one profile ever carries it.
    moved = await authenticated_client.put(
        default_path, json={"profile_id": alpha["id"]}
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text
    assert moved.json() == {"profile_id": alpha["id"], "model_name": "alpha-1"}
    assert _marked(await _listing(authenticated_client, org_id)) == {alpha["id"]}

    # A model the provider does not list is refused, not stored.
    unknown = await authenticated_client.put(
        default_path, json={"profile_id": beta["id"], "model_name": "gamma-9"}
    )
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST, unknown.text

    # A member may see the choice but not make or clear it.
    member = await signup_user(async_client, "default-model-member")
    await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=member
    )
    as_member = auth_headers(member)
    refused = await async_client.put(
        default_path, json={"profile_id": beta["id"]}, headers=as_member
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    refused_clear = await async_client.delete(default_path, headers=as_member)
    assert refused_clear.status_code == status.HTTP_403_FORBIDDEN
    seen = await async_client.get(
        f"/organizations/{org_id}/agent-runtime/profiles", headers=as_member
    )
    assert seen.json()["organization_default_runtime"]["profile_id"] == alpha["id"]

    # Archiving the default drops the mark with it, so restoring the provider
    # later does not quietly make it everyone's model again.
    archived = await authenticated_client.delete(
        f"/organizations/{org_id}/agent-runtime/profiles/{alpha['id']}"
    )
    assert archived.status_code == status.HTTP_204_NO_CONTENT, archived.text
    listing = await _listing(authenticated_client, org_id)
    assert listing["organization_default_runtime"] is None
    assert _marked(listing) == set()
    restored = await authenticated_client.post(
        f"/organizations/{org_id}/agent-runtime/profiles/{alpha['id']}/restore"
    )
    assert restored.status_code == status.HTTP_200_OK, restored.text
    assert (await _listing(authenticated_client, org_id))[
        "organization_default_runtime"
    ] is None

    # Clearing removes it wherever it is.
    again = await authenticated_client.put(
        default_path, json={"profile_id": beta["id"]}
    )
    assert again.status_code == status.HTTP_200_OK, again.text
    cleared = await authenticated_client.delete(default_path)
    assert cleared.status_code == status.HTTP_204_NO_CONTENT, cleared.text
    listing = await _listing(authenticated_client, org_id)
    assert listing["organization_default_runtime"] is None
    assert _marked(listing) == set()


@pytest.mark.asyncio
async def test_testing_a_saved_provider_in_mock_llm_mode(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The listing goes to a real server on this computer; the completion to
    # the deterministic mock model. Nothing in the check itself is replaced.
    monkeypatch.setattr(settings, "e2e_llm_mode", "mock")
    org_id = await _fresh_organization(authenticated_client)
    reject = [False]
    with _model_server(reject=reject) as base_url:
        served = await _provider(authenticated_client, org_id, base_url=base_url)
        test_path = (
            f"/organizations/{org_id}/agent-runtime/profiles/{served['id']}/test"
        )

        working = await authenticated_client.post(test_path)
        assert working.status_code == status.HTTP_200_OK, working.text
        assert working.json() == {
            "ok": True,
            "message": f"{served['name']} answered using served-1.",
            "models": ["served-1", "served-2"],
        }

        # The key was revoked after it was saved.
        reject[0] = True
        refused = await authenticated_client.post(test_path)
        assert refused.status_code == status.HTTP_200_OK, refused.text
        assert refused.json() == {
            "ok": False,
            "message": f"{served['name']} rejected this API key.",
            "models": None,
        }

    stopped = await _provider(authenticated_client, org_id, model_names=["local-1"])
    down = await authenticated_client.post(
        f"/organizations/{org_id}/agent-runtime/profiles/{stopped['id']}/test"
    )
    assert down.status_code == status.HTTP_200_OK, down.text
    assert down.json() == {
        "ok": False,
        "message": "The model server isn't running on this computer.",
        "models": None,
    }

    missing = await authenticated_client.post(
        f"/organizations/{org_id}/agent-runtime/profiles/{uuid4()}/test"
    )
    assert missing.status_code == status.HTTP_404_NOT_FOUND
