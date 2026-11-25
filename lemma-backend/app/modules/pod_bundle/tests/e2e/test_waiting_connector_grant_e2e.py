"""A grant on a connector the import left out waits for it, and lands.

The bundle's agent is granted ``connector.use`` on ``calendar``, which the
bundle marks optional. The importing organization has never installed that
catalog connector, so the import goes ahead without it. It used to drop the
grant, so when somebody installed the connector and the pod declared it, the
agent was refused it until the bundle was imported again. The grant now waits,
and is in place on whatever the pod's connector is called once it has one.

The worker, the database, the import job and the connectors module are real;
the catalog entry and the installation are rows, as the catalog import and the
Connectors page would have left them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import status
from lemma_pod_bundle import pack_bundle
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import ResourceType
from app.core.authorization.pending_grants import pending_lock_name

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    start_and_plan_import,
    wait_import,
)
from app.modules.pod_bundle.tests.e2e.test_connector_bundle_e2e import (
    _catalog_connector,
    _install,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _write(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, connector_id: str) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    _write(root / "pod.json", {"name": "Meetings", "format_version": 2})
    _write(
        root / "connectors" / "calendar" / "calendar.json",
        {"connector": connector_id, "mode": "PER_MEMBER", "required": False},
    )
    _write(
        root / "agents" / "briefer" / "briefer.json",
        {
            "name": "briefer",
            "instruction": "Brief me on my next meeting.",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "calendar",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        },
    )
    return pack_bundle(root)


async def _blocked_behind(
    step: AsyncSession, task: asyncio.Task[object], *, lock_name: str, label: str
) -> None:
    """Wait until Postgres shows a backend waiting on exactly ``lock_name``'s
    advisory lock and blocked by ``step``'s backend -- the declaration ``task``
    stuck behind the step. Fails at once if ``task`` finishes instead, which
    means nothing held it back, and then cancels and drains it."""
    from sqlalchemy import text

    from app.core.infrastructure.db.session import async_session_maker
    from app.modules.test_support.e2e.waiters import eventually

    step_pid = await step.scalar(text("SELECT pg_backend_pid()"))
    hashed = await step.scalar(
        text("SELECT hashtextextended(:name, 0)"), {"name": lock_name}
    )
    unsigned = int(hashed) & 0xFFFF_FFFF_FFFF_FFFF

    async def waiting() -> int:
        async with async_session_maker() as probe:
            count = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_locks l "
                    "WHERE l.locktype = 'advisory' AND NOT l.granted "
                    "AND l.classid::bigint = :high AND l.objid::bigint = :low "
                    "AND :step = ANY(pg_blocking_pids(l.pid))"
                ),
                {
                    "high": unsigned >> 32,
                    "low": unsigned & 0xFFFF_FFFF,
                    "step": step_pid,
                },
            )
        return int(count or 0)

    blocked = False
    try:
        await eventually(
            label=label,
            probe=waiting,
            done=lambda count: count > 0,
            timeout_seconds=10,
            fail_fast=lambda _count: (
                "it finished without waiting: nothing held the lock"
                if task.done()
                else None
            ),
        )
        blocked = True
    finally:
        if not blocked:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _connector_grants(client, pod_id: str) -> list[dict[str, object]]:
    got = await client.get(f"/pods/{pod_id}/agents/briefer/permissions")
    assert got.status_code == status.HTTP_200_OK, got.text
    return [g for g in got.json()["grants"] if g["resource_type"] == "connector"]


async def test_a_left_out_connectors_grant_lands_when_the_pod_declares_it(
    scenario, db_session, worker, tmp_path
):
    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Waiting")
    client, pod_id = scenario.owner_client, scenario.pod_id

    import_id = await start_and_plan_import(
        client, pod_id, _bundle(tmp_path, connector_id)
    )
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})

    assert final["status"] == "COMPLETED", final
    assert any("'calendar' waits for it" in w for w in final["warnings"]), final
    assert await _connector_grants(client, pod_id) == []

    # Somebody installs it and the pod declares it -- under a slug of its
    # own, not the bundle's -- with nothing imported again.
    install_id = await _install(db_session, scenario.org_id, connector_id)
    declared = await client.post(
        f"/pods/{pod_id}/connectors",
        json={"auth_config_id": install_id, "slug": "my-cal", "mode": "PER_MEMBER"},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text

    grants = await _connector_grants(client, pod_id)
    assert [(g["resource_name"], g["permission_ids"]) for g in grants] == [
        ("my-cal", ["connector.use"])
    ]

    # Granted once: a second connector of the same kind gets nothing.
    second = await client.post(
        f"/pods/{pod_id}/connectors",
        json={"auth_config_id": install_id, "slug": "cal-2", "mode": "PER_MEMBER"},
    )
    assert second.status_code == status.HTTP_201_CREATED, second.text
    assert [g["resource_name"] for g in await _connector_grants(client, pod_id)] == [
        "my-cal"
    ]


async def test_a_left_out_connector_the_org_has_installed_still_drops_its_grant(
    scenario, db_session, worker, tmp_path
):
    """Leaving out a connector the org has an installation of is the
    importer's choice, and its grant is dropped as they were told."""
    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Chose")
    client, pod_id = scenario.owner_client, scenario.pod_id
    install_id = await _install(db_session, scenario.org_id, connector_id)

    import_id = await start_and_plan_import(
        client, pod_id, _bundle(tmp_path, connector_id)
    )
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"connectors": {"calendar": {"skip": True}}},
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final
    assert any("'calendar' was dropped" in w for w in final["warnings"]), final

    declared = await client.post(
        f"/pods/{pod_id}/connectors",
        json={"auth_config_id": install_id, "slug": "my-cal", "mode": "PER_MEMBER"},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    assert await _connector_grants(client, pod_id) == []


async def test_a_connector_declared_while_the_grant_step_runs_keeps_its_grant(
    scenario, db_session, monkeypatch, tmp_path
):
    """The grant step resolves (no installation, so the grant waits), and a
    connector of that kind is declared before it holds. Holding sees the
    connector and grants on it -- after the step's own replacement of the
    agent's grants, which would otherwise wipe it."""
    from app.core.authorization.service import AuthorizationDataService
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.contracts.pod_connectors import (
        ensure_pod_connector_for_installation,
    )
    from app.modules.pod_bundle.domain.connector_plan import ConnectorChoice
    from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
    from app.modules.pod_bundle.infrastructure import applier as applier_module
    from app.modules.pod_bundle.infrastructure.kept_parts import CreatedParts

    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Raced")
    client, pod_id = scenario.owner_client, UUID(scenario.pod_id)
    created = await client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "briefer", "instruction": "Brief me on my next meeting."},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    install_id = UUID(await _install(db_session, scenario.org_id, connector_id))
    root = tmp_path / "raced"
    _write(
        root / "connectors" / "calendar" / "calendar.json",
        {"connector": connector_id, "mode": "PER_MEMBER", "required": False},
    )
    _write(
        root / "agents" / "briefer" / "briefer.json",
        {
            "name": "briefer",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "calendar",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        },
    )

    uow = SqlAlchemyUnitOfWork(db_session)
    user_id = UUID(scenario.owner_user["id"])
    ctx = await AuthorizationDataService(db_session).build_user_context(
        user_id=user_id, pod_id=pod_id
    )

    async def declared_meanwhile(_uow, *, organization_id, name):
        # The grant step asks whether the organization has an installation:
        # it has none yet -- and a connector of that kind is declared before
        # the step holds the grant.
        await ensure_pod_connector_for_installation(
            uow, ctx, pod_id=pod_id, auth_config_id=install_id
        )
        return

    monkeypatch.setattr(
        "app.modules.connectors.contracts.provisioning.installation_for_bundle_name",
        declared_meanwhile,
    )
    made = CreatedParts()
    made.add(StepKind.AGENT, "briefer")
    applier = applier_module.BundleApplier(
        uow=uow,
        ctx=ctx,
        pod_id=pod_id,
        user_id=user_id,
        bundle_root=root,
        connector_choices={"calendar": ConnectorChoice(skip=True)},
        created=made,
    )
    await applier.apply_step(
        PlanStep(
            index=0,
            kind=StepKind.AGENT_GRANTS,
            name="briefer",
            action=StepAction.CREATE,
        )
    )
    await db_session.commit()

    grants = await _connector_grants(client, str(pod_id))
    assert [g["permission_ids"] for g in grants] == [["connector.use"]], grants


async def test_a_restricted_connector_made_pod_wide_gets_the_waiting_grant(
    scenario, db_session, worker, tmp_path
):
    """A held grant is bounded by what its importer could use, which a
    RESTRICTED connector is not: declared restricted it waits on, and made
    pod-wide later it lands."""
    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Restricted")
    client, pod_id = scenario.owner_client, scenario.pod_id
    import_id = await start_and_plan_import(
        client, pod_id, _bundle(tmp_path, connector_id)
    )
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    install_id = await _install(db_session, scenario.org_id, connector_id)
    declared = await client.post(
        f"/pods/{pod_id}/connectors",
        json={
            "auth_config_id": install_id,
            "slug": "my-cal",
            "mode": "PER_MEMBER",
            "visibility": "RESTRICTED",
        },
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    assert await _connector_grants(client, pod_id) == []

    widened = await client.patch(
        f"/pods/{pod_id}/connectors/my-cal", json={"visibility": "POD"}
    )
    assert widened.status_code == status.HTTP_200_OK, widened.text
    grants = await _connector_grants(client, pod_id)
    assert [(g["resource_name"], g["permission_ids"]) for g in grants] == [
        ("my-cal", ["connector.use"])
    ]


async def test_a_declaration_racing_the_grant_step_settles_only_after_it(
    scenario, db_session
):
    """The replay race, on two real transactions. The grant step locks what
    the grantee waits for and then meets an authorization check, which
    releases a clean session by committing it. Its locks must survive that:
    a declaration settling the same key in another transaction waits for the
    step, then finds the waiting grant the step removed gone, and grants
    nothing."""
    from types import SimpleNamespace

    from app.core.authorization.pending_grants import (
        PendingGrant,
        delete_grantee_pending_grants,
        replace_grantee_pending_grants,
        settle_pending_grants,
    )
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.transaction_locks import connection_released
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.pod_bundle.infrastructure.connector_grants import (
        lock_waiting_grants,
    )

    await scenario.create_org_with_pod(name_prefix="Replay")
    pod_id = UUID(scenario.pod_id)
    grantee, connector_id = uuid4(), uuid4()
    key = f"replayed-{uuid4().hex[:6]}:PER_MEMBER"
    await replace_grantee_pending_grants(
        db_session,
        ctx=SimpleNamespace(  # type: ignore[arg-type]
            is_superuser=True, is_named_workload=False, permission_ids=frozenset()
        ),
        pod_id=pod_id,
        grantee_type="FUNCTION",
        grantee_id=grantee,
        resource_type=ResourceType.CONNECTOR,
        grants=[PendingGrant(ResourceType.CONNECTOR, key, ("connector.use",))],
        created_by_user_id=None,
    )
    await db_session.commit()

    async with async_session_maker() as step, async_session_maker() as declaring:
        await lock_waiting_grants(
            SqlAlchemyUnitOfWork(step),
            pod_id=pod_id,
            grantee_type="FUNCTION",
            grantee_id=grantee,
        )
        # What an uncached authorization check does to a clean session.
        async with connection_released(step):
            pass
        settling = asyncio.create_task(
            settle_pending_grants(
                declaring,
                pod_id=pod_id,
                resource_type=ResourceType.CONNECTOR,
                resource_key=key,
                resource_id=connector_id,
            )
        )
        await _blocked_behind(
            step,
            settling,
            lock_name=pending_lock_name(pod_id, ResourceType.CONNECTOR, key),
            label="settlement waits for the step",
        )

        await delete_grantee_pending_grants(
            step, pod_id=pod_id, grantee_type="FUNCTION", grantee_id=grantee
        )
        await step.commit()
        assert await settling == 0
        await declaring.commit()


async def test_a_replayed_grant_step_leaves_no_removed_grant_active(
    scenario, db_session, monkeypatch, tmp_path
):
    """The replay race through the grant step itself. A first attempt held
    the agent's calendar grant; the organization has since installed the
    calendar, so the replay drops it. Meanwhile somebody declares the
    connector in another transaction. That declaration must wait for the
    step -- past its authorization checks -- and then find nothing to grant,
    so the grant the replay removed never becomes active."""

    from app.core.authorization.context import ResourceRef
    from app.core.authorization.permissions import Permissions
    from app.core.authorization.service import AuthorizationDataService
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.contracts.bundles import (
        WaitingConnectorGrant,
        hold_grants_until_declared,
    )
    from app.modules.connectors.contracts.pod_connectors import (
        ensure_pod_connector_for_installation,
    )
    from app.modules.pod_bundle.domain.connector_plan import ConnectorChoice
    from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
    from app.modules.pod_bundle.infrastructure.applier import BundleApplier
    from app.modules.pod_bundle.infrastructure.kept_parts import CreatedParts

    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Replayed")
    client, pod_id = scenario.owner_client, UUID(scenario.pod_id)
    user_id = UUID(scenario.owner_user["id"])
    created = await client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "briefer", "instruction": "Brief me on my next meeting."},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    root = tmp_path / "replayed"
    _write(
        root / "connectors" / "calendar" / "calendar.json",
        {"connector": connector_id, "mode": "PER_MEMBER", "required": False},
    )
    _write(
        root / "agents" / "briefer" / "briefer.json",
        {
            "name": "briefer",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "calendar",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        },
    )
    # What the first attempt held, before the calendar was installed.
    importer = await AuthorizationDataService(db_session).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    await hold_grants_until_declared(
        SqlAlchemyUnitOfWork(db_session),
        importer,
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=UUID(created.json()["id"]),
        grants=[
            WaitingConnectorGrant(
                connector=connector_id,
                mode="PER_MEMBER",
                permission_ids=("connector.use",),
                slug="calendar",
            )
        ],
        created_by_user_id=user_id,
    )
    await db_session.commit()
    install_id = UUID(await _install(db_session, scenario.org_id, connector_id))

    async def declare_elsewhere() -> None:
        async with async_session_maker() as session:
            ctx = await AuthorizationDataService(session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            await ensure_pod_connector_for_installation(
                SqlAlchemyUnitOfWork(session),
                ctx,
                pod_id=pod_id,
                auth_config_id=install_id,
            )
            await session.commit()

    declaring: list[asyncio.Task[None]] = []
    async with async_session_maker() as step:
        ctx = await AuthorizationDataService(step).build_user_context(
            user_id=user_id, pod_id=pod_id
        )

        async def lookup_while_declaring(_uow, *, organization_id, name):
            # Mid-step, after the step locked what the agent waits for: an
            # uncached authorization check on the step's own session, which
            # commits a clean session to give its connection back...
            assert await ctx.can(
                Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id)
            )
            # ...then the organization has the calendar now, so the replay
            # drops the grant, while somebody declares the connector elsewhere.
            declaring.append(asyncio.create_task(declare_elsewhere()))
            await _blocked_behind(
                step,
                declaring[0],
                lock_name=pending_lock_name(
                    pod_id,
                    ResourceType.CONNECTOR,
                    f"{connector_id}:PER_MEMBER",
                ),
                label="the declaration waits for the step",
            )
            return install_id

        monkeypatch.setattr(
            "app.modules.connectors.contracts.provisioning."
            "installation_for_bundle_name",
            lookup_while_declaring,
        )
        made = CreatedParts()
        made.add(StepKind.AGENT, "briefer")
        await BundleApplier(
            uow=SqlAlchemyUnitOfWork(step),
            ctx=ctx,
            pod_id=pod_id,
            user_id=user_id,
            bundle_root=root,
            connector_choices={"calendar": ConnectorChoice(skip=True)},
            created=made,
        ).apply_step(
            PlanStep(
                index=0,
                kind=StepKind.AGENT_GRANTS,
                name="briefer",
                action=StepAction.CREATE,
            )
        )
        await step.commit()
    await declaring[0]

    assert await _connector_grants(client, str(pod_id)) == []
