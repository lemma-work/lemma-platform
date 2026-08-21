"""Request context and resource authorization primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import UUID

from app.core.authorization.permissions import equivalent_permission_ids
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import safe_to_release


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
    # Every Lemma account, pod membership or not. Never anonymous: authorization
    # still runs against a signed-in principal, so this waives pod scope rather
    # than opening the resource to the open internet.
    PUBLIC = "PUBLIC"


# Spellings accepted from older payloads and bundles, per canonical level.
_VISIBILITY_ALIASES: dict[str, ResourceVisibility] = {
    "PRIVATE": ResourceVisibility.PERSONAL,
    "OWNER": ResourceVisibility.PERSONAL,
    "USER": ResourceVisibility.PERSONAL,
    "ALL": ResourceVisibility.POD,
}


def normalize_resource_visibility(
    value: str | ResourceVisibility | None,
    *,
    default: ResourceVisibility = ResourceVisibility.POD,
) -> ResourceVisibility | None:
    """Canonical string -> ``ResourceVisibility``.

    The single place that decides what a visibility string means. Three
    hand-rolled copies of this used to live in the app, table and authorization
    services, each ending in a silent ``return POD`` — so any level added to the
    enum was quietly downgraded by whichever copy was not updated. Returns
    ``None`` for an unrecognized value so callers choose between raising and
    defaulting rather than inheriting a silent fallback.
    """
    if value is None:
        return default
    if isinstance(value, ResourceVisibility):
        return value
    normalized = str(value).strip().upper()
    if not normalized:
        return default
    try:
        return ResourceVisibility(normalized)
    except ValueError:
        return _VISIBILITY_ALIASES.get(normalized)


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
    def hydrated_table(
        cls,
        pod_id: UUID,
        table_id: UUID,
        *,
        visibility: "ResourceVisibility | str | None",
        owner_user_id: UUID | None,
    ) -> "ResourceRef":
        """A table reference that authorization will not re-read.

        ``_hydrate_resource`` returns early once ``visibility`` is set, so a
        caller holding the row it just selected can hand both fields over
        instead of paying a second read of the same row to authorize it.

        Separate from ``table`` and with both arguments required, because they
        must travel together: hydration fills them as a pair, and setting only
        the visibility would leave ``owner_user_id`` None and silently deny the
        table's own owner. ``owner_user_id`` may legitimately *be* None — an
        unowned table — which is why this cannot be inferred from the values.
        """
        resolved = (
            normalize_resource_visibility(visibility)
            if isinstance(visibility, str)
            else visibility
        )
        return cls(
            resource_type=ResourceType.DATASTORE_TABLE,
            resource_id=table_id,
            pod_id=pod_id,
            visibility=resolved or ResourceVisibility.POD,
            owner_user_id=owner_user_id,
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
    # Human name of the denied resource ("customers", "/knowledge"), resolved on
    # the denial path only. A workload denial that names just the permission id
    # leaves the operator to guess which of the resources a function touches was
    # the one it lacked — the fix instruction needs the name, not the verb.
    resource_name: str | None = None


# Grantee kind -> the CLI command that adds a grant to it. A denial is the moment
# the operator needs the fix, and the fix is one command.
_GRANT_COMMAND_BY_TYPE = {
    "datastore_table": "<table>:read,write",
    "folder": "<path>:read",
    "document": "doc:<path>:read",
    "function": "function:<name>:execute",
    "agent": "agent:<name>:execute",
    "workflow": "workflow:<name>:execute",
    "connector": "connector:<name>:use",
    "connector_account": "account:<id>:use",
    "app": "app:<name>:read",
    "schedule": "schedule:<name>:read",
}


def _denial_resource_details(decision: "AuthorizationDecision") -> dict[str, object]:
    resource = decision.resource
    if resource is None:
        return {}
    details: dict[str, object] = {"resource_type": resource.resource_type.value}
    if decision.resource_name:
        details["resource_name"] = decision.resource_name
    return details


def _denial_message(permission_id: str, decision: "AuthorizationDecision") -> str:
    """Say what was denied AND on what.

    "Missing permission datastore.table.read" is unactionable when the caller
    touches several tables: it names the verb, never the noun. The docs have
    always told operators the error names the resource, so make that true.
    """
    resource = decision.resource
    if resource is None:
        return f"Missing permission {permission_id}"
    resource_type = resource.resource_type.value
    target = f" on {resource_type}"
    if decision.resource_name:
        target += f" '{decision.resource_name}'"
    message = f"Missing permission {permission_id}{target}"
    if decision.reason_code == "MISSING_WORKLOAD_RESOURCE_GRANT":
        spec = _GRANT_COMMAND_BY_TYPE.get(resource_type)
        if spec and decision.resource_name:
            spec = spec.replace("<table>", decision.resource_name)
            spec = spec.replace("<path>", decision.resource_name)
            spec = spec.replace("<name>", decision.resource_name)
            spec = spec.replace("<id>", decision.resource_name)
            message += (
                f" — grant it with `lemma agents|functions permissions add "
                f"<workload> {spec}`"
            )
    return message


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
    _decision_cache: dict[
        tuple[str, ResourceType | None, UUID | None], AuthorizationDecision
    ] = field(default_factory=dict)
    #: permission_id -> session-approval answer, for this request only.
    #:
    #: The lookup is a Redis GET, and it runs while FastAPI holds the request's
    #: pooled connection. The decision cache above keys on the resource too, so
    #: a workload touching several resources under one permission repeats the
    #: same approval lookup once per resource — each one a network round trip
    #: with a database connection checked out and idle behind it. The answer
    #: cannot change within a request, so ask once.
    _session_approval_cache: dict[str, bool] = field(default_factory=dict)

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
            _denial_message(permission_id, decision),
            code=decision.reason_code,
            status_code=403,
            # Structured so tool-error handlers can carry the denied permission
            # into a request_approval payload (session approvals key on it), and
            # so callers can act on the resource without parsing prose.
            details={
                "permission_ids": [permission_id],
                **_denial_resource_details(decision),
            },
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
        missing_ids: list[str] = []
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
            # Name the resource here too — a multi-permission denial is exactly
            # where "which one?" is hardest to answer.
            label = permission_id
            if decision.resource is not None:
                label += f" on {decision.resource.resource_type.value}"
                if decision.resource_name:
                    label += f" '{decision.resource_name}'"
            missing.append(label)
            missing_ids.append(permission_id)
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
                details={"permission_ids": list(missing_ids)},
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
        await self._release_connection_after_check()
        return decision

    async def _release_connection_after_check(self) -> None:
        """Give the pooled connection back once a decision is reached.

        Authorization runs on every request and reads from the application
        database — the pod row, the resource row, the role snapshot. Under a
        FastAPI yield-dependency the session it reads through stays checked out
        until the response is written, so the cost is not the queries (they are
        milliseconds) but the connection held for everything that comes after.

        Doing it here rather than at each call site is the point: `require` and
        `can` are called from route dependencies, from services, and from
        agent tools, and every one of them was leaving the connection held.

        Guarded. Authorization itself is read-only, so normally nothing is
        pending and this simply returns the connection. A caller that has
        already written keeps the old behaviour rather than having its work
        committed on its behalf.
        """
        session = getattr(self.authorizer, "session", None)
        if session is None:
            return
        # `safe_to_release` carries the full list of reasons not to: pending or
        # flushed writes, staged outbox events, a transaction-scoped advisory
        # lock. This runs mid-flow from services and agent tools, not only from
        # route dependencies, so the caller's transaction is not ours to end.
        if not safe_to_release(session):
            return
        await session.commit()
