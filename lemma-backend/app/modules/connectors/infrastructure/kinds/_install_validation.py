"""Install-config validation, applied to every kind without exception.

The previous validator returned early for any non-OAuth2 native connector --
which is precisely ``sql``, ``mcp`` and ``openapi``, the three whose config is
entirely tenant-written. Their ``additionalProperties: false`` declarations were
decorative: arbitrary keys were accepted and stored, including ones the runtime
later read as credentials.
"""

from __future__ import annotations

from typing import Any

import jsonschema

from app.modules.connectors.domain.connector import KindSpec
from app.modules.connectors.domain.errors import ConnectorValidationError

# An install that declares no schema still may not carry arbitrary keys.
_CLOSED_EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def validate_against_schema(
    schema: dict[str, Any] | None,
    value: dict[str, Any] | None,
    *,
    what: str,
) -> dict[str, Any]:
    """Validate ``value`` against ``schema``, raising a domain error on failure.

    Violations are reported as a compact list of ``path: message`` pairs. The
    caller supplied this config, so telling them exactly which field is wrong is
    useful rather than leaky -- unlike upstream provider errors, which are
    deliberately not reflected.
    """
    payload = dict(value or {})
    effective = schema if schema is not None else _CLOSED_EMPTY_SCHEMA
    validator = jsonschema.Draft202012Validator(effective)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if not errors:
        return payload

    violations = [
        {
            "path": "/".join(str(part) for part in error.absolute_path) or "(root)",
            "message": error.message,
        }
        for error in errors[:10]
    ]
    raise ConnectorValidationError(
        f"Invalid {what}.",
        details={"reason": f"invalid_{what.replace(' ', '_')}", "violations": violations},
    )


def validate_install_config(
    spec: KindSpec,
    config: dict[str, Any] | None,
    config_source: Any = None,
) -> dict[str, Any]:
    """Validate an install's config against its kind's schema.

    One exception, and it is not a loophole: for an OAuth2 kind the install
    schema describes the *org-supplied* client credentials, which only exist
    when the org brings its own OAuth app. A system-default install uses the
    platform's client and legitimately supplies nothing, so validating it
    against that schema would reject every such install for missing a
    client_id it was never meant to provide. It is still checked -- against a
    closed empty schema, so stray keys are refused rather than stored.

    The tenant-configured kinds (sql/mcp/http) are unaffected: their schema
    describes the connection itself, which is always supplied.
    """
    from app.modules.connectors.domain.auth_config import AuthConfigSource
    from app.modules.connectors.domain.connector import AuthScheme

    org_supplies_credentials = config_source == AuthConfigSource.ORG_CUSTOM
    if spec.auth_scheme == AuthScheme.OAUTH2:
        if not org_supplies_credentials:
            return validate_against_schema(None, config, what="install config")
        # Org-custom OAuth credentials may arrive flat or nested under
        # `oauth2_credentials`; both shapes are part of the existing API. The
        # schema describes the flat one, so validate whichever was supplied and
        # return the caller's original shape untouched.
        supplied = dict(config or {})
        nested = supplied.get("oauth2_credentials")
        validate_against_schema(
            spec.install_schema,
            nested if isinstance(nested, dict) else supplied,
            what="install config",
        )
        return supplied
    return validate_against_schema(spec.install_schema, config, what="install config")


def validate_credentials(
    spec: KindSpec, credentials: dict[str, Any] | None
) -> dict[str, Any]:
    return validate_against_schema(spec.credential_schema, credentials, what="credentials")
