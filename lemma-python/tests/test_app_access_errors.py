"""App handoff clients expose expected refusals as typed error responses."""

import httpx
import pytest

from lemma_sdk.openapi_client import Client
from lemma_sdk.openapi_client.api.apps import (
    app_access_redeem,
    app_access_request_authorize,
    app_access_request_create,
)
from lemma_sdk.openapi_client.models import (
    AppAccessAuthorizeRequest,
    AppAccessCreateRequest,
    AppAccessRedeemRequest,
    ErrorResponse,
)


@pytest.mark.parametrize(
    ("operation", "status_code"),
    [
        ("create", 401),
        ("create", 429),
        ("create", 503),
        ("authorize", 401),
        ("authorize", 403),
        ("authorize", 404),
        ("authorize", 503),
        ("redeem", 401),
        ("redeem", 503),
    ],
)
def test_expected_app_access_refusals_are_parsed(
    operation: str, status_code: int
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"message": "Access refused", "code": "APP_ACCESS_INVALID"},
            request=request,
        )

    with httpx.Client(
        base_url="https://api.example.test", transport=httpx.MockTransport(refuse)
    ) as transport:
        client = Client(
            base_url="https://api.example.test", raise_on_unexpected_status=True
        ).set_httpx_client(transport)
        if operation == "create":
            response = app_access_request_create.sync_detailed(
                client=client, body=AppAccessCreateRequest(challenge="c" * 43)
            )
        elif operation == "authorize":
            response = app_access_request_authorize.sync_detailed(
                "r" * 43,
                client=client,
                body=AppAccessAuthorizeRequest(
                    app_origin="https://orders.apps.example.test"
                ),
            )
        else:
            response = app_access_redeem.sync_detailed(
                client=client,
                body=AppAccessRedeemRequest(
                    request_id="r" * 43, code="c" * 43, verifier="v" * 64
                ),
            )
    assert response.status_code == status_code
    assert isinstance(response.parsed, ErrorResponse)
    assert response.parsed.message == "Access refused"
    assert response.parsed.code == "APP_ACCESS_INVALID"
