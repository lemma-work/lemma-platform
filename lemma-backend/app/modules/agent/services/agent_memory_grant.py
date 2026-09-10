"""The `/memory` folder grant that the MEMORY toolset implies.

MEMORY says an agent should keep durable facts in pod files. Saying so without
the permission to write them ships a switch that does nothing — the agent reads
its instructions, tries, and gets `needs_approval` on every attempt. So the
grant is derived from the toolset rather than left to whoever configures the
agent to remember.

Derived, and therefore re-derived on every write. Grants go through helpers with
*replace* semantics: a permissions block on an update wipes whatever was there
before it. A grant added once would survive exactly until the next edit. Being
recomputed from the toolsets each time is what makes it hold — and what makes
turning MEMORY off take it away again.

`/me` needs nothing: the personal tree is already the requester's own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


from app.core.authorization.context import Context, ResourceType
from app.core.authorization.grants import (
    delete_resource_grantee_grant,
    normalize_pod_resource_grants,
    replace_resource_grantee_grant,
)
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import BadRequestError, DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.datastore.contracts.agent_tools import build_file_service
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.datastore.contracts import DatastoreConflictError

logger = get_logger(__name__)

MEMORY_FOLDER_PATH = "/memory"

# folder.write implies folder.read, so one permission covers reading the pod's
# shared memory and writing to it.
_MEMORY_PERMISSIONS = (Permissions.FOLDER_WRITE,)


@dataclass(frozen=True, slots=True)
class _FolderGrant:
    """Shape ``normalize_pod_resource_grants`` resolves names through."""

    resource_name: str
    resource_type: ResourceType = ResourceType.FOLDER
    permission_ids: list[str] = field(default_factory=lambda: list(_MEMORY_PERMISSIONS))


async def sync_memory_folder_grant(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    agent_id: UUID,
    toolsets: list[AgentToolset] | list[str] | None,
    ctx: Context,
    created_by_user_id: UUID,
) -> None:
    """Add or remove the agent's `/memory` grant to match its toolsets.

    Surgical on purpose — ``replace_resource_grantee_grant`` and
    ``delete_resource_grantee_grant`` touch only this one resource, so an
    agent's other grants are never read, rewritten, or silently dropped on the
    way past.
    """
    wants_memory = AgentToolset.MEMORY in (toolsets or [])
    folder_id = await _memory_folder_id(
        uow, pod_id=pod_id, ctx=ctx, create=wants_memory
    )
    if folder_id is None:
        # Nothing to grant on, and nothing to revoke either: no `/memory`
        # folder means no grant can exist against it.
        return
    if wants_memory:
        await replace_resource_grantee_grant(
            uow.session,
            pod_id=pod_id,
            resource_type=ResourceType.FOLDER,
            resource_id=folder_id,
            grantee_type="AGENT",
            grantee_id=agent_id,
            permission_ids=list(_MEMORY_PERMISSIONS),
            created_by_user_id=created_by_user_id,
        )
        return
    await delete_resource_grantee_grant(
        uow.session,
        pod_id=pod_id,
        resource_type=ResourceType.FOLDER,
        resource_id=folder_id,
        grantee_type="AGENT",
        grantee_id=agent_id,
    )


async def _memory_folder_id(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ctx: Context, create: bool
) -> UUID | None:
    """The `/memory` folder's id, creating it first when memory is being turned on.

    A grant is keyed to a resource row, so the folder has to exist before it can
    be granted — and on a pod where nobody has written memory yet, it does not.
    Provisioning it here is also the friendlier answer: the agent can list an
    empty `/memory` instead of meeting an error on its first look.
    """
    if create:
        try:
            await build_file_service(uow).create_folder(pod_id, MEMORY_FOLDER_PATH, ctx)
        except DatastoreConflictError:
            pass  # Already there — the ordinary case after the first agent.
        except DomainError as denial:
            # Editing an agent and writing pod files are different permissions.
            # Someone who holds the first but not the second should still be
            # able to save the agent; the grant lands the next time it is
            # edited by someone who can create the folder.
            #
            # Any 403, not `DatastoreAccessDeniedError` alone: the denial that
            # actually arrives here is raised by the authorization context
            # (`INSUFFICIENT_PERMISSION` on `folder.write`), which never passes
            # through the datastore's own error type. Catching only that one
            # meant this guard had never fired -- a role holding `agent.create`
            # without `folder.write` got a 403 on the whole request instead of
            # an agent, which is what turning memory on by default surfaced.
            if denial.status_code != 403:
                raise
            logger.warning("agent.memory.folder_provisioning_denied.observed")
            return None
    try:
        normalized = await normalize_pod_resource_grants(
            uow.session, pod_id=pod_id, grants=[_FolderGrant(MEMORY_FOLDER_PATH)]
        )
    except BadRequestError:
        # Raised for a name it cannot resolve. Here that only means the folder
        # does not exist, which is not the caller's mistake and must not fail
        # their request.
        #
        # `BadRequestError` since these stopped being `HTTPException`s: they are
        # raised in workers as well as handlers, and a worker cannot tell a 400
        # from a dropped connection when both are just `Exception`.
        #
        # Deliberately narrow. `DomainError` would also swallow a real
        # authorization failure from further in and hand the agent a silent
        # missing grant instead.
        return None
    return normalized[0].resource_id if normalized else None


async def derive_agent_memory_grant(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    agent_id: UUID,
    toolsets: list[AgentToolset] | list[str] | None,
    ctx: Context,
    created_by_user_id: UUID,
) -> None:
    """:func:`sync_memory_folder_grant`, for every caller who makes an agent.

    The distinction was a live bug. The sync was called from the agent HTTP
    controller and nowhere else, so an agent created straight through
    ``AgentService`` -- which is what the pod bundle applier does -- got MEMORY
    in its toolsets and no folder to write to. Dormant until MEMORY became a
    default for new agents (#476), and then fatal: the source agent held
    ``folder:/memory``, the export recorded the grant, and applying it against a
    pod where nothing had created that folder failed the whole import with
    ``Unknown resource name(s): folder:/memory``.

    Best-effort. Creating or editing an agent must not fail because a folder
    could not be provisioned -- the agent is still perfectly usable, and the
    grant comes back on the next save, since it is re-derived every time. That
    *is* a behaviour change for the bundle applier, which used to call the sync
    unguarded and fail the import step.

    **A floor, not the whole story.** An inline ``permissions`` list *replaces*
    every grant a grantee holds, so a derived grant applied before one of those
    is the first thing wiped. The three call sites that run after a replace --
    two in ``agent_controller`` and ``BundleApplier._apply_agent_grants`` -- must
    keep re-deriving. What this removes is the need for a caller to know that in
    order to get the ordinary case right.
    """
    try:
        await sync_memory_folder_grant(
            uow,
            pod_id=pod_id,
            agent_id=agent_id,
            toolsets=toolsets,
            ctx=ctx,
            created_by_user_id=created_by_user_id,
        )
    except Exception:  # noqa: BLE001
        # The agent is the thing the caller asked for; the folder is a
        # convenience the next save re-derives.
        #
        # With the traceback, because "the next save re-derives it" is only true
        # of a transient failure. A deterministic one -- a schema change, a
        # permission the role never had -- repeats every save, and without the
        # cause this line cannot be told apart from a datastore blip.
        logger.warning("agent.memory.derivation_failed.degraded", exc_info=True)
