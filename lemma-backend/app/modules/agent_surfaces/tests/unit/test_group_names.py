"""What a pod's own assistant is called, wherever a person reads it.

The rule has two answers — the pod's name, or the agent's own — and two callers
need it: a group that is addressing the bot, and an email ``From`` header. The
assistant's row name is ``pod_default``, which is neither answer, so what is
worth pinning is that the pod, and not that identifier, decides.

The header's caller carries most of an agent's mail — a reply inside a thread —
so it is driven for real: ``SurfaceDelivery.egress_metadata``, the shipping
object, over a faked connection. Only the connection is faked. The agent's
identity comes from the real ``surface_agent_identity``, which is what turns
the row's ``kind`` into ``is_pod_default``, and the pod's name from the real
``pod_name``. The session counts the pod read, because "a named agent's message
costs no pod lookup" is a claim worth asserting rather than assuming.

The pod table's read is faked rather than patched: a double installed inside the
module under test certifies the half the test did not write (``make lint``
counts them), and what is under test here is which name wins, not how the read
is spelled.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.infrastructure.models.agent import AgentModel
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery
from app.modules.agent_surfaces.services.group_names import sender_name_for
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

pytestmark = pytest.mark.asyncio


class _Scalar:
    """One ``scalar_one_or_none()`` answer."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _AgentRow:
    """One ``one_or_none()`` answer: an agent's id, name, kind and icon."""

    def __init__(self, row: tuple[UUID, str, str, str | None] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[UUID, str, str, str | None] | None:
        return self._row


class _Session:
    """The connection both reads go through, answered without a database.

    Which query arrived is read off the table it selects, so the fake answers
    the same way the real session would: the agent's row for the ``agents``
    read, the name for the ``pods`` one. ``pod_reads`` is counted because a
    named agent's message must not cost one.
    """

    def __init__(
        self,
        *,
        pod_name: str | None = None,
        agent: tuple[UUID, str, str, str | None] | None = None,
    ) -> None:
        self._pod_name = pod_name
        self._agent = agent
        self.pod_reads = 0

    async def execute(self, statement, **_kwargs):
        selects = {column.table for column in statement.selected_columns}
        if AgentModel.__table__ in selects:
            return _AgentRow(self._agent)
        self.pod_reads += 1
        return _Scalar(self._pod_name)


def _uow(session: _Session):
    return SimpleNamespace(session=session)


def _email_target(
    *,
    pod_id: UUID,
    agent_id: UUID,
    routed_agent_id: UUID | None = None,
    surface_type: SurfacePlatform = SurfacePlatform.RESEND,
) -> SurfaceEgressTarget:
    """One resolved outbound message, in the shape ``resolve_egress_target`` returns.

    ``routed_agent_id`` is the agent that actually answered the thread, which is
    not always the one the surface points at now.
    """
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        agent_id=agent_id,
        name=surface_type.value.lower(),
        surface_type=surface_type,
        config=SurfaceConfig(),
        surface_identity_email="agent.pod@ops.test",
    )
    return SurfaceEgressTarget(
        link=AgentSurfaceConversationLink(
            id=uuid4(),
            surface_id=surface.id,
            conversation_id=uuid4(),
            platform=surface.surface_type.value,
            external_thread_id="thread-1",
            routed_agent_id=routed_agent_id,
        ),
        surface=surface,
        pod_id=pod_id,
        # `egress_metadata` reads neither the adapter nor the credentials: the
        # name comes from the link, the surface and the connection.
        adapter=SimpleNamespace(),
        event=ParsedInboundSurfaceEvent(
            platform=surface.surface_type,
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="thread-1",
            message_text="What did you ship?",
        ),
        credentials={},
    )


def _delivery(session: _Session) -> SurfaceDelivery:
    """The real ``SurfaceDelivery``. Only ``uow`` is on the path under test."""
    return SurfaceDelivery(
        uow=_uow(session),
        surface_repository=AsyncMock(),
        conversation_link_repository=AsyncMock(),
        adapter_registry=SimpleNamespace(get=lambda _platform: None),
        credential_resolver=AsyncMock(),
    )


async def test_the_pods_own_assistant_is_named_by_its_pod():
    """`Sales`, not `Lem` and not `pod_default`.

    This is the name the email ``From`` header was missing: the pod's assistant
    went out as the platform's word for it, beside a person's name and the
    product, which is the header that was reported.
    """
    assert (
        await sender_name_for(
            _uow(_Session(pod_name="Sales")),
            is_pod_default=True,
            agent_name="Lem",
            pod_id=uuid4(),
        )
        == "Sales"
    )


async def test_a_named_agent_keeps_its_own_name():
    """One agent, one name — and no read for a pod it is not answering for."""
    session = _Session(pod_name="Sales")
    assert (
        await sender_name_for(
            _uow(session), is_pod_default=False, agent_name="Priya", pod_id=uuid4()
        )
        == "Priya"
    )
    assert session.pod_reads == 0


async def test_a_pod_that_no_longer_resolves_names_nobody():
    """None rather than the assistant's stored name, which is an identifier.

    The caller falls back to the deployment's own name, which is at least true
    of the deployment. ``pod_default`` in a ``From`` line is not a name at all.
    """
    assert (
        await sender_name_for(
            _uow(_Session(pod_name=None)),
            is_pod_default=True,
            agent_name="pod_default",
            pod_id=uuid4(),
        )
        is None
    )


async def test_a_reply_from_the_pods_assistant_is_named_by_its_pod():
    """The reported header, on the path most of an agent's mail travels.

    A reply inside a thread is delivered through ``egress_metadata``, and this
    is the branch that resolves the name for it. The agent's row is the
    assistant's own — id and all — and what a member calls the pod is the name
    that belongs in the From column, not the identifier on the row.
    """
    pod_id = uuid4()
    session = _Session(
        pod_name="Sales",
        agent=(pod_id, "pod_default", AgentKind.POD_DEFAULT.value, None),
    )

    metadata = await _delivery(session).egress_metadata(
        _email_target(pod_id=pod_id, agent_id=pod_id)
    )

    assert metadata["email_sender_name"] == "Sales"
    # The chat name is a different question and keeps its own answer.
    assert metadata["agent_display_name"] == DEFAULT_RESPONDER_NAME
    assert session.pod_reads == 1


async def test_a_reply_from_a_named_agent_carries_its_own_name():
    """`Priya`, and the pod table is not read to say so.

    The lookup is the whole cost of the pod arm, and a named agent is called by
    its own name everywhere — so its mail must not pay it.
    """
    pod_id, agent_id = uuid4(), uuid4()
    session = _Session(
        pod_name="Sales", agent=(agent_id, "Priya", AgentKind.USER.value, None)
    )

    metadata = await _delivery(session).egress_metadata(
        _email_target(pod_id=pod_id, agent_id=agent_id)
    )

    assert metadata["email_sender_name"] == "Priya"
    assert session.pod_reads == 0


async def test_the_agent_that_answered_the_thread_is_the_one_named():
    """A surface can be moved to another agent; an older thread keeps its name.

    The link records who actually answered, so a thread the pod's assistant
    opened goes on being signed by the pod even after the surface is pointed at
    a named agent.
    """
    pod_id, moved_to = uuid4(), uuid4()
    session = _Session(
        pod_name="Sales",
        agent=(pod_id, "pod_default", AgentKind.POD_DEFAULT.value, None),
    )

    metadata = await _delivery(session).egress_metadata(
        _email_target(pod_id=pod_id, agent_id=moved_to, routed_agent_id=pod_id)
    )

    assert metadata["email_sender_name"] == "Sales"


async def test_a_chat_reply_never_carries_an_email_sender_name():
    """The From line is email's, and a chat send must not pay for the lookup."""
    pod_id = uuid4()
    session = _Session(
        pod_name="Sales",
        agent=(pod_id, "pod_default", AgentKind.POD_DEFAULT.value, None),
    )

    metadata = await _delivery(session).egress_metadata(
        _email_target(
            pod_id=pod_id, agent_id=pod_id, surface_type=SurfacePlatform.TELEGRAM
        )
    )

    assert "email_sender_name" not in metadata
    assert session.pod_reads == 0
