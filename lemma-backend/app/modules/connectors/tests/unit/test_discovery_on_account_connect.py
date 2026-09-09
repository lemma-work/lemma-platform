"""Discovery runs when the account arrives, whichever way it arrived.

An install whose credential lives on the *account* has nothing to discover
with until an account exists, so it is committed with zero operations and the
first connection is the earliest moment the tool list can be fetched.

That step was wired into the OAuth callback alone. An MCP server connected with
an API key, a header, or no auth at all goes through `create_account` instead,
so it came up with zero tools every time and stayed that way until somebody
found the refresh endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.services import install_provisioning

pytestmark = pytest.mark.unit


class _Repository:
    """The operation store, recording what discovery decided to write."""

    def __init__(self, existing: list[object] | None = None) -> None:
        self.existing = existing or []
        self.replaced: list[dict] = []

    async def list_by_auth_config(self, auth_config_id, limit=None):
        del auth_config_id, limit
        return list(self.existing)

    async def replace_for_auth_config(self, **kwargs):
        self.replaced.append(kwargs)


class _Service:
    def __init__(self, repository: _Repository) -> None:
        self.auth_config_operation_repository = repository
        self.asked_for: list[object] = []
        self.uow = SimpleNamespace(commit=self._commit)

    async def _commit(self) -> None:
        return None

    async def get_connector(self, connector_id):
        self.asked_for.append(connector_id)
        return SimpleNamespace(id=connector_id, spec_for=lambda _kind: None)


def _auth_config():
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        connector_id="mcp",
        kind="mcp",
        config={},
        config_source=None,
    )


@pytest.fixture
def discovery(monkeypatch):
    """Record whether discovery was reached, without leaving the process."""
    reached: list[object] = []

    async def _discover(auth_config, connector, **kwargs):
        del connector, kwargs
        reached.append(auth_config)
        return

    async def _credentials(_service, _auth_config):
        return {"bearer_token": "t"}

    monkeypatch.setattr(install_provisioning, "discover_install_operations", _discover)
    monkeypatch.setattr(install_provisioning, "discovery_credentials", _credentials)
    return reached


async def test_an_install_with_no_operations_is_discovered(discovery):
    service = _Service(_Repository(existing=[]))
    config = _auth_config()

    await install_provisioning.discover_operations_for_new_account(service, config)

    assert discovery == [config]


async def test_an_install_that_already_has_operations_is_left_alone(discovery):
    """The tool list belongs to the server, so the second and later people to
    connect would re-ask the same question and get the same answer."""
    service = _Service(_Repository(existing=[object()]))

    await install_provisioning.discover_operations_for_new_account(
        service, _auth_config()
    )

    assert discovery == []


async def test_a_service_with_no_operation_store_does_nothing(discovery):
    service = _Service(_Repository())
    service.auth_config_operation_repository = None

    await install_provisioning.discover_operations_for_new_account(
        service, _auth_config()
    )

    assert discovery == []


def test_creating_an_account_reaches_discovery() -> None:
    """The wiring itself, which is what was missing.

    `create_account` is the credential-managed path -- an API key, a header, no
    auth -- and it committed the account and returned. Asserted on the source
    because the alternative is standing up the whole service to observe one
    call it either makes or does not.
    """
    import inspect

    from app.modules.connectors.services.connector_service import ConnectorService

    source = inspect.getsource(ConnectorService.create_account)
    assert "discover_operations_for_new_account" in source
    # After the commit: a discovery failure must not undo the connection.
    assert source.index("uow.commit") < source.index(
        "discover_operations_for_new_account"
    )
