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
    spec: KindSpec, config: dict[str, Any] | None
) -> dict[str, Any]:
    return validate_against_schema(spec.install_schema, config, what="install config")


def validate_credentials(
    spec: KindSpec, credentials: dict[str, Any] | None
) -> dict[str, Any]:
    return validate_against_schema(spec.credential_schema, credentials, what="credentials")
