"""Turning a resource reference into the facts a decision needs.

Every `app/modules/*` import in the authorization path is here: hydrating a
reference means reading the row it points at, and those rows belong to modules.
Concentrating them in one file does not reduce `core_module_imports` -- this is
still `app/core` -- but it is what makes a later inversion a change to one
small file rather than to the decision engine.

A mixin rather than a helper class because the methods below call into
`GrantResolutionMixin` and read `self.session`; `Authorizer` composes both.
"""

from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.session_approvals import has_session_approval


from app.core.authorization.resource_tables import (
    RESOURCE_TABLES,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig


async def _session_approval(
    ctx: "Context",
    *,
    session_id: str | None,
    workload_actor_id: str | None,
    permission_id: str,
) -> bool:
    """``has_session_approval``, asked at most once per permission per request.

    The lookup is a Redis round trip made while the request's pooled database
    connection is checked out, so repeating it per check is the expensive part.

    Caching it means an approval granted *during* a request is not seen by that
    request. That is correct for an ordinary request, which is short; for a
    long-lived streamed one it means the approval takes effect on the next call
    rather than mid-stream. Acceptable because approvals are awaited through the
    approval flow rather than polled here -- but it is a staleness window, not
    an invariant.
    """
    cached = ctx._session_approval_cache.get(permission_id)  # noqa: SLF001
    if cached is not None:
        return cached
    approved = await has_session_approval(
        session_id=session_id,
        workload_actor_id=workload_actor_id,
        permission_id=permission_id,
    )
    ctx._session_approval_cache[permission_id] = approved  # noqa: SLF001
    return approved


#: Datastore's file columns, via the one table that names them. `FOLDER` and
#: `DOCUMENT` share a row, so either serves.
_FILES = RESOURCE_TABLES[ResourceType.FOLDER]


class ResourceHydrationMixin:
    """Hydration half of `Authorizer`."""

    session: AsyncSession

    async def hydrate_resource_ref(self, resource: ResourceRef) -> ResourceRef:
        """Fill in visibility / owner / path for a bare ``ResourceRef``.

        ``resolve_resource_ref`` returns only the identity triple; ``authorize``
        hydrates internally. Callers that need to *report* on a resource rather
        than just authorize it (the share preview) need the same fields, and
        should not reach into the private helper to get them.
        """
        return await self._hydrate_resource(resource)

    async def _is_public_read(self, permission_id: str, resource: ResourceRef) -> bool:
        if not permission_id.endswith(".read"):
            return False
        hydrated = await self._hydrate_resource(resource)
        return hydrated.visibility == ResourceVisibility.PUBLIC

    async def _hydrate_resource(self, resource: ResourceRef) -> ResourceRef:
        if resource.visibility is not None:
            return resource
        if resource.resource_id is None:
            return resource
        # Folder/document hydration also fetches the row's path so folder grants
        # can cascade to descendants in the matcher.
        if resource.resource_type is ResourceType.POD:
            # A pod's own id is the pod it belongs to, so this needs no table.
            # Every caller happens to pass `pod_id` today; deriving it here is
            # what stops the clamp depending on that continuing to be true.
            return ResourceRef(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                organization_id=resource.organization_id,
                pod_id=resource.pod_id or resource.resource_id,
                owner_user_id=resource.owner_user_id,
                visibility=resource.visibility,
            )
        if resource.resource_type in (ResourceType.FOLDER, ResourceType.DOCUMENT):
            return await self._hydrate_datastore_file(resource)
        if resource.resource_type == ResourceType.CONNECTOR:
            return await self._hydrate_connector(resource)
        if resource.resource_type == ResourceType.CONNECTOR_ACCOUNT:
            return await self._hydrate_connector_account(resource)
        if resource.resource_type == ResourceType.CONNECTOR_AUTH_CONFIG:
            return await self._hydrate_connector_auth_config(resource)
        table = RESOURCE_TABLES.get(resource.resource_type)
        if table is None:
            # Not a silent pass-through: every type is classified below, and
            # `_assert_every_resource_type_is_classified` refuses to import this
            # module if one is not. Reaching here means a type is registered as
            # unroutable and something built a ref for it anyway.
            return resource
        if table.visibility_column is None:
            stmt = select(table.pod_column, table.owner_column).where(
                table.id_column == resource.resource_id
            )
        else:
            stmt = select(
                table.pod_column, table.owner_column, table.visibility_column
            ).where(table.id_column == resource.resource_id)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        if table.visibility_column is None:
            visibility = ResourceVisibility.PERSONAL
        else:
            visibility = self._normalize_visibility(row[2])
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=resource.pod_id or row[0],
            owner_user_id=resource.owner_user_id or row[1],
            visibility=visibility,
        )

    async def _hydrate_datastore_file(self, resource: ResourceRef) -> ResourceRef:
        """Hydrate a FOLDER/DOCUMENT ref, including its path so folder grants
        can cascade to descendants."""
        # A path-only check carries the POD id in resource_id — the caller uses
        # `resource_id or pod_id` because a ResourceRef wants one, and the path
        # is what the grant cascade actually matches on. Querying for a file row
        # whose id equals a pod id can only ever return nothing, so skip it:
        # this ran on every datastore path check.
        if resource.resource_id is None or resource.resource_id == resource.pod_id:
            return resource

        stmt = select(
            _FILES.pod_column,
            _FILES.owner_column,
            _FILES.visibility_column,
            _FILES.path_column,
        ).where(_FILES.id_column == resource.resource_id)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=resource.pod_id or row[0],
            owner_user_id=resource.owner_user_id or row[1],
            visibility=resource.visibility or self._normalize_visibility(row[2]),
            path=resource.path or row[3],
        )

    async def _hydrate_connector(self, resource: ResourceRef) -> ResourceRef:
        if resource.pod_id is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=resource.pod_id,
            owner_user_id=resource.owner_user_id,
            # Connectors are org-wide capability resources, always
            # available to everyone: a grant on the app is a capability grant
            # ("may use this app type"), never a sharing grant, so it must not
            # restrict the app. The real access boundary is the connected
            # *account*, which is user-owned and enforced separately in
            # ``account_resolution_service`` (own account is returned directly;
            # someone else's requires ``connector_account.use``). Keeping the
            # app POD-visible means a workload holding ``connector.use``
            # is gated on that capability alone, regardless of which user the run
            # is delegated for -- the regression that denied delegated runs for
            # any member who did not also personally hold an app grant.
            visibility=ResourceVisibility.POD,
        )

    async def _hydrate_connector_account(self, resource: ResourceRef) -> ResourceRef:
        stmt = select(
            Account.organization_id,
            Account.user_id,
        ).where(
            Account.id == resource.resource_id,
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        pod_id = resource.pod_id
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id or row[0],
            pod_id=pod_id,
            owner_user_id=resource.owner_user_id or row[1],
            visibility=(
                await self._visibility_from_optional_grants(
                    pod_id=pod_id,
                    resource_type=resource.resource_type,
                    resource_id=resource.resource_id,
                )
                if pod_id is not None
                else resource.visibility
            ),
        )

    async def _hydrate_connector_auth_config(
        self, resource: ResourceRef
    ) -> ResourceRef:
        stmt = select(AuthConfig.organization_id, AuthConfig.created_by_user_id).where(
            AuthConfig.id == resource.resource_id
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id or row[0],
            pod_id=resource.pod_id,
            owner_user_id=resource.owner_user_id or row[1],
            visibility=resource.visibility,
        )
