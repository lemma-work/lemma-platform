"""A provider must only ever see sandboxes labelled with its own namespace.

This is a safety boundary, not a tidiness one. `reclaim_orphans` destroys every
provider object it can identify as ours that has no sandbox row, and a test run
owns a throwaway database in which no real workspace has a row. Share the
namespace with a live account and the sweep deletes live workspaces.

The provider has always supported the isolation; the factory did not pass it, so
every deployment and every test ran as ``lemma`` regardless. Then the factory
passed it and the *default* was still shared, which is how `lemma-dev` and
`lemma-prod` -- two API keys resolving to one E2B team -- ended up destroying
each other's sandboxes every five minutes. So there is no shared default now:
the namespace is derived from ENVIRONMENT, and refused outright for the two
environment names that many deployments answer to at once.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.e2b_common import (
    DEFAULT_METADATA_NAMESPACE,
    meta_sandbox_id,
)
from app.modules.workspace.services.provider_factory import (
    resolve_metadata_namespace,
)


def test_the_factory_carries_the_configured_namespace(monkeypatch) -> None:
    """Defaulting it in two places is how the isolation went missing."""
    monkeypatch.setattr(workspace_settings, "provider", "e2b")
    monkeypatch.setattr(workspace_settings, "e2b_api_key", "test-key")
    monkeypatch.setattr(workspace_settings, "e2b_metadata_namespace", "lemma-e2e-abc")

    from app.modules.workspace.services.provider_factory import build_provider

    provider = build_provider()

    assert provider._config.metadata_namespace == "lemma-e2e-abc"


def test_nothing_ships_with_a_shared_default() -> None:
    """The default was the bug, so there is no longer one to fall back to."""
    assert workspace_settings.e2b_metadata_namespace is None


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("development", "lemma-development"), ("production", "lemma-production")],
)
def test_a_deployed_environment_derives_its_own_namespace(
    environment: str, expected: str
) -> None:
    """`ENVIRONMENT` already tells the deployments apart, so use it.

    This is what makes the fix need no configuration change: `lemma-dev` reports
    `development` and `lemma-prod` reports `production`, so deriving from it
    separates the two the moment it deploys.
    """
    assert (
        resolve_metadata_namespace(configured=None, environment=environment) == expected
    )


@pytest.mark.parametrize("environment", ["local", "testing"])
def test_a_shared_environment_name_is_refused_rather_than_derived(
    environment: str,
) -> None:
    """Deriving here would rebuild the same collision between colleagues.

    Every developer's machine reports `local` and every CI run reports
    `testing`, so a derived value would be identical across all of them -- and
    those are exactly the deployments that pair a throwaway database with a real
    E2B account, which is the combination that turns the orphan sweep into a
    deletion.
    """
    with pytest.raises(RuntimeError) as raised:
        resolve_metadata_namespace(configured=None, environment=environment)

    message = str(raised.value)
    # The message has to name the hazard, not just the missing variable. Someone
    # hitting this at startup needs to know why it is not merely a nuisance.
    assert "E2B_METADATA_NAMESPACE" in message
    assert "destroys the user's files" in message


@pytest.mark.parametrize("environment", ["local", "testing", "production"])
def test_an_explicit_namespace_always_wins(environment: str) -> None:
    """Including where derivation would refuse: stating one is the way out."""
    assert (
        resolve_metadata_namespace(configured="lemma-mine", environment=environment)
        == "lemma-mine"
    )


def test_namespaces_do_not_collide_on_the_metadata_key() -> None:
    """Isolation is only real if the key itself differs."""
    assert meta_sandbox_id("lemma") != meta_sandbox_id("lemma-e2e-abc")


@pytest.mark.parametrize("namespace", ["lemma-e2e-abc", "lemma-conformance"])
def test_a_foreign_sandbox_is_unidentifiable(namespace: str) -> None:
    """What keeps the sweep away from another namespace's sandboxes.

    `reclaim_orphans` skips anything whose sandbox id it cannot read, and the id
    is stored under a namespaced key -- so a provider in one namespace reads
    None for a sandbox labelled by another, and leaves it alone.
    """
    production_metadata = {meta_sandbox_id(DEFAULT_METADATA_NAMESPACE): "some-uuid"}

    assert production_metadata.get(meta_sandbox_id(namespace)) is None
