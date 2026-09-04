"""Connectors and accounts → when the third party is the thing that is broken.

A connector puts somebody else's server on the critical path of a pod. That
server will be down, and it will be slow, and neither is a reason for the pod to
be down or slow with it. The provider here really returns 500 and really hangs,
so what is being tested is Lemma's behaviour rather than a description of it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.waiting import UNTIL_A_RUN_SETTLES

pytestmark = [
    journey("Connectors and accounts"),
    capability("Use a connector"),
]

#: Lemma normalises an operationId into a readable name, so the provider's
#: `brokenOperation` is reached as `broken_operation`.
BROKEN = "broken_operation"
SLOW = "slow_operation"


@pytest.fixture
async def connected(world, provider):
    alice = await world.person("priya")
    organization = alice.organization
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url,
        spec_url=provider.spec_url,
    )
    await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        credentials={"access_token": "alice-provider-token"},
    )
    provider.clear()
    return alice, organization, auth_config


@scenario("A provider that fails is reported as the provider failing")
@proves("PS-CONN-032")
@covers("connector.operation.execute")
async def test_a_failing_provider_is_reported_not_swallowed(connected):
    alice, organization, auth_config = connected

    response = await alice.is_refused_running_operation(
        BROKEN,
        auth_config=auth_config,
        in_organization=organization,
        payload={},
    )

    # A 5xx from someone else's server is not this pod being broken. What
    # matters is that it comes back as an error a person can act on rather than
    # as an empty success — which would have the agent carry on with nothing.
    assert response >= 400, response


@scenario("A pod stays usable while a provider is failing")
@proves("PS-CONN-032")
@covers("connector.operation.execute", "table.create")
async def test_a_failing_provider_does_not_take_the_pod_with_it(connected, run):
    alice, organization, auth_config = connected
    pod = await alice.creates_a_pod(named=run.name("pod"))

    for _ in range(3):
        await alice.is_refused_running_operation(
            BROKEN,
            auth_config=auth_config,
            in_organization=organization,
            payload={},
        )

    # The pod is still a pod. A connector failing repeatedly must not exhaust a
    # connection pool, wedge a worker, or otherwise leak into unrelated work.
    await alice.creates_a_table(in_pod=pod)
    await alice.opens_pod(pod)


@scenario("A provider that hangs is given up on rather than waited on forever")
@proves("PS-CONN-032")
@covers("connector.operation.execute")
async def test_a_hanging_provider_is_given_up_on(connected):
    alice, organization, auth_config = connected

    started = time.monotonic()
    try:
        await asyncio.wait_for(
            alice.api.call(
                "POST",
                f"/organizations/{organization['id']}/connectors/"
                f"{auth_config['name']}/operations/{SLOW}/execute",
                json={"payload": {}},
            ),
            timeout=UNTIL_A_RUN_SETTLES,
        )
    except TimeoutError:  # pragma: no cover - the failure this is looking for
        raise AssertionError(
            "a hanging provider held the request open for 90s; Lemma has no "
            "outbound timeout, so one slow third party can hold connections "
            "open until the pool is gone"
        ) from None

    # The provider sleeps for 30s. Answering at all — with whatever status —
    # means Lemma stopped waiting on its own terms.
    assert time.monotonic() - started < 90.0
