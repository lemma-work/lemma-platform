"""Who gets the loopback relay to this Mac: the workspace of the user this
Mac's own Agent Host is paired to -- only. (locald's end checks the switch.)"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.services.host_loopback_policy import (
    LocalHostFacts,
    is_local_host_users_browser_sandbox,
    local_agent_host_ids,
)


#: The user this Mac's Agent Host is paired to.
PAIRED = uuid4()
#: The host id of that pairing, as this Mac's Agent Host config records it.
THIS_MAC = uuid4()


def _sandbox(
    *,
    kind: SandboxKind = SandboxKind.WORKSPACE,
    owner_kind: SandboxOwnerKind = SandboxOwnerKind.USER,
    owner_id: UUID = PAIRED,
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
    """A Desktop install; records whose pairings were asked about.

    `pairings` is which live host ids each user holds.
    """

    def __init__(
        self,
        *,
        is_desktop: bool = True,
        local: frozenset[UUID] = frozenset({THIS_MAC}),
        pairings: dict[UUID, set[UUID]] | None = None,
    ) -> None:
        self.asked: list[UUID] = []
        pairings = {PAIRED: {THIS_MAC}} if pairings is None else pairings

        async def paired(user_id: UUID, host_ids) -> bool:
            self.asked.append(user_id)
            return bool(pairings.get(user_id, set()) & set(host_ids))

        self.facts = LocalHostFacts(
            is_desktop=lambda: is_desktop,
            local_host_ids=lambda: local,
            paired_to_any_of=paired,
        )

    async def grants(self, sandbox: Sandbox) -> bool:
        return await is_local_host_users_browser_sandbox(sandbox, facts=self.facts)


async def test_the_workspace_of_the_user_paired_to_this_mac_gets_the_relay() -> None:
    desktop = Desktop()
    assert await desktop.grants(_sandbox()) is True
    assert desktop.asked == [PAIRED]


async def test_nobody_gets_it_while_this_macs_host_is_paired_to_nobody() -> None:
    """Unpaired, or its pairing revoked."""
    assert await Desktop(pairings={}).grants(_sandbox()) is False


async def test_a_user_paired_to_another_machine_never_does() -> None:
    """A teammate's own Mac, or an Agent Host run inside their own sandbox,
    holds a different host id: a pairing there is not this Mac."""
    teammate = uuid4()
    desktop = Desktop(pairings={PAIRED: {THIS_MAC}, teammate: {uuid4()}})
    assert await desktop.grants(_sandbox(owner_id=teammate)) is False


async def test_a_user_with_no_host_never_does() -> None:
    assert await Desktop().grants(_sandbox(owner_id=uuid4())) is False


async def test_without_a_local_agent_host_pairing_nobody_does() -> None:
    desktop = Desktop(local=frozenset())
    assert await desktop.grants(_sandbox()) is False
    assert desktop.asked == []


async def test_a_function_sandbox_never_does_even_the_paired_users() -> None:
    """A function has no browser, and the guest refuses the grant anyway."""
    desktop = Desktop()
    assert await desktop.grants(_sandbox(kind=SandboxKind.FUNCTION)) is False
    assert desktop.asked == [], "pairings should not even be asked"


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
    assert desktop.asked == [], "pairings are not a question off Desktop"


def test_the_local_host_ids_are_read_from_the_agent_host_config(
    tmp_path: Path,
) -> None:
    disabled = uuid4()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "installation_id": "this-mac",
                "targets": [
                    {"host_id": str(THIS_MAC), "host_secret": "never-read"},
                    {"host_id": str(disabled), "enabled": False},
                    {"host_id": "not-a-uuid"},
                    "not-a-target",
                ],
            }
        )
    )
    assert local_agent_host_ids(str(config)) == frozenset({THIS_MAC})


@pytest.mark.parametrize(
    "contents",
    [None, "not json", "[]", '{"targets": {}}', '{"installation_id": "x"}'],
)
def test_a_missing_or_unreadable_config_is_no_local_host(
    tmp_path: Path, contents: str | None
) -> None:
    config = tmp_path / "config.json"
    if contents is not None:
        config.write_text(contents)
    assert local_agent_host_ids(str(config)) == frozenset()
    assert local_agent_host_ids(None) == frozenset()
