"""The stand-ins three surface test files share, and the objects they build.

Not a test module -- pytest does not collect it. It exists because the ingress
service stopped being one object: `SurfaceEgress` and `MemberReach` have their
own files and their own tests, and all three need the same Slack surface, the
same parsed event and the same adapter that runs the real delivery ladder over
stubbed platform verbs.

Duplicating those builders per file is how two of them drift and one test starts
certifying a shape production never produces. One definition, three callers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4
from datetime import datetime, timezone

import pytest

from app.core.infrastructure.db.session_uow import SESSION_UOW_KEY
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.email_one_reply import EmailOneReplyMixin
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery
from app.modules.agent_surfaces.services.egress_progress import SurfaceProgress
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.member_reach import MemberReach
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    AttachmentIngest,
)
from app.modules.agent_surfaces.services.turn_starter import SurfaceTurnStarter
from app.modules.test_support.surface_routing_double import routing_surfaces_double

#: Where `agent` publishes what a surface does to a conversation, and where the
#: doubles below are installed. On the contract rather than on the surface
#: modules that call it: the operations belong to another module, and the real
#: ones reach a database.
_CONVERSATIONS = "app.modules.agent.contracts.conversations_for_surfaces"
_AGENT_DIRECTORY = "app.modules.agent.contracts.agents"


@pytest.fixture(autouse=True)
def conversation_operations(monkeypatch):
    """`agent`'s conversation operations, doubled for every test in this file.

    Autouse because nearly every path here opens, reads or resumes a
    conversation. Tests reach the doubles through ``agent_conversations``,
    which is the same module the code under test calls.
    """
    doubles = {
        "surface_conversation": AsyncMock(return_value=None),
        "open_surface_conversation": AsyncMock(),
        "start_surface_turn": AsyncMock(return_value=uuid4()),
        "append_notification_message": AsyncMock(),
        "pending_interaction": AsyncMock(return_value=None),
        "pending_question": AsyncMock(return_value=None),
        "pending_approval": AsyncMock(return_value=None),
        "resolve_pending_interaction": AsyncMock(return_value=True),
        "retry_failed_run": AsyncMock(),
        "surface_agent_identity": AsyncMock(return_value=None),
        "conversation_metadata_value": AsyncMock(return_value=None),
        "set_conversation_metadata_value": AsyncMock(),
    }
    for name, double in doubles.items():
        monkeypatch.setattr(f"{_CONVERSATIONS}.{name}", double)
    monkeypatch.setattr(
        f"{_AGENT_DIRECTORY}.agent_name_for_id", AsyncMock(return_value="Surface Agent")
    )


def _pending(kind: str, *, tool_call_id: str, tool_args: dict | None = None):
    """One paused call, in the shape the published operation returns it."""
    return SimpleNamespace(
        tool_call_id=tool_call_id,
        kind=kind,
        tool_args=tool_args or {},
        agent_run_id=uuid4(),
        is_approval=kind == "request_approval",
    )


def _surface_conversation(surface, *, conversation_id: UUID | None = None):
    """A conversation as the published lookup returns it."""
    return SimpleNamespace(
        id=conversation_id or uuid4(),
        user_id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        title=None,
        updated_at=datetime.now(timezone.utc),
    )


def _registry(adapter):
    return SimpleNamespace(get=lambda platform: adapter)


def _slack_surface(*, agent_id: UUID | None = None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=agent_id if agent_id is not None else uuid4(),
        surface_type=SurfacePlatform.SLACK,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        surface_identity_id="U-BOT",
        config=SurfaceConfig(),
        is_active=True,
    )


def _teams_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="teams",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TEAMS,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        external_tenant_id="tenant-123",
        external_channel_id="19:channel",
        config=SurfaceConfig(),
        is_active=True,
    )


def _telegram_surface(*, agent_id: UUID | None = None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=agent_id if agent_id is not None else uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        mode=SurfaceMode.DM,
        account_id=None,
        config=SurfaceConfig(),
        is_active=True,
    )


def _slack_channel_event(*, channel_id: str = "C999") -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="T123",
        external_channel_id=channel_id,
        external_thread_id="1700000000.000200",
        external_message_id="1700000000.000201",
        sender_external_user_id="U123",
        sender_display_name="New User",
        message_text="Hello from a channel",
        is_dm=False,
        mentioned_agent=True,
        reply_target={"channel": channel_id},
        metadata={"mentioned_user_ids": ["U-BOT"]},
    )


def _slack_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="T123",
        external_channel_id="D123",
        external_thread_id="D123",
        external_message_id="1700000000.000100",
        sender_external_user_id="U123",
        sender_display_name="New User",
        message_text="Hello from Slack",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"channel": "D123"},
    )


def _conversation(surface: AgentSurfaceEntity, user_id: UUID) -> Conversation:
    return Conversation(
        id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        user_id=user_id,
        title="Surface chat",
        metadata={},
    )


class _EmptyScalarResult:
    def __iter__(self):
        return iter(())

    def first(self):
        return None

    def all(self):
        return []


class _EmptyExecuteResult:
    def scalars(self):
        return _EmptyScalarResult()


def _delivering_adapter(platform: str = "SLACK") -> AsyncMock:
    """An adapter mock that runs the real ``deliver`` over stubbed platform verbs.

    Egress hands the platform one envelope now instead of calling a verb per
    kind of content, so a fully mocked adapter returns a mock from ``deliver``
    and never reaches ``_render_decision`` at all. Binding the real delivery
    methods keeps these tests asserting what they were written to assert: which
    verb the ladder tries, and what it falls back to.
    """
    adapter = AsyncMock()
    adapter.platform = platform
    for cls in BaseSurfaceAdapter.__mro__:
        for name, function in vars(cls).items():
            if name == "deliver" or name.startswith(
                ("_deliver", "_send_text_fallback")
            ):
                setattr(adapter, name, MethodType(function, adapter))
    if _delivers_one_reply(platform):
        # A one-reply platform folds the whole envelope into a single send, so
        # the mock borrows that too -- otherwise `deliver` stops at a mocked
        # `_render_one` and the transport is never reached.
        adapter._render_one = MethodType(EmailOneReplyMixin._render_one, adapter)
    return adapter


def _delivers_one_reply(platform: str) -> bool:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        DeliveryCardinality,
        get_platform_capabilities,
    )

    caps = get_platform_capabilities(platform)
    return bool(caps and caps.delivery_cardinality is DeliveryCardinality.ONE)


class SurfaceDoubles(SimpleNamespace):
    """The session, repositories and adapter one scenario is built over."""

    uow: SimpleNamespace
    surface_repository: AsyncMock
    conversation_link_repository: AsyncMock
    adapter: AsyncMock


def build_doubles(
    *,
    adapter,
    surfaces: list[AgentSurfaceEntity] | None = None,
    conversation: Conversation | None = None,
    existing_link: AgentSurfaceConversationLink | None = None,
) -> SurfaceDoubles:
    resolved_surfaces = surfaces or []
    surface_repository = AsyncMock()
    surface_repository.list_active_for_routing.side_effect = routing_surfaces_double(
        resolved_surfaces
    )
    surface_repository.get.side_effect = lambda surface_id: next(
        (surface for surface in resolved_surfaces if surface.id == surface_id),
        None,
    )

    conversation_link_repository = AsyncMock()
    conversation_link_repository.get_by_external_thread.return_value = existing_link
    conversation_link_repository.create.side_effect = lambda link: link
    conversation_link_repository.update_last_event.side_effect = lambda **kwargs: (
        existing_link
    )
    conversation_link_repository.update_conversation.side_effect = lambda **kwargs: (
        existing_link.model_copy(
            update={
                "conversation_id": kwargs["conversation_id"],
                "last_event": kwargs.get("last_event", {}),
                "last_message_id": kwargs.get("last_message_id"),
            }
        )
        if existing_link is not None
        else None
    )

    # Egress reads the pod from the conversation now, not from the surface, so a
    # link implies a conversation. For every scenario in these files the two pods
    # are the same; a personal DM is where they differ, and that has its own
    # coverage.
    if resolved_surfaces:
        agent_conversations.surface_conversation.return_value = _surface_conversation(
            resolved_surfaces[0]
        )

    agent_conversations.surface_agent_identity.return_value = (
        SimpleNamespace(
            id=uuid4(), name="Surface Agent", is_pod_default=False, icon_url=None
        )
        if any(surface.agent_id for surface in resolved_surfaces)
        else None
    )
    if conversation is not None:
        agent_conversations.open_surface_conversation.return_value = conversation

    session_model = SimpleNamespace(conversation_metadata={})
    organization_id = uuid4()

    async def _fake_get(model, item_id):
        del item_id
        if getattr(model, "__name__", "") == "Pod":
            # `is_deleted` is a non-nullable column, so a stand-in without
            # it is a Pod no database could return.
            return SimpleNamespace(organization_id=organization_id, is_deleted=False)
        return session_model

    uow = SimpleNamespace(
        session=SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            execute=AsyncMock(return_value=_EmptyExecuteResult()),
            flush=AsyncMock(),
            # `connection_released` commits through the *session*, and
            # `commit_now` reaches the unit of work through `session.info`.
            # Without both, every release on the ingress path is a silent no-op
            # here and the tests certify a hold as clean -- which is how this
            # double looked when the dedup claims were still holding one.
            commit=AsyncMock(),
            info={},
        ),
        # Egress releases the pooled connection before every platform call, so
        # the double needs the method the real unit of work has. Given rather
        # than made optional in the object: a helper that shrugs at a uow
        # without `commit` would also shrug in production, where that means the
        # connection is quietly held across the send.
        commit=AsyncMock(),
    )
    uow.session.info[SESSION_UOW_KEY] = uow

    adapter.enrich_inbound_event.side_effect = lambda *, credentials, event: event
    adapter.unresolved_sender_reply = Mock(return_value=None)
    adapter.linked_sender_confirmation = Mock(return_value=None)
    return SurfaceDoubles(
        uow=uow,
        surface_repository=surface_repository,
        conversation_link_repository=conversation_link_repository,
        adapter=adapter,
    )


def build_ingress_service(
    *,
    adapter,
    surfaces: list[AgentSurfaceEntity] | None = None,
    resolved_user: ResolvedSurfaceUser | None = None,
    conversation: Conversation | None = None,
    existing_link: AgentSurfaceConversationLink | None = None,
):
    doubles = build_doubles(
        adapter=adapter,
        surfaces=surfaces,
        conversation=conversation,
        existing_link=existing_link,
    )
    resolved_surfaces = surfaces or []
    membership = SimpleNamespace(
        get_user_pod_ids=AsyncMock(
            return_value=[surface.pod_id for surface in resolved_surfaces]
        ),
        get_user_email=AsyncMock(return_value="sender@example.com"),
    )
    identity = SimpleNamespace(
        resolve=AsyncMock(
            return_value=resolved_user
            or ResolvedSurfaceUser(
                internal_user_id=uuid4(),
                external_user_id="U123",
                email="sender@example.com",
                display_name="Sender",
            )
        )
    )
    credentials = SimpleNamespace(
        for_surface=AsyncMock(return_value={}),
        for_platform=AsyncMock(return_value={}),
    )
    # Real objects over doubled collaborators, and the two it reaches into are
    # constructor arguments now rather than bases it inherited -- so a test can
    # replace routing without replacing ingress.
    router = SurfaceRouter(
        uow=doubles.uow,
        surface_repository=doubles.surface_repository,
        conversation_link_repository=doubles.conversation_link_repository,
        pod_membership_port=membership,
        identity_service=identity,
        credential_resolver=credentials,
    )
    binder = ConversationBinder(
        uow=doubles.uow,
        surface_repository=doubles.surface_repository,
        conversation_link_repository=doubles.conversation_link_repository,
    )
    return AgentSurfaceIngressService(
        uow=doubles.uow,
        router=router,
        binder=binder,
        surface_repository=doubles.surface_repository,
        conversation_link_repository=doubles.conversation_link_repository,
        adapter_registry=_registry(adapter),
        credential_resolver=credentials,
        event_dedup_store=SimpleNamespace(
            claim_message=AsyncMock(return_value=True),
            claim_stranger_reply=AsyncMock(return_value=True),
        ),
    )


def build_egress(
    *,
    adapter,
    surfaces: list[AgentSurfaceEntity] | None = None,
    conversation: Conversation | None = None,
    existing_link: AgentSurfaceConversationLink | None = None,
    doubles: SurfaceDoubles | None = None,
) -> SurfaceEgress:
    """A real `SurfaceEgress` over doubled collaborators.

    The collaborators are stand-ins; the object is not. Every method under test
    here is the shipping one, which is the point of it having a constructor:
    the version of this that built an eight-mixin service and then assigned
    `service._resolve_credentials = AsyncMock(...)` was patching the subject.
    """
    doubles = doubles or build_doubles(
        adapter=adapter,
        surfaces=surfaces,
        conversation=conversation,
        existing_link=existing_link,
    )
    delivery = SurfaceDelivery(
        uow=doubles.uow,
        surface_repository=doubles.surface_repository,
        conversation_link_repository=doubles.conversation_link_repository,
        adapter_registry=_registry(adapter),
        credential_resolver=SimpleNamespace(
            for_surface=AsyncMock(return_value={}),
            for_platform=AsyncMock(return_value={}),
        ),
    )
    return SurfaceEgress(
        uow=doubles.uow,
        delivery=delivery,
        progress=SurfaceProgress(delivery=delivery),
    )


def build_member_reach(
    *,
    adapter,
    surfaces: list[AgentSurfaceEntity] | None = None,
    pod_ids: list[UUID] | None = None,
    identities: list | None = None,
    existing_link: AgentSurfaceConversationLink | None = None,
) -> MemberReach:
    resolved_surfaces = surfaces or []
    doubles = build_doubles(
        adapter=adapter, surfaces=resolved_surfaces, existing_link=existing_link
    )
    external_user_repository = AsyncMock()
    external_user_repository.list_by_resolved_users.return_value = identities or []
    return MemberReach(
        egress=build_egress(adapter=adapter, doubles=doubles),
        pod_membership_port=SimpleNamespace(
            get_user_pod_ids=AsyncMock(
                return_value=(
                    pod_ids
                    if pod_ids is not None
                    else [surface.pod_id for surface in resolved_surfaces]
                )
            ),
            get_user_email=AsyncMock(return_value="sender@example.com"),
        ),
        external_user_repository=external_user_repository,
        conversation_link_repository=doubles.conversation_link_repository,
    )


def _resend_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="resend",
        surface_type=SurfacePlatform.RESEND,
        mode=SurfaceMode.EMAIL,
        config=SurfaceConfig(),
        surface_identity_email="agent.pod@ops.test",
        is_active=True,
    )


def _telegram_event(*, chat_id: str, message_id: str) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id=chat_id,
        external_thread_id=chat_id,
        external_message_id=message_id,
        sender_external_user_id="777",
        sender_display_name="Telegram User",
        message_text="Hello from Telegram",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"chat_id": chat_id, "message_id": message_id},
    )


async def _ask_user_link(surface, conversation_id, parsed_event):
    return AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )


_ASK_USER_QUESTIONS = [
    {
        "question": "Pick a color",
        "header": "color",
        "options": [{"label": "Red"}, {"label": "Blue"}],
    }
]
# Wrapped shape (hand-built / legacy). pydantic-ai actually flattens the single
# `request: AskUserRequest` param, so production persists the FLAT shape below.
_ASK_USER_TOOL_ARGS = {"request": {"questions": _ASK_USER_QUESTIONS}}
_ASK_USER_TOOL_ARGS_FLAT = {"questions": _ASK_USER_QUESTIONS}
_REQUEST_APPROVAL_TOOL_ARGS = {
    "tool_name": "pod_write_record",
    "title": "Write a record",
    "reason": "The agent wants to write a record to your table.",
    "args": {"table_id": "tbl-1", "data": {"col": "val"}},
}


class ScopeCountingFactory:
    """A unit-of-work factory over one doubled session, counting open scopes.

    `SurfaceTurnStarter` exists so that no connection is held across platform
    I/O, and the only way to assert that is to watch the scopes open and close.
    `active` is how many are open right now; `opened` is how many ever were.
    """

    def __init__(self, uow: object | None = None) -> None:
        self._uow = uow
        self.active = 0
        self.opened = 0

    @asynccontextmanager
    async def __call__(self):
        self.active += 1
        self.opened += 1
        try:
            yield (
                self._uow
                if self._uow is not None
                else SimpleNamespace(session=SimpleNamespace())
            )
        finally:
            self.active -= 1


def build_turn_starter(
    *,
    adapter,
    surfaces: list[AgentSurfaceEntity] | None = None,
    conversation: Conversation | None = None,
    existing_link: AgentSurfaceConversationLink | None = None,
    uow_factory: object | None = None,
    file_ingest_service: object | None = None,
) -> SurfaceTurnStarter:
    """The worker's half, over doubled collaborators.

    It takes a factory and nothing else that could be `None`. The object this
    replaced took *either* a unit of work or a factory, so a test could write
    `uow_factory=lambda: None` and get a half-built ingress service for a method
    that never touched a session -- which is what two of these files did.
    """
    doubles = build_doubles(
        adapter=adapter,
        surfaces=surfaces,
        conversation=conversation,
        existing_link=existing_link,
    )
    return SurfaceTurnStarter(
        uow_factory=uow_factory or ScopeCountingFactory(doubles.uow),
        adapter_registry=_registry(adapter),
        event_dedup_store=SimpleNamespace(
            claim_message=AsyncMock(return_value=True),
            claim_stranger_reply=AsyncMock(return_value=True),
        ),
        file_ingest_service=file_ingest_service
        or SimpleNamespace(
            ingest_attachments=AsyncMock(return_value=AttachmentIngest())
        ),
    )
