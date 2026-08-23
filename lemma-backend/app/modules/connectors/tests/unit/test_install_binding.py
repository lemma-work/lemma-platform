from __future__ import annotations

import pytest

from app.modules.connectors.domain.install_binding import resolve_external_ref

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
