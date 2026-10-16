"""The after-commit analytics sink emits what the eleven consumer groups emitted.

``analytics_parity_golden.json`` was recorded from the FastStream
``analytics-*`` consumers (each driven with the outbox payload of a committed
scenario event, through a pass-through inbox) immediately before they were
replaced. Here the same scenario commits through a real unit of work and the
sink projects it: the captured analytics -- names, actors, properties, groups,
in order -- must be identical. The rows the projections read back (pod creator,
delivery marker, organization member count, conversation scope) are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from app.core.analytics import MemorySink, configure
from app.core.analytics.domain_events import domain_event_sink
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.registry.assembly import register_module_work
from app.core.tests.e2e.analytics_parity_scenario import (
    ScenarioRows,
    normalise,
    scenario_events,
)
from app.modules.analytics.module import module as analytics_module
from app.modules.test_support.e2e import fixtures as e2e_fixtures

postgres_container = e2e_fixtures.postgres_container
redis_container = e2e_fixtures.redis_container
supertokens_container = e2e_fixtures.supertokens_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
db_session = e2e_fixtures.db_session
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend
worker = e2e_fixtures.worker
test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
e2e_process_clients = e2e_fixtures.e2e_process_clients
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org

pytestmark = [pytest.mark.e2e]

GOLDEN = Path(__file__).with_name("analytics_parity_golden.json")


async def _rows(client, org: dict, user: dict) -> ScenarioRows:
    response = await client.post(
        "/pods",
        json={"name": "parity", "type": "ASSISTANT", "organization_id": org["id"]},
    )
    assert response.status_code == 201, response.text
    pod_id = response.json()["id"]
    response = await client.post(f"/pods/{pod_id}/conversations", json={})
    assert response.status_code in (200, 201), response.text
    return ScenarioRows(
        pod_id=UUID(pod_id),
        organization_id=UUID(org["id"]),
        user_id=UUID(str(user["id"])),
        conversation_id=UUID(response.json()["id"]),
    )


async def test_the_sink_emits_what_the_consumer_groups_emitted(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    register_module_work([analytics_module])
    rows = await _rows(authenticated_client, fixed_test_org, fixed_test_user)
    sink = domain_event_sink()
    await sink.flush()  # anything the setup committed is not this scenario's
    captured = MemorySink()
    configure(captured, deployment="parity")
    try:
        async with async_session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            uow.collect_events(scenario_events(rows))
            await uow.commit()
        await sink.flush()
    finally:
        configure(None)

    expected = json.loads(GOLDEN.read_text())
    assert normalise(captured.events, rows) == expected


async def test_a_rolled_back_unit_of_work_offers_nothing(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    register_module_work([analytics_module])
    rows = await _rows(authenticated_client, fixed_test_org, fixed_test_user)
    sink = domain_event_sink()
    await sink.flush()
    captured = MemorySink()
    configure(captured, deployment="parity")
    try:
        async with async_session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            uow.collect_events(scenario_events(rows))
            await uow.rollback()
        await sink.flush()
    finally:
        configure(None)
    assert captured.events == []
