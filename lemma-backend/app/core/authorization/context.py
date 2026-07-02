"""Request context and resource authorization primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import UUID

from app.core.authorization.permissions import equivalent_permission_ids
from app.core.domain.errors import DomainError


class ActorType(str, Enum):
    USER = "USER"
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"
    DELEGATED_USER_WORKLOAD = "DELEGATED_USER_WORKLOAD"
    SYSTEM = "SYSTEM"
    ANONYMOUS = "ANONYMOUS"


class ResourceType(str, Enum):
    ORGANIZATION = "organization"
    POD = "pod"
    POD_MEMBER = "pod_member"
    ROLE = "role"
    DATASTORE_TABLE = "datastore_table"
    DATASTORE_RECORD = "datastore_record"
    FOLDER = "folder"
    DOCUMENT = "document"
    APP = "app"
    AGENT = "agent"
    FUNCTION = "function"
    WORKFLOW = "workflow"
    SCHEDULE = "schedule"
    CONVERSATION = "conversation"
    CONNECTOR = "connector"
    CONNECTOR_ACCOUNT = "connector_account"
    CONNECTOR_AUTH_CONFIG = "connector_auth_config"


class ResourceVisibility(str, Enum):
    PERSONAL = "PERSONAL"
    POD = "POD"
    RESTRICTED = "RESTRICTED"
    PUBLIC = "PUBLIC"


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    type: str
    id: UUID


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_type: ResourceType
    resource_id: UUID | None = None
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    owner_user_id: UUID | None = None
    visibility: ResourceVisibility | None = None
    # Only meaningful for hierarchical datastore resources (FOLDER/DOCUMENT):
    # the resource's stored path, used to cascade folder grants to descendants.
    path: str | None = None

    @classmethod
    def organization(cls, organization_id: UUID) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.ORGANIZATION,
            resource_id=organization_id,
            organization_id=organization_id,
        )

    @classmethod
    def pod(cls, pod_id: UUID, organization_id: UUID | None = None) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.POD,
            resource_id=pod_id,
            organization_id=organization_id,
            pod_id=pod_id,
        )

    @classmethod
    def table(cls, pod_id: UUID, table_id: UUID) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.DATASTORE_TABLE,
            resource_id=table_id,
            pod_id=pod_id,
        )

    @classmethod
    def app(cls, pod_id: UUID, app_id: UUID) -> "ResourceRef":
        return cls(resource_type=ResourceType.APP, resource_id=app_id, pod_id=pod_id)

    @classmethod
    def schedule(cls, pod_id: UUID, schedule_id: UUID) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.SCHEDULE,
            resource_id=schedule_id,
            pod_id=pod_id,
        )

    @classmethod
    def connector(
        cls,
        pod_id: UUID,
        pod_connector_id: UUID,
    ) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.CONNECTOR,
            resource_id=pod_connector_id,
            pod_id=pod_id,
        )

    @classmethod
    def connector_account(cls, pod_id: UUID, pod_account_id: UUID) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.CONNECTOR_ACCOUNT,
            resource_id=pod_account_id,
            pod_id=pod_id,
        )

    @classmethod
    def connector_auth_config(
        cls,
        organization_id: UUID,
        auth_config_id: UUID,
    ) -> "ResourceRef":
        return cls(
            resource_type=ResourceType.CONNECTOR_AUTH_CONFIG,
            resource_id=auth_config_id,
            organization_id=organization_id,
        )


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason_code: str
    permission_id: str
    resource: ResourceRef | None = None
    matched_role_ids: tuple[UUID, ...] = ()
    matched_grant_ids: tuple[UUID, ...] = ()


class AuthorizerProtocol(Protocol):
    async def authorize(
        self,
        ctx: "Context",
        permission_id: str,
        resource: ResourceRef | None = None,
    ) -> AuthorizationDecision: ...

    async def accessible_resource_ids(
        self,
        ctx: "Context",
        permission_id: str,
        resource_type: ResourceType,
        pod_id: UUID,
    ) -> frozenset[UUID]: ...


@dataclass(slots=True)
class Context:
    actor_type: ActorType
    actor_id: str
    authorizer: AuthorizerProtocol
    request_id: str | None = None
    user_id: UUID | None = None
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    role_ids: frozenset[UUID] = field(default_factory=frozenset)
    role_names: frozenset[str] = field(default_factory=frozenset)
    permission_ids: frozenset[str] = field(default_factory=frozenset)
    principal_refs: frozenset[PrincipalRef] = field(default_factory=frozenset)
    grant_principal_sets: tuple[frozenset[PrincipalRef], ...] = ()
    workload_principal_refs: frozenset[PrincipalRef] = field(default_factory=frozenset)
    delegated_by_user_id: UUID | None = None
    delegation_session_id: str | None = None
    delegation_scope: frozenset[str] = field(default_factory=frozenset)
    delegation_actor_name: str | None = None
    is_superuser: bool = False
    # The default pod agent (``pod_default``) runs as a DELEGATED_USER_WORKLOAD but
    # inherits the invoking user's context verbatim, scoped to its own pod. This
    # flag lets pod-scoped USER-only authorization shortcuts (e.g. the org-owner
    # pod allow) treat it as the user it is acting for — without widening
    # real (grant-backed) agent/function workloads, which leave it False.
    is_user_equivalent: bool = False
    _decision_cache: dict[tuple[str, ResourceType | None, UUID | None], AuthorizationDecision] = field(
        default_factory=dict
    )

    @property
    def is_authenticated(self) -> bool:
        return self.actor_type != ActorType.ANONYMOUS

    def has_permission(self, permission_id: str) -> bool:
        return bool(equivalent_permission_ids(permission_id) & self.permission_ids)

    async def can(
        self,
        permission_id: str,
        resource: ResourceRef | None = None,
    ) -> bool:
        decision = await self._authorize(permission_id, resource)
        return decision.allowed

    async def require(
        self,
        permission_id: str,
        resource: ResourceRef | None = None,
    ) -> None:
        decision = await self._authorize(permission_id, resource)
        if decision.allowed:
            return
        if decision.reason_code == "AUTH_REQUIRED":
            raise DomainError(
                "Authentication required",
                code="AUTH_REQUIRED",
                status_code=401,
            )
        raise DomainError(
            f"Missing permission {permission_id}",
            code=decision.reason_code,
            status_code=403,
            # Structured so tool-error handlers can carry the denied permission
            # into a request_approval payload (session approvals key on it).
            details={"permission_ids": [permission_id]},
        )

    async def require_all(
        self,
        requirements: Sequence[tuple[str, "ResourceRef | None"]],
    ) -> None:
        """Authorize several ``(permission_id, resource)`` pairs together and
        raise ONE error naming every missing permission.

        Sequential :meth:`require` calls surface only the first failure, so a
        caller that needs more than one grant (e.g. ``agent.execute`` *and*
        ``agent.read`` to dispatch an agent) otherwise learns about them one 403
        at a time. Checking them together lets the operator add every missing
        grant in a single pass. Decisions are cached, so any later per-action
        :meth:`require` re-checks don't re-authorize.
        """
        missing: list[str] = []
        first_reason: str | None = None
        auth_required = False
        for permission_id, resource in requirements:
            decision = await self._authorize(permission_id, resource)
            if decision.allowed:
                continue
            if decision.reason_code == "AUTH_REQUIRED":
                auth_required = True
            elif first_reason is None:
                first_reason = decision.reason_code
            missing.append(permission_id)
        if auth_required and not first_reason:
            raise DomainError(
                "Authentication required",
                code="AUTH_REQUIRED",
                status_code=401,
            )
        if missing:
            raise DomainError(
                f"Missing permission(s): {', '.join(missing)}",
                code=first_reason or "AUTH_REQUIRED",
                status_code=403,
                details={"permission_ids": list(missing)},
            )

    async def accessible_resource_ids(
        self,
        permission_id: str,
        resource_type: ResourceType,
        pod_id: UUID | None = None,
    ) -> frozenset[UUID]:
        resolved_pod_id = pod_id or self.pod_id
        if resolved_pod_id is None:
            return frozenset()
        return await self.authorizer.accessible_resource_ids(
            self,
            permission_id,
            resource_type,
            resolved_pod_id,
        )

    async def _authorize(
        self,
        permission_id: str,
        resource: ResourceRef | None,
    ) -> AuthorizationDecision:
        key = (
            permission_id,
            resource.resource_type if resource else None,
            resource.resource_id if resource else None,
        )
        cached = self._decision_cache.get(key)
        if cached is not None:
            return cached
        decision = await self.authorizer.authorize(self, permission_id, resource)
        self._decision_cache[key] = decision
        return decision
