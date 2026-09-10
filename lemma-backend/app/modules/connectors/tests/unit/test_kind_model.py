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
from pydantic import ValidationError

from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connector import (
    AuthProvider,
    ComposioKindSpec,
    ConnectorEntity,
    ConnectorKind,
    KindSpecAdapter,
    HttpKindSpec,
    kind_to_provider,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
    InstallOperationEntity,
    ResolvedOperation,
)
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity


def _dual_kind_connector() -> ConnectorEntity:
    """gmail ships as both a native OpenAPI connector and a Composio toolkit."""
    return ConnectorEntity(
        id="gmail",
        kinds=[
            HttpKindSpec(),
            ComposioKindSpec(toolkit_slug="gmail"),
        ],
    )


def test_one_connector_can_be_installed_as_two_different_kinds():
    # This is why kind lives on the install, not the catalog row: splitting
    # gmail into two connector rows would rename its grant resource id, the
    # accounts.connector_id foreign key and the pod-bundle export key.
    connector = _dual_kind_connector()
    assert connector.supported_kinds() == [
        ConnectorKind.HTTP,
        ConnectorKind.COMPOSIO,
    ]
    assert connector.spec_for(ConnectorKind.COMPOSIO).toolkit_slug == "gmail"
    assert connector.spec_for("http").kind is ConnectorKind.HTTP


def test_spec_for_rejects_a_kind_the_connector_does_not_support():
    with pytest.raises(ValueError, match="cannot be installed as 'sql'"):
        _dual_kind_connector().spec_for(ConnectorKind.SQL)


@pytest.mark.parametrize(
    ("kind", "provider"),
    [
        (ConnectorKind.COMPOSIO, AuthProvider.COMPOSIO),
        (ConnectorKind.HTTP, AuthProvider.LEMMA),
        (ConnectorKind.SQL, AuthProvider.LEMMA),
        (ConnectorKind.MCP, AuthProvider.LEMMA),
    ],
)
def test_kind_maps_onto_the_legacy_provider_vocabulary(kind, provider):
    assert kind_to_provider(kind) is provider


def test_capability_for_resolves_lemma_to_the_connectors_own_native_kind():
    connector = _dual_kind_connector()
    assert connector.capability_for(AuthProvider.LEMMA).kind is ConnectorKind.HTTP
    assert (
        connector.capability_for(AuthProvider.COMPOSIO).kind is ConnectorKind.COMPOSIO
    )


def test_a_kind_spec_must_name_its_kind():
    """The `provider`-shaped dict a caller could once pass is no longer read.

    It resolved to the vendored-package kind, which is the one kind that no
    longer exists, so accepting it would silently mislabel an install rather
    than fail.
    """
    with pytest.raises(ValidationError):
        ConnectorEntity(
            id="slack",
            kinds=[{"provider": "LEMMA", "auth_scheme": "OAUTH2"}],
        )


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
            kind=ConnectorKind.HTTP,
            provider_config={"client_id": "x"},
        )
        assert entity.kind is ConnectorKind.HTTP
        assert entity.config == {"client_id": "x"}
        # ...and still reads back the old way for callers not yet migrated.
        assert entity.provider is AuthProvider.LEMMA
        assert entity.provider_config == entity.config

    def test_an_install_states_its_kind(self):
        entity = AuthConfigEntity(
            organization_id=uuid4(),
            connector_id="mcp",
            name="internal-mcp",
            kind=ConnectorKind.MCP,
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
        kind=ConnectorKind.COMPOSIO,
        name="gmail_send_email",
    )
    assert catalog.kind is ConnectorKind.COMPOSIO
    assert catalog.provider is AuthProvider.COMPOSIO


def test_an_operation_defaults_to_the_native_kind():
    """`provider=` is no longer accepted, and the default is no longer
    `package`. Nothing writes a row without naming its kind, but a default that
    named a kind which does not exist would be unreadable the moment it did."""
    catalog = ConnectorOperationEntity(
        id="gmail:http:messages_send",
        connector_id="gmail",
        name="messages_send",
    )
    assert catalog.kind is ConnectorKind.HTTP


def test_trigger_entity_defaults_to_the_native_kind():
    trigger = ConnectorTriggerEntity(
        id="gmail:http:new_message",
        connector_id="gmail",
        event_type="new_message",
    )
    assert trigger.kind is ConnectorKind.HTTP
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
