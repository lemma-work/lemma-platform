from __future__ import annotations

from typing import Any

from ..errors import LemmaNotFoundError
from ..openapi_client.api.agent_surfaces import (
    agent_surface_channels,
    agent_surface_available,
    agent_surface_create,
    agent_surface_delete,
    agent_surface_get,
    agent_surface_list,
    agent_surface_send,
    agent_surface_setup,
    agent_surface_setup_guide,
    agent_surface_slack_manifest,
    agent_surface_telegram_managed_get,
    agent_surface_telegram_managed_start,
    agent_surface_update,
)
from ..openapi_client.api.agent_surfaces_me import (
    agent_surface_list_mine,
    agent_surface_set_my_default,
)
from ..openapi_client.models.agent_surface_list_response import AgentSurfaceListResponse
from ..openapi_client.models.agent_surface_response import AgentSurfaceResponse
from ..openapi_client.models.available_surface_channels_response import (
    AvailableSurfaceChannelsResponse,
)
from ..openapi_client.models.available_surfaces_response import (
    AvailableSurfacesResponse,
)
from ..openapi_client.models.set_default_surface_request import SetDefaultSurfaceRequest
from ..openapi_client.models.surface_create_request import SurfaceCreateRequest
from ..openapi_client.models.surface_platform_setup_guide import (
    SurfacePlatformSetupGuide,
)
from ..openapi_client.models.surface_send_request import SurfaceSendRequest
from ..openapi_client.models.surface_send_response import SurfaceSendResponse
from ..openapi_client.models.surface_setup_response import SurfaceSetupResponse
from ..openapi_client.models.surface_update_request import SurfaceUpdateRequest
from ..openapi_client.models.telegram_managed_bot_setup_request import (
    TelegramManagedBotSetupRequest,
)
from ..openapi_client.models.telegram_managed_bot_setup_response import (
    TelegramManagedBotSetupResponse,
)
from ..openapi_client.models.user_surfaces_response import UserSurfacesResponse
from .base import BoundResource, Resource


class PodSurfaces(BoundResource):
    """Agent surfaces, addressed by ``name`` (unique per pod).

    A pod may hold several surfaces of the same platform (different bots/accounts,
    each routed to its own agent); ``name`` defaults to the lowercased platform,
    so the common single-surface-per-platform case needs no explicit name.
    ``create`` provisions a surface, ``update`` applies a partial patch by name,
    ``send`` delivers a proactive message to a pod member over an existing thread,
    and ``setup``/``setup_guide`` return the live/pre-creation checklist.
    """

    def list(self, *, limit: int = 100) -> AgentSurfaceListResponse:
        return self._call(agent_surface_list, self._pod_uuid(), limit=limit)

    def available(self) -> AvailableSurfacesResponse:
        return self._call(agent_surface_available, self._pod_uuid())

    def create(self, request: SurfaceCreateRequest | dict) -> AgentSurfaceResponse:
        return self._call(
            agent_surface_create,
            self._pod_uuid(),
            body=request,
            body_model=SurfaceCreateRequest,
        )

    def update(
        self, name: str, request: SurfaceUpdateRequest | dict
    ) -> AgentSurfaceResponse:
        return self._call(
            agent_surface_update,
            self._pod_uuid(),
            name,
            body=request,
            body_model=SurfaceUpdateRequest,
        )

    def upsert(self, name: str, request: dict) -> AgentSurfaceResponse:
        """Back-compat create-or-update addressed by surface name (which defaults
        to the lowercased platform). Patches the surface if it exists, else
        creates it. New code should call ``create``/``update`` directly.

        A surface's name is pod-unique and its platform is not, so a caller
        naming a second Slack surface has to say which platform it is: the
        platform comes from ``request`` when present, and only falls back to
        the name for the original ``upsert("slack", ...)`` call shape.
        """
        surface_name = str(name).lower()
        body: dict[str, Any] = dict(request or {})
        platform = str(body.pop("platform", None) or name).upper()
        body.pop("name", None)
        try:
            return self.update(surface_name, body)
        except LemmaNotFoundError:
            return self.create({**body, "platform": platform, "name": surface_name})

    def get(self, name: str) -> AgentSurfaceResponse:
        return self._call(agent_surface_get, self._pod_uuid(), name)

    def delete(self, name: str) -> None:
        self._call(agent_surface_delete, self._pod_uuid(), name)

    def send(
        self, name: str, request: SurfaceSendRequest | dict
    ) -> SurfaceSendResponse:
        return self._call(
            agent_surface_send,
            self._pod_uuid(),
            name,
            body=request,
            body_model=SurfaceSendRequest,
        )

    def setup(self, name: str) -> SurfaceSetupResponse:
        return self._call(agent_surface_setup, self._pod_uuid(), name)

    def setup_guide(self, platform: str) -> SurfacePlatformSetupGuide:
        """Pre-creation platform checklist — works before any surface exists."""
        return self._call(agent_surface_setup_guide, self._pod_uuid(), platform)

    def slack_manifest(self) -> Any:
        """The Slack app manifest to paste when running your own Slack app.

        Takes no pod, despite living here: it describes the *deployment* — its
        event URL, its OAuth callback, the scopes its code reads — and is the
        same document for every pod. It is also what you need before there is
        anything to scope it to, since the app it creates is what issues the
        client id that connects the account a surface is built on.
        """
        return self._call(agent_surface_slack_manifest)

    def start_telegram_bot_setup(
        self,
        request: TelegramManagedBotSetupRequest | dict,
    ) -> TelegramManagedBotSetupResponse:
        return self._call(
            agent_surface_telegram_managed_start,
            self._pod_uuid(),
            body=request,
            body_model=TelegramManagedBotSetupRequest,
        )

    def get_telegram_bot_setup(
        self,
        setup_id: str,
    ) -> TelegramManagedBotSetupResponse:
        return self._call(
            agent_surface_telegram_managed_get,
            self._pod_uuid(),
            setup_id,
        )

    def channels(self, name: str) -> AvailableSurfaceChannelsResponse:
        return self._call(agent_surface_channels, self._pod_uuid(), name)


class UserSurfaces(Resource):
    """The caller's own surfaces across every pod they belong to, grouped by
    platform. ``set_default`` picks which surface answers them on a platform when
    several could (e.g. a shared bot spanning orgs).
    """

    def list_mine(self) -> UserSurfacesResponse:
        return self._call(agent_surface_list_mine)

    def set_default(self, request: SetDefaultSurfaceRequest | dict) -> None:
        self._call(
            agent_surface_set_my_default,
            body=request,
            body_model=SetDefaultSurfaceRequest,
        )
