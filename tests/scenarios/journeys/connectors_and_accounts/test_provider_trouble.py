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

#: What discovery names the provider's two bad operations. An `http`
#: connector names an operation from its `summary`, not its `operationId` —
#: `createWidget` ("Create a widget") is `create_a_widget`. These used to be
#: `broken_operation` and `slow_operation`, which exist nowhere: every scenario
#: below was answered 404 `OPERATION_NOT_FOUND` before any provider was
#: called, and passed, because a 404 is ">= 400" and is also "answering at
#: all". `_the_provider_failed` and the fixture's own check are what stop that
#: happening again.
BROKEN = "an_operation_the_provider_cannot_serve"
SLOW = "an_operation_that_takes_far_too_long"


@pytest.fixture
async def connected(world, provider, run):
    """A pod whose connector reaches the provider as Priya, and nothing else.

    The pod connector is PER_MEMBER and Priya has connected her account, so a
    refusal below can only be the provider's — not "connect your account", not
    "no such connector". That is checked rather than assumed: an error from
    the wrong place would satisfy "is reported as an error" just as well.
    """
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
        credentials=await alice.credential_for(
            auth_config, holding="alice-provider-token"
        ),
    )
    pod = await alice.creates_a_pod(named=run.name("provider"))
    try:
        connector = await alice.declares_pod_connector(
            auth_config, in_pod=pod, slug="flaky"
        )
        offered = {
            str(o["name"]) for o in await alice.operations_through(connector, in_pod=pod)
        }
        assert {BROKEN, SLOW} <= offered, (
            f"the provider's failing operations are not where these scenarios "
            f"look for them; the pod connector offers {sorted(offered)}"
        )
        provider.clear()
        yield alice, pod, connector
    finally:
        await alice.deletes_pod(pod)


def _the_provider_failed(refusal) -> None:
    """The refusal is the provider's failure carried back, not one of ours.

    Every connector refusal Lemma makes about itself — no account, no such
    connector, a disabled installation — would also be ">= 400". Only an
    `OPERATION_EXECUTION_*` code says the call went out and the provider is
    what failed.
    """
    assert refusal.code.startswith("OPERATION_EXECUTION"), (
        f"expected the provider's failure to be reported as such; got {refusal}"
    )


@scenario("A provider that fails is reported as the provider failing")
@proves("PS-CONN-032")
@covers("pod.connector.operation.execute")
async def test_a_failing_provider_is_reported_not_swallowed(connected):
    alice, pod, connector = connected

    refusal = await alice.is_refused_running_operation(
        BROKEN, through=connector, in_pod=pod, payload={}
    )

    # A 5xx from someone else's server is not this pod being broken. What
    # matters is that it comes back as an error a person can act on rather than
    # as an empty success — which would have the agent carry on with nothing.
    assert refusal.status >= 400, refusal
    # Not "the provider was called": the breaker is keyed by organization and
    # outlives a run on a standing stack, so after enough failures Lemma stops
    # asking — and says so with a code of the same family, which is correct.
    _the_provider_failed(refusal)


@scenario("A pod stays usable while a provider is failing")
@proves("PS-CONN-032")
@covers("pod.connector.operation.execute", "table.create")
async def test_a_failing_provider_does_not_take_the_pod_with_it(connected):
    alice, pod, connector = connected

    for _ in range(3):
        _the_provider_failed(
            await alice.is_refused_running_operation(
                BROKEN, through=connector, in_pod=pod, payload={}
            )
        )

    # The pod is still a pod. A connector failing repeatedly must not exhaust a
    # connection pool, wedge a worker, or otherwise leak into unrelated work.
    await alice.creates_a_table(in_pod=pod)
    await alice.opens_pod(pod)


@scenario("A provider that hangs is given up on rather than waited on forever")
@proves("PS-CONN-032")
@covers("pod.connector.operation.execute")
async def test_a_hanging_provider_is_given_up_on(connected):
    alice, pod, connector = connected

    started = time.monotonic()
    try:
        answered = await asyncio.wait_for(
            alice.api.call(
                "POST",
                f"/pods/{pod['id']}/connectors/{connector['slug']}"
                f"/operations/{SLOW}/execute",
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

    waited = time.monotonic() - started
    assert waited < 90.0, waited
    # Not "answered at all": a provider that answers inside Lemma's ceiling
    # gets a 200, and that is Lemma waiting rather than giving up. The provider
    # here hangs past the ceiling, so the answer has to be Lemma's own refusal
    # — a timeout, or the breaker that a run of timeouts opens.
    assert answered.status_code >= 500, (answered.status_code, answered.text[:500])
    assert str(answered.json().get("code", "")).startswith("OPERATION_EXECUTION"), (
        answered.text[:500]
    )
