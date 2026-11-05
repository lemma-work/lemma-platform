from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from lemma_sdk.errors import (
    LemmaConflictError,
    LemmaDeprecationWarning,
    LemmaNotFoundError,
    LemmaPermissionError,
)
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


def _connectors_over(handler: Any) -> BoundConnectors:
    """A connectors facade whose raw-httpx calls hit a mocked transport."""
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.example.test",
        )
    )
    return BoundConnectors(transport, org_id=ORG_ID)


def test_missing_skill_doc_raises_the_same_not_found_as_every_other_call() -> None:
    # These two methods bypass the generated client, and used to raise a bare
    # LemmaAPIError that `except LemmaNotFoundError` did not catch -- dropping
    # the server's code, details and request id with it.
    connectors = _connectors_over(
        lambda _: httpx.Response(
            404,
            json={"message": "no skill doc", "code": "not_found"},
            headers={"x-request-id": "req-9"},
        )
    )

    with pytest.raises(LemmaNotFoundError) as excinfo:
        connectors.apps.skill("github")

    assert excinfo.value.code == "not_found"
    assert excinfo.value.request_id == "req-9"
    assert excinfo.value.message == "no skill doc"


def test_connector_status_maps_a_permission_denial_to_its_typed_error() -> None:
    connectors = _connectors_over(
        lambda _: httpx.Response(403, json={"message": "not your org"})
    )

    with pytest.raises(LemmaPermissionError) as excinfo:
        connectors.status()

    assert excinfo.value.message == "not your org"


POD_ID = "00000000-0000-0000-0000-0000000000aa"
POD_ACCOUNT_ID = "00000000-0000-0000-0000-0000000000bb"
ACCOUNT_ID = "00000000-0000-0000-0000-0000000000cc"


def _pod_connectors_over(handler: Any) -> BoundConnectors:
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.example.test",
        )
    )
    return BoundConnectors(transport, org_id=ORG_ID, pod_id=POD_ID)


def test_execute_runs_through_the_pod_connector_named_by_slug() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"sent": True}})

    connectors = _pod_connectors_over(handler)

    result = connectors.execute("support-inbox", "GMAIL_SEND_EMAIL", {"to": "a@b.c"})

    assert result.result == {"sent": True}
    [request] = seen
    assert request.method == "POST"
    assert request.url.path == (
        f"/pods/{POD_ID}/connectors/support-inbox/operations/GMAIL_SEND_EMAIL/execute"
    )
    # The pod connector decides the account: nothing names one.
    assert json.loads(request.content) == {"payload": {"to": "a@b.c"}}


# --- Deprecated: an installation and an account, as before pod connectors ---


@pytest.fixture
def _fresh_deprecations(monkeypatch: pytest.MonkeyPatch) -> None:
    # The transport warns once per message per process; start each test clean.
    monkeypatch.setattr("lemma_sdk.transport._warned_deprecations", set())


def _org_execute_path(auth_config: str, operation: str) -> str:
    return (
        f"/organizations/{ORG_ID}/connectors/{auth_config}/operations/"
        f"{operation}/execute"
    )


def test_execute_with_an_account_takes_the_deprecated_route_and_warns() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"sent": True}})

    connectors = _pod_connectors_over(handler)

    with pytest.warns(LemmaDeprecationWarning, match="drop account_id"):
        result = connectors.execute(
            "workspace-gmail",
            "GMAIL_SEND_EMAIL",
            {"to": "a@b.c"},
            account_id=ACCOUNT_ID,
        )

    assert result.result == {"sent": True}
    [request] = seen
    assert request.url.path == _org_execute_path("workspace-gmail", "GMAIL_SEND_EMAIL")
    # The pod still travels: the server serves the call through its connectors.
    assert json.loads(request.content) == {
        "payload": {"to": "a@b.c"},
        "account_id": ACCOUNT_ID,
        "pod_id": POD_ID,
    }


def test_execute_without_a_pod_takes_the_deprecated_route_and_warns() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {}})

    connectors = _connectors_over(handler)

    with pytest.warns(LemmaDeprecationWarning):
        connectors.execute("workspace-gmail", "GMAIL_LIST", {})

    [request] = seen
    assert request.url.path == _org_execute_path("workspace-gmail", "GMAIL_LIST")
    assert json.loads(request.content) == {"payload": {}}


def test_an_installation_name_falls_back_to_the_deprecated_route(
    _fresh_deprecations: None,
) -> None:
    """Code written before pod connectors passed the installation's name. The
    pod route does not know it; the deprecated route does, and the server's
    deprecation header is what says so -- not a guess on the client."""
    seen: list[httpx.Request] = []
    notice = "connector.operation.execute is deprecated: call the pod route"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/pods/"):
            return httpx.Response(
                404,
                json={"code": "POD_CONNECTOR_NOT_FOUND", "message": "no such slug"},
            )
        return httpx.Response(
            200,
            json={"result": {"ok": True}},
            headers={"X-Lemma-Deprecation": notice, "Deprecation": "true"},
        )

    connectors = _pod_connectors_over(handler)

    with pytest.warns(LemmaDeprecationWarning, match="call the pod route"):
        result = connectors.execute("workspace-gmail", "GMAIL_LIST", {"n": 1})

    assert result.result == {"ok": True}
    pod_call, org_call = seen
    assert pod_call.url.path.startswith(f"/pods/{POD_ID}/connectors/workspace-gmail/")
    assert org_call.url.path == _org_execute_path("workspace-gmail", "GMAIL_LIST")
    assert json.loads(org_call.content) == {"payload": {"n": 1}, "pod_id": POD_ID}


def test_any_other_not_found_is_raised_not_retried_elsewhere() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            404, json={"code": "OPERATION_NOT_FOUND", "message": "no such op"}
        )

    connectors = _pod_connectors_over(handler)

    with pytest.raises(LemmaNotFoundError) as excinfo:
        connectors.execute("gmail", "NOPE", {})

    assert excinfo.value.code == "OPERATION_NOT_FOUND"
    assert len(seen) == 1


def test_operations_execute_sends_the_account_and_pod_it_is_given() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {}})

    connectors = _connectors_over(handler)
    other_pod = "00000000-0000-0000-0000-0000000000dd"

    connectors.operations.execute(
        "workspace-gmail",
        "GMAIL_SEND_EMAIL",
        {"to": "a@b.c"},
        account_id=ACCOUNT_ID,
        pod_id=other_pod,
    )
    connectors.operations.execute("workspace-gmail", "GMAIL_LIST", {})

    first, second = seen
    assert first.method == "POST"
    assert first.url.path == _org_execute_path("workspace-gmail", "GMAIL_SEND_EMAIL")
    assert json.loads(first.content) == {
        "payload": {"to": "a@b.c"},
        "account_id": ACCOUNT_ID,
        "pod_id": other_pod,
    }
    # Neither given, and no pod bound: only the payload is sent.
    assert json.loads(second.content) == {"payload": {}}


def test_a_connection_required_refusal_carries_its_connect_link() -> None:
    link = f"https://app.example.test/pods/{POD_ID}/connections?connector=gmail"
    connectors = _pod_connectors_over(
        lambda _: httpx.Response(
            409,
            json={
                "code": "CONNECTION_REQUIRED",
                "message": "Connect your gmail account to use 'gmail' in this pod.",
                "details": {"pod_connector": "gmail", "connect_url": link},
            },
        )
    )

    with pytest.raises(LemmaConflictError) as excinfo:
        connectors.execute("gmail", "GMAIL_SEND_EMAIL", {})

    assert excinfo.value.code == "CONNECTION_REQUIRED"
    assert excinfo.value.connect_url == link
    assert link in str(excinfo.value)


def test_pod_connector_listing_and_discovery_are_pod_scoped() -> None:
    seen: list[httpx.Request] = []

    discovered = {
        "connector_id": "gmail",
        "items": [],
        "total_operations": 0,
        "returned_count": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/operations"):
            return httpx.Response(200, json=discovered)
        if "/operations/" in request.url.path:
            return httpx.Response(200, json={"name": "op", "input_schema": {}})
        return httpx.Response(200, json={"items": []})

    connectors = _pod_connectors_over(handler)

    connectors.pod_connectors()
    connectors.pod_operations("gmail", "send email", limit=5)
    connectors.pod_operation("gmail", "GMAIL_SEND_EMAIL")

    assert [r.url.path for r in seen] == [
        f"/pods/{POD_ID}/connectors",
        f"/pods/{POD_ID}/connectors/gmail/operations",
        f"/pods/{POD_ID}/connectors/gmail/operations/GMAIL_SEND_EMAIL",
    ]
    assert dict(seen[1].url.params) == {"query": "send email", "limit": "5"}


def test_declaring_a_connector_and_sharing_an_account_are_pod_requests() -> None:
    seen: list[httpx.Request] = []

    stamp = "2026-10-22T00:00:00Z"
    connector = {
        "id": POD_ACCOUNT_ID,
        "pod_id": POD_ID,
        "slug": "support-inbox",
        "auth_config_id": AUTH_CONFIG_ID,
        "mode": "SHARED",
        "required": False,
        "visibility": "POD",
        "status": "READY",
        "created_at": stamp,
        "updated_at": stamp,
    }
    pod_account = {
        "id": POD_ACCOUNT_ID,
        "pod_id": POD_ID,
        "account_id": ACCOUNT_ID,
        "auth_config_id": AUTH_CONFIG_ID,
        "owner_user_id": ACCOUNT_ID,
        "visibility": "POD",
        "is_member_default": False,
        "source": "MANUAL",
        "created_at": stamp,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        if request.url.path.endswith("/connectors"):
            return httpx.Response(201, json=connector)
        return httpx.Response(201, json=pod_account)

    connectors = _pod_connectors_over(handler)

    connectors.declare_pod_connector(
        "support-inbox",
        AUTH_CONFIG_ID,
        "SHARED",
        shared_pod_account_id=POD_ACCOUNT_ID,
        purpose="The team inbox",
    )
    connectors.add_pod_account(ACCOUNT_ID, visibility="POD")
    connectors.pod_accounts()

    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", f"/pods/{POD_ID}/connectors"),
        ("POST", f"/pods/{POD_ID}/connector-accounts"),
        ("GET", f"/pods/{POD_ID}/connector-accounts"),
    ]
    assert json.loads(seen[0].content) == {
        "slug": "support-inbox",
        "auth_config_id": AUTH_CONFIG_ID,
        "mode": "SHARED",
        "shared_pod_account_id": POD_ACCOUNT_ID,
        "required": False,
        "purpose": "The team inbox",
    }
    assert json.loads(seen[1].content) == {
        "account_id": ACCOUNT_ID,
        "visibility": "POD",
    }
