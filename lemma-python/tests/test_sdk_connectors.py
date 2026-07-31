from __future__ import annotations

from typing import Any

import pytest

from lemma_sdk.openapi_client.api.connectors import connector_account_create
from lemma_sdk.openapi_client.models.account_create_schema import AccountCreateSchema
from lemma_sdk.resources.connectors import BoundConnectors
from lemma_sdk.transport import LemmaTransport


ORG_ID = "00000000-0000-0000-0000-000000000001"
AUTH_CONFIG_ID = "00000000-0000-0000-0000-000000000002"


def _request(**values: Any) -> AccountCreateSchema:
    return AccountCreateSchema.from_dict({"credentials": {}, **values})


def _resource(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> BoundConnectors:
    def sync_detailed(organization_id, *, client, body):  # type: ignore[no-untyped-def]
        captured["organization_id"] = organization_id
        captured["body"] = body.to_dict()
        return type(
            "Response",
            (),
            {"status_code": 200, "parsed": {"id": "account-1"}, "headers": {}},
        )()

    monkeypatch.setattr(connector_account_create, "sync_detailed", sync_detailed)
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    return BoundConnectors(transport, org_id=ORG_ID)


def test_account_create_legacy_name_is_merged_into_body_without_extra_path_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    resource = _resource(monkeypatch, captured)

    result = resource.accounts.create("workspace-slack", _request())

    assert result == {"id": "account-1"}
    assert str(captured["organization_id"]) == ORG_ID
    assert captured["body"]["auth_config_name"] == "workspace-slack"
    assert "auth_config_id" not in captured["body"]


def test_account_create_request_name_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    resource = _resource(monkeypatch, captured)

    resource.accounts.create("legacy-name", _request(auth_config_name="request-name"))

    assert captured["body"]["auth_config_name"] == "request-name"


def test_account_create_preserves_auth_config_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    resource = _resource(monkeypatch, captured)

    resource.accounts.create("ignored", _request(auth_config_id=AUTH_CONFIG_ID))

    assert captured["body"]["auth_config_id"] == AUTH_CONFIG_ID
    assert "auth_config_name" not in captured["body"]


def test_account_create_rejects_conflicting_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = _resource(monkeypatch, {})

    with pytest.raises(ValueError, match="Specify only one"):
        resource.accounts.create(
            "legacy-name",
            _request(auth_config_name="request-name", auth_config_id=AUTH_CONFIG_ID),
        )


def test_account_create_rejects_missing_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = _resource(monkeypatch, {})

    with pytest.raises(ValueError, match="Either auth_config_name or auth_config_id"):
        resource.accounts.create("", _request())
