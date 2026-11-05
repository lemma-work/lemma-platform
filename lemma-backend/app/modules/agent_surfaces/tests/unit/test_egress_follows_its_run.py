"""What a run shows while it works goes to the place that run was asked.

One conversation serves a person across their email and their WhatsApp. An
email run that is still working -- or paused on a card -- when a WhatsApp
message opens a turn of its own is no longer the conversation's newest run.
Its typing, its progress and its cards must still go to the email, where its
answer will go, not to the WhatsApp the newest run was asked on. A message no
run sends follows the newest run.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery
from app.modules.agent_surfaces.services.egress_progress import SurfaceProgress
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.pending_envelope import RunFiles
from app.modules.channels.contracts import Source, SourceKind
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

pytestmark = pytest.mark.asyncio

_CONVERSATION = uuid4()
_EMAIL_RUN = uuid4()
_WHATSAPP_RUN = uuid4()


def _place(platform: str) -> Source:
    return Source(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=uuid4(),
        account_id=uuid4(),
        surface_id=uuid4(),
        platform=platform,
        kind=SourceKind.PERSON,
        external_user_id="14155550000",
    )


class _Seats(InMemoryChannelSeats):
    """The seats double, noting which place each target was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.asked_for: list[UUID | None] = []

    async def reply_address(self, conversation_id, *, at_source_id=None):
        self.asked_for.append(at_source_id)
        return await super().reply_address(conversation_id, at_source_id=at_source_id)


@pytest.fixture
def places(monkeypatch) -> tuple[Source, Source]:
    """The email run was asked by email; the WhatsApp run, newer, on WhatsApp."""
    email, whatsapp = _place("RESEND"), _place("WHATSAPP")
    asked = {_EMAIL_RUN: email.id, _WHATSAPP_RUN: whatsapp.id}
    monkeypatch.setattr(
        "app.modules.agent.contracts.channel_replies.run_asked_at",
        AsyncMock(side_effect=lambda _uow, run_id: asked[run_id]),
    )
    monkeypatch.setattr(
        "app.modules.agent.contracts.channel_replies.latest_run_asked_at",
        AsyncMock(return_value=whatsapp.id),
    )
    return email, whatsapp


def _delivery(seats: InMemoryChannelSeats) -> SurfaceDelivery:
    return SurfaceDelivery(
        uow=SimpleNamespace(session=object()),
        surface_repository=AsyncMock(get=AsyncMock(return_value=None)),
        seats=seats,
        adapter_registry=SimpleNamespace(get=lambda _platform: None),
        credential_resolver=AsyncMock(),
    )


async def test_the_older_runs_target_is_its_own_place_not_the_newest_runs(places):
    email, _ = places
    seats = _Seats()

    await _delivery(seats).resolve_egress_target(_CONVERSATION, run_id=_EMAIL_RUN)

    assert seats.asked_for == [email.id]


async def test_a_message_no_run_sends_follows_the_newest_run(places):
    _, whatsapp = places
    seats = _Seats()

    await _delivery(seats).resolve_egress_target(_CONVERSATION)

    assert seats.asked_for == [whatsapp.id]


async def test_the_email_runs_typing_and_progress_go_to_the_email(places):
    """Every live verb names its run, so none of them drifts to WhatsApp."""
    email, _ = places
    seats = _Seats()
    progress = SurfaceProgress(delivery=_delivery(seats))
    handle = SimpleNamespace(kind=None)

    await progress.show_typing(conversation_id=_CONVERSATION, run_id=_EMAIL_RUN)
    await progress.send_progress_update(
        conversation_id=_CONVERSATION, run_id=_EMAIL_RUN, progress_text="Reading"
    )
    await progress.append_streamed_text(
        conversation_id=_CONVERSATION, run_id=_EMAIL_RUN, progress_handle=None, text=""
    )
    await progress.finish_with_answer(
        conversation_id=_CONVERSATION,
        run_id=_EMAIL_RUN,
        progress_handle=handle,
        message="Done",
    )
    await progress.clear_progress(
        conversation_id=_CONVERSATION, run_id=_EMAIL_RUN, progress_handle=handle
    )

    assert seats.asked_for == [email.id] * 5


async def test_the_paused_email_runs_cards_go_to_the_email(places, monkeypatch):
    """A card names the run it is for through the files it may carry, and its
    plain-words fallback and a sign-in link name it outright."""
    email, _ = places
    seats = _Seats()
    delivery = _delivery(seats)
    egress = SurfaceEgress(
        uow=SimpleNamespace(session=object()),
        delivery=delivery,
        progress=SurfaceProgress(delivery=delivery),
    )
    # Nothing is pending, so no card is drawn: only where each would go is
    # asked, which is the whole of what this pins.
    for read in ("pending_approval", "pending_question"):
        monkeypatch.setattr(
            f"app.modules.agent.contracts.channels.{read}",
            AsyncMock(return_value=None),
        )

    files = RunFiles(_EMAIL_RUN)
    await egress.send_approval_prompt_for_conversation(
        conversation_id=_CONVERSATION, attach_files_of=files
    )
    await egress.send_questions_for_conversation(
        conversation_id=_CONVERSATION, attach_files_of=files
    )
    await egress.send_prompt_as_text_for_conversation(
        conversation_id=_CONVERSATION, kind="ask_user", run_id=_EMAIL_RUN
    )
    await egress.send_sign_in_prompt_for_conversation(
        conversation_id=_CONVERSATION, run_id=_EMAIL_RUN
    )

    assert seats.asked_for == [email.id] * 4
