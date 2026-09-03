from __future__ import annotations

import pytest

from app.modules.connectors.domain.install_binding import (
    bind_external_ref,
    resolve_external_ref,
)

pytestmark = pytest.mark.unit


def test_jira_speaks_for_its_cloud_site():
    assert (
        resolve_external_ref(
            "jira",
            {
                "access_token": "t",
                "user_data": {"cloud_id": "abc-123", "base_url": "x"},
            },
        )
        == "abc-123"
    )


def test_teams_prefers_the_normalized_tenant_over_the_raw_claim():
    assert (
        resolve_external_ref(
            "microsoft_teams", {"user_data": {"tenant_id": "tenant-a", "tid": "other"}}
        )
        == "tenant-a"
    )


def test_teams_falls_back_to_the_raw_claim():
    assert (
        resolve_external_ref("microsoft_teams", {"raw_response": {"tid": "t2"}}) == "t2"
    )


def test_slack_speaks_for_a_workspace_not_for_the_authorizing_user():
    """The workspace is shared and is what an inbound event names; the user's
    own id is `provider_account_id` and is deliberately not this."""
    ref = resolve_external_ref(
        "slack",
        {"raw_response": {"team": {"id": "T123"}, "authed_user": {"id": "U456"}}},
    )
    assert ref == "T123"


def test_a_brokered_connection_is_the_ref_for_any_connector():
    assert resolve_external_ref("notion", {"connection_id": "ca_789"}) == "ca_789"


def test_a_connectors_own_tenant_wins_over_the_brokered_connection():
    ref = resolve_external_ref(
        "slack", {"raw_response": {"team": {"id": "T1"}}, "connection_id": "ca_1"}
    )
    assert ref == "T1"


def test_a_connector_with_no_tenant_has_none():
    assert resolve_external_ref("github", {"access_token": "t"}) is None


@pytest.mark.parametrize(
    "credentials",
    [
        None,
        {},
        {"user_data": None},
        {"raw_response": {"team": "not-a-dict"}},
        {"raw_response": {"team": {"id": ""}}},
        {"raw_response": {"team": {"id": {"nested": "object"}}}},
    ],
)
def test_a_missing_or_malformed_tenant_is_absent_rather_than_wrong(credentials):
    assert resolve_external_ref("slack", credentials) is None


def test_an_over_long_value_is_refused_rather_than_truncated():
    assert (
        resolve_external_ref("slack", {"raw_response": {"team": {"id": "T" * 256}}})
        is None
    )


def test_a_github_install_is_bound_from_the_callback():
    """The App install redirect is the only place the installation is named.

    The credentials that come back are the authorizing user's own: the same
    person authorizing in two organizations gets two installations and one
    indistinguishable pair of tokens, so `resolve_external_ref` has nothing to
    read (see `test_github_account_has_no_tenant_of_its_own` above).
    """
    assert (
        bind_external_ref(
            "github",
            {"access_token": "gho_x"},
            "https://api.example.com/oauth/callback"
            "?code=c&installation_id=158040062&setup_action=install",
        )
        == "158040062"
    )


def test_the_callback_outranks_the_credentials():
    """A reconnect that moves an account to another tenant has to move the key.

    The provider naming the tenant *for this authorization* is a stronger
    statement than a value found lying in the credential blob, which on a
    re-auth may still describe where the account used to point.
    """
    ref = bind_external_ref(
        "slack",
        {"raw_response": {"team": {"id": "T_OLD"}}},
        "https://api.example.com/oauth/callback?installation_id=999",
    )
    # Slack declares no callback param, so its credentials still decide.
    assert ref == "T_OLD"


def test_connectors_without_a_callback_param_are_unchanged():
    for connector_id, credentials, expected in (
        ("slack", {"raw_response": {"team_id": "T1"}}, "T1"),
        ("notion", {"connection_id": "ca_1"}, "ca_1"),
        ("gmail", {"access_token": "t"}, None),
    ):
        url = "https://api.example.com/oauth/callback?code=c&installation_id=42"
        assert bind_external_ref(connector_id, credentials, url) == expected
        assert bind_external_ref(connector_id, credentials, url) == (
            resolve_external_ref(connector_id, credentials)
        )


def test_a_callback_without_the_param_falls_through():
    assert bind_external_ref("github", {"connection_id": "ca_2"}, "https://x/cb") == (
        "ca_2"
    )
    assert bind_external_ref("github", {"access_token": "t"}, None) is None
    # setup_action=request means an admin was *asked* to install; no id yet.
    assert (
        bind_external_ref("github", {}, "https://x/cb?setup_action=request&code=c")
        is None
    )


def test_an_oversized_or_empty_installation_id_is_not_a_key():
    assert bind_external_ref("github", {}, "https://x/cb?installation_id=") is None
    assert (
        bind_external_ref("github", {}, "https://x/cb?installation_id=" + "9" * 256)
        is None
    )
