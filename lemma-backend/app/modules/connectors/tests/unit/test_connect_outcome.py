"""What the callback tells the app happened.

A GitHub App's user token reaches only the repositories the App is installed
on, so a first connect ends with a valid token that can read nothing. Reporting
that as connected is the failure this decides against: installation is the unit
of access, so an account without one has no repository access at all and the
app must be told there is a step left.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.api.connect_request_controller import (
    CONNECTED,
    INSTALL_REQUIRED,
    PENDING_APPROVAL,
    _connect_outcome,
    _return_to_app,
)
from app.modules.connectors.domain.account import AccountEntity

pytestmark = pytest.mark.unit


def _account(**overrides: object) -> AccountEntity:
    base: dict[str, object] = {
        "user_id": uuid4(),
        "organization_id": uuid4(),
        "auth_config_id": uuid4(),
        "connector_id": "github",
        "external_ref": None,
    }
    return AccountEntity(**{**base, **overrides})


def test_an_unbound_github_account_has_a_step_left() -> None:
    assert _connect_outcome(_account(), None) == INSTALL_REQUIRED


def test_a_bound_account_is_connected() -> None:
    assert _connect_outcome(_account(external_ref="12345"), None) == CONNECTED


def test_no_other_connector_is_asked_to_install_anything() -> None:
    assert _connect_outcome(_account(connector_id="slack"), None) == CONNECTED


class TestOrganizationApproval:
    """`setup_action=request` is a member asking an owner, not an install.

    Nothing was installed and no `installation_id` exists, so the account looks
    exactly like one that skipped the step -- and telling that person to install
    it themselves is advice they cannot act on.
    """

    def test_a_pending_request_is_not_an_outstanding_install(self) -> None:
        assert _connect_outcome(_account(), "request") == PENDING_APPROVAL

    def test_the_value_is_read_the_way_github_sends_it(self) -> None:
        assert _connect_outcome(_account(), " Request ") == PENDING_APPROVAL

    def test_an_actual_install_is_not_a_request(self) -> None:
        assert _connect_outcome(_account(), "install") == INSTALL_REQUIRED

    def test_it_does_not_override_a_finished_connection(self) -> None:
        """The approval landed and the install happened; a stale query parameter
        must not reopen a connection that is already bound."""
        assert _connect_outcome(_account(external_ref="99"), "request") == CONNECTED


class TestReturningToTheApp:
    def test_it_lands_on_a_page_that_actually_renders(self) -> None:
        """Not `/connectors`: that route is a stub whose only job is
        `redirect('/')`, so landing there dropped the query string and the
        person arrived home with nothing said at all."""
        response = _return_to_app(
            INSTALL_REQUIRED, connector_id="github", account_id="abc"
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert "/connectors?" not in location
        assert "connect=install_required" in location
        assert "connector=github" in location
        assert "account=abc" in location

    def test_a_reason_is_bounded(self) -> None:
        """A URL nobody can log or click is not a better error message."""
        response = _return_to_app("error", reason="x" * 5000)
        assert len(response.headers["location"]) < 1000


class TestReturningWhereTheyStarted:
    """The connectors UI lives under a pod; a connect request is scoped to an
    organisation and cannot work out that path on its own. So the flow records
    where the person was and puts them back there."""

    def test_it_comes_back_to_the_recorded_path(self) -> None:
        response = _return_to_app(
            CONNECTED, came_from="/pod/abc123/connectors", connector_id="github"
        )
        location = response.headers["location"]
        assert "/pod/abc123/connectors?" in location
        assert "connect=connected" in location

    def test_a_path_that_already_has_a_query_keeps_it(self) -> None:
        response = _return_to_app(CONNECTED, came_from="/pod/abc/connectors?tab=apps")
        location = response.headers["location"]
        assert "tab=apps&" in location
        assert "connect=connected" in location

    def test_an_offsite_destination_is_refused(self) -> None:
        """The value arrives from a client, is stored, and ends up in a
        `Location` header -- which is the shape of an open redirect."""
        for hostile in ("//evil.example", "https://evil.example", "\\\\evil"):
            location = _return_to_app(CONNECTED, came_from=hostile).headers["location"]
            assert location.startswith("http://localhost") or "evil" not in location
            assert "evil.example" not in location
