from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming


def test_a_name_is_a_pure_function_of_durable_state() -> None:
    """This is what makes create idempotent: two calls agree, so a retry after
    a lost response finds the container instead of creating a second one."""
    sandbox_id = uuid4()
    first = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 7)
    second = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 7)
    assert first == second


def test_the_epoch_is_in_the_name_so_it_fences_before_routing() -> None:
    """A label would have to be read to be checked, and by then the operation
    has already been sent to the container."""
    sandbox_id = uuid4()
    assert naming.container_name(
        sandbox_id, SandboxKind.WORKSPACE, 4
    ) != naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 5)


def test_kinds_do_not_collide_even_when_ids_do() -> None:
    """A pod id and a user id are drawn from the same space and may coincide."""
    shared = uuid4()
    assert naming.container_name(
        shared, SandboxKind.WORKSPACE, 1
    ) != naming.container_name(shared, SandboxKind.FUNCTION, 1)


def test_names_round_trip_back_to_identity() -> None:
    sandbox_id = uuid4()
    for kind in (SandboxKind.WORKSPACE, SandboxKind.FUNCTION):
        name = naming.container_name(sandbox_id, kind, 12)
        assert naming.parse_container_name(name) == (sandbox_id, kind, 12)


def test_docker_leading_slash_is_tolerated() -> None:
    """Docker reports container names as "/name" in listings."""
    sandbox_id = uuid4()
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 2)
    assert naming.parse_container_name(f"/{name}") == (
        sandbox_id,
        SandboxKind.WORKSPACE,
        2,
    )


@pytest.mark.parametrize(
    "name",
    [
        "",
        "postgres",
        "ab-w-abc-def",  # the pre-consolidation scheme
        "lemma-ws-not-a-uuid-1",
        "lemma-xx-00000000000000000000000000000001-1",
        "lemma-ws-00000000000000000000000000000001-notanint",
        "lemma-ws-00000000000000000000000000000001",  # too few parts
    ],
)
def test_foreign_names_are_not_claimed(name: str) -> None:
    """A sweep destroys what it can identify, so misparsing someone else's
    container as ours would delete it."""
    assert naming.parse_container_name(name) is None


def test_names_are_portable_across_providers() -> None:
    """Kubernetes object names are stricter than Docker's: lowercase
    alphanumeric and dashes only. Hex uuids keep this seam usable there."""
    name = naming.container_name(uuid4(), SandboxKind.WORKSPACE, 1)
    assert name.replace("-", "").isalnum()
    assert name == name.lower()


def test_a_volume_name_is_only_ever_used_for_a_new_volume() -> None:
    sandbox_id = uuid4()
    assert naming.volume_name(sandbox_id, 1) != naming.volume_name(sandbox_id, 2)
    # Deliberately unlike the legacy `ab-ws-{token}` shape, so a derived name
    # can never be mistaken for an adopted one.
    assert not naming.volume_name(sandbox_id, 1).startswith("ab-ws-")


@pytest.mark.parametrize("bad", [0, -1])
def test_generations_and_epochs_must_be_positive(bad: int) -> None:
    with pytest.raises(ValueError):
        naming.container_name(uuid4(), SandboxKind.WORKSPACE, bad)
    with pytest.raises(ValueError):
        naming.volume_name(uuid4(), bad)
