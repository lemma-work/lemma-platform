"""An invitation's note goes from the request to the email and nowhere else."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.identity.api.schemas.organization_schemas import (
    OrganizationInvitationRequest,
)
from app.modules.identity.domain.events import OrganizationInvitationCreatedEvent
from app.modules.identity.domain.organization_entities import (
    INVITATION_NOTE_CHARS,
    OrganizationInvitationEntity,
    OrganizationRole,
)
from app.modules.identity.events.identity_work import send_identity_email

pytestmark = pytest.mark.unit


class _Port:
    def __init__(self) -> None:
        self.invited: dict[str, object] = {}

    async def send_invitation_email(self, **kwargs: object) -> bool:
        self.invited = kwargs
        return True


def _invitation(note: str | None) -> OrganizationInvitationEntity:
    invitation = OrganizationInvitationEntity(
        email="dev@example.com",
        organization_id=uuid4(),
        role=OrganizationRole.ORG_MEMBER,
        note=note,
    )
    invitation.mark_created(
        organization_name="Acme",
        invited_by_user_id=uuid4(),
        invited_by_email="ada@example.com",
        accept_url="https://lemma.work/invitations/x/accept",
        pod_name="Support",
        note=invitation.note,
    )
    return invitation


async def test_the_note_rides_the_event_into_the_email():
    [event] = _invitation("You answered most of these.").collect_events()
    assert isinstance(event, OrganizationInvitationCreatedEvent)
    port = _Port()

    await send_identity_email(event.event_type, event.model_dump(mode="json"), port)

    assert port.invited["note"] == "You answered most of these."
    assert port.invited["pod_name"] == "Support"


def test_a_blank_note_is_no_note_and_a_long_one_is_refused():
    assert (
        OrganizationInvitationRequest(
            email="dev@example.com", role=OrganizationRole.ORG_MEMBER, note="  "
        ).note
        is None
    )
    with pytest.raises(ValidationError):
        OrganizationInvitationRequest(
            email="dev@example.com",
            role=OrganizationRole.ORG_MEMBER,
            note="x" * (INVITATION_NOTE_CHARS + 1),
        )


def test_the_note_is_never_written_with_the_invitation():
    """Only the email reads it, so it has no column; the repository leaves it
    out of what it writes, or the insert would name a column that is not there."""
    from app.modules.identity.infrastructure.models.organization_models import (
        OrganizationInvitation,
    )

    assert "note" not in OrganizationInvitation.__table__.columns
