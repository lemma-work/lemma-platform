"""SQL helpers for projecting resource allowed actions."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import (
    Boolean,
    String,
    and_,
    case,
    cast,
    exists,
    false,
    func,
    literal,
    or_,
)
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import aliased
from sqlalchemy.sql import ColumnElement

from app.core.authorization.context import (
    Assistant,
    Context,
    OtherPod,
    Person,
    PrincipalRef,
    ResourceType,
)
from app.core.authorization.delegation import DESTRUCTIVE_ACTIONS
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import (
    equivalent_permission_ids,
    pod_principal_may_hold,
)
from app.core.authorization.resource_actions import (
    RESOURCE_ACTIONS,
    owner_actions_for_resource,
)
from app.modules.datastore.infrastructure.models.datastore_models import DatastoreFile

# Resource types whose grants cascade down a path hierarchy (a grant on a
# folder authorizes its descendants). All other types stay exact-id match.
_PATH_CASCADING_RESOURCE_TYPES = (ResourceType.FOLDER, ResourceType.DOCUMENT)


def read_action_for_resource(resource_type: ResourceType) -> str:
    for action in RESOURCE_ACTIONS.get(resource_type, ()):
        if action.endswith(".read"):
            return action
    raise ValueError(f"Resource type {resource_type.value} has no read action")


def actions_for_resource(resource_type: ResourceType) -> tuple[str, ...]:
    return RESOURCE_ACTIONS.get(resource_type, ())


def allowed_actions_expr(
    *,
    ctx: Context,
    resource_type: ResourceType,
    resource_id_col,
    pod_id_col,
    owner_user_id_col=None,
    visibility_col=None,
    resource_path_col=None,
) -> ColumnElement:
    """Return a PostgreSQL text[] expression with allowed actions for a row.

    The expression keeps grant checks in the restricted/delegated branches so
    normal POD/PUBLIC rows and owner rows can be resolved from the current
    context without consulting the grants table.

    ``resource_path_col`` is only used for FOLDER/DOCUMENT resources: when
    provided, a grant on any ancestor folder (or the pod-wide grant)
    cascades to this row. Omit it for all other resource types.
    """

    resource_actions = actions_for_resource(resource_type)
    all_actions = list(resource_actions)
    if ctx.is_superuser:
        return _text_array(all_actions)

    role_actions = [action for action in resource_actions if ctx.has_permission(action)]
    owner_actions = list(
        dict.fromkeys([*owner_actions_for_resource(resource_type), *role_actions])
    )
    grant_candidate_actions: Sequence[str] = resource_actions
    if ctx.is_assistant:
        # Default pod agent (user-equivalent): destructive actions never
        # project as available — at execution time they require a session
        # approval (DESTRUCTIVE_ACTION_REQUIRES_APPROVAL otherwise).
        role_actions = [a for a in role_actions if a not in DESTRUCTIVE_ACTIONS]
        owner_actions = [a for a in owner_actions if a not in DESTRUCTIVE_ACTIONS]
        grant_candidate_actions = [
            a for a in resource_actions if a not in DESTRUCTIVE_ACTIONS
        ]
    acts_as_a_person = isinstance(ctx.actor, (Person, Assistant))
    if acts_as_a_person and ctx.pod_id is not None and not ctx.belongs_to_its_pod():
        # As in ``Authorizer._outside_the_pod_decision``: someone who left the
        # pod keeps nothing there, rows they own included -- Public reads only.
        role_actions, owner_actions, grant_candidate_actions = [], [], []
    empty_actions = _text_array([])

    # Before the outsider branch: another pod is an outsider too, but one this
    # pod may have granted things to.
    if isinstance(ctx.actor, OtherPod):
        return _pod_allowed_actions_expr(
            ctx=ctx,
            resource_type=resource_type,
            resource_actions=resource_actions,
            resource_id_col=resource_id_col,
            pod_id_col=pod_id_col,
            visibility_col=visibility_col,
            resource_path_col=resource_path_col,
        )

    if ctx.is_outsider:
        return _anonymous_allowed_actions_expr(
            resource_actions=resource_actions,
            visibility_col=visibility_col,
            pod_id_col=pod_id_col,
            pod_id=ctx.pod_id,
        )

    if ctx.is_named_workload:
        return _delegated_allowed_actions_expr(
            ctx=ctx,
            resource_type=resource_type,
            resource_id_col=resource_id_col,
            pod_id_col=pod_id_col,
            owner_user_id_col=owner_user_id_col,
            visibility_col=visibility_col,
            all_actions=all_actions,
            empty_actions=empty_actions,
            resource_path_col=resource_path_col,
        )

    principal_sets = ctx.grant_principal_sets or (ctx.principal_refs,)
    is_person = isinstance(ctx.actor, Person) and ctx.user_id is not None
    owns = (
        owner_user_id_col == ctx.user_id
        if owner_user_id_col is not None and ctx.user_id is not None
        else false()
    )
    if visibility_col is None:
        visibility = literal("POD")
    else:
        visibility = visibility_col

    def allowed(action: str) -> ColumnElement[bool]:
        """``Authorizer.authorize`` for one action, rule for rule, as SQL.

        The two must agree (``test_ladder_projection_agree_e2e``): a wider
        listing offers actions that 403, a narrower one hides what the caller
        may open. Each line below is one of the ladder's branches.
        """
        granted = (
            _grant_exists_for_principal_sets(
                resource_type=resource_type,
                resource_id_col=resource_id_col,
                pod_id_col=pod_id_col,
                principal_sets=principal_sets,
                action=action,
                resource_path_col=resource_path_col,
            )
            if action in grant_candidate_actions
            else false()
        )
        branches = []
        # The owner's own actions on what they own.
        if action in owner_actions:
            branches.append(owns)
        # A person reads what is Public.
        if is_person and action.endswith(".read"):
            branches.append(visibility == "PUBLIC")
        if action in role_actions:
            # Held by role: anywhere but someone else's PERSONAL, and a
            # RESTRICTED row still needs its grant.
            branches.append(
                or_(
                    visibility.in_(["PUBLIC", "POD"]),
                    owns,
                    and_(visibility == "RESTRICTED", granted),
                )
            )
        else:
            # Not held by role: a grant, never on someone else's PERSONAL -- and
            # an unreadable level counts as PERSONAL, as hydration reads it.
            branches.append(
                and_(
                    granted,
                    or_(visibility.in_(["POD", "PUBLIC", "RESTRICTED"]), owns),
                )
            )
        return or_(*branches) if branches else false()

    return _conditional_actions_array_expr(
        [(action, allowed(action)) for action in resource_actions]
    )


def allowed_read_filter(
    *,
    ctx: Context,
    resource_type: ResourceType,
    resource_id_col,
    pod_id_col,
    owner_user_id_col=None,
    visibility_col=None,
    resource_path_col=None,
) -> ColumnElement[bool]:
    allowed_actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=resource_type,
        resource_id_col=resource_id_col,
        pod_id_col=pod_id_col,
        owner_user_id_col=owner_user_id_col,
        visibility_col=visibility_col,
        resource_path_col=resource_path_col,
    )
    return allowed_actions_contains(
        allowed_actions, read_action_for_resource(resource_type)
    )


def allowed_actions_contains(
    allowed_actions: ColumnElement,
    action: str,
) -> ColumnElement[bool]:
    """``allowed_actions @> ARRAY[action]`` — does this row permit *action*?

    ``return_type`` is not cosmetic. Without it the expression inherits the
    left operand's type, so SQLAlchemy labels a *boolean* as ``text[]`` and
    hands the row's ``True`` to the array result processor. In a WHERE clause
    that never shows, because nothing reads the value back; the first time this
    was SELECTed it raised ``'bool' object is not iterable``.
    """
    return allowed_actions.op("@>", return_type=Boolean)(_text_array([action]))


def _anonymous_allowed_actions_expr(
    *,
    resource_actions: Sequence[str],
    visibility_col,
    pod_id_col=None,
    pod_id: UUID | None = None,
) -> ColumnElement:
    """Public reads, and only this pod's when the context is pinned to one.

    The same pin `Authorizer._is_public_read` applies row by row, so a listing
    and a single read cannot disagree about what an outsider's run may see.
    """
    public_read_actions = [
        action for action in resource_actions if action.endswith(".read")
    ]
    if visibility_col is None or not public_read_actions:
        return _text_array([])
    is_public = visibility_col == "PUBLIC"
    if pod_id is not None:
        if pod_id_col is None:
            return _text_array([])
        is_public = and_(is_public, pod_id_col == pod_id)
    return case(
        (is_public, _text_array(public_read_actions)),
        else_=_text_array([]),
    )


def _pod_allowed_actions_expr(
    *,
    ctx: Context,
    resource_type: ResourceType,
    resource_actions: Sequence[str],
    resource_id_col,
    pod_id_col,
    visibility_col,
    resource_path_col=None,
) -> ColumnElement:
    """Another pod's reach, row by row: what `Authorizer._pod_decision` allows.

    Its grants on anything but a personal row, Public reads on top, and nothing
    outside the pod the context is pinned to. Grants apply whatever the row's
    visibility, as the authorizer's grant path does: a pod is granted a table
    by name, not by the table first being made restricted.
    """
    if pod_id_col is None or ctx.pod_id is None:
        return _text_array([])
    resource_actions = [a for a in resource_actions if pod_principal_may_hold(a)]
    granted = _grant_actions_array_expr(
        ctx=ctx,
        resource_type=resource_type,
        resource_id_col=resource_id_col,
        pod_id_col=pod_id_col,
        principal_sets=ctx.grant_principal_sets or (ctx.principal_refs,),
        candidate_actions=resource_actions,
        resource_path_col=resource_path_col,
    )
    public_reads = [action for action in resource_actions if action.endswith(".read")]
    whens = [(pod_id_col != ctx.pod_id, _text_array([]))]
    if visibility_col is not None:
        whens.append((visibility_col == "PERSONAL", _text_array([])))
        whens.append(
            (
                visibility_col == "PUBLIC",
                func.array_cat(granted, _text_array(public_reads)),
            )
        )
    return case(*whens, else_=granted)


def _delegated_allowed_actions_expr(
    *,
    ctx: Context,
    resource_type: ResourceType,
    resource_id_col,
    pod_id_col,
    owner_user_id_col,
    visibility_col,
    all_actions: Sequence[str],
    empty_actions: ColumnElement,
    resource_path_col=None,
) -> ColumnElement:
    # PS-ACCESS-020 for a listing: an action shows only if the workload was
    # granted it AND the invoking person could take it themselves -- the same
    # two halves ``workload_authority`` decides one request at a time. This
    # used to be the workload's grants alone, so search and listings returned
    # rows a direct read of the same row refused (an agent with a pod-wide
    # folder grant searched a RESTRICTED folder its person could not open).
    from app.core.authorization.workload_authority import invoking_person_context

    if ctx.pod_id is not None and not ctx.belongs_to_its_pod():
        # The person it acts for left the pod: the ladder refuses the workload
        # outright (``_outside_the_pod_decision``), Public rows included.
        return empty_actions
    person = invoking_person_context(ctx.authorizer, ctx)
    person_actions = (
        allowed_actions_expr(
            ctx=person,
            resource_type=resource_type,
            resource_id_col=resource_id_col,
            pod_id_col=pod_id_col,
            owner_user_id_col=owner_user_id_col,
            visibility_col=visibility_col,
            resource_path_col=resource_path_col,
        )
        if person is not None
        else None
    )
    workload_actions = _conditional_actions_array_expr(
        [
            (
                action,
                and_(
                    _grant_exists_for_action(
                        resource_type=resource_type,
                        resource_id_col=resource_id_col,
                        pod_id_col=pod_id_col,
                        principal_group=ctx.workload_principal_refs,
                        action=action,
                        resource_path_col=resource_path_col,
                    ),
                    allowed_actions_contains(person_actions, action)
                    if person_actions is not None
                    else literal(True),
                ),
            )
            for action in all_actions
        ]
    )
    if visibility_col is None:
        return workload_actions
    if owner_user_id_col is not None and ctx.user_id is not None:
        personal_denied = and_(
            visibility_col == "PERSONAL",
            or_(owner_user_id_col.is_(None), owner_user_id_col != ctx.user_id),
        )
    else:
        personal_denied = visibility_col == "PERSONAL"
    return case((personal_denied, empty_actions), else_=workload_actions)


def _grant_actions_array_expr(
    *,
    ctx: Context,
    resource_type: ResourceType,
    resource_id_col,
    pod_id_col,
    principal_sets: tuple[frozenset[PrincipalRef], ...],
    candidate_actions: Sequence[str],
    resource_path_col=None,
) -> ColumnElement:
    return _conditional_actions_array_expr(
        [
            (
                action,
                _grant_exists_for_principal_sets(
                    resource_type=resource_type,
                    resource_id_col=resource_id_col,
                    pod_id_col=pod_id_col,
                    principal_sets=principal_sets,
                    action=action,
                    resource_path_col=resource_path_col,
                ),
            )
            for action in candidate_actions
        ]
    )


def _conditional_actions_array_expr(
    action_conditions: Sequence[tuple[str, ColumnElement[bool]]],
) -> ColumnElement:
    return _text_array(
        [
            case((condition, literal(action)), else_=None)
            for action, condition in action_conditions
        ],
        remove_null=True,
    )


def _grant_exists_for_principal_sets(
    *,
    resource_type: ResourceType,
    resource_id_col,
    pod_id_col,
    principal_sets: tuple[frozenset[PrincipalRef], ...],
    action: str,
    resource_path_col=None,
) -> ColumnElement[bool]:
    if not principal_sets or any(not group for group in principal_sets):
        return false()
    checks = [
        _grant_exists_for_action(
            resource_type=resource_type,
            resource_id_col=resource_id_col,
            pod_id_col=pod_id_col,
            principal_group=group,
            action=action,
            resource_path_col=resource_path_col,
        )
        for group in principal_sets
    ]
    return and_(*checks)


def _grant_exists_for_action(
    *,
    resource_type: ResourceType,
    resource_id_col,
    pod_id_col,
    principal_group: frozenset[PrincipalRef],
    action: str,
    resource_path_col=None,
) -> ColumnElement[bool]:
    if not principal_group:
        return false()
    principal_clauses = [
        and_(
            ResourcePermissionGrantModel.grantee_type == principal.type,
            ResourcePermissionGrantModel.grantee_id == principal.id,
        )
        for principal in principal_group
    ]
    base_conditions = [
        ResourcePermissionGrantModel.pod_id == pod_id_col,
        ResourcePermissionGrantModel.resource_type.in_(
            grant_resource_type_values(resource_type)
        ),
        ResourcePermissionGrantModel.permission_id.in_(
            equivalent_permission_ids(action)
        ),
        or_(*principal_clauses),
    ]

    if (
        resource_type in _PATH_CASCADING_RESOURCE_TYPES
        and resource_path_col is not None
    ):
        # Cascade: the row is authorized if a grant targets this exact row, an
        # ancestor folder (granted path is a prefix), or the pod-wide grant
        # (grant keyed on the pod id). ``granted`` is the folder the grant
        # points at; its path is read live so renames/moves stay consistent.
        #
        # Two EXISTS, not one with an OR inside. Written as one, ``granted``
        # sits in the FROM of both branches but only the second constrains it,
        # so the planner cannot use ``granted.id = resource_id`` as a join key:
        # every (row, grant) pair scanned the whole file table -- every pod's
        # files -- before it could answer false. Split, the second EXISTS
        # reaches ``granted`` through its primary key.
        granted = aliased(DatastoreFile)
        pod_wide = exists().where(
            *base_conditions,
            ResourcePermissionGrantModel.resource_id == pod_id_col,
        )
        on_self_or_ancestor = exists().where(
            *base_conditions,
            granted.id == ResourcePermissionGrantModel.resource_id,
            granted.pod_id == pod_id_col,
            or_(
                resource_path_col == granted.path,
                func.left(resource_path_col, func.length(granted.path) + 1)
                == granted.path.concat("/"),
            ),
        )
        return or_(pod_wide, on_self_or_ancestor)

    return exists().where(
        *base_conditions,
        ResourcePermissionGrantModel.resource_id == resource_id_col,
    )


def _text_array(values: Sequence, *, remove_null: bool = False) -> ColumnElement:
    if not values:
        expression = cast(array([], type_=String), ARRAY(String))
    else:
        expression = array(list(values), type_=String)
    if remove_null:
        return cast(func.array_remove(expression, None), ARRAY(String))
    return cast(expression, ARRAY(String))
