"""A provider must only ever see sandboxes labelled with its own namespace.

This is a safety boundary, not a tidiness one. `reclaim_orphans` destroys every
provider object it can identify as ours that has no sandbox row, and a test run
owns a throwaway database in which no real workspace has a row. Share the
namespace with a live account and the sweep deletes live workspaces.

The provider has always supported the isolation; the factory did not pass it, so
every deployment and every test ran as ``lemma`` regardless.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.e2b_common import (
    DEFAULT_METADATA_NAMESPACE,
    meta_sandbox_id,
)


def test_the_factory_carries_the_configured_namespace(monkeypatch) -> None:
    """Defaulting it in two places is how the isolation went missing."""
    monkeypatch.setattr(workspace_settings, "provider", "e2b")
    monkeypatch.setattr(workspace_settings, "e2b_api_key", "test-key")
    monkeypatch.setattr(workspace_settings, "e2b_metadata_namespace", "lemma-e2e-abc")

    from app.modules.workspace.services.provider_factory import build_provider

    provider = build_provider()

    assert provider._config.metadata_namespace == "lemma-e2e-abc"


def test_the_shipped_default_is_the_production_namespace() -> None:
    """Production keeps the bare name; everything else must opt out of it."""
    assert workspace_settings.e2b_metadata_namespace == DEFAULT_METADATA_NAMESPACE


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
