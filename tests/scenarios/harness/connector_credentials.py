"""A credential a connector kind will accept, built from what it says it takes.

Scenarios used to connect every account with ``{"access_token": ...}``. That is
the key the HTTP executor turns into a bearer header, and the `openapi` kind a
scenario installs declares it — but a catalogue connector picked from a
deployment need not. Telegram's credential schema requires ``bot_token`` and is
closed, so since the API began enforcing credential schemas the same body was a
400 and every scenario on that fixture failed during setup, reading as the
connector being broken rather than the scenario sending the wrong shape.

So the shape is asked of the kind. Every required field is filled; a field that
holds a secret gets the token the scenario chose, so what it asserts on is still
the value it sent; and ``access_token`` is kept wherever the schema lets it be
stored, because that is the one an HTTP connector actually spends.
"""

from __future__ import annotations

from typing import Any

JSON = dict[str, Any]

#: Field names that hold the secret itself, whatever the kind calls it.
_TOKEN_NAMES = frozenset(
    {"access_token", "bot_token", "api_key", "api_token", "token", "password"}
)

#: What the HTTP executor sends as the bearer header.
_BEARER = "access_token"


def _placeholder(name: str, field: JSON) -> Any:
    enum = field.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if "default" in field:
        return field["default"]
    kind = field.get("type")
    if kind in {"integer", "number"}:
        return 1
    if kind == "boolean":
        return True
    return f"scenario-{name}"


def _holds_the_secret(name: str, field: JSON) -> bool:
    return name in _TOKEN_NAMES or field.get("format") == "password"


def credential_matching(schema: JSON | None, token: str) -> JSON:
    """A credential ``schema`` accepts, carrying ``token`` as its secret.

    No schema, or one naming no fields, is a kind that stores whatever it is
    given, and gets the bearer form it always did.
    """
    properties: JSON = (schema or {}).get("properties") or {}
    if not properties:
        return {_BEARER: token}
    required = [str(name) for name in (schema or {}).get("required") or []]
    closed = (schema or {}).get("additionalProperties") is False

    credential: JSON = {}
    for name in required:
        field = properties.get(name) or {}
        credential[name] = (
            token if _holds_the_secret(name, field) else _placeholder(name, field)
        )
    if _BEARER in properties or not closed:
        credential.setdefault(_BEARER, token)
    if not any(value == token for value in credential.values()):
        # Nothing required and no bearer field: put the token in the first
        # field that holds a secret, so the account is not connected empty.
        secret = next(
            (
                name
                for name, field in properties.items()
                if _holds_the_secret(name, field)
            ),
            None,
        )
        if secret is not None:
            credential[secret] = token
    return credential


def credential_schema_of(connector: JSON, kind: str | None) -> JSON | None:
    """The credential schema of the kind an install was made as."""
    kinds = [one for one in connector.get("kinds") or [] if isinstance(one, dict)]
    chosen = next(
        (one for one in kinds if kind is not None and str(one.get("kind")) == str(kind)),
        kinds[0] if len(kinds) == 1 else None,
    )
    return (chosen or {}).get("credential_schema")
