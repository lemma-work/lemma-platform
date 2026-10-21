from __future__ import annotations

from typing import Any

from ..errors import LemmaConflictError, LemmaNotFoundError
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
    agent_surface_slack_app_create,
    agent_surface_slack_app_creation_forget,
    agent_surface_slack_app_creation_list,
    agent_surface_slack_app_creation_save,
    agent_surface_slack_install_availability,
    agent_surface_slack_install_start,
    agent_surface_slack_manager_bind,
    agent_surface_slack_manager_list,
    agent_surface_slack_manifest,
    agent_surface_slack_person_sign_in,
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
from ..openapi_client.models.slack_app_create_request import SlackAppCreateRequest
from ..openapi_client.models.slack_app_create_response import SlackAppCreateResponse
from ..openapi_client.models.slack_app_workspace_list_response import (
    SlackAppWorkspaceListResponse,
)
from ..openapi_client.models.slack_app_workspace_response import (
    SlackAppWorkspaceResponse,
)
from ..openapi_client.models.slack_configuration_token_request import (
    SlackConfigurationTokenRequest,
)
from ..openapi_client.models.slack_managed_workspace_list_response import (
    SlackManagedWorkspaceListResponse,
)
from ..openapi_client.models.slack_managed_workspace_response import (
    SlackManagedWorkspaceResponse,
)
from ..openapi_client.models.slack_manager_bind_request import (
    SlackManagerBindRequest,
)
from ..openapi_client.models.slack_install_availability_response import (
    SlackInstallAvailabilityResponse,
)
from ..openapi_client.models.slack_install_request import SlackInstallRequest
from ..openapi_client.models.slack_install_response import SlackInstallResponse
from ..openapi_client.models.slack_sign_in_request import SlackSignInRequest
from ..openapi_client.models.slack_sign_in_response import SlackSignInResponse
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

# The refusal a create meets when its agent already reaches the platform
# somewhere else; its details name where.
_AGENT_PLATFORM_CONFLICT = "AGENT_SURFACE_AGENT_PLATFORM_CONFLICT"


def _agent_platform_holder(exc: LemmaConflictError) -> str | None:
    """The name of the surface the agent already has, if that is the conflict."""
    if exc.code != _AGENT_PLATFORM_CONFLICT or not isinstance(exc.details, dict):
        return None
    holder = exc.details.get("conflicting_surface")
    name = holder.get("name") if isinstance(holder, dict) else None
    return str(name) if name else None


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
        the name for the original ``upsert("slack", ...)`` call shape. A
        ``name`` in ``request`` is the surface to address, with ``name`` the
        argument then read as its platform -- ``upsert("resend", {"name":
        "resend-assistant", ...})`` -- rather than being dropped.

        Addressed by platform, it means *this agent's* surface on that
        platform, whatever it is called. An agent reaches a platform in one
        place, and some of those places the caller never named: every agent is
        given a mailbox when it is created, called ``resend-<agent>``. So when
        the create is refused because the agent already has one, the surface
        the refusal names is the one to update. Never when the caller chose a
        name: that is a request for a distinct surface, and rewriting another
        one in its place would change the wrong thing.
        """
        body: dict[str, Any] = dict(request or {})
        named = str(body.pop("name", None) or "").strip().lower()
        surface_name = named or str(name).lower()
        platform = str(body.pop("platform", None) or name).upper()
        try:
            return self.update(surface_name, body)
        except LemmaNotFoundError:
            pass
        try:
            return self.create({**body, "platform": platform, "name": surface_name})
        except LemmaConflictError as exc:
            held = _agent_platform_holder(exc)
            if named or held is None or held == surface_name:
                raise
            return self.update(held, body)

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

    def slack_app_workspaces(self) -> SlackAppWorkspaceListResponse:
        """The Slack workspaces this pod's organization lets Lemma create apps in.

        ``can_manage`` says whether the caller may save tokens and create apps
        (an organization owner or editor); others are shown no workspace.
        """
        return self._call(agent_surface_slack_app_creation_list, self._pod_uuid())

    def save_slack_configuration_token(
        self, request: SlackConfigurationTokenRequest | dict
    ) -> SlackAppWorkspaceResponse:
        """Let Lemma create Slack apps in a workspace, from the refresh token of
        an app configuration token (api.slack.com/apps). Lemma rotates it at
        once, which proves it works and names the workspace."""
        return self._call(
            agent_surface_slack_app_creation_save,
            self._pod_uuid(),
            body=request,
            body_model=SlackConfigurationTokenRequest,
        )

    def forget_slack_configuration_token(self, team_id: str) -> None:
        """Stop Lemma creating apps in that workspace. Apps it made are kept,
        and the token stays valid at Slack until revoked there."""
        self._call(agent_surface_slack_app_creation_forget, self._pod_uuid(), team_id)

    def create_slack_app(
        self,
        request: SlackAppCreateRequest | dict,
        *,
        idempotency_key: str | None = None,
    ) -> SlackAppCreateResponse:
        """Create an agent's own Slack app (the pod's own agent unless
        ``agent_id`` is given), named after it. Authorise the returned install
        (``auth_config_id``) to finish. Asking again for the same agent and
        workspace answers with the same app until it is authorised."""
        kwargs: dict[str, Any] = {}
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key
        return self._call(
            agent_surface_slack_app_create,
            self._pod_uuid(),
            body=request,
            body_model=SlackAppCreateRequest,
            **kwargs,
        )

    def slack_managed_workspaces(self) -> SlackManagedWorkspaceListResponse:
        """The Slack workspaces Lemma's own app manages for this pod's
        organization. Lemma's app answers nobody: it is where a workspace sees
        its teammates, adds one, and hands one a channel."""
        return self._call(agent_surface_slack_manager_list, self._pod_uuid())

    def bind_slack_manager(
        self, request: SlackManagerBindRequest | dict
    ) -> SlackManagedWorkspaceResponse:
        """Make Lemma's own app the manager of the workspace an account
        (``account_id``) authorised it in. Only for a grant finished in a
        browser signed in as the caller."""
        return self._call(
            agent_surface_slack_manager_bind,
            self._pod_uuid(),
            body=request,
            body_model=SlackManagerBindRequest,
        )

    def start_slack_sign_in(
        self, request: SlackSignInRequest | dict
    ) -> SlackSignInResponse:
        """Start Sign in with Slack, proving the signed-in person is the Slack
        user (``team_id``, ``slack_user_id``) their App Home's link was for.
        Open the returned ``authorization_url`` in the same browser session."""
        return self._call(
            agent_surface_slack_person_sign_in,
            body=request,
            body_model=SlackSignInRequest,
        )

    def slack_install_availability(self) -> SlackInstallAvailabilityResponse:
        """Whether Lemma's own Slack app is set up on this server, so "Add to
        Slack" can work. Ask before giving anybody an organization for it."""
        return self._call(agent_surface_slack_install_availability)

    def start_slack_install(
        self, request: SlackInstallRequest | dict
    ) -> SlackInstallResponse:
        """Add Lemma's own Slack app to a workspace for an organization
        (``organization_id``): Slack's consent page, bot scopes only. Open the
        returned ``authorization_url`` in a browser signed in as the caller --
        a grant finished anywhere else adopts nothing."""
        return self._call(
            agent_surface_slack_install_start,
            body=request,
            body_model=SlackInstallRequest,
        )

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
