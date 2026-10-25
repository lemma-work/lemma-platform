"""Only the person who opened a call may hold it.

The call socket skips the HTTP auth gate and authenticates itself, so these
are the checks that stand between one pod member and another's call. Each is
refused before any voice is opened: no provider is contacted.
"""

from __future__ import annotations

import pytest

from app.modules.calls.config import calls_settings

pytestmark = pytest.mark.e2e


async def _closed_with(base_url: str, path: str, token: str | None) -> int:
    import websockets

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = base_url.replace("http://", "ws://") + path
    async with websockets.connect(url, additional_headers=headers) as socket:
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await socket.recv()
    assert closed.value.rcvd is not None
    return closed.value.rcvd.code


async def test_another_member_and_no_session_are_refused_the_call(
    scenario, backend_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A key so the server offers calls; the refusals happen before it is used.
    monkeypatch.setattr(calls_settings, "gemini_api_key", "test-key-unused")
    await scenario.create_org_with_pod(name_prefix="calls-access")
    pod_id = scenario.pod_id
    opened = await scenario.owner_client.post(
        f"/pods/{pod_id}/calls", json={"display_name": "Ria"}
    )
    assert opened.status_code == 201, opened.text
    socket_path = f"/pods/{pod_id}/calls/{opened.json()['call_id']}/socket"
    colleague = await scenario.create_user("calls-colleague")
    await scenario.add_user_to_pod(user=colleague, role="POD_USER")
    base_url = backend_server["host_base_url"]

    assert await _closed_with(base_url, socket_path, colleague["token"]) == 4404
    assert await _closed_with(base_url, socket_path, None) == 4401
