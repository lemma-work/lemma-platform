"""Who gets the loopback relay to the owner's Mac: their own workspace, only."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.identity.contracts import installation
from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.services.host_loopback_policy import (
    is_owner_browser_sandbox,
)

pytestmark = pytest.mark.asyncio

OWNER = uuid4()


def _sandbox(
    *,
    kind: SandboxKind = SandboxKind.WORKSPACE,
    owner_kind: SandboxOwnerKind = SandboxOwnerKind.USER,
    owner_id: UUID = OWNER,
) -> Sandbox:
    return Sandbox(
        id=uuid4(),
        kind=kind,
        owner_kind=owner_kind,
        owner_id=owner_id,
        slug="default",
        display_name="",
        profile_name="workspace",
        profile_digest="sha256:test",
        desired_state=SandboxDesiredState.PRESENT,
        epoch=1,
        storage_generation=1,
    )


@pytest.fixture
def desktop(monkeypatch) -> list[UUID]:
    """A Desktop install whose owner is `OWNER`; records who was asked about."""
    asked: list[UUID] = []

    async def is_owner(user_id: UUID) -> bool:
        asked.append(user_id)
        return user_id == OWNER

    monkeypatch.setattr(installation, "is_desktop_installation", lambda: True)
    monkeypatch.setattr(installation, "is_installation_owner", is_owner)
    return asked


async def test_the_owners_own_workspace_gets_the_relay(desktop) -> None:
    assert await is_owner_browser_sandbox(_sandbox()) is True
    assert desktop == [OWNER]


async def test_an_invited_persons_workspace_never_does(desktop) -> None:
    assert await is_owner_browser_sandbox(_sandbox(owner_id=uuid4())) is False


async def test_a_function_sandbox_never_does_even_the_owners(desktop) -> None:
    """A function has no browser, and the guest refuses the grant anyway."""
    assert (
        await is_owner_browser_sandbox(_sandbox(kind=SandboxKind.FUNCTION)) is False
    )
    assert desktop == [], "ownership should not even be asked"


async def test_a_sandbox_not_owned_by_a_person_never_does(desktop) -> None:
    other_kinds = [kind for kind in SandboxOwnerKind if kind is not SandboxOwnerKind.USER]
    for owner_kind in other_kinds:
        sandbox = _sandbox(owner_kind=owner_kind, owner_id=OWNER)
        assert await is_owner_browser_sandbox(sandbox) is False, owner_kind
    assert desktop == []


async def test_off_desktop_nobody_does(monkeypatch) -> None:
    async def must_not_ask(user_id: UUID) -> bool:
        raise AssertionError("ownership is not a question off Desktop")

    monkeypatch.setattr(installation, "is_desktop_installation", lambda: False)
    monkeypatch.setattr(installation, "is_installation_owner", must_not_ask)
    assert await is_owner_browser_sandbox(_sandbox()) is False
