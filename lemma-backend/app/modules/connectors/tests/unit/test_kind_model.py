"""The kind model, and the compatibility shims that carry callers across.

``provider`` (LEMMA/COMPOSIO) and a separate notion of kind both used to
discriminate; ``kind`` is now the only axis. Everything outside this module --
agent surfaces, schedule composition, pod bundles -- still speaks the old
vocabulary for one release, so these lock down both the new shape and the
translation, including the one case that forced kind onto the install rather
than the catalog row: a connector installable *both* ways.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connector import (
    AuthProvider,
    ComposioKindSpec,
    ConnectorEntity,
    ConnectorKind,
    KindSpecAdapter,
    PackageKindSpec,
    kind_to_provider,
    provider_to_kind,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
    InstallOperationEntity,
    ResolvedOperation,
)
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity


def _dual_kind_connector() -> ConnectorEntity:
    """gmail ships as both a vendored package and a Composio toolkit."""
    return ConnectorEntity(
        id="gmail",
        kinds=[
            PackageKindSpec(package_name="lemma_connectors.gmail"),
            ComposioKindSpec(toolkit_slug="gmail"),
        ],
    )


def test_one_connector_can_be_installed_as_two_different_kinds():
    # This is why kind lives on the install, not the catalog row: splitting
    # gmail into two connector rows would rename its grant resource id, the
    # accounts.connector_id foreign key and the pod-bundle export key.
    connector = _dual_kind_connector()
    assert connector.supported_kinds() == [
        ConnectorKind.PACKAGE,
        ConnectorKind.COMPOSIO,
    ]
    assert connector.spec_for(ConnectorKind.COMPOSIO).toolkit_slug == "gmail"
    assert connector.spec_for("package").package_name == "lemma_connectors.gmail"


def test_spec_for_rejects_a_kind_the_connector_does_not_support():
    with pytest.raises(ValueError, match="cannot be installed as 'sql'"):
        _dual_kind_connector().spec_for(ConnectorKind.SQL)


@pytest.mark.parametrize(
    ("kind", "provider"),
    [
        (ConnectorKind.COMPOSIO, AuthProvider.COMPOSIO),
        (ConnectorKind.PACKAGE, AuthProvider.LEMMA),
        (ConnectorKind.HTTP, AuthProvider.LEMMA),
        (ConnectorKind.SQL, AuthProvider.LEMMA),
        (ConnectorKind.MCP, AuthProvider.LEMMA),
    ],
)
def test_kind_maps_onto_the_legacy_provider_vocabulary(kind, provider):
    assert kind_to_provider(kind) is provider


def test_provider_maps_back_to_the_kind_that_actually_shipped():
    # LEMMA covered every non-Composio install. At the point of the collapse the
    # native catalog was entirely vendored packages, so this is lossless for all
    # existing data; http/sql/mcp installs must state their kind explicitly.
    assert provider_to_kind(AuthProvider.COMPOSIO) is ConnectorKind.COMPOSIO
    assert provider_to_kind(AuthProvider.LEMMA) is ConnectorKind.PACKAGE
    assert provider_to_kind("LEMMA") is ConnectorKind.PACKAGE


def test_capability_for_resolves_lemma_to_the_connectors_own_native_kind():
    connector = _dual_kind_connector()
    assert connector.capability_for(AuthProvider.LEMMA).kind is ConnectorKind.PACKAGE
    assert (
        connector.capability_for(AuthProvider.COMPOSIO).kind is ConnectorKind.COMPOSIO
    )


def test_connector_still_accepts_provider_capabilities_from_unmigrated_callers():
    connector = ConnectorEntity(
        id="slack",
        provider_capabilities=[{"provider": "LEMMA", "auth_scheme": "OAUTH2"}],
    )
    assert connector.supported_kinds() == [ConnectorKind.PACKAGE]
    assert connector.provider_capabilities == connector.kinds


def test_install_schema_reads_the_legacy_auth_config_schema_key():
    # The catalog JSON and the API still say auth_config_schema.
    spec = KindSpecAdapter.validate_python(
        {"kind": "http", "auth_config_schema": {"type": "object"}}
    )
    assert spec.install_schema == {"type": "object"}
    assert spec.auth_config_schema == spec.install_schema


class TestAuthConfigEntity:
    def test_accepts_legacy_provider_and_provider_config(self):
        entity = AuthConfigEntity(
            organization_id=uuid4(),
            connector_id="slack",
            name="slack-eng",
            provider=AuthProvider.LEMMA,
            provider_config={"client_id": "x"},
        )
        assert entity.kind is ConnectorKind.PACKAGE
        assert entity.config == {"client_id": "x"}
        # ...and still reads back the old way for callers not yet migrated.
        assert entity.provider is AuthProvider.LEMMA
        assert entity.provider_config == entity.config

    def test_explicit_kind_wins_over_a_legacy_provider(self):
        entity = AuthConfigEntity(
            organization_id=uuid4(),
            connector_id="mcp",
            name="internal-mcp",
            kind=ConnectorKind.MCP,
            provider=AuthProvider.LEMMA,
        )
        assert entity.kind is ConnectorKind.MCP
        assert entity.provider is AuthProvider.LEMMA
        assert entity.uses_native and not entity.uses_composio

    def test_installs_are_not_default_unless_asked(self):
        entity = AuthConfigEntity(
            organization_id=uuid4(), connector_id="slack", name="slack-support"
        )
        assert entity.is_default is False


def test_operation_entities_carry_kind_and_the_legacy_provider_view():
    catalog = ConnectorOperationEntity(
        id="gmail:composio:gmail_send_email",
        connector_id="gmail",
        provider=AuthProvider.COMPOSIO,
        name="gmail_send_email",
    )
    assert catalog.kind is ConnectorKind.COMPOSIO
    assert catalog.provider is AuthProvider.COMPOSIO


def test_trigger_entity_accepts_legacy_provider():
    trigger = ConnectorTriggerEntity(
        id="gmail:lemma:new_message",
        connector_id="gmail",
        provider=AuthProvider.LEMMA,
        event_type="new_message",
    )
    assert trigger.kind is ConnectorKind.PACKAGE
    assert trigger.provider is AuthProvider.LEMMA


def test_resolved_operation_records_which_table_answered():
    catalog = ConnectorOperationEntity(
        id="sql:sql:query", connector_id="sql", kind=ConnectorKind.SQL, name="query"
    )
    install = InstallOperationEntity(
        id=uuid4(),
        auth_config_id=uuid4(),
        organization_id=uuid4(),
        name="query",
        execution={"kind": "mcp", "tool_name": "query"},
    )
    assert ResolvedOperation.from_catalog(catalog).source == "catalog"
    assert ResolvedOperation.from_install(install).source == "install"
    # The install descriptor is what the executor runs, so it must survive.
    assert ResolvedOperation.from_install(install).execution["tool_name"] == "query"


def test_install_operations_require_an_execution_descriptor():
    # A discovered operation is only reachable through its kind's executor, so
    # there is no meaningful row without one.
    with pytest.raises(ValueError):
        InstallOperationEntity(
            id=uuid4(),
            auth_config_id=uuid4(),
            organization_id=uuid4(),
            name="query",
        )
