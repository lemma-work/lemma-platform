from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.domain.models import SurfaceChannelInfo
from app.modules.agent_surfaces.domain.models import SurfaceContextMessage
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope


class SurfaceAccountInfo(BaseModel):
    id: UUID
    user_id: UUID
    organization_id: UUID | None = None
    auth_config_id: UUID | None = None
    email: str | None = None
    connector_id: str
    credentials: dict[str, Any] = {}


class SurfaceAccountSummary(BaseModel):
    """The non-secret half of a connected account.

    Deliberately *not* :class:`SurfaceAccountInfo` minus a field: this is the
    shape the pod-visible read path uses, and it carries no ``credentials`` at
    all, so a connection can never leak one by omission. The read path also
    skips the credential decryption that loading a full account would force.
    """

    id: UUID
    user_id: UUID
    connector_id: str
    display_name: str | None = None
    email: str | None = None
    status: str | None = None


class SurfaceAccountPort(Protocol):
    async def get_account(self, account_id: UUID) -> SurfaceAccountInfo | None: ...

    async def list_account_summaries(
        self, account_ids: Sequence[UUID]
    ) -> dict[UUID, SurfaceAccountSummary]:
        """Batch counterpart of :meth:`get_account` for the read path — one
        query for a whole page of surfaces. Missing ids are simply absent."""
        ...


class SurfaceConnectionOwnerInfo(BaseModel):
    """Who owns a connected account, and whether they are still in the pod.

    ``is_pod_member`` is the fact the surfaces UI exists to surface: an account
    whose owner has left still *works* (credentials resolve off the account row,
    not the caller), but nobody left in the pod can re-authorize it.
    """

    user_id: UUID
    name: str | None = None
    email: str | None = None
    is_pod_member: bool = False


class SurfaceConnectionOwnerPort(Protocol):
    async def list_pod_owners(
        self, user_ids: Sequence[UUID], *, pod_id: UUID
    ) -> dict[UUID, SurfaceConnectionOwnerInfo]: ...


class SurfaceAuthConfigInfo(BaseModel):
    id: UUID
    kind: str
    connector_id: str
    # "SYSTEM_DEFAULT" (Lemma's own OAuth app) or "ORG_CUSTOM" (org brought its
    # own app). Drives whether the org must wire up its own provider webhook.
    config_source: str | None = None


class SurfaceAuthConfigPort(Protocol):
    async def get_auth_config(
        self, auth_config_id: UUID
    ) -> SurfaceAuthConfigInfo | None: ...


class SurfaceInstallationRepositoryPort(Protocol):
    async def get(self, id: UUID) -> AgentSurfaceEntity | None: ...

    async def merge_conversation_metadata(
        self, conversation_id: UUID, updates: dict
    ) -> None: ...

    async def get_by_pod_and_name(
        self,
        *,
        pod_id: UUID,
        name: str,
    ) -> AgentSurfaceEntity | None: ...

    async def get_active_by_address(
        self,
        *,
        platform: str,
        address: str,
    ) -> AgentSurfaceEntity | None: ...

    async def list_by_pod(
        self,
        pod_id: UUID,
        *,
        platform: str | None = None,
        agent_id: UUID | None = None,
        match_agent: bool = False,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentSurfaceEntity], UUID | None]: ...

    async def list_active_by_type(
        self, surface_type: str
    ) -> list[AgentSurfaceEntity]: ...

    async def list_active_native_receiver_surfaces(
        self,
        platforms: set[SurfacePlatform],
    ) -> list[AgentSurfaceEntity]: ...

    async def get_by_platform_and_account_id(
        self,
        *,
        platform: str,
        account_id: UUID,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None: ...

    async def get_system_credential_conflict_in_org(
        self,
        *,
        pod_id: UUID,
        platform: str,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None: ...

    async def get_account_conflict_in_org(
        self,
        *,
        pod_id: UUID,
        account_id: UUID,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None: ...

    async def create(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity: ...

    async def update(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity: ...

    async def delete(self, id: UUID) -> None: ...


class SurfaceAccountBindingPort(Protocol):
    """Validates the connected account for a platform and derives the
    non-secret routing identity fields."""

    async def resolve_binding(
        self,
        platform: SurfacePlatform,
        account_id: UUID | None = None,
    ) -> tuple[str | None, str | None, str | None]: ...

    # Returns (external_tenant_id, external_workspace_id, surface_identity_id).


class SurfacePlatformAdapterPort(Protocol):
    platform: str

    def split_inbound_payloads(
        self, payload: dict[str, Any]
    ) -> list[dict[str, Any]]: ...

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None: ...

    async def enrich_inbound_event(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent: ...

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None: ...

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    # The text primitive `deliver` degrades onto, and the only way to say
    # something before a conversation exists (the signup and setup replies).

    async def deliver(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryReceipt:
        """The one outbound seam for conversation content.

        Every kind of content is a field on the envelope, and the receipt says
        how each part landed -- natively, degraded to text or a link, or
        reaching nobody.

        The ``_render_*`` hooks it composes are deliberately not declared on
        this port. They are a platform's private half of this call, and naming
        them here made the seam read as six verbs a caller could choose between
        -- which is how content came to be rendered past ``deliver`` in the
        first place.
        """
        ...

    async def fetch_thread_context(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list["SurfaceContextMessage"]: ...

    # Fetch the last few messages of the inbound thread/channel for background
    # context on a group mention (each user has a separate conversation, so this
    # gives continuity). Best-effort, fetched fresh per run. Default: none.

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> "ParsedSurfaceInteraction | None": ...

    async def acknowledge_interaction(
        self,
        *,
        credentials: dict[str, Any],
        interaction: "ParsedSurfaceInteraction",
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        raise NotImplementedError

    # Parse an interaction submission (Slack block_actions, Teams Action.Submit)
    # into a routable interaction, or None when the payload is not an interaction.

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    # Show live progress text on platforms that support an editable message
    # (Telegram, Teams). Returns an opaque handle (e.g. {"message_id": ...}) to
    # pass back on the next call so the same message is edited. None → platform
    # has no editable progress; the caller keeps using typing indicators.

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None: ...

    # Clean up the streaming progress message at run end (e.g. delete it before
    # the final answer is delivered).

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None: ...

    # (content, file_name, mime_type) for a user-provided inbound attachment, or
    # None when it cannot be downloaded. Used by inbound auto-ingest; not an
    # agent tool.

    async def list_channels(
        self, *, credentials: dict[str, Any]
    ) -> list[SurfaceChannelInfo]: ...

    # Channels/groups the bot can be configured in (Slack/Teams). Empty for
    # platforms without an enumerable channel concept.

    def unresolved_sender_reply(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None: ...

    # (message, reply_metadata) for unresolved senders; None → default signup prompt.

    def linked_sender_confirmation(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None: ...

    # Non-None → send this reply instead of starting a chat (identity-link events).


class ColdEmailThread(BaseModel):
    """What a cold-opened email thread leaves behind, so the reply can find it.

    Delivery writes these onto a conversation link. The three ``external_*``
    fields are not bookkeeping: they are precisely the tuple
    ``get_by_external_thread`` will query with when the person replies, so
    getting one of them wrong means the reply silently starts a new
    conversation and the asker waits forever.
    """

    external_thread_id: str
    external_channel_id: str | None = None
    external_message_id: str | None = None
    # A serialized ParsedInboundSurfaceEvent. Stored as ``link.last_event``
    # because ``_resolve_egress_target`` refuses to send on a link whose last
    # event is missing or unparseable — without it the agent's own next message
    # in this conversation would quietly go nowhere.
    last_event: dict[str, Any] = {}


@runtime_checkable
class SurfaceNotificationEgressPort(Protocol):
    """Exactly what notification delivery needs from the ingress service.

    It exists because delivery used to hold this collaborator as an untyped
    constructor kwarg and called two methods on it that were never written —
    ``agent_name_for_surface`` and a cold-email send. Both were invisible to
    mypy and to every test, and both raised ``AttributeError`` in production on
    the first message that found a channel. Naming the contract is what makes
    that a typecheck failure instead of an outage.
    """

    async def agent_name_for_surface(
        self, surface: AgentSurfaceEntity
    ) -> str | None: ...

    async def send_agent_message_for_conversation(
        self,
        *,
        conversation_id: UUID,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool: ...

    async def open_cold_email_thread(
        self,
        *,
        surface: AgentSurfaceEntity,
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ColdEmailThread | None:
        """First contact by email, returning the thread the reply will land in.

        ``None`` means this platform cannot start a thread it has no prior
        message for — every chat platform, and any mailbox reached through an
        endpoint keyed by a provider message id. That is a clean "no", not a
        failure to be retried on another channel.
        """
        ...


class SurfaceEventDedupStorePort(Protocol):
    async def claim_message(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> bool: ...

    async def release_message(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> None:
        """Hand a claim back, so a redelivery of the same message is not a duplicate.

        The claim is taken while the message is being prepared, but the work it
        guards -- the queued run -- is dispatched after that. Losing the dispatch
        with the claim still held would make the delivery unrecoverable: every
        retry would see a duplicate and drop it.
        """


class SurfacePodMembershipPort(Protocol):
    """Port for resolving pod membership for surface routing checks."""

    async def get_user_pod_ids(self, user_id: UUID) -> list[UUID]: ...

    async def get_user_email(self, user_id: UUID) -> str | None: ...

    async def get_user_default_surface_id(
        self, user_id: UUID, platform: str
    ) -> UUID | None:
        """The user's preferred surface id for ``platform`` (from
        ``users.preferences``), used to disambiguate a sender reachable via a
        shared system bot across pods in multiple orgs. None if unset."""
        ...

    async def clear_user_default_surface_id(self, user_id: UUID, platform: str) -> None:
        """Clear the user's stored default surface for ``platform``.

        Called when a stored default points at a surface the user is no longer a
        member of (a stale default), so routing stops silently honoring it."""
        ...

    async def set_user_default_surface_id(
        self, user_id: UUID, platform: str, surface_id: UUID
    ) -> None:
        """Persist the user's explicit platform-to-surface choice."""
        ...

    async def get_pod_member_id(self, user_id: UUID, pod_id: UUID) -> UUID | None:
        """The pod-scoped member id, or None when they are not in the pod.

        Notifications store this alongside the user id so a workflow FORM
        assignee (which is a pod member) and an agent's recipient are the same
        kind of thing, and so removing someone from a pod takes their inbox
        entries with it.
        """

    async def resolve_pod_recipient(
        self, *, pod_id: UUID, reference: str
    ) -> UUID | None:
        """Resolve a user id from a pod member id, user id, or email address.

        An agent naming a colleague has whichever of these it happens to know;
        refusing two of the three would just push the lookup into the prompt.
        Always scoped to the pod, so a valid id for someone outside it resolves
        to None rather than to a person the caller cannot see.
        """

    async def get_user_display_name(self, user_id: UUID) -> str | None:
        """Used to attribute a message to the human behind the run."""
