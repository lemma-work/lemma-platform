from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from starlette.routing import Mount


os.environ.setdefault(
    "WORKSPACE_RUNTIME_CREDENTIAL_KEY",
    "embedded-workspace-runtime-key-000000",
)
os.environ.setdefault("AGENTBOX_WORKSPACE_IMAGE", "workspace:test")
os.environ.setdefault("AGENTBOX_FUNCTION_IMAGE", "function:test")

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
local_app_module = importlib.import_module("local_app")


def test_local_app_runs_the_worker_in_the_backend_process() -> None:
    """The managed-local topology is two processes: this one, and the frontend."""

    app = local_app_module.create_local_app()

    assert app.state.embedded_worker is True


def test_local_app_embeds_no_lemma_application() -> None:
    """The AgentBox manager used to be mounted here and dialled over loopback.

    Provisioning is in-process now, so there is no embedded Lemma application
    left -- and one reappearing would mean a service boundary had been put back
    inside a single process, which is the thing this work removed. The MCP
    mounts are a different kind of thing: protocol endpoints of this app, not a
    second application it talks to over HTTP.
    """

    app = local_app_module.create_local_app()

    assert app.state.embedded_apps == ()
    assert not [
        route
        for route in app.routes
        if isinstance(route, Mount) and "agentbox" in route.path
    ]
