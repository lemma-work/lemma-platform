"""A GitHub delivery, through the real route, to a schedule run.

This is the loop the connector was named for and never had: `POST
/webhooks/github` refused every source but `composio`, so no GitHub event could
reach a schedule at all. Each test here is one hop of that loop, asserted at the
database rather than at a mock.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.webhook_sources.github import source_event_id
from app.modules.connectors.config import connector_settings
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_pod,
    _create_schedule,
    _create_workflow,
    _seed_connector_trigger,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

WEBHOOK_SECRET = "an-e2e-webhook-secret"
INSTALLATION_ID = "158040062"
REPOSITORY_ID = 1296269


@pytest.fixture(autouse=True)
def _github_webhook_secret(monkeypatch):
    monkeypatch.setattr(
        connector_settings, "connector_github_app_webhook_secret", WEBHOOK_SECRET
    )
    monkeypatch.setattr(
        connector_settings,
        "connector_github_app_webhook_secret_previous",
        None,
        raising=False,
    )


def _payload(
    *,
    action: str = "opened",
    installation_id: str = INSTALLATION_ID,
    head_sha: str = "d0e1f2a",
) -> dict:
    return {
        "action": action,
        "number": 42,
        "pull_request": {
            "id": 279147437,
            "number": 42,
            "title": "Teach the parser about trailing commas",
            "head": {"sha": head_sha, "ref": "feature/commas"},
            "base": {"ref": "main"},
        },
        "repository": {
            "id": REPOSITORY_ID,
            "name": "api",
            "full_name": "octo/api",
            "owner": {"login": "octo"},
            "default_branch": "main",
        },
        "installation": {"id": int(installation_id)},
    }


async def _deliver(
    client: AsyncClient,
    payload: dict,
    *,
    event: str = "pull_request",
    secret: str = WEBHOOK_SECRET,
    delivery_id: str = "72d3162e-cc78-11e3-81ab-4c9367dc0958",
):
    # Signed over the exact bytes sent, which is why the body is serialized once
    # and passed as `content`: `json=` would re-serialize and the digest would
    # not match what arrives.
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return await client.post(
        "/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery_id,
        },
    )


async def _runs_for(db_session: AsyncSession, schedule_id: str) -> list[ScheduleRun]:
    db_session.expire_all()
    result = await db_session.execute(
        select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id)
    )
    return list(result.scalars().all())


async def _wait_for_run_count(
    db_session: AsyncSession, schedule_id: str, *, count: int
) -> list[ScheduleRun]:
    return await eventually(
        label=f"{count} schedule runs",
        probe=lambda: _runs_for(db_session, schedule_id),
        done=lambda runs: len(runs) == count,
        timeout_seconds=30,
        interval_seconds=0.15,
    )


TRIGGER_ID = "github:http:pull_request"


async def _github_schedule(
    client: AsyncClient, db_session: AsyncSession, org_id: str, *, config: dict
) -> tuple[str, str]:
    await _seed_connector_trigger(
        db_session,
        connector_id="github",
        trigger_id=TRIGGER_ID,
        event_type="pull_request",
    )
    pod_id = await _create_pod(client, org_id)
    workflow = await _create_workflow(
        client,
        pod_id,
        start={
            "type": "EVENT",
            "config": {
                "connector_id": "github",
                "connector_trigger_id": TRIGGER_ID,
                "trigger_config": {"source": "github"},
            },
        },
        name_prefix="github-webhook",
    )
    schedule = await _create_schedule(
        client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config=config,
    )
    return pod_id, schedule["id"]


@pytest.mark.asyncio
async def test_a_signed_pull_request_delivery_starts_one_run(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession, worker
):
    _ = worker
    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
        },
    )

    response = await _deliver(authenticated_client, _payload())
    assert response.status_code == 200, response.text

    runs = await _wait_for_run_count(db_session, schedule_id, count=1)
    assert runs[0].source_event_id


@pytest.mark.asyncio
async def test_a_redelivery_does_not_run_the_schedule_twice(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession, worker
):
    """GitHub reissues the delivery id, so it cannot be the idempotency key.

    A redelivery from the App's advanced tab -- or GitHub's own retry after a
    timeout -- is the same event happening once.
    """
    _ = worker
    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
        },
    )
    payload = _payload()

    first = await _deliver(authenticated_client, payload, delivery_id="delivery-one")
    assert first.status_code == 200
    await _wait_for_run_count(db_session, schedule_id, count=1)

    second = await _deliver(authenticated_client, payload, delivery_id="delivery-two")
    assert second.status_code == 200, second.text

    # A genuinely new event, delivered after the redelivery. Two things make
    # this the sound way to write the assertion. The run row is written by the
    # worker off the outbox rather than inline in the request, so reading the
    # count straight after the redelivery would pass whether or not anything was
    # deduplicated -- the extra run simply would not exist yet. And a waiter
    # that stops at "two runs" is satisfied by a third still in flight.
    #
    # So: wait for the id this new event *must* produce, then assert the set of
    # ids is exactly the two expected. Both are computed here from the payloads,
    # which is also what pins them to the event rather than to the delivery -- a
    # `source_event_id` taken from `X-GitHub-Delivery` matches neither.
    later = _payload(head_sha="9f8e7d6")
    third = await _deliver(authenticated_client, later, delivery_id="delivery-three")
    assert third.status_code == 200, third.text

    expected = {
        source_event_id("pull_request", INSTALLATION_ID, REPOSITORY_ID, payload),
        source_event_id("pull_request", INSTALLATION_ID, REPOSITORY_ID, later),
    }
    runs = await eventually(
        label="the later pull_request event produced its own run",
        probe=lambda: _runs_for(db_session, schedule_id),
        done=lambda rs: expected <= {run.source_event_id for run in rs},
        timeout_seconds=30,
        interval_seconds=0.15,
    )
    assert {run.source_event_id for run in runs} == expected, (
        "the redelivery started a run of its own"
    )


@pytest.mark.asyncio
async def test_an_unsigned_delivery_is_refused_and_stores_nothing(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession
):
    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
        },
    )

    response = await _deliver(authenticated_client, _payload(), secret="not-the-secret")
    assert response.status_code == 403
    assert await _runs_for(db_session, schedule_id) == []


@pytest.mark.asyncio
async def test_another_installations_events_do_not_match(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession
):
    """The routing key is tenant-scoped, and this is why.

    One App serves every organization that installed it and they all arrive at
    one URL, so `{source, event}` alone would run this schedule on a stranger's
    pull requests.
    """
    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
        },
    )

    response = await _deliver(
        authenticated_client, _payload(installation_id="99999999")
    )
    # Accepted -- it is a real delivery -- but it must match nothing.
    assert response.status_code == 200, response.text

    # Runs are written asynchronously off the outbox, so an empty read here
    # would pass whether or not the stranger's event matched. A delivery that
    # *must* produce a run gives the first one time to produce one too, and the
    # set equality is then what says it did not.
    ours = _payload()
    mine = await _deliver(authenticated_client, ours, delivery_id="delivery-ours")
    assert mine.status_code == 200, mine.text

    expected = {source_event_id("pull_request", INSTALLATION_ID, REPOSITORY_ID, ours)}
    runs = await eventually(
        label="our own installation's event produced a run",
        probe=lambda: _runs_for(db_session, schedule_id),
        done=lambda rs: expected <= {run.source_event_id for run in rs},
        timeout_seconds=30,
        interval_seconds=0.15,
    )
    assert {run.source_event_id for run in runs} == expected, (
        "another installation's pull request started a run here"
    )


@pytest.mark.asyncio
async def test_a_schedule_scoped_to_actions_ignores_the_others(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession, worker
):
    """`actions` cannot live in the routing key.

    Containment runs `config @> criteria`, so every key in the criteria must be
    in every schedule that could match -- which makes an optional key
    impossible to express there. It is a second pass instead.
    """
    _ = worker
    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
            "actions": ["closed"],
        },
    )

    opened = await _deliver(authenticated_client, _payload(action="opened"))
    assert opened.status_code == 200

    closed_payload = _payload(action="closed")
    closed = await _deliver(
        authenticated_client, closed_payload, delivery_id="delivery-closed"
    )
    assert closed.status_code == 200, closed.text

    # Same reasoning as the installation test above: wait for the run the
    # `closed` delivery must produce, then assert it is the only one.
    expected = {
        source_event_id("pull_request", INSTALLATION_ID, REPOSITORY_ID, closed_payload)
    }
    runs = await eventually(
        label="the closed pull_request produced a run",
        probe=lambda: _runs_for(db_session, schedule_id),
        done=lambda rs: expected <= {run.source_event_id for run in rs},
        timeout_seconds=30,
        interval_seconds=0.15,
    )
    assert {run.source_event_id for run in runs} == expected, (
        "an action the schedule did not ask for started a run"
    )


@pytest.mark.asyncio
async def test_an_event_nobody_subscribed_to_is_acknowledged(
    authenticated_client: AsyncClient, fixed_test_org
):
    """An App subscribes to events at the App level, so unwanted ones arrive.

    A non-2xx answer to those has GitHub disable the hook for the events that
    do matter.
    """
    response = await _deliver(
        authenticated_client, {"installation": {"id": 1}}, event="star"
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_an_unknown_source_is_still_refused(authenticated_client: AsyncClient):
    """The registry is the allow-list; absence is a refusal."""
    response = await authenticated_client.post("/webhooks/jira", json={"x": 1})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_uninstalling_the_app_stands_its_schedules_down(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession
):
    """An uninstall invalidates everything at once, and silently.

    The installation stops existing, so no token can be minted and no delivery
    will ever arrive again. Left alone the schedule stays active and simply
    never fires, which looks exactly like an agent with nothing to do.
    """
    from sqlalchemy import select

    from app.modules.schedule.infrastructure.models.schedule import Schedule

    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
        },
    )

    response = await _deliver(
        authenticated_client,
        {"action": "deleted", "installation": {"id": int(INSTALLATION_ID)}},
        event="installation",
        delivery_id="delivery-uninstall",
    )
    # Acknowledged: it already happened, and a non-2xx only makes GitHub send
    # it again.
    assert response.status_code == 200, response.text

    db_session.expire_all()
    row = (
        await db_session.execute(select(Schedule).where(Schedule.id == schedule_id))
    ).scalar_one()
    assert row.is_active is False
    # Why it stopped, not merely that it did.
    assert row.config["deactivated_reason"] == "github_installation_deleted"
    # The routing key survives, so reconnecting and reactivating is enough.
    assert row.config["installation_id"] == INSTALLATION_ID


@pytest.mark.asyncio
async def test_another_installations_uninstall_leaves_this_one_alone(
    authenticated_client: AsyncClient, fixed_test_org, db_session: AsyncSession
):
    from sqlalchemy import select

    from app.modules.schedule.infrastructure.models.schedule import Schedule

    _, schedule_id = await _github_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        config={
            "source": "github",
            "installation_id": INSTALLATION_ID,
            "event": "pull_request",
        },
    )

    response = await _deliver(
        authenticated_client,
        {"action": "deleted", "installation": {"id": 99999999}},
        event="installation",
        delivery_id="delivery-other-uninstall",
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    row = (
        await db_session.execute(select(Schedule).where(Schedule.id == schedule_id))
    ).scalar_one()
    assert row.is_active is True
