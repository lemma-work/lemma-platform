"""What the callback page says when a connection is not finished.

A GitHub App's user token reaches only the repositories the App is installed
on, so a first connect ends with a valid token that can read nothing. The two
questions — does this account still need an installation, and where would it be
done — have different answers, and collapsing them into one nullable URL meant a
deployment that could not name an install location reported the connection as
finished.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.api.connect_request_controller import (
    _outstanding_install,
)
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.account import AccountEntity


def _account(**overrides: object) -> AccountEntity:
    from uuid import uuid4

    base: dict[str, object] = {
        "user_id": uuid4(),
        "organization_id": uuid4(),
        "auth_config_id": uuid4(),
        "connector_id": "github",
        "external_ref": None,
    }
    return AccountEntity(**{**base, **overrides})


@pytest.fixture
def slug(monkeypatch: pytest.MonkeyPatch):
    def _set(value: str | None) -> None:
        monkeypatch.setattr(
            connector_settings, "connector_github_app_slug", value, raising=False
        )

    return _set


def test_an_unbound_github_account_is_sent_where_it_can_be_installed(slug) -> None:
    slug("lemmadev")

    assert _outstanding_install(_account()) == (
        True,
        "https://github.com/apps/lemmadev/installations/new",
    )


def test_it_still_says_the_step_is_outstanding_with_no_slug_to_name(slug) -> None:
    """The deployment cannot say *where*; that does not make the account done.

    `github_install_url` returns None with no `CONNECTOR_GITHUB_APP_SLUG`, and
    reading that as "nothing outstanding" reported a connection that can read
    nothing as finished.
    """
    slug(None)

    assert _outstanding_install(_account()) == (True, None)


def test_a_bound_account_needs_nothing(slug) -> None:
    slug("lemmadev")

    assert _outstanding_install(_account(external_ref="12345")) == (False, None)


def test_no_other_connector_is_asked_to_install_anything(slug) -> None:
    slug("lemmadev")

    assert _outstanding_install(_account(connector_id="slack")) == (False, None)
