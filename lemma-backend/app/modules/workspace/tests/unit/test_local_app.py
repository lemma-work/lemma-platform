from __future__ import annotations

import base64
import importlib
import os
import sys
from pathlib import Path

from starlette.routing import Mount


os.environ.setdefault("AGENTBOX_API_KEY", "embedded-agentbox-test-key")
os.environ.setdefault(
    "AGENTBOX_API_URL", "http://127.0.0.1:8711/internal/agentbox"
)
os.environ.setdefault("AGENTBOX_PROVIDER", "docker")
os.environ.setdefault(
    "AGENTBOX_ENDPOINT_STATE_KEYS",
    base64.urlsafe_b64encode(b"embedded-agentbox-endpoint-state-key").decode(),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
local_app_module = importlib.import_module("local_app")


def test_local_app_embeds_agentbox_in_backend_process() -> None:
    app = local_app_module.create_local_app()

    assert app.state.embedded_worker is True
    assert app.state.embedded_agentbox is True
    assert app.state.embedded_apps == (local_app_module.AGENTBOX_MOUNT_PATH,)

    mount = next(
        route
        for route in app.routes
        if isinstance(route, Mount)
        and route.path == local_app_module.AGENTBOX_MOUNT_PATH
    )
    assert mount.app.state.shutdown_process_telemetry is False
