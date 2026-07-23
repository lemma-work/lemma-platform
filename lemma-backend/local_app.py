"""Managed-local Lemma application entrypoint.

This is the two-process desktop/backend topology: one Python process owns the
API, Streaq worker, scheduler, and AgentBox manager, while the Next frontend is
the only other Lemma application process. Infrastructure and sandbox compute
remain outside this process.
"""

from __future__ import annotations

from fastapi import FastAPI

from agentbox.api.app import app as agentbox_app
from app.app import create_app as create_api_app
from app.standalone import EmbeddedApp, build_standalone_app

AGENTBOX_MOUNT_PATH = "/internal/agentbox"


def create_local_app() -> FastAPI:
    from app.events import streaq_worker

    local_app = build_standalone_app(
        create_api_app(),
        streaq_worker,
        embedded_apps=(EmbeddedApp(AGENTBOX_MOUNT_PATH, agentbox_app),),
    )
    local_app.state.embedded_agentbox = True
    return local_app


app = create_local_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "local_app:app",
        host="0.0.0.0",
        port=8711,
        reload=False,
        ws="websockets-sansio",
        access_log=False,
    )
