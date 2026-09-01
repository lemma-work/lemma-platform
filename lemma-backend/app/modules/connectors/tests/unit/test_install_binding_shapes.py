"""The routing key has to survive the shape a credential actually arrives in.

`resolve_external_ref` declared a `dict` and `_dig` only walks mappings, but
every OAuth path holds a typed `OAuthCredentials`. So the column this function
fills was `None` for every OAuth account in the database -- 0 of 55 populated
on a live instance -- and nothing failed, because an absent routing key just
routes nothing.
"""

from __future__ import annotations

from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.install_binding import resolve_external_ref


def test_a_typed_credential_resolves_the_same_as_a_mapping():
    """The regression. Both shapes reach this function from paths one `if`
    apart, and only one of them used to work."""
    credentials = OAuthCredentials(
        access_token="t", raw_response={"team": {"id": "T0001"}}
    )
    assert resolve_external_ref("slack", credentials) == "T0001"
    assert resolve_external_ref("slack", credentials.model_dump()) == "T0001"


def test_nothing_to_resolve_is_not_an_error():
    assert resolve_external_ref("slack", None) is None
    assert resolve_external_ref("slack", {}) is None
    assert resolve_external_ref("slack", OAuthCredentials(access_token="t")) is None


def test_an_unknown_connector_resolves_nothing_rather_than_guessing():
    """A wrong routing key is worse than none: it sends another tenant's events
    to this account."""
    assert resolve_external_ref("not-a-connector", {"team": {"id": "T1"}}) is None
