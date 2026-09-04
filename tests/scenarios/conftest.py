"""Fixtures for the product scenario suite.

One session-scoped stack, one ``world`` per scenario. See ``harness/stack.py``
for what "stack" means and ``harness/world.py`` for why scenarios go through a
world rather than touching it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from functools import partial

import pytest
import pytest_asyncio

from harness import environment, run as run_scope
from harness.egress import Egress
from harness.environment import Deployment
from harness.provider_view import ProviderView
from harness.provision import provision, sweep
from harness.run import Run
from harness.stack import Stack, start_stack
from harness.world import Sessions, World

pytest_plugins = ["harness.reporting"]


#: A cleanup that never finishes is worse than one that gives up: it hangs the
#: session *after* every test has already passed, so the run reports nothing at
#: all and looks like an infrastructure fault. Bounded, and it says when it ran
#: out — whatever it did not reach, `make scenarios-reset` will.
SWEEP_BUDGET = 120.0


def await_sweep(base_url: str) -> str:
    """Run the sweep from a synchronous fixture teardown, under a time budget."""

    async def bounded() -> str:
        try:
            return await asyncio.wait_for(sweep(base_url), timeout=SWEEP_BUDGET)
        except TimeoutError:
            return (
                f"sweep gave up after {SWEEP_BUDGET:.0f}s and left some of this "
                f"run's resources behind. The run itself is unaffected — every "
                f"test had already finished. Run `make scenarios-reset` against "
                f"this target to clear them."
            )

    return asyncio.run(bounded())


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--base-url",
        action="store",
        default=None,
        help=(
            "Run against an already-running Lemma instead of booting one. "
            "Useful for iterating on a scenario, and for pointing the suite at "
            "a deployed environment."
        ),
    )


@pytest.fixture(scope="session")
def stack(request: pytest.FixtureRequest) -> Iterator[Stack]:
    existing = request.config.getoption("--base-url")
    if existing:
        # Someone else owns the lifecycle; boot nothing and tear nothing down.
        yield Stack(
            base_url=existing.rstrip("/"),
            redis_url="",
            database_url="",
            ours=False,
        )
        return
    yield from start_stack()


@pytest.fixture(scope="session")
def target(stack: Stack) -> Deployment:
    """What the system under test is, asked of it rather than assumed.

    Every scenario decides from this whether it can prove anything here — is a
    real model answering, may this run sign anyone up. Asked once, because it is
    configuration and configuration does not move under a run.

    It also vets the target before a single write. A stack this process booted is
    disposable by construction; a deployment is somebody's, and the suite has no
    way to give back an organization it creates there.
    """
    described = environment.describe(stack.base_url)
    environment.confirm_writable(described)
    return described


@pytest.fixture(scope="session")
def run() -> Run:
    """This pass of the suite, so everything it makes says which run made it.

    The tenant is shared with every run before and after this one, so a name is
    only useful if it carries that. See `harness/run.py`.
    """
    return run_scope.begins()


@pytest.fixture(scope="session")
def sessions(stack: Stack, target: Deployment) -> Iterator[Sessions]:
    """The standing cast's sessions, opened once and used all run.

    Carries the means to build the tenant, but only for a stack this process
    booted: one of those starts empty, so somebody has to. It is built the first
    time a scenario asks for the cast rather than at session start, so a run
    that never asks pays nothing for it.

    A deployment is the opposite case. Its tenant was provisioned deliberately,
    by a person who could see what it did — and a run that quietly registered
    accounts there would be doing the one thing this whole design is arranged to
    avoid. So it does not, and `Sessions.company_of` says what to run instead.
    """
    open_sessions = Sessions(
        build_tenant=partial(provision, stack.base_url) if stack.ours else None
    )
    yield open_sessions
    # Sweep only what this run made, and only if it made anything. A standing
    # pod that keeps every run's leavings does not just get untidy: an
    # unprocessable document retries for as long as it exists, so a few runs'
    # worth of them starve document work for everything else in that pod, and
    # the run that finally notices is the one that looks broken.
    if open_sessions.tokens:
        try:
            print("\n" + await_sweep(stack.base_url))
        except Exception as failed:  # noqa: BLE001 — a failed sweep must not fail the run
            print(f"\ncould not sweep the tenant: {failed}")


@pytest_asyncio.fixture
async def world(
    stack: Stack, target: Deployment, sessions: Sessions
) -> AsyncIterator[World]:
    """A fresh world for one scenario, on the shared stack."""
    world = World(base_url=stack.base_url, sessions=sessions)
    try:
        yield world
    finally:
        await world.aclose()


@pytest_asyncio.fixture
async def a_pod_of_its_own(world: World, run: Run) -> AsyncIterator[tuple]:
    """A pod this run makes, and removes when the scenario is done.

    For the scenarios that deliberately leave a pod in a state: a burst that
    fills the staging pool, a document no converter can read and that therefore
    retries for as long as it exists. Those belong in a pod of their own, and
    not for tidiness — document work queues per pod, so one stuck file makes
    every later document scenario in that pod look broken. It cost an afternoon
    to find that out, which is why it is written down here.

    Everything else should use a standing pod through `works_in`. A pod per
    scenario is what this suite is moving away from.
    """
    daniel = await world.person("daniel")
    pod = await daniel.creates_a_pod(named=run.name("scratch"))
    try:
        yield daniel, pod
    finally:
        await daniel.deletes_pod(pod)


@pytest_asyncio.fixture
async def egress(stack: Stack) -> AsyncIterator[Egress]:
    """What Lemma said to the outside world, during this scenario only.

    Forgotten between scenarios, and for the same reason the old per-scenario
    recorders were: a scenario asserting "the agent replied once" has to be
    asking about *its* traffic. Shared, it would be asking about whatever ran
    before it, which is a test that passes for the wrong reason on a good day
    and flakes on a bad one.
    """
    live = stack.egress
    # `is None` alone is not the question. A run with SCENARIOS_EGRESS=off
    # against a stack it booted still has an Egress — in mode `off`, serving
    # nothing — so scenarios needing a stand-in sailed past this skip and
    # failed later against a proxy that was never going to answer. Asking
    # whether anything is actually standing in covers both, and makes a local
    # `off` run a faithful rehearsal of a deployment rather than a worse one.
    if live is None or getattr(live, "mode", "off") not in {"fake", "replay"}:
        # A deployment run owns no proxy, so nothing stands in for Telegram or
        # the connector provider — and a scenario that needs one has nothing to
        # talk to. A skip, not an error: `LOOPBACK_REACHABLE` used to say this
        # before the stand-ins were retired, and deleting it took the sentence
        # with it. Fifty-two scenarios turned from "skipped, and here is why"
        # into a stack trace on every deployment run.
        pytest.skip(
            "no egress proxy: this run targets a deployment the suite does not "
            "own, so nothing stands in for Telegram or the connector provider. "
            "Run without --base-url to get these scenarios."
        )
    live.forget()
    yield live


@pytest.fixture
def provider(egress) -> ProviderView:
    """A third-party HTTP API a connector can be pointed at.

    Served by the proxy, at a reserved hostname the product connects to as it
    would any other. Nothing is started here — the `egress` fixture has already
    forgotten the previous scenario's traffic, which is what keeps "exactly
    which calls arrived" a per-scenario question.
    """
    return ProviderView(egress)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Do not *ask* a deployment a question only a broken one can answer.

    Three scenarios need the target configured to be missing something — no
    document converter, no search provider, an organization capped at zero
    spend. Each proves a real promise about how the product behaves when a
    dependency is absent, and each runs for real in the fast lane, where
    `harness/stack.py` boots precisely that deployment on purpose. Two of them
    are the only place their promise is proved anywhere in the repository.

    Against somebody else's Lemma they cannot run, and should not: a healthy
    deployment is not in the state under test, and nobody is going to break dev
    so a scenario can watch. They used to report as skips there, which is the
    wrong word — a skip says "this could have run and did not", and it put three
    permanent entries on a list whose whole value is that somebody reads it.

    So they are deselected instead. `--base-url` is the question being asked:
    with one, the lifecycle belongs to somebody else and the suite cannot decide
    how the target is configured. Without one it booted the target itself and
    knows exactly how, because it chose.
    """
    if not config.getoption("--base-url"):
        return
    kept, dropped = [], []
    for item in items:
        (dropped if item.get_closest_marker("stack_lane") else kept).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
