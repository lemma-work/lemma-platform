"""A contact's function call runs as the function itself, with no member behind it.

End to end against the real database, Redis and API, with no sandbox: the run
is created and queued exactly as a contact's conversation creates it, and the
run's own token is presented to the API exactly as the runtime presents it.
What that proves:

* the run row has no user and names the contact, and the function is told
  that contact whatever the model wrote;
* the run's token reaches the pod's data on the function's grants alone -- not
  the owner's -- and nothing that needs a member;
* a replayed tool call does not start a second run, and the daily allowance
  holds;
* only the function's owner or a pod admin may open it to contacts, and only
  if its input declares ``contact_id``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select
from starlette import status

from app.core.authorization.context import ActorType
from app.core.authorization.delegation_revocation import revoke_delegation
from app.core.authorization.function_run import (
    FunctionRunClaims,
    build_function_run_context,
    mint_function_run_token,
)
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.contact_tools import (
    ContactFunctionRequest,
    build_contact_toolset,
)
from app.modules.contacts.infrastructure.models import ContactModel
from app.modules.function.config import function_settings
from app.modules.function.contracts.contact_functions import (
    ContactFunctionUnavailable,
    run_function_for_contact,
)
from app.modules.function.domain.entities import (
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel
from app.modules.test_support.e2e.function_helpers import (
    create_table,
    replace_function_resource_grants,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

_REVISION = f"sha256:{'c' * 64}"
_CONTACT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "contact_id": {}},
}


@pytest.fixture
def short_wait(monkeypatch):
    """No sandbox answers here, so every call reaches its deadline; make it brief."""
    monkeypatch.setattr(function_settings, "function_contact_call_wait_seconds", 0.5)


async def _seed_function(
    db_manager,
    *,
    pod_id: str,
    owner_id: str,
    contacts_invoke: bool = True,
    input_schema: dict | None = None,
    function_type: FunctionType = FunctionType.API,
) -> FunctionModel:
    function = FunctionModel(
        id=uuid7(),
        pod_id=UUID(pod_id),
        user_id=UUID(owner_id),
        name=f"book_slot_{uuid4().hex[:8]}",
        input_schema=_CONTACT_SCHEMA if input_schema is None else input_schema,
        output_schema={},
        type=function_type,
        status=FunctionStatus.READY,
        visibility="POD",
        revision_hash=_REVISION,
        contacts_invoke=contacts_invoke,
    )
    async with db_manager.session_factory() as session:
        session.add(function)
        await session.commit()
    return function


async def _seed_contact(db_manager, *, pod_id: str) -> UUID:
    contact = ContactModel(id=uuid7(), pod_id=UUID(pod_id), display_name="Dana")
    async with db_manager.session_factory() as session:
        session.add(contact)
        await session.commit()
    return contact.id


async def _runs(db_manager, function_id: UUID) -> list[FunctionRunModel]:
    async with db_manager.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(FunctionRunModel).where(
                        FunctionRunModel.function_id == function_id
                    )
                )
            ).all()
        )


async def test_a_contacts_call_runs_for_nobody_and_is_told_its_contact(
    db_manager, test_pod, fixed_test_user, authenticated_client, short_wait
):
    pod_id = test_pod["id"]
    function = await _seed_function(
        db_manager, pod_id=pod_id, owner_id=fixed_test_user["id"]
    )
    contact_id = await _seed_contact(db_manager, pod_id=pod_id)
    tools = build_contact_toolset(
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory)
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            contact_id=contact_id, pod_id=UUID(pod_id), conversation_id=uuid4()
        ),
        tool_call_id="call-1",
    )

    result = await tools.tools["contact_function"].function(
        ctx,
        ContactFunctionRequest(
            name=function.name,
            input={"subject": "Late", "contact_id": str(uuid4())},
        ),
    )

    # Nothing ran it, so the deadline passed: reported as in hand, not failed.
    assert result["success"] is False
    assert "do not tell them it failed" in result["error"]
    [run] = await _runs(db_manager, function.id)
    assert run.user_id is None
    assert run.contact_id == contact_id
    assert run.input_data == {"subject": "Late", "contact_id": str(contact_id)}
    assert run.status == FunctionRunStatus.CANCELLED

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function.name}/runs"
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    [item] = listed.json()["items"]
    assert item["user_id"] is None
    assert item["contact_id"] == str(contact_id)
    assert item["actor"] == f"contact:{contact_id}"


async def test_a_replayed_tool_call_does_not_run_twice(
    db_manager, test_pod, fixed_test_user, short_wait
):
    pod_id = test_pod["id"]
    function = await _seed_function(
        db_manager, pod_id=pod_id, owner_id=fixed_test_user["id"]
    )
    contact_id = await _seed_contact(db_manager, pod_id=pod_id)
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    key = f"{uuid4()}:call-7"

    async def call():
        return await run_function_for_contact(
            factory,
            pod_id=UUID(pod_id),
            name=function.name,
            contact_id=contact_id,
            input_data={"subject": "Book"},
            idempotency_key=key,
        )

    first = await call()
    second = await call()

    assert first.still_running is True
    # The replay finds the first attempt's run, now cancelled, and reports it.
    assert second.completed is False
    assert second.status == FunctionRunStatus.CANCELLED.value
    assert len(await _runs(db_manager, function.id)) == 1


async def test_a_contacts_daily_allowance_holds(
    db_manager, test_pod, fixed_test_user, short_wait, monkeypatch
):
    monkeypatch.setattr(function_settings, "function_contact_calls_per_day", 1)
    pod_id = test_pod["id"]
    function = await _seed_function(
        db_manager, pod_id=pod_id, owner_id=fixed_test_user["id"]
    )
    contact_id = await _seed_contact(db_manager, pod_id=pod_id)
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async def call(key: str):
        return await run_function_for_contact(
            factory,
            pod_id=UUID(pod_id),
            name=function.name,
            contact_id=contact_id,
            input_data={"subject": "Again"},
            idempotency_key=key,
        )

    await call("first")
    with pytest.raises(ContactFunctionUnavailable, match="as often as it may"):
        await call("second")

    assert len(await _runs(db_manager, function.id)) == 1


async def test_a_function_not_opened_to_contacts_reads_as_missing(
    db_manager, test_pod, fixed_test_user, short_wait
):
    pod_id = test_pod["id"]
    function = await _seed_function(
        db_manager,
        pod_id=pod_id,
        owner_id=fixed_test_user["id"],
        contacts_invoke=False,
    )

    with pytest.raises(ContactFunctionUnavailable, match="not found"):
        await run_function_for_contact(
            SessionUnitOfWorkFactory(db_manager.session_factory),
            pod_id=UUID(pod_id),
            name=function.name,
            contact_id=await _seed_contact(db_manager, pod_id=pod_id),
            input_data={},
        )

    assert await _runs(db_manager, function.id) == []


async def _seed_run(
    db_manager, function: FunctionModel, contact_id: UUID, *, running: bool = False
) -> UUID:
    run_id = uuid7()
    async with db_manager.session_factory() as session:
        session.add(
            FunctionRunModel(
                id=run_id,
                function_id=function.id,
                revision_hash=_REVISION,
                user_id=None,
                contact_id=contact_id,
                input_data={"contact_id": str(contact_id)},
                status=FunctionRunStatus.RUNNING
                if running
                else FunctionRunStatus.PENDING,
                job_id=f"function-run:{run_id}",
                deadline_at=datetime.now(timezone.utc) + timedelta(minutes=2),
                started_at=datetime.now(timezone.utc) if running else None,
            )
        )
        await session.commit()
    return run_id


def _token(function: FunctionModel, run_id: UUID, contact_id: UUID, **overrides):
    claims = FunctionRunClaims(
        run_id=run_id,
        function_id=function.id,
        pod_id=overrides.get("pod_id", function.pod_id),
        revision_hash=_REVISION,
        contact_id=contact_id,
    )
    return claims, {
        "Authorization": f"Bearer {mint_function_run_token(claims, ttl_seconds=120)}"
    }


async def test_a_runs_token_reaches_pod_data_on_the_functions_grants_alone(
    db_manager, test_pod, fixed_test_user, authenticated_client, async_client
):
    pod_id = test_pod["id"]
    function = await _seed_function(
        db_manager, pod_id=pod_id, owner_id=fixed_test_user["id"]
    )
    contact_id = await _seed_contact(db_manager, pod_id=pod_id)
    run_id = await _seed_run(db_manager, function, contact_id)
    granted = f"bookings_{uuid4().hex[:8]}"
    ungranted = f"payroll_{uuid4().hex[:8]}"
    for table in (granted, ungranted):
        await create_table(authenticated_client, pod_id, table, enable_rls=False)
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function.name,
        [
            {
                "resource_type": "datastore_table",
                "resource_name": granted,
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                ],
            }
        ],
    )
    claims, headers = _token(function, run_id, contact_id)

    written = await async_client.post(
        f"/pods/{pod_id}/datastore/tables/{granted}/records",
        json={"data": {"title": "Tuesday 10:00"}},
        headers=headers,
    )
    assert written.status_code == status.HTTP_201_CREATED, written.text

    # The owner administers the pod and could write here; the run cannot,
    # because it carries the function's grants and not the owner's authority.
    refused = await async_client.post(
        f"/pods/{pod_id}/datastore/tables/{ungranted}/records",
        json={"data": {"title": "raise"}},
        headers=headers,
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text

    for path in ("/users/me", f"/pods/{pod_id}/functions", f"/pods/{pod_id}"):
        member_only = await async_client.get(path, headers=headers)
        assert member_only.status_code == status.HTTP_403_FORBIDDEN, path
        assert member_only.json()["code"] == "FUNCTION_RUN_ROUTE_NOT_ALLOWED"

    _other_pod_claims, other_pod = _token(function, run_id, contact_id, pod_id=uuid4())
    elsewhere = await async_client.get(
        f"/pods/{pod_id}/datastore/tables", headers=other_pod
    )
    assert elsewhere.status_code == status.HTTP_403_FORBIDDEN

    async with db_manager.session_factory() as session:
        ctx = await build_function_run_context(session, claims)
    assert ctx.actor_type == ActorType.FUNCTION
    assert ctx.actor_id == str(function.id)
    assert ctx.user_id is None
    assert ctx.contact_id == contact_id
    assert ctx.pod_id == function.pod_id

    # Deleting a function revokes its workload; its in-flight run tokens stop
    # working with it, not when they expire.
    await revoke_delegation(actor_id=function.id)
    revoked = await async_client.post(
        f"/pods/{pod_id}/datastore/tables/{granted}/records",
        json={"data": {"title": "after delete"}},
        headers=headers,
    )
    assert revoked.status_code == status.HTTP_403_FORBIDDEN
    assert revoked.json()["code"] == "DELEGATION_REVOKED"


async def test_a_runs_token_reports_its_own_run_and_no_other(
    db_manager, test_pod, fixed_test_user, async_client
):
    pod_id = test_pod["id"]
    function = await _seed_function(
        db_manager,
        pod_id=pod_id,
        owner_id=fixed_test_user["id"],
        function_type=FunctionType.JOB,
    )
    contact_id = await _seed_contact(db_manager, pod_id=pod_id)
    run_id = await _seed_run(db_manager, function, contact_id, running=True)
    other_run = await _seed_run(db_manager, function, contact_id, running=True)
    _claims, headers = _token(function, run_id, contact_id)
    terminal = {
        "status": "completed",
        "output_data": {"booked": True},
        "error": None,
        "stdout": "",
        "stderr": "",
    }

    stray = await async_client.post(
        f"/internal/function-runtime/runs/{other_run}:terminal",
        json=terminal,
        headers=headers,
    )
    assert stray.status_code == status.HTTP_401_UNAUTHORIZED

    reported = await async_client.post(
        f"/internal/function-runtime/runs/{run_id}:terminal",
        json=terminal,
        headers=headers,
    )
    assert reported.status_code == status.HTTP_200_OK, reported.text
    async with db_manager.session_factory() as session:
        run = await session.get(FunctionRunModel, run_id)
    assert run is not None and run.status == FunctionRunStatus.COMPLETED


async def test_only_the_owner_or_a_pod_admin_opens_a_function_to_contacts(
    db_manager,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    authenticated_client,
    async_client,
):
    pod_id = test_pod["id"]
    editor = await signup_user(async_client, "contacts-editor")
    org_member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=editor
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_EDITOR",
    )
    owners = await _seed_function(
        db_manager,
        pod_id=pod_id,
        owner_id=fixed_test_user["id"],
        contacts_invoke=False,
    )
    editors = await _seed_function(
        db_manager, pod_id=pod_id, owner_id=editor["id"], contacts_invoke=False
    )
    no_contact_input = await _seed_function(
        db_manager,
        pod_id=pod_id,
        owner_id=fixed_test_user["id"],
        contacts_invoke=False,
        input_schema={"type": "object", "properties": {"subject": {}}},
    )

    def opening(name: str) -> str:
        return f"/pods/{pod_id}/functions/{name}/contacts"

    colleague = await async_client.put(
        opening(owners.name),
        json={"contacts_invoke": True},
        headers=auth_headers(editor),
    )
    assert colleague.status_code == status.HTTP_403_FORBIDDEN, colleague.text

    own = await async_client.put(
        opening(editors.name),
        json={"contacts_invoke": True},
        headers=auth_headers(editor),
    )
    assert own.status_code == status.HTTP_200_OK, own.text

    undeclared = await authenticated_client.put(
        opening(no_contact_input.name), json={"contacts_invoke": True}
    )
    assert undeclared.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert undeclared.json()["code"] == "FUNCTION_CONTACT_INPUT_REQUIRED"

    admin = await authenticated_client.put(
        opening(owners.name), json={"contacts_invoke": True}
    )
    assert admin.status_code == status.HTTP_200_OK, admin.text
    async with db_manager.session_factory() as session:
        opened = await session.get(FunctionModel, owners.id)
        refused = await session.get(FunctionModel, no_contact_input.id)
    assert opened is not None and opened.contacts_invoke is True
    assert refused is not None and refused.contacts_invoke is False
