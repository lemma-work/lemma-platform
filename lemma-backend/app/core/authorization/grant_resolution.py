"""Which grants apply to a resource, and what visibility they confer.

Split out of `Authorizer` with the hydration half, to bring that file under the
600-line ceiling. This is the half that walks ancestor folders and matches grant
rows against a principal group -- the memoised, query-heavy part.

A mixin: it reads `self.session` and the two per-instance memos `Authorizer`
builds in its constructor, which are documented there.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import (
    AuthorizationDecision,
    Context,
    PrincipalRef,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
    normalize_resource_visibility,
)
from app.core.authorization.session_approvals import has_session_approval


from app.core.authorization.grants import (
    HUMAN_GRANTEE_TYPES,
    grant_resource_type_values,
)
from app.core.authorization.models import (
    ResourcePermissionGrantModel,
)
from app.core.authorization.permissions import (
    equivalent_permission_ids,
)
from app.core.authorization.resource_tables import (
    RESOURCE_TABLES,
)


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


class GrantResolutionMixin:
    """Grant-resolution half of `Authorizer`."""

    session: AsyncSession
    _grant_rows: dict[
        tuple[UUID, tuple[str, ...], tuple[UUID, ...], frozenset[PrincipalRef]],
        list[tuple[UUID, str]],
    ]
    _folder_ids_by_paths: dict[tuple[UUID, tuple[str, ...]], list[UUID]]

    async def _visibility_from_optional_grants(
        self,
        *,
        pod_id: UUID,
        resource_type: ResourceType,
        resource_id: UUID,
    ) -> ResourceVisibility:
        conditions = [
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(resource_type)
            ),
            ResourcePermissionGrantModel.resource_id == resource_id,
            # Human sharing grants only. A grant to an agent is a workload
            # capability grant and says nothing about whether people may see the
            # resource -- which is why `clear_human_sharing_grants` deletes the
            # human ones and preserves these when a resource leaves RESTRICTED.
            # Counting them here contradicted that: pinning a shared account on
            # an agent was itself what made the account RESTRICTED, so the act of
            # configuring the agent locked every pod member out of the very
            # account it was configured to use.
            ResourcePermissionGrantModel.grantee_type.in_(HUMAN_GRANTEE_TYPES),
        ]
        grant_exists_stmt = select(exists().where(*conditions))
        has_grants = (await self.session.execute(grant_exists_stmt)).scalar_one()
        if has_grants:
            return ResourceVisibility.RESTRICTED
        return ResourceVisibility.POD

    async def _resource_grant_decision(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> AuthorizationDecision | None:
        visibility = resource.visibility or ResourceVisibility.POD
        if (
            visibility == ResourceVisibility.PERSONAL
            and resource.owner_user_id != ctx.user_id
        ):
            return None
        grant_ids = await self._matching_grant_ids(ctx, permission_id, resource)
        if not grant_ids:
            return None
        return AuthorizationDecision(
            True,
            "RESOURCE_GRANT_MATCH",
            permission_id,
            resource,
            matched_grant_ids=tuple(grant_ids),
        )

    async def _matching_grant_ids(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
    ) -> list[UUID]:
        return await self._matching_grant_ids_for_principal_sets(
            ctx,
            permission_id,
            resource,
            ctx.grant_principal_sets or (ctx.principal_refs,),
        )

    @staticmethod
    def _ancestor_folder_paths(path: str) -> list[str]:
        """Return the ancestor folder paths of ``path`` (excluding itself).

        ``/a/b/c.md`` -> ``["/", "/a", "/a/b"]``. The root ``/`` is included so
        a grant on the (optional) root folder cascades pod-wide.
        """
        segments = [segment for segment in path.split("/") if segment]
        paths = ["/"]
        accumulated = ""
        for segment in segments[:-1]:
            accumulated = f"{accumulated}/{segment}"
            paths.append(accumulated)
        return paths

    async def _acceptable_grant_resource_ids(
        self, resource: ResourceRef
    ) -> list[UUID] | None:
        """Resource ids a grant may target to authorize ``resource``.

        For hierarchical FOLDER/DOCUMENT resources this is ``{self id} ∪
        {ancestor folder ids} ∪ {pod id (pod-wide root grant)}`` so a grant
        on the resource itself or any ancestor folder cascades down. Returns
        ``None`` for every other resource type, signalling the caller to keep
        exact-id matching.
        """
        if resource.resource_type not in (ResourceType.FOLDER, ResourceType.DOCUMENT):
            return None
        if resource.pod_id is None:
            return [resource.resource_id] if resource.resource_id else []
        acceptable: set[UUID] = set()
        if resource.resource_id is not None:
            acceptable.add(resource.resource_id)
        # A grant keyed on the pod id itself is the pod-wide document grant.
        acceptable.add(resource.pod_id)
        if resource.path:
            # Resolve grant-target ids by path for this resource's OWN folder
            # plus every ancestor folder. Including the resource's own path is
            # what lets a grant *on* the folder authorize the folder itself when
            # the caller authorizes by path only -- e.g. the list_files /
            # search-scope pre-check passes resource_name but no resource_id, so
            # ``_require_document_action`` falls back to the pod id and the
            # self-grant would otherwise never match. This mirrors the SQL
            # projection's self-match (``resource_path_col == granted.path``).
            candidate_paths = [
                *self._ancestor_folder_paths(resource.path),
                resource.path,
            ]
            acceptable.update(
                await self._folder_ids_for_paths(
                    resource.pod_id, tuple(candidate_paths)
                )
            )
        return list(acceptable)

    async def _folder_ids_for_paths(
        self, pod_id: UUID, candidate_paths: tuple[str, ...]
    ) -> list[UUID]:
        """Ancestor-folder ids for ``candidate_paths``, resolved once.

        The paths depend on the *resource*, not on the permission being
        checked, so a caller asking many permissions about one folder was
        re-resolving the same ancestor chain each time. Memoized for the same
        reason and with the same lifetime as ``_grant_rows`` below.
        """
        key = (pod_id, candidate_paths)
        cached = self._folder_ids_by_paths.get(key)
        if cached is not None:
            return cached
        stmt = select(_FILES.id_column).where(
            _FILES.pod_column == pod_id,
            _FILES.path_column.in_(candidate_paths),
        )
        resolved = list((await self.session.execute(stmt)).scalars().all())
        self._folder_ids_by_paths[key] = resolved
        return resolved

    async def _matching_grant_ids_for_principal_sets(
        self,
        ctx: Context,
        permission_id: str,
        resource: ResourceRef,
        principal_sets: tuple[frozenset[PrincipalRef], ...],
    ) -> list[UUID]:
        if resource.pod_id is None or resource.resource_id is None:
            return []
        permission_ids = equivalent_permission_ids(permission_id)
        if not principal_sets or any(not group for group in principal_sets):
            return []

        # For folders/documents, a grant on any ancestor folder (or the pod-wide
        # root grant) cascades down; every other resource type stays exact-match.
        acceptable_ids = await self._acceptable_grant_resource_ids(resource)
        if acceptable_ids is None:
            target_ids: tuple[UUID, ...] = (resource.resource_id,)
        elif not acceptable_ids:
            return []
        else:
            target_ids = tuple(sorted(acceptable_ids))

        resource_type_values = grant_resource_type_values(resource.resource_type)
        matched_ids: list[UUID] = []
        for principal_group in principal_sets:
            rows = await self._grant_rows_for_principal_group(
                pod_id=resource.pod_id,
                resource_type_values=resource_type_values,
                target_ids=target_ids,
                principal_group=principal_group,
            )
            group_ids = [
                grant_id
                for grant_id, granted_permission_id in rows
                if granted_permission_id in permission_ids
            ]
            if not group_ids:
                return []
            matched_ids.extend(group_ids)
        return matched_ids

    async def _grant_rows_for_principal_group(
        self,
        *,
        pod_id: UUID,
        resource_type_values: tuple[str, ...],
        target_ids: tuple[UUID, ...],
        principal_group: frozenset[PrincipalRef],
    ) -> list[tuple[UUID, str]]:
        """``(grant_id, permission_id)`` for one principal group, read once.

        The permission is deliberately *not* in the WHERE clause, and that is
        the whole point. Everything else in the key — the pod, the resource
        targets, the principals — is fixed for a caller asking many questions
        about one resource, so filtering by permission in SQL turned N
        permissions into N round trips over the same handful of rows.
        ``/pods/{id}/permissions/me`` asks 51 of them; a POD_VIEWER paid ~40.

        Selecting the permission alongside the id and filtering in Python
        returns exactly the rows the per-permission query returned, so every
        decision, reason code and matched grant id is identical by
        construction. Nothing here knows how to rebuild a decision — it only
        avoids asking the same question again.

        The memo is per ``Authorizer``, which is built per context, so its
        lifetime matches the decision cache that already sits in front of it.
        """
        from sqlalchemy import or_, and_

        key = (pod_id, resource_type_values, target_ids, principal_group)
        cached = self._grant_rows.get(key)
        if cached is not None:
            return cached
        clauses = [
            (
                ResourcePermissionGrantModel.grantee_type == principal.type,
                ResourcePermissionGrantModel.grantee_id == principal.id,
            )
            for principal in principal_group
        ]
        stmt = select(
            ResourcePermissionGrantModel.id,
            ResourcePermissionGrantModel.permission_id,
        ).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type.in_(resource_type_values),
            ResourcePermissionGrantModel.resource_id.in_(target_ids),
            or_(*(and_(*clause) for clause in clauses)),
        )
        rows = [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]
        self._grant_rows[key] = rows
        return rows

    @staticmethod
    def _normalize_visibility(value: str | None) -> ResourceVisibility:
        # An unreadable value falls back to POD here (rather than raising) so a
        # malformed row degrades to pod-scoped — the safe direction — instead of
        # 500ing the whole request.
        return normalize_resource_visibility(value) or ResourceVisibility.POD
