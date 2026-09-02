"""Every `{ENV_VAR}` a catalog URL names is a real, declared setting.

The substitution in `env_system_oauth_config` resolves placeholders by *name*,
straight from the environment -- it has to, because the names are catalog data
rather than fields. That makes the catalog and `ConnectorSettings` two places
naming the same variable with nothing holding them together: rename the field,
or misspell the placeholder, and the connector reports itself unconfigured on
a deployment that set the variable correctly. Nothing else would say why.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.modules.connectors.config import ConnectorSettings
from app.modules.connectors.infrastructure.adapters.env_system_oauth_config import (
    _ENV_PLACEHOLDER,
    _FILLABLE_FIELDS,
)

pytestmark = pytest.mark.unit

CATALOG = Path(__file__).resolve().parents[5] / "scripts" / "lemma_apps_config.json"


def _apps() -> list[dict]:
    return json.loads(CATALOG.read_text())


def _placeholders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for app in _apps():
        oauth2 = app.get("oauth2_config") or {}
        for field in _FILLABLE_FIELDS:
            value = oauth2.get(field)
            if isinstance(value, str):
                for name in _ENV_PLACEHOLDER.findall(value):
                    found.setdefault(app.get("name", "?"), []).append(name)
    return found


def test_every_placeholder_names_a_declared_setting():
    fields = set(ConnectorSettings.model_fields)
    for connector_id, names in _placeholders().items():
        for name in names:
            assert name.lower() in fields, (
                f"{connector_id} names {name}, which ConnectorSettings does not "
                "declare -- nothing would fill it and the connector would go "
                "quietly unconfigured."
            )


def test_github_connects_through_the_authorize_endpoint_not_the_install_page():
    """Pinned because sending people to the install page does not work twice.

    `/apps/{slug}/installations/new` redirects back with `code` and
    `installation_id` on a *first* install and only then. Someone who already
    has the App -- every reconnect, and every second person in an organization
    where somebody installed it already -- is shown the configure page and never
    redirected anywhere at all. Verified live: the connect request stayed
    PENDING and no account was ever created.

    The authorize endpoint always round-trips a code. The installation is
    resolved from the token afterwards; see `github_installation`.
    """
    github = next(a for a in _apps() if a["name"] == "github")
    assert github["oauth2_config"]["authorization_url"] == (
        "https://github.com/login/oauth/authorize"
    )
    # And nothing needs substituting into it -- the slug identifies the App for
    # the *install* link, which is a different journey.
    assert _placeholders().get("github") is None


def test_no_placeholder_hides_in_a_field_that_is_never_filled():
    """A placeholder outside `_FILLABLE_FIELDS` is dead text that ships as-is."""
    for app in _apps():
        oauth2 = app.get("oauth2_config") or {}
        for field, value in oauth2.items():
            if field in _FILLABLE_FIELDS:
                continue
            assert not re.search(r"\{[A-Z][A-Z0-9_]*\}", json.dumps(value)), (
                f"{app.get('name')}.{field} carries an env placeholder that "
                "nothing substitutes."
            )
