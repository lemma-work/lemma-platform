"""Who gets the loopback relay to the owner's Mac: their own workspace, only."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.services.host_loopback_policy import (
    InstallationFacts,
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


class Desktop:
    """A Desktop install whose owner is `OWNER`; records who was asked about."""

    def __init__(self, *, is_desktop: bool = True) -> None:
        self.asked: list[UUID] = []

        async def is_owner(user_id: UUID) -> bool:
            self.asked.append(user_id)
            return user_id == OWNER

        self.facts = InstallationFacts(is_desktop=lambda: is_desktop, is_owner=is_owner)

    async def grants(self, sandbox: Sandbox) -> bool:
        return await is_owner_browser_sandbox(sandbox, facts=self.facts)


async def test_the_owners_own_workspace_gets_the_relay() -> None:
    desktop = Desktop()
    assert await desktop.grants(_sandbox()) is True
    assert desktop.asked == [OWNER]


async def test_an_invited_persons_workspace_never_does() -> None:
    assert await Desktop().grants(_sandbox(owner_id=uuid4())) is False


async def test_a_function_sandbox_never_does_even_the_owners() -> None:
    """A function has no browser, and the guest refuses the grant anyway."""
    desktop = Desktop()
    assert await desktop.grants(_sandbox(kind=SandboxKind.FUNCTION)) is False
    assert desktop.asked == [], "ownership should not even be asked"


async def test_a_sandbox_not_owned_by_a_person_never_does() -> None:
    desktop = Desktop()
    for owner_kind in SandboxOwnerKind:
        if owner_kind is SandboxOwnerKind.USER:
            continue
        assert await desktop.grants(_sandbox(owner_kind=owner_kind)) is False
    assert desktop.asked == []


async def test_off_desktop_nobody_does() -> None:
    desktop = Desktop(is_desktop=False)
    assert await desktop.grants(_sandbox()) is False
    assert desktop.asked == [], "ownership is not a question off Desktop"
