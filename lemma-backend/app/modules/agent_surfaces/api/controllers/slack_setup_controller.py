"""The Slack app manifest — the one surface read that belongs to no pod.

It describes this *deployment*: its event URL, its OAuth callback, the scopes
its code reads. The same document for every pod and org, and needed *before*
there is anything to scope it to — the app it creates is what issues the client
id that connects the account a surface is built on. Scoping it to a pod only
made it unreadable until after the point where it was needed.

Its own module rather than a corner of ``surface_controller``, which is the
pod-scoped surface resource and already over the size the architecture ratchet
allows.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.api.dependencies import CurrentUser
from app.modules.agent_surfaces.platforms.slack.manifest import (
    build_slack_app_manifest,
)

# Deployment-wide reads that belong to no pod. The Slack manifest is the whole
# of it: it describes this *deployment* — its event URL, its OAuth callback, the
# scopes its code reads — and is the same document for every pod and org. It is
# also what you need *before* you have anything to scope it to, since the app it
# creates is what issues the client id that connects the account a surface is
# built on. Scoping it to a pod only made it unreadable until after the point
# where it was needed.
platform_router = APIRouter(prefix="/surface-setup", tags=["Agent Surfaces"])


@platform_router.get(
    "/slack/manifest",
    operation_id="agent.surface.slack_manifest",
)
async def get_slack_app_manifest(
    user: CurrentUser,
    agent_name: Annotated[
        str | None,
        Query(
            description=(
                "Name the app after this agent, so a bot made for one agent "
                "arrives already called by its name. Defaults to Lemma."
            ),
            max_length=255,
        ),
    ] = None,
) -> dict:
    """The Slack app manifest to paste when running your own Slack app.

    Served rather than copied out of the repo so the URLs always match the
    deployment answering this request, and the scopes always match the code
    that will consume the events.

    Signed-in access is the only gate, and that is enough: every value in here
    is already public — this deployment's URLs and the scopes its own code
    asks for. It carries no credential and reveals nothing about a pod: the
    agent name is supplied by the caller and echoed back, never read from one.
    """
    del user
    return build_slack_app_manifest(agent_name=agent_name)
