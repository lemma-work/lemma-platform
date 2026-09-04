"""Which pod a resource belongs to, and which resources have no pod at all.

`Authorizer.authorize` confines a pod's default agent to the pod it was invoked
in by comparing the resource's pod against the context's. That comparison needs
the resource's pod, and this is what supplies it.

Its own module, not because `service.py` was long, but because the thing that
makes this safe is a *check over the whole set* — and a set worth checking is a
set worth naming. `assert_every_resource_type_is_classified` runs at import: a
`ResourceType` in none of the four groups below stops the process rather than
quietly opting itself out of the clamp, which is what used to happen.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import InstrumentedAttribute

from app.core.authorization.context import ResourceRef, ResourceType
from app.modules.agent.infrastructure.models import AgentModel, ConversationModel
from app.modules.apps.infrastructure.models import AppModel
from app.modules.datastore.infrastructure.models import DatastoreFile, DatastoreTable
from app.modules.function.infrastructure.models import FunctionModel
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.workflow.infrastructure.models import WorkflowModel


@dataclass(frozen=True, slots=True)
class ResourceTable:
    """The columns a resource type is hydrated from.

    Named fields rather than the 5-tuple this replaced, whose first element was
    the model and was never read — the caller unpacked it as `_model`.
    """

    id_column: InstrumentedAttribute
    pod_column: InstrumentedAttribute
    owner_column: InstrumentedAttribute
    #: ``None`` for a table with no visibility column; the caller treats such a
    #: resource as PERSONAL.
    visibility_column: InstrumentedAttribute | None
    #: Set only for the hierarchical datastore types, whose grants cascade to
    #: descendants by path. Naming it here keeps this module the only place in
    #: `app/core` that reaches for a datastore column.
    path_column: InstrumentedAttribute | None = None


def _table(
    id_column, pod_column, owner_column, visibility_column=None, path_column=None
) -> ResourceTable:
    return ResourceTable(
        id_column, pod_column, owner_column, visibility_column, path_column
    )


#: Where a resource type learns which pod and owner it belongs to.
#:
#: A dict rather than an `if` ladder, and module-level rather than rebuilt per
#: call, so that the set of types it covers can be *checked* — see
#: `assert_every_resource_type_is_classified` below.
#:
#: Two readers, which is the other reason it is a table. `Authorizer` hydrates a
#: resource from it, and `get_resource_creator` reads `owner_column` from it;
#: that used to be a second `if` ladder over the same types, with its own
#: `else: return None`, free to drift from this one.
#:
#: `FOLDER`/`DOCUMENT` carry rows for the owner lookup, but hydration never
#: reaches them: `_hydrate_datastore_file` intercepts both several lines
#: earlier, because a datastore file also needs the path its folder grants
#: cascade on.
RESOURCE_TABLES: dict[ResourceType, ResourceTable] = {
    ResourceType.AGENT: _table(
        AgentModel.id,
        AgentModel.pod_id,
        AgentModel.user_id,
        AgentModel.visibility,
    ),
    ResourceType.FUNCTION: _table(
        FunctionModel.id,
        FunctionModel.pod_id,
        FunctionModel.user_id,
        FunctionModel.visibility,
    ),
    ResourceType.CONVERSATION: _table(
        ConversationModel.id,
        ConversationModel.pod_id,
        ConversationModel.user_id,
        None,
    ),
    ResourceType.DATASTORE_TABLE: _table(
        DatastoreTable.id,
        DatastoreTable.pod_id,
        DatastoreTable.user_id,
        DatastoreTable.visibility,
    ),
    ResourceType.APP: _table(
        AppModel.id,
        AppModel.pod_id,
        AppModel.user_id,
        AppModel.visibility,
    ),
    ResourceType.WORKFLOW: _table(
        WorkflowModel.id,
        WorkflowModel.pod_id,
        WorkflowModel.user_id,
        WorkflowModel.visibility,
    ),
    ResourceType.FOLDER: _table(
        DatastoreFile.id,
        DatastoreFile.pod_id,
        DatastoreFile.owner_user_id,
        DatastoreFile.visibility,
        DatastoreFile.path,
    ),
    ResourceType.DOCUMENT: _table(
        DatastoreFile.id,
        DatastoreFile.pod_id,
        DatastoreFile.owner_user_id,
        DatastoreFile.visibility,
        DatastoreFile.path,
    ),
    ResourceType.SCHEDULE: _table(
        Schedule.id,
        Schedule.pod_id,
        Schedule.user_id,
        Schedule.visibility,
    ),
}

#: Types hydrated by a method of their own rather than by a table lookup.
HYDRATED_BY_METHOD = frozenset(
    {
        # Its own id is its pod.
        ResourceType.POD,
        # These also have rows above, for the owner lookup; hydration takes
        # the method because it needs the path as well.
        ResourceType.FOLDER,
        ResourceType.DOCUMENT,
        # Org-scoped tables with no pod column; see the methods for why that is
        # the right answer rather than a gap.
        ResourceType.CONNECTOR,
        ResourceType.CONNECTOR_ACCOUNT,
        ResourceType.CONNECTOR_AUTH_CONFIG,
    }
)

#: Types that do not belong to a pod, so the cross-pod clamp has nothing to
#: compare and its absence is a decision rather than an omission. A delegated
#: pod agent is refused an org-scoped action before hydration is reached at all,
#: by `_is_pod_scoped_permission`.
NOT_POD_SCOPED = frozenset({ResourceType.ORGANIZATION, ResourceType.ROLE})

#: Pod-scoped types for which nothing builds a `ResourceRef` today. They exist
#: as permission ids and grant targets, and never reach `authorize` as a
#: resource. Listed rather than left out: the moment something does build one,
#: `pod_is_unknowable` denies instead of waving it through, and the fix is to
#: give the type a row above.
NO_REFS_CONSTRUCTED = frozenset(
    {ResourceType.POD_MEMBER, ResourceType.DATASTORE_RECORD}
)


def assert_every_resource_type_is_classified() -> None:
    """Refuse to import if a `ResourceType` is in none of the sets above.

    The cross-pod clamp in `Authorizer.authorize` only fires when a resource's
    pod is known, so a type nobody classified was a type the clamp silently
    skipped. Adding one to `ResourceType` used to be enough to opt it out; now
    it fails here, at import, with the name of the type.
    """
    classified = (
        set(RESOURCE_TABLES) | HYDRATED_BY_METHOD | NOT_POD_SCOPED | NO_REFS_CONSTRUCTED
    )
    missing = set(ResourceType) - classified
    if missing:
        raise RuntimeError(
            "ResourceType(s) not classified for authorization hydration: "
            + ", ".join(sorted(t.name for t in missing))
            + ". Add a row to _RESOURCE_TABLES, or name the type in one of "
            "HYDRATED_BY_METHOD / NOT_POD_SCOPED / NO_REFS_CONSTRUCTED."
        )


assert_every_resource_type_is_classified()


def pod_is_unknowable(resource: ResourceRef) -> bool:
    """Should a pod-scoped check refuse this resource for want of a pod?

    True when the resource belongs to a pod in principle but hydration could not
    say which. False for types that have no pod at all, whose exemption is
    recorded in `NOT_POD_SCOPED`.
    """
    if resource.resource_type in NOT_POD_SCOPED:
        return False
    return resource.pod_id is None
