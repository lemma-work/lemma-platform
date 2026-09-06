"""A token endpoint answering 200 with the wrong shape must not raise.

`_check_admin_consent_granted` reads `access_token` off the decoded body. Entra
answers with an object; a proxy, a captive portal or a misrouted host can answer
200 with a list or a bare string, and `.get` on one of those is an
`AttributeError`.

That used to be absorbed by an `except Exception` around the whole block. When
that handler was narrowed to the transport errors it can actually raise, the
`AttributeError` stopped being caught -- so a malformed 200 would have failed
the Teams setup request instead of reporting "consent not granted", which is the
established answer for "we could not tell".

The rule is `access_token_from`, tested here directly. Nothing is patched: the
function takes the decoded body and returns a token or `None`, so there is no
HTTP client, cache or settings object to stand up in front of it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.agent_surfaces.services.surface_consent import access_token_from

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="empty-list"),
        pytest.param([{"access_token": "t"}], id="list-of-objects"),
        pytest.param("access_token=t", id="bare-string"),
        pytest.param(None, id="null"),
        pytest.param(7, id="number"),
        pytest.param(True, id="bool"),
    ],
)
def test_a_body_that_is_not_an_object_has_no_token(payload: Any) -> None:
    # `None` rather than a raise: the caller turns it into `granted: false`,
    # which is what "we could not tell" has always meant here.
    assert access_token_from(payload) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"error": "invalid_client"}, id="error-response"),
        pytest.param({"access_token": None}, id="null-token"),
        pytest.param({"access_token": ""}, id="empty-token"),
    ],
)
def test_an_object_without_a_usable_token_has_no_token(payload: Any) -> None:
    assert not access_token_from(payload)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"access_token": 12345}, id="number"),
        pytest.param({"access_token": ["t"]}, id="list"),
        pytest.param({"access_token": {"value": "t"}}, id="object"),
    ],
)
def test_a_token_that_is_not_a_string_is_refused(payload: Any) -> None:
    # It would otherwise reach an `Authorization: Bearer` header as whatever it
    # is, and the failure would surface as a Graph 401 rather than as the
    # malformed token response it actually is.
    assert access_token_from(payload) is None


def test_the_ordinary_response_still_yields_its_token() -> None:
    assert access_token_from({"access_token": "eyJ0", "expires_in": 3599}) == "eyJ0"
