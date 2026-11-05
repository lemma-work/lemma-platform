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

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import (
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.resource_tables import (
    FILE_COLUMNS,
    RESOURCE_TABLES,
)


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

    async def _is_public_read(
        self,
        permission_id: str,
        resource: ResourceRef,
        *,
        pod_id: UUID | None = None,
    ) -> bool:
        """Is this a read of something Public -- in ``pod_id``, when one is given?

        ``pod_id`` pins an anonymous context to the pod whose bot is answering
        (see ``authorization.anonymous``): Public in another pod is not public
        *here*, and a resource with no pod at all is not this pod's to show.
        """
        if not permission_id.endswith(".read"):
            return False
        hydrated = await self._hydrate_resource(resource)
        if pod_id is not None and hydrated.pod_id != pod_id:
            return False
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
            pod_id=row[0] or resource.pod_id,
            owner_user_id=row[1],
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
            FILE_COLUMNS.pod_column,
            FILE_COLUMNS.owner_column,
            FILE_COLUMNS.visibility_column,
            FILE_COLUMNS.path_column,
        ).where(FILE_COLUMNS.id_column == resource.resource_id)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return resource
        return ResourceRef(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            organization_id=resource.organization_id,
            pod_id=row[0] or resource.pod_id,
            owner_user_id=row[1],
            visibility=self._normalize_visibility(row[2]),
            path=row[3],
        )
