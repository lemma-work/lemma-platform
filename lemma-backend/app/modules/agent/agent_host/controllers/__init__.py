"""The Agent Host's HTTP routes, which the agent module's router registers."""

from __future__ import annotations

from fastapi import APIRouter


def routers() -> list[APIRouter]:
    from app.modules.agent.agent_host.controllers.hosts import router as hosts
    from app.modules.agent.agent_host.controllers.legacy import router as legacy
    from app.modules.agent.agent_host.controllers.link import router as link
    from app.modules.agent.agent_host.controllers.transfers import (
        router as transfers,
    )

    return [hosts, link, transfers, legacy]
