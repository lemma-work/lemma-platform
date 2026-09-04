"""What a person has to supply to connect an account, according to the catalog.

The rule used to be "a non-empty object", which is wrong in both directions.

Too strict for a server that needs nothing. The MCP catalog entry declares one
optional field whose own description reads "Leave empty if the server needs no
auth", and the frontend says so to the person filling the form -- and then the
create was refused, after the install had already been committed, leaving an
install with no account that can never run and whose name is now taken. The
catalog invited exactly what the backend rejected.

Too lax for everything else. A SQL install needs a username and a password, and
`{"passwrod": "..."}` sailed through to surface later as an opaque provider auth
error. `additionalProperties: false` was decorative here, which the sibling
docstring for install config says was fixed -- on that side only.

The schema is the authority for both, and `validate_credentials` was written
for this and never called from anywhere.
"""

from __future__ import annotations

from typing import Any

from app.modules.connectors.domain.connector import ConnectorEntity, ConnectorKind
from app.modules.connectors.infrastructure.kinds._install_validation import (
    validate_credentials,
)


def validated_account_credentials(
    connector: ConnectorEntity,
    kind: ConnectorKind,
    credentials: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the credentials to store, or raise saying which field is wrong."""
    if credentials is not None and not isinstance(credentials, dict):
        from app.modules.connectors.domain.errors import ConnectorValidationError

        raise ConnectorValidationError("Credentials must be an object.")
    spec = connector.spec_for(kind)
    schema = getattr(spec, "credential_schema", None) if spec else None
    if schema is None:
        # Nothing declared, so there is nothing to check against -- and the
        # shared validator reads a missing schema as a CLOSED EMPTY one, which
        # would reject every credential rather than none. That reading is right
        # for install config and wrong here: the legacy package kind carries
        # freeform provider credentials and declares no shape for them.
        #
        # Keep the old floor instead. A kind that declares no credential schema
        # is not evidence that no credential is needed.
        from app.modules.connectors.domain.errors import ConnectorValidationError

        if not credentials:
            raise ConnectorValidationError(
                "Credential-managed accounts require a non-empty credentials object."
            )
        return credentials
    return validate_credentials(spec, credentials or {})
