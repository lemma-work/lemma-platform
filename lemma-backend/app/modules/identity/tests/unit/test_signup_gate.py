"""Who the signup gate lets in, per deployment kind and signup mode.

The store is stood in front of the gate as a collaborator -- it is the
database's half, proved against Postgres in `tests/e2e/test_signup_modes_e2e.py`.
What is under test here is the decision: that the first account on an empty
deployment gets in whatever the mode, the order the checks run in, and that the
refusal carries a message a stranger at the sign-up page can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import pytest

from app.modules.identity.config import IdentitySettings
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.services.signup_gate import Admission, SignupGate

pytestmark = pytest.mark.unit


INVITATION = UUID("6f1d8c1e-2c2a-4b0e-9d3b-0c9a0f5e7a11")


@dataclass
class _Store:
    users: bool = True
    invited: set[str] = field(default_factory=set)
    #: The id each invited address's invitation carries.
    invitation_ids: dict[str, UUID] = field(default_factory=dict)
    user_reads: int = 0

    async def has_any_user(self) -> bool:
        self.user_reads += 1
        return self.users

    async def has_pending_invitation(
        self, email: str, *, now: datetime, invitation_id: UUID | None = None
    ) -> bool:
        if email not in self.invited:
            return False
        return invitation_id is None or self.invitation_ids.get(email) == invitation_id


def _gate(store: _Store, **settings: object) -> SignupGate:
    return SignupGate(settings=IdentitySettings(**settings), store=store)


def test_signup_mode_defaults_to_open_on_a_server_and_invite_only_on_desktop() -> None:
    assert IdentitySettings().effective_signup_mode() == "open"
    assert (
        IdentitySettings(deployment_kind="desktop").effective_signup_mode()
        == "invite_only"
    )
    assert (
        IdentitySettings(
            deployment_kind="desktop", signup_mode="open"
        ).effective_signup_mode()
        == "open"
    )


def test_a_blank_signup_mode_means_the_default_rather_than_an_error() -> None:
    """`SIGNUP_MODE=` in a .env -- as the configuration guide writes it -- is unset."""
    assert IdentitySettings(signup_mode="").signup_mode is None


@pytest.mark.asyncio
async def test_open_admits_anyone_without_reading_the_database() -> None:
    store = _Store()

    assert await _gate(store).admit("anyone@example.com") is Admission.OPEN
    assert store.user_reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("deployment_kind", ["server", "desktop"])
@pytest.mark.parametrize("mode", ["invite_only", "closed"])
async def test_the_first_account_gets_in_whatever_the_mode(
    deployment_kind: str, mode: str
) -> None:
    gate = _gate(_Store(users=False), deployment_kind=deployment_kind, signup_mode=mode)

    assert await gate.admit("me@example.com") is Admission.FIRST_ACCOUNT


@pytest.mark.asyncio
async def test_once_anybody_has_an_account_the_mode_applies() -> None:
    gate = _gate(_Store(users=True), deployment_kind="desktop", signup_mode="closed")

    with pytest.raises(SignupNotAllowedError):
        await gate.admit("second@example.com")


@pytest.mark.asyncio
async def test_invite_only_admits_an_invited_address_and_refuses_the_rest() -> None:
    store = _Store(invited={"guest@example.com"})
    gate = _gate(store, deployment_kind="desktop")

    assert await gate.admit("guest@example.com") is Admission.INVITED
    with pytest.raises(SignupNotAllowedError) as refused:
        await gate.admit("stranger@example.com")

    assert refused.value.code == SignupNotAllowedError.INVITE_ONLY
    assert refused.value.status_code == 403
    assert refused.value.message == SignupNotAllowedError.INVITE_ONLY_MESSAGE


@pytest.mark.asyncio
async def test_closed_refuses_even_an_invited_address() -> None:
    store = _Store(invited={"guest@example.com"})
    gate = _gate(store, deployment_kind="desktop", signup_mode="closed")

    with pytest.raises(SignupNotAllowedError) as refused:
        await gate.admit("guest@example.com")

    assert refused.value.code == SignupNotAllowedError.CLOSED
    assert refused.value.message == SignupNotAllowedError.CLOSED_MESSAGE


@pytest.mark.asyncio
async def test_a_server_may_be_made_invite_only() -> None:
    store = _Store(invited={"guest@example.com"})
    gate = _gate(store, signup_mode="invite_only")

    assert await gate.admit("guest@example.com") is Admission.INVITED
    with pytest.raises(SignupNotAllowedError):
        await gate.admit("stranger@example.com")


@pytest.mark.asyncio
async def test_an_unproven_address_must_present_its_invitation() -> None:
    """Typing an invited person's address is not being them.

    A Desktop installation shared with email verification off never checks
    that a password sign-up owns its address. Matching the invitation by
    address alone let anybody who knew an invitee's email take their seat.
    """
    store = _Store(
        invited={"guest@example.com"},
        invitation_ids={"guest@example.com": INVITATION},
    )
    gate = _gate(store, deployment_kind="desktop")

    for presented in (None, "", "not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(SignupNotAllowedError) as refused:
            await gate.admit(
                "guest@example.com", invitation_id=presented, email_proven=False
            )
        assert refused.value.code == SignupNotAllowedError.INVITE_ONLY

    assert (
        await gate.admit(
            "guest@example.com", invitation_id=str(INVITATION), email_proven=False
        )
        is Admission.INVITED
    )
    # The invitation is for one address; presenting it for another is refused.
    with pytest.raises(SignupNotAllowedError):
        await gate.admit(
            "stranger@example.com", invitation_id=str(INVITATION), email_proven=False
        )


@pytest.mark.asyncio
async def test_a_proven_address_is_admitted_by_its_invitation_alone() -> None:
    """A provider or a code already vouched for the address."""
    store = _Store(invited={"guest@example.com"})
    gate = _gate(store, deployment_kind="desktop")

    assert await gate.admit("guest@example.com", email_proven=True) is Admission.INVITED
