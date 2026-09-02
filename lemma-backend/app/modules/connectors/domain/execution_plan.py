"""The plan an operation call runs, carrying no session-bound state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ResolvedConnectorExecution:
    """Session-free output of the connector-operation resolve phase, handed to
    the external execute phase so the operation runs with no pooled DB connection
    held. Carries only plain values -- never ORM/session-bound objects."""

    connector_id: str
    operation_execution_name: str
    provider: str | None
    third_party_credentials: dict[str, Any]
    payload: dict[str, Any]
    # The install's kind selects the executor; `provider` above is the legacy
    # view kept only while the old gateways still exist behind the composio and
    # package plugins.
    kind: str | None = None
    # Non-secret per-install connection config (SQL host, MCP/OpenAPI server
    # URL). Secrets stay in third_party_credentials.
    connection_config: dict[str, Any] | None = None
    # Polymorphic descriptor the kind's executor reads. None for package
    # operations, which the vendored client describes itself.
    execution: dict[str, Any] | None = None
    operation_name: str | None = None
    input_schema: dict[str, Any] | None = None
    # Plain identifiers (never session-bound) so the execute phase can flag the
    # account for re-auth on an unauthorized failure without re-resolving it.
    account_id: UUID | None = None
    #: `accounts.external_ref` for the resolved account -- the upstream tenant
    #: it speaks for. Read in the resolve phase because it lives on the row;
    #: used in the execute phase, which holds no connection to read it with.
    account_external_ref: str | None = None
    account_user_id: UUID | None = None
    organization_id: UUID | None = None
    #: Who asked for this operation. Distinct from ``account_user_id``, which is
    #: whoever owns the connected account -- a pod member can run an operation
    #: through an account somebody else connected, and analytics wants the person
    #: who acted, not the person who set it up.
    acting_user_id: UUID | None = None
