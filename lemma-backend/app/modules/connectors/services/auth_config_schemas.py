"""JSON Schemas for an org's own connector credentials (``auth_configs.config``).

Most OAuth connectors want the same two fields, so the default is shared. A
connector only appears here when the provider genuinely needs something the
OAuth pair does not cover.
"""

from __future__ import annotations

from typing import Any

from app.modules.connectors.domain.connector import AuthScheme

_OAUTH2_BASE: dict[str, Any] = {
    "type": "object",
    "required": ["client_id", "client_secret"],
    "properties": {
        "client_id": {
            "type": "string",
            "title": "Client ID",
        },
        "client_secret": {
            "type": "string",
            "title": "Client secret",
            "format": "password",
        },
    },
    "additionalProperties": False,
}

# Slack's own app signs the events it sends us, and that signature verifies
# against a third credential the OAuth pair says nothing about. It sits on the
# same page of the same app, so asking for it anywhere else turns one setup
# into two — which is exactly what it used to be, with the signing secret
# living on a surface that does not exist until after this screen.
#
_SLACK_SIGNING_SECRET: dict[str, Any] = {
    "type": "string",
    "title": "Signing secret",
    "format": "password",
    "description": (
        "From the same Basic Information page. Lets Lemma verify that events "
        "really came from your app."
    ),
}


def default_auth_config_schema(
    auth_scheme: AuthScheme, connector_id: str | None = None
) -> dict[str, Any]:
    """The install-config schema to offer when the catalog declares none."""
    if auth_scheme != AuthScheme.OAUTH2:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    schema: dict[str, Any] = {
        **_OAUTH2_BASE,
        "properties": dict(_OAUTH2_BASE["properties"]),
    }
    if str(connector_id or "").lower() == "slack":
        schema["properties"]["signing_secret"] = _SLACK_SIGNING_SECRET
        schema["required"] = [*_OAUTH2_BASE["required"], "signing_secret"]
    return schema
