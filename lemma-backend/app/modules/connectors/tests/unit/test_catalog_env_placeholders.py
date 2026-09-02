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


def test_the_github_app_install_url_is_the_one_in_the_catalog():
    """Pinned because it is the whole connect flow.

    A GitHub App is installed, not merely authorized: sending someone to
    `login/oauth/authorize` yields a working user token that can reach no
    repository, because the App was never installed anywhere.
    """
    assert _placeholders().get("github") == ["CONNECTOR_GITHUB_APP_SLUG"]
    github = next(a for a in _apps() if a["name"] == "github")
    assert github["oauth2_config"]["authorization_url"] == (
        "https://github.com/apps/{CONNECTOR_GITHUB_APP_SLUG}/installations/new"
    )


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
