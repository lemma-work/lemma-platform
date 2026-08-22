"""Fixtures for the product scenario suite.

One session-scoped stack, one ``world`` per scenario. See ``harness/stack.py``
for what "stack" means and ``harness/world.py`` for why scenarios go through a
world rather than touching it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from functools import partial

import pytest
import pytest_asyncio

from harness import environment, run as run_scope
from harness.environment import Deployment
from harness.provision import provision
from harness.run import Run
from harness.fake_platform import start_fake_provider
from harness.stack import Stack, start_stack
from harness.world import Sessions, World

pytest_plugins = ["harness.reporting"]


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
def sessions(stack: Stack, target: Deployment) -> Sessions:
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
    return Sessions(
        build_tenant=partial(provision, stack.base_url) if stack.ours else None
    )


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


@pytest.fixture
def provider() -> Iterator[object]:
    """A third-party HTTP API a connector can be pointed at.

    Per-scenario rather than session-scoped: scenarios assert on exactly which
    calls arrived, and a shared recorder would make that depend on what ran
    before it.
    """
    fake = start_fake_provider()
    try:
        yield fake
    finally:
        fake.stop()
