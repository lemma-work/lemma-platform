"""Who the signup gate lets in, per deployment kind and signup mode.

The store is stood in front of the gate as a collaborator -- it is the
database's half, and its own race behaviour is proved against Postgres in
`tests/e2e/test_installation_owner_e2e.py`. What is under test here is the
decision: the order the owner and mode checks run in, and that the refusal
carries a message a stranger at the sign-up page can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.modules.identity.config import IdentitySettings
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.services.installation import Admission, SignupGate

pytestmark = pytest.mark.unit


@dataclass
class _Store:
    first: bool = False
    owner: UUID | None = None
    invited: set[str] = field(default_factory=set)
    reservations: list[str] = field(default_factory=list)
    owner_reads: int = 0

    async def reserve_first_signup(
        self, email: str, *, now: datetime, reservation_ttl: timedelta
    ) -> bool:
        self.reservations.append(email)
        return self.first

    async def owner_user_id(
        self, *, now: datetime, reservation_ttl: timedelta
    ) -> UUID | None:
        self.owner_reads += 1
        return self.owner

    async def has_pending_invitation(self, email: str, *, now: datetime) -> bool:
        return email in self.invited


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
async def test_a_server_stays_open_and_never_touches_the_owner_slot() -> None:
    store = _Store(first=True)

    assert await _gate(store).admit("anyone@example.com") is Admission.OPEN
    assert store.reservations == []


@pytest.mark.asyncio
async def test_the_first_desktop_signup_is_the_owner_whatever_the_mode() -> None:
    for mode in ("open", "invite_only", "closed"):
        store = _Store(first=True)
        gate = _gate(store, deployment_kind="desktop", signup_mode=mode)

        assert await gate.admit("me@example.com") is Admission.OWNER, mode
        assert store.reservations == ["me@example.com"]


@pytest.mark.asyncio
async def test_invite_only_admits_an_invited_address_and_refuses_the_rest() -> None:
    store = _Store(invited={"guest@example.com"})
    gate = _gate(store, deployment_kind="desktop")

    assert await gate.admit("guest@example.com") is Admission.INVITED
    with pytest.raises(SignupNotAllowedError) as refused:
        await gate.admit("stranger@example.com")

    assert refused.value.code == SignupNotAllowedError.INVITE_ONLY
    assert refused.value.status_code == 403
    assert refused.value.message == (
        "This Lemma is invite-only. Ask its owner for an invitation."
    )


@pytest.mark.asyncio
async def test_closed_refuses_even_an_invited_address() -> None:
    store = _Store(invited={"guest@example.com"})
    gate = _gate(store, deployment_kind="desktop", signup_mode="closed")

    with pytest.raises(SignupNotAllowedError) as refused:
        await gate.admit("guest@example.com")

    assert refused.value.code == SignupNotAllowedError.CLOSED


@pytest.mark.asyncio
async def test_a_server_may_be_made_invite_only_without_growing_an_owner() -> None:
    store = _Store(first=True, invited={"guest@example.com"})
    gate = _gate(store, signup_mode="invite_only")

    assert await gate.admit("guest@example.com") is Admission.INVITED
    with pytest.raises(SignupNotAllowedError):
        await gate.admit("stranger@example.com")
    assert store.reservations == []


@pytest.mark.asyncio
async def test_nobody_owns_a_server_even_if_a_row_says_so() -> None:
    owner = uuid4()
    store = _Store(owner=owner)

    assert await _gate(store).is_installation_owner(owner) is False
    assert store.owner_reads == 0


@pytest.mark.asyncio
async def test_only_the_recorded_account_owns_a_desktop_installation() -> None:
    owner = uuid4()
    gate = _gate(_Store(owner=owner), deployment_kind="desktop")

    assert await gate.is_installation_owner(owner) is True
    assert await gate.is_installation_owner(uuid4()) is False
    assert (
        await _gate(
            _Store(owner=None), deployment_kind="desktop"
        ).is_installation_owner(owner)
        is False
    )


@pytest.mark.asyncio
async def test_the_installation_view_reports_kind_ownership_and_mode() -> None:
    owner = uuid4()
    view = await _gate(_Store(owner=owner), deployment_kind="desktop").view_for(owner)

    assert (view.deployment, view.is_owner, view.signup_mode) == (
        "desktop",
        True,
        "invite_only",
    )
