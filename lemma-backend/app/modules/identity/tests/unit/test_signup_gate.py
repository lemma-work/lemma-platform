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

import pytest

from app.modules.identity.config import IdentitySettings
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.services.signup_gate import Admission, SignupGate

pytestmark = pytest.mark.unit


@dataclass
class _Store:
    users: bool = True
    invited: set[str] = field(default_factory=set)
    user_reads: int = 0

    async def has_any_user(self) -> bool:
        self.user_reads += 1
        return self.users

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
