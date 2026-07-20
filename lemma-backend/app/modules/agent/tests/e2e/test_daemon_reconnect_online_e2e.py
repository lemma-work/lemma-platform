"""Regression coverage for daemon websocket reconnect status ownership."""

from __future__ import annotations

import contextlib
import json
from uuid import uuid4

import pytest
from asgiref.testing import ApplicationCommunicator

from app.modules.agent.api.controllers import runtime_config_controller


pytestmark = pytest.mark.e2e


async def test_daemon_websocket_reconnect_keeps_harness_row_online(
    authenticated_client,
    fixed_test_user,
    test_app,
    monkeypatch,
):
    """A stale connection's disconnect must not mark the replacement offline."""
    # Profile bootstrap is unrelated to this websocket ownership race. Keep the
    # daemon.ready handshake real without coupling this regression to bootstrap.
    async def skip_profile_bootstrap(**_kwargs):
        return None

    monkeypatch.setattr(
        runtime_config_controller,
        "_ensure_user_daemon_default_profile",
        skip_profile_bootstrap,
    )

    device_key = f"reconnect-e2e-{uuid4().hex[:8]}"
    harness_catalog = {
        "CODEX": {
            "available": True,
            "display_name": "Codex reconnect E2E",
            "models": ["gpt-5.5"],
        }
    }
    scope = {
        "type": "websocket",
        "path": "/me/agent-runtime/daemon/ws",
        "raw_path": b"/me/agent-runtime/daemon/ws",
        "query_string": b"",
        "headers": [
            (
                b"authorization",
                f"Bearer {fixed_test_user['token']}".encode(),
            ),
            (b"host", b"testserver"),
        ],
        "scheme": "ws",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "subprotocols": [],
    }

    async def connect(communicator: ApplicationCommunicator) -> str:
        await communicator.send_input({"type": "websocket.connect"})
        accepted = await communicator.receive_output(timeout=5)
        assert accepted["type"] == "websocket.accept"

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "daemon.ready",
                        "payload": {
                            "device_key": device_key,
                            "display_name": "Reconnect E2E laptop",
                            "device_info": {"platform": "test"},
                            "harness_catalog": harness_catalog,
                        },
                    }
                ),
            }
        )
        ready_ack = await communicator.receive_output(timeout=5)
        assert ready_ack["type"] == "websocket.send"
        ready_payload = json.loads(ready_ack["text"])
        assert ready_payload["type"] == "daemon.ready_ack"
        return ready_payload["daemon_id"]

    async def disconnect(communicator: ApplicationCommunicator) -> None:
        with contextlib.suppress(Exception):
            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
            await communicator.wait(timeout=5)

    first_connection = ApplicationCommunicator(test_app, scope.copy())
    second_connection = ApplicationCommunicator(test_app, scope.copy())
    try:
        daemon_id = await connect(first_connection)

        first_status = await authenticated_client.get("/agent-runtime/harnesses")
        assert first_status.status_code == 200, first_status.text
        first_rows = [
            item
            for item in first_status.json()["items"]
            if item.get("daemon_id") == daemon_id
        ]
        assert len(first_rows) == 1
        assert first_rows[0]["daemon_status"] == "ONLINE"

        replacement_id = await connect(second_connection)
        assert replacement_id == daemon_id

        # The first websocket is stale now. Its finally block must not clobber
        # the ONLINE row owned by the replacement websocket.
        await disconnect(first_connection)

        final_status = await authenticated_client.get("/agent-runtime/harnesses")
        assert final_status.status_code == 200, final_status.text
        final_rows = [
            item
            for item in final_status.json()["items"]
            if item.get("daemon_id") == daemon_id
        ]
        assert len(final_rows) == 1
        assert final_rows[0]["daemon_status"] == "ONLINE"
    finally:
        await disconnect(second_connection)
