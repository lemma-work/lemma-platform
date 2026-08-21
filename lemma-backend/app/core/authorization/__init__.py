"""Core authorization primitives."""

from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.delegation import (
    DelegationClaims,
    WorkloadPrincipalType,
    parse_delegation_claims,
)
from app.core.authorization.permissions import (
    PermissionDefinition,
    PermissionScope,
    Permissions,
)

__all__ = [
    "ActorType",
    "Context",
    "DelegationClaims",
    "PermissionDefinition",
    "PermissionScope",
    "Permissions",
    "PrincipalRef",
    "ResourceRef",
    "ResourceType",
    "ResourceVisibility",
    "WorkloadPrincipalType",
    "parse_delegation_claims",
]
