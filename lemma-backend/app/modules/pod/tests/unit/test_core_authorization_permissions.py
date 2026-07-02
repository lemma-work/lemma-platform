from __future__ import annotations

from uuid import uuid4

import pytest

from fastapi import HTTPException

from app.core.authorization.context import (
    ActorType,
    AuthorizationDecision,
    Context,
    PrincipalRef,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.grants import (
    grant_resource_type_values,
    connector_resource_id,
    normalize_pod_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.permissions import (
    ORG_EDITOR_PERMISSIONS,
    PERMISSION_DEFINITIONS,
    POD_ADMIN_PERMISSIONS,
    POD_EDITOR_PERMISSIONS,
    POD_USER_PERMISSIONS,
    POD_VIEWER_PERMISSIONS,
    Permissions,
    equivalent_permission_ids,
)
from app.core.authorization.resource_actions import owner_actions_for_resource
from app.core.authorization.service import Authorizer


class CountingAuthorizer:
    def __init__(self):
        self.calls = 0

    async def authorize(self, ctx, permission_id, resource=None):
        self.calls += 1
        return AuthorizationDecision(
            allowed=True,
            reason_code="TEST_ALLOW",
            permission_id=permission_id,
            resource=resource,
        )


def test_permission_registry_has_unique_ids():
    ids = [item.id for item in PERMISSION_DEFINITIONS]

    assert len(ids) == len(set(ids))
    assert all(item.id == item.id.lower() for item in PERMISSION_DEFINITIONS)
    assert all("." in item.id for item in PERMISSION_DEFINITIONS)


def test_system_role_permissions_are_explicit_unions():
    assert POD_VIEWER_PERMISSIONS < POD_USER_PERMISSIONS
    assert POD_USER_PERMISSIONS < POD_EDITOR_PERMISSIONS
    assert POD_EDITOR_PERMISSIONS < POD_ADMIN_PERMISSIONS
    assert Permissions.POD_ROLE_MANAGE in POD_ADMIN_PERMISSIONS
    assert Permissions.POD_ROLE_MANAGE not in POD_EDITOR_PERMISSIONS
    assert Permissions.CONNECTOR_USE in POD_USER_PERMISSIONS
    assert Permissions.CONNECTOR_MANAGE not in POD_EDITOR_PERMISSIONS
    assert Permissions.CONNECTOR_MANAGE in ORG_EDITOR_PERMISSIONS
    assert Permissions.CONNECTOR_ACCOUNT_MANAGE in POD_ADMIN_PERMISSIONS
    # Pod users can create their own schedules, but managing pod-wide schedules
    # (update/delete on schedules they do not own) stays an editor/admin power.
    assert Permissions.SCHEDULE_CREATE in POD_USER_PERMISSIONS
    assert Permissions.SCHEDULE_UPDATE not in POD_USER_PERMISSIONS
    assert Permissions.SCHEDULE_DELETE not in POD_USER_PERMISSIONS


def test_schedule_owner_can_fully_manage_owned_schedules():
    # A schedule is owned by its creator; owners get the resource's full action
    # set so a pod user can manage the schedules they create without holding the
    # pod-wide schedule update/delete permissions.
    owner_actions = owner_actions_for_resource(ResourceType.SCHEDULE)
    assert set(owner_actions) == {
        Permissions.SCHEDULE_READ,
        Permissions.SCHEDULE_UPDATE,
        Permissions.SCHEDULE_DELETE,
    }


def test_equivalent_permission_ids_include_higher_resource_permissions():
    assert equivalent_permission_ids(Permissions.APP_READ) >= {
        Permissions.APP_READ,
        Permissions.APP_UPDATE,
        Permissions.APP_DELETE,
    }
    assert equivalent_permission_ids(Permissions.DATASTORE_TABLE_READ) >= {
        Permissions.DATASTORE_TABLE_READ,
        Permissions.DATASTORE_TABLE_UPDATE,
        Permissions.DATASTORE_TABLE_DELETE,
    }
    assert equivalent_permission_ids(Permissions.CONNECTOR_USE) == {
        Permissions.CONNECTOR_USE
    }
    assert equivalent_permission_ids(Permissions.CONNECTOR_ACCOUNT_USE) >= {
        Permissions.CONNECTOR_ACCOUNT_USE,
        Permissions.CONNECTOR_ACCOUNT_MANAGE,
    }


def test_execute_implies_read_for_agent_function_workflow():
    assert equivalent_permission_ids(Permissions.AGENT_READ) == {
        Permissions.AGENT_READ,
        Permissions.AGENT_EXECUTE,
    }
    assert equivalent_permission_ids(Permissions.FUNCTION_READ) == {
        Permissions.FUNCTION_READ,
        Permissions.FUNCTION_EXECUTE,
    }
    assert equivalent_permission_ids(Permissions.WORKFLOW_READ) == {
        Permissions.WORKFLOW_READ,
        Permissions.WORKFLOW_EXECUTE,
    }
    # Read must never satisfy an execute check.
    assert equivalent_permission_ids(Permissions.AGENT_EXECUTE) == {
        Permissions.AGENT_EXECUTE
    }
    assert equivalent_permission_ids(Permissions.FUNCTION_EXECUTE) == {
        Permissions.FUNCTION_EXECUTE
    }
    assert equivalent_permission_ids(Permissions.WORKFLOW_EXECUTE) == {
        Permissions.WORKFLOW_EXECUTE
    }


def test_implied_permissions_map_is_valid_and_flattened():
    from app.core.authorization.permissions import IMPLIED_PERMISSIONS, PERMISSION_BY_ID

    for granted, implied in IMPLIED_PERMISSIONS.items():
        assert granted in PERMISSION_BY_ID
        assert granted not in implied
        for permission_id in implied:
            assert permission_id in PERMISSION_BY_ID
            # The map stores flattened sets: anything implied transitively must
            # already be listed directly (equivalent_permission_ids is a
            # single-hop reverse lookup and relies on this).
            assert IMPLIED_PERMISSIONS.get(permission_id, frozenset()) <= implied


def test_connector_resource_ref_factories_are_pod_and_org_scoped():
    pod_id = uuid4()
    pod_connector_id = uuid4()
    pod_account_id = uuid4()
    organization_id = uuid4()
    auth_config_id = uuid4()

    pod_app_ref = ResourceRef.connector(pod_id, pod_connector_id)
    pod_account_ref = ResourceRef.connector_account(pod_id, pod_account_id)
    auth_config_ref = ResourceRef.connector_auth_config(
        organization_id,
        auth_config_id,
    )

    assert pod_app_ref.resource_type == ResourceType.CONNECTOR
    assert pod_app_ref.resource_id == pod_connector_id
    assert pod_app_ref.pod_id == pod_id
    assert pod_account_ref.resource_type == ResourceType.CONNECTOR_ACCOUNT
    assert pod_account_ref.resource_id == pod_account_id
    assert pod_account_ref.pod_id == pod_id
    assert auth_config_ref.resource_type == ResourceType.CONNECTOR_AUTH_CONFIG
    assert auth_config_ref.resource_id == auth_config_id
    assert auth_config_ref.organization_id == organization_id


@pytest.mark.asyncio
async def test_context_caches_authorization_decisions():
    authorizer = CountingAuthorizer()
    resource = ResourceRef(
        resource_type=ResourceType.APP,
        resource_id=uuid4(),
        pod_id=uuid4(),
    )
    ctx = Context(
        actor_type=ActorType.USER,
        actor_id=str(uuid4()),
        user_id=uuid4(),
        permission_ids=frozenset({Permissions.APP_READ}),
        authorizer=authorizer,
    )

    assert await ctx.can(Permissions.APP_READ, resource)
    assert await ctx.can(Permissions.APP_READ, resource)
    assert authorizer.calls == 1


def test_resource_grant_validation_only_accepts_pod_permissions():
    class Grant:
        resource_type = ResourceType.DATASTORE_TABLE
        resource_name = "customers"
        permission_ids = [Permissions.DATASTORE_TABLE_READ]

    validate_pod_resource_grant_permissions([Grant()])

    Grant.permission_ids = [Permissions.CONNECTOR_MANAGE]
    with pytest.raises(HTTPException) as exc_info:
        validate_pod_resource_grant_permissions([Grant()])
    assert exc_info.value.status_code == 400
    assert "Only pod-scoped permissions" in exc_info.value.detail

    Grant.permission_ids = ["missing.permission"]
    with pytest.raises(HTTPException) as unknown_exc:
        validate_pod_resource_grant_permissions([Grant()])
    assert unknown_exc.value.status_code == 400
    assert "Unknown permission" in unknown_exc.value.detail


def test_resource_grant_validation_rejects_permission_resource_type_mismatch():
    class Grant:
        resource_type = ResourceType.AGENT
        resource_name = "helper"
        permission_ids = [Permissions.APP_READ]

    with pytest.raises(HTTPException) as exc_info:
        validate_pod_resource_grant_permissions([Grant()])
    assert exc_info.value.status_code == 400
    assert "do not apply to the resource type" in exc_info.value.detail
    assert "agent:app.read" in exc_info.value.detail

    Grant.permission_ids = [Permissions.AGENT_EXECUTE]
    validate_pod_resource_grant_permissions([Grant()])


def test_connector_grants_validate_use_permission():
    class Grant:
        resource_type = ResourceType.CONNECTOR
        resource_name = "telegram"
        permission_ids = [Permissions.CONNECTOR_USE]

    validate_pod_resource_grant_permissions([Grant()])

    Grant.permission_ids = [Permissions.AGENT_EXECUTE]
    with pytest.raises(HTTPException) as exc_info:
        validate_pod_resource_grant_permissions([Grant()])
    assert exc_info.value.status_code == 400
    assert "do not apply to the resource type" in exc_info.value.detail


class _FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return iter(self._values)

    def all(self):
        return list(self._values)


class _FakeSession:
    """Returns the supplied scalar values for every executed statement."""

    def __init__(self, values):
        self._values = values

    async def execute(self, _stmt):
        return _FakeScalarResult(self._values)


@pytest.mark.asyncio
async def test_resource_grant_normalization_resolves_connector_app_string_id():
    pod_id = uuid4()

    class Grant:
        resource_type = ResourceType.CONNECTOR
        resource_name = "telegram"
        permission_ids = [Permissions.CONNECTOR_USE]

    normalized = await normalize_pod_resource_grants(
        _FakeSession(["telegram"]),
        pod_id=pod_id,
        grants=[Grant()],
    )

    assert normalized[0].resource_id == connector_resource_id("telegram")
    assert normalized[0].permission_ids == [Permissions.CONNECTOR_USE]


@pytest.mark.asyncio
async def test_resource_grant_normalization_reports_missing_connector_app():
    pod_id = uuid4()

    class Grant:
        resource_type = ResourceType.CONNECTOR
        resource_name = "telegram"
        permission_ids = [Permissions.CONNECTOR_USE]

    with pytest.raises(HTTPException) as exc_info:
        await normalize_pod_resource_grants(
            _FakeSession([]),
            pod_id=pod_id,
            grants=[Grant()],
        )

    assert exc_info.value.status_code == 400
    assert "Unknown resource name(s)" in exc_info.value.detail
    assert "connector:telegram" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resource_grant_normalization_reports_all_unknown_names():
    pod_id = uuid4()

    class AgentGrant:
        resource_type = ResourceType.AGENT
        resource_name = "missing_agent"
        permission_ids = [Permissions.AGENT_EXECUTE]

    class FunctionGrant:
        resource_type = ResourceType.FUNCTION
        resource_name = "missing_function"
        permission_ids = [Permissions.FUNCTION_EXECUTE]

    with pytest.raises(HTTPException) as exc_info:
        await normalize_pod_resource_grants(
            _FakeSession([]),
            pod_id=pod_id,
            grants=[AgentGrant(), FunctionGrant()],
        )

    assert exc_info.value.status_code == 400
    assert "agent:missing_agent" in exc_info.value.detail
    assert "function:missing_function" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resource_grant_normalization_rejects_unsupported_resource_type():
    pod_id = uuid4()

    class Grant:
        resource_type = ResourceType.CONVERSATION
        resource_name = "some-conversation"
        permission_ids = [Permissions.CONVERSATION_READ]

    with pytest.raises(HTTPException) as exc_info:
        await normalize_pod_resource_grants(
            _FakeSession([]),
            pod_id=pod_id,
            grants=[Grant()],
        )

    assert exc_info.value.status_code == 400
    assert "do not support name-based grants" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resource_grant_can_satisfy_specific_resource_without_broad_permission(
    monkeypatch,
):
    matched_grant_id = uuid4()
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    async def matching_grants(ctx, permission_id, resource):
        return [matched_grant_id]

    monkeypatch.setattr(authorizer, "_matching_grant_ids", matching_grants)
    resource = ResourceRef(
        resource_type=ResourceType.FOLDER,
        resource_id=uuid4(),
        pod_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{uuid4()}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset(),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(ctx, Permissions.FOLDER_WRITE, resource)

    assert decision.allowed
    assert decision.reason_code == "RESOURCE_GRANT_MATCH"
    assert decision.matched_grant_ids == (matched_grant_id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("workload_type", ["FUNCTION", "AGENT"])
async def test_named_delegated_workload_grant_alone_suffices_for_pod_visible_resource(
    monkeypatch,
    workload_type,
):
    # Grant-first: the workload's explicit grant is standalone authority — the
    # invoking user here holds NO matching role capability (viewer-like) and
    # the action is still allowed.
    workload_grant_id = uuid4()
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    workload_principal_refs = frozenset({PrincipalRef(workload_type, uuid4())})

    async def matching_grants_for_principal_sets(
        ctx,
        permission_id,
        resource,
        principal_sets,
    ):
        assert principal_sets == (workload_principal_refs,)
        return [workload_grant_id]

    monkeypatch.setattr(
        authorizer,
        "_matching_grant_ids_for_principal_sets",
        matching_grants_for_principal_sets,
    )
    resource = ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"{workload_type.lower()}:{next(iter(workload_principal_refs)).id}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset(),
        workload_principal_refs=workload_principal_refs,
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(
        ctx,
        Permissions.DATASTORE_RECORD_WRITE,
        resource,
    )

    assert decision.allowed
    assert decision.reason_code == "POD_VISIBLE"
    assert decision.matched_grant_ids == (workload_grant_id,)


@pytest.mark.asyncio
async def test_named_delegated_workload_requires_workload_grant_for_pod_visible_resource(
    monkeypatch,
):
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    workload_principal_refs = frozenset({PrincipalRef("FUNCTION", uuid4())})

    async def no_matching_grants_for_principal_sets(
        ctx,
        permission_id,
        resource,
        principal_sets,
    ):
        return []

    monkeypatch.setattr(
        authorizer,
        "_matching_grant_ids_for_principal_sets",
        no_matching_grants_for_principal_sets,
    )
    resource = ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{next(iter(workload_principal_refs)).id}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset({Permissions.DATASTORE_RECORD_READ}),
        workload_principal_refs=workload_principal_refs,
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(
        ctx,
        Permissions.DATASTORE_RECORD_READ,
        resource,
    )

    assert not decision.allowed
    assert decision.reason_code == "MISSING_WORKLOAD_RESOURCE_GRANT"


@pytest.mark.asyncio
async def test_named_delegated_workload_grant_alone_suffices_for_restricted_resource(
    monkeypatch,
):
    # RESTRICTED resources follow the same grant-first rule: the workload's
    # grant decides; no additional per-user grant is required.
    workload_grant_id = uuid4()
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    user_principal_refs = frozenset({PrincipalRef("POD_MEMBER", uuid4())})
    workload_principal_refs = frozenset({PrincipalRef("FUNCTION", uuid4())})

    async def workload_only_grants_for_principal_sets(
        ctx,
        permission_id,
        resource,
        principal_sets,
    ):
        if principal_sets == (workload_principal_refs,):
            return [workload_grant_id]
        return []

    monkeypatch.setattr(
        authorizer,
        "_matching_grant_ids_for_principal_sets",
        workload_only_grants_for_principal_sets,
    )
    resource = ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=uuid4(),
        visibility=ResourceVisibility.RESTRICTED,
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{next(iter(workload_principal_refs)).id}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset({Permissions.DATASTORE_RECORD_READ}),
        principal_refs=user_principal_refs | workload_principal_refs,
        grant_principal_sets=(user_principal_refs, workload_principal_refs),
        workload_principal_refs=workload_principal_refs,
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(
        ctx,
        Permissions.DATASTORE_RECORD_READ,
        resource,
    )

    assert decision.allowed
    assert decision.reason_code == "WORKLOAD_RESOURCE_GRANT"
    assert decision.matched_grant_ids == (workload_grant_id,)


@pytest.mark.asyncio
async def test_delegation_scope_is_implication_expanded(monkeypatch):
    # A {function.execute} scope must also admit the implied function.read a
    # run needs; anything outside the scope's implications stays denied.
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    workload_principal_refs = frozenset({PrincipalRef("AGENT", uuid4())})

    async def matching_grants_for_principal_sets(ctx, permission_id, resource, principal_sets):
        return [uuid4()]

    monkeypatch.setattr(
        authorizer,
        "_matching_grant_ids_for_principal_sets",
        matching_grants_for_principal_sets,
    )
    pod_id = uuid4()

    def make_ctx():
        return Context(
            actor_type=ActorType.DELEGATED_USER_WORKLOAD,
            actor_id=f"agent:{next(iter(workload_principal_refs)).id}",
            user_id=uuid4(),
            pod_id=pod_id,
            permission_ids=frozenset(),
            workload_principal_refs=workload_principal_refs,
            delegation_scope=frozenset({Permissions.FUNCTION_EXECUTE}),
            authorizer=authorizer,
        )

    resource = ResourceRef(
        resource_type=ResourceType.FUNCTION,
        resource_id=uuid4(),
        pod_id=pod_id,
        visibility=ResourceVisibility.POD,
    )

    read_decision = await authorizer.authorize(
        make_ctx(), Permissions.FUNCTION_READ, resource
    )
    assert read_decision.allowed

    unrelated = ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=pod_id,
        visibility=ResourceVisibility.POD,
    )
    unrelated_decision = await authorizer.authorize(
        make_ctx(), Permissions.DATASTORE_RECORD_READ, unrelated
    )
    assert not unrelated_decision.allowed
    assert unrelated_decision.reason_code == "DELEGATION_SCOPE_VIOLATION"


@pytest.mark.asyncio
async def test_personal_resource_owned_by_other_denied_despite_workload_grant(
    monkeypatch,
):
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    workload_principal_refs = frozenset({PrincipalRef("AGENT", uuid4())})

    async def matching_grants_for_principal_sets(ctx, permission_id, resource, principal_sets):
        return [uuid4()]

    monkeypatch.setattr(
        authorizer,
        "_matching_grant_ids_for_principal_sets",
        matching_grants_for_principal_sets,
    )
    resource = ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=uuid4(),
        visibility=ResourceVisibility.PERSONAL,
        owner_user_id=uuid4(),
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"agent:{next(iter(workload_principal_refs)).id}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset({Permissions.DATASTORE_RECORD_READ}),
        workload_principal_refs=workload_principal_refs,
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(
        ctx, Permissions.DATASTORE_RECORD_READ, resource
    )

    assert not decision.allowed
    assert decision.reason_code == "PERSONAL_RESOURCE_DENIED"


@pytest.mark.asyncio
async def test_default_pod_agent_delegation_runs_as_user_without_workload_grants(
    monkeypatch,
):
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    async def fail_if_workload_grants_are_checked(*args, **kwargs):
        raise AssertionError("default pod agent should not require workload grants")

    monkeypatch.setattr(
        authorizer,
        "_matching_grant_ids_for_principal_sets",
        fail_if_workload_grants_are_checked,
    )
    resource = ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"agent:{uuid4()}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset({Permissions.DATASTORE_RECORD_READ}),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(
        ctx,
        Permissions.DATASTORE_RECORD_READ,
        resource,
    )

    assert decision.allowed
    assert decision.reason_code == "POD_VISIBLE"


def _default_pod_agent_ctx(
    *, pod_id, permission_ids, role_names=frozenset(), authorizer=None
):
    """A user-equivalent default-pod-agent delegated context (no workload refs)."""
    return Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"agent:{uuid4()}",
        user_id=uuid4(),
        pod_id=pod_id,
        role_names=role_names,
        permission_ids=frozenset(permission_ids),
        is_user_equivalent=True,
        authorizer=authorizer,
    )


def test_is_pod_scoped_permission_distinguishes_pod_and_org_actions():
    assert Authorizer._is_pod_scoped_permission(Permissions.APP_UPDATE)
    assert Authorizer._is_pod_scoped_permission(Permissions.DATASTORE_RECORD_WRITE)
    assert not Authorizer._is_pod_scoped_permission(Permissions.ORG_UPDATE)
    # POD_CREATE is an ORG-scoped action (create a pod in an org), so it is not
    # available to a pod-scoped delegated token.
    assert not Authorizer._is_pod_scoped_permission(Permissions.POD_CREATE)


def test_org_owner_shortcut_covers_pod_scoped_child_resources():
    pod_id = uuid4()
    ctx = _default_pod_agent_ctx(
        pod_id=pod_id,
        permission_ids=frozenset(),  # authority comes ONLY from org-owner shortcut
        role_names=frozenset({"ORG_OWNER"}),
    )
    # The pod entity itself (the original shortcut case).
    assert Authorizer._is_org_owner_of_pod(
        ctx, Permissions.APP_UPDATE, ResourceRef.pod(pod_id)
    )
    # A pod-scoped CHILD resource (an app) — the previously-missing case that broke
    # the bundle upload after app.create.
    app_ref = ResourceRef.app(pod_id, uuid4())
    assert Authorizer._is_org_owner_of_pod(ctx, Permissions.APP_UPDATE, app_ref)
    # Org-scoped actions are NOT covered by the pod-owner shortcut.
    assert not Authorizer._is_org_owner_of_pod(
        ctx, Permissions.ORG_UPDATE, ResourceRef.pod(pod_id)
    )
    # A different pod is not covered.
    assert not Authorizer._is_org_owner_of_pod(
        ctx, Permissions.APP_UPDATE, ResourceRef.app(uuid4(), uuid4())
    )
    # Non-org-owners get nothing from this shortcut.
    non_owner = _default_pod_agent_ctx(pod_id=pod_id, permission_ids=frozenset())
    assert not Authorizer._is_org_owner_of_pod(non_owner, Permissions.APP_UPDATE, app_ref)


@pytest.mark.asyncio
async def test_default_pod_agent_org_owner_can_update_app_via_shortcut():
    """Regression: org owner's default pod agent deploys an app it owns no explicit
    pod-role permission for — app.update on the APP resource resolves through the
    org-owner-of-pod shortcut instead of 403-ing after app.create."""
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    pod_id = uuid4()
    # visibility is set so authorize() skips DB hydration; owner is someone else so
    # the plain owner shortcut does not fire.
    app_ref = ResourceRef(
        resource_type=ResourceType.APP,
        resource_id=uuid4(),
        pod_id=pod_id,
        owner_user_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )
    ctx = _default_pod_agent_ctx(
        pod_id=pod_id,
        permission_ids=frozenset(),  # no explicit app.update
        role_names=frozenset({"ORG_OWNER"}),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(ctx, Permissions.APP_UPDATE, app_ref)

    assert decision.allowed
    assert decision.reason_code == "ORG_OWNER_POD"


@pytest.mark.asyncio
async def test_default_pod_agent_is_blocked_from_org_scoped_actions():
    """The default pod agent is clamped to its pod: org-scoped permissions are
    denied at this layer even when the invoking user holds them."""
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    ctx = _default_pod_agent_ctx(
        pod_id=uuid4(),
        permission_ids=frozenset({Permissions.ORG_UPDATE}),
        role_names=frozenset({"ORG_OWNER"}),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(ctx, Permissions.ORG_UPDATE, resource=None)

    assert not decision.allowed
    assert decision.reason_code == "DELEGATED_POD_SCOPE_ONLY"


@pytest.mark.asyncio
async def test_default_pod_agent_is_blocked_from_another_pod():
    """The default pod agent cannot act on resources pinned to a different pod."""
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    other_pod_app = ResourceRef(
        resource_type=ResourceType.APP,
        resource_id=uuid4(),
        pod_id=uuid4(),  # a DIFFERENT pod
        visibility=ResourceVisibility.POD,
    )
    ctx = _default_pod_agent_ctx(
        pod_id=uuid4(),
        permission_ids=frozenset({Permissions.APP_UPDATE}),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(
        ctx, Permissions.APP_UPDATE, other_pod_app
    )

    assert not decision.allowed
    assert decision.reason_code == "DELEGATED_POD_SCOPE_ONLY"


@pytest.mark.asyncio
async def test_default_pod_agent_allowed_pod_action_in_own_pod():
    """A pod-scoped action the user holds, on a resource in the pinned pod, passes
    the clamp and resolves through the normal user path."""
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    pod_id = uuid4()
    app_ref = ResourceRef(
        resource_type=ResourceType.APP,
        resource_id=uuid4(),
        pod_id=pod_id,
        owner_user_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )
    ctx = _default_pod_agent_ctx(
        pod_id=pod_id,
        permission_ids=frozenset({Permissions.APP_UPDATE}),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(ctx, Permissions.APP_UPDATE, app_ref)

    assert decision.allowed
    assert decision.reason_code == "POD_VISIBLE"


@pytest.mark.asyncio
async def test_resource_grant_does_not_bypass_someone_else_personal_resource(
    monkeypatch,
):
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    async def matching_grants(ctx, permission_id, resource):
        return [uuid4()]

    monkeypatch.setattr(authorizer, "_matching_grant_ids", matching_grants)
    resource = ResourceRef(
        resource_type=ResourceType.FOLDER,
        resource_id=uuid4(),
        pod_id=uuid4(),
        owner_user_id=uuid4(),
        visibility=ResourceVisibility.PERSONAL,
    )
    ctx = Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"function:{uuid4()}",
        user_id=uuid4(),
        pod_id=resource.pod_id,
        permission_ids=frozenset(),
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(ctx, Permissions.FOLDER_READ, resource)

    assert not decision.allowed
    assert decision.reason_code == "INSUFFICIENT_PERMISSION"


def test_document_and_folder_grants_are_authorization_aliases():
    assert set(grant_resource_type_values(ResourceType.DOCUMENT)) == {
        "document",
        "folder",
    }
    assert set(grant_resource_type_values(ResourceType.FOLDER)) == {
        "document",
        "folder",
    }
