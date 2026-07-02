"""SQL helpers for projecting resource allowed actions."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import String, and_, case, cast, exists, false, func, literal, or_
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import aliased
from sqlalchemy.sql import ColumnElement

from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
    ResourceType,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import equivalent_permission_ids
from app.core.authorization.resource_actions import RESOURCE_ACTIONS, owner_actions_for_resource
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

    role_actions = [
        action for action in resource_actions if ctx.has_permission(action)
    ]
    owner_actions = list(
        dict.fromkeys([*owner_actions_for_resource(resource_type), *role_actions])
    )
    empty_actions = _text_array([])

    if ctx.actor_type == ActorType.ANONYMOUS:
        return _anonymous_allowed_actions_expr(
            resource_actions=resource_actions,
            visibility_col=visibility_col,
        )

    if ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD and ctx.workload_principal_refs:
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

    restricted_actions = _grant_actions_array_expr(
        ctx=ctx,
        resource_type=resource_type,
        resource_id_col=resource_id_col,
        pod_id_col=pod_id_col,
        principal_sets=ctx.grant_principal_sets or (ctx.principal_refs,),
        candidate_actions=resource_actions,
        resource_path_col=resource_path_col,
    )

    whens = []
    if owner_user_id_col is not None and ctx.user_id is not None:
        whens.append((owner_user_id_col == ctx.user_id, _text_array(owner_actions)))
    if visibility_col is not None:
        whens.append((visibility_col.in_(["POD", "PUBLIC"]), _text_array(role_actions)))
        whens.append((visibility_col == "RESTRICTED", restricted_actions))
        whens.append((visibility_col == "PERSONAL", empty_actions))
    else:
        whens.append((literal(True), _text_array(role_actions)))
    return case(*whens, else_=empty_actions)


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
    return allowed_actions_contains(allowed_actions, read_action_for_resource(resource_type))


def allowed_actions_contains(
    allowed_actions: ColumnElement,
    action: str,
) -> ColumnElement[bool]:
    return allowed_actions.op("@>")(_text_array([action]))


def _anonymous_allowed_actions_expr(
    *,
    resource_actions: Sequence[str],
    visibility_col,
) -> ColumnElement:
    public_read_actions = [action for action in resource_actions if action.endswith(".read")]
    if visibility_col is None or not public_read_actions:
        return _text_array([])
    return case(
        (visibility_col == "PUBLIC", _text_array(public_read_actions)),
        else_=_text_array([]),
    )


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
    # Grant-first: the workload's own grants decide the projection for every
    # visibility, mirroring _authorize_delegated_workload. The invoking user's
    # role neither widens nor narrows it; only PERSONAL rows owned by someone
    # else stay empty.
    workload_actions = _conditional_actions_array_expr(
        [
            (
                action,
                _grant_exists_for_action(
                    resource_type=resource_type,
                    resource_id_col=resource_id_col,
                    pod_id_col=pod_id_col,
                    principal_group=ctx.workload_principal_refs,
                    action=action,
                    resource_path_col=resource_path_col,
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
        granted = aliased(DatastoreFile)
        return exists().where(
            *base_conditions,
            or_(
                ResourcePermissionGrantModel.resource_id == pod_id_col,
                and_(
                    granted.id == ResourcePermissionGrantModel.resource_id,
                    granted.pod_id == pod_id_col,
                    or_(
                        resource_path_col == granted.path,
                        func.left(
                            resource_path_col, func.length(granted.path) + 1
                        )
                        == granted.path.concat("/"),
                    ),
                ),
            ),
        )

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
