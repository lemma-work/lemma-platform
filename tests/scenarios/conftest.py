"""Fixtures for the product scenario suite.

One session-scoped stack, one ``world`` per scenario. See ``harness/stack.py``
for what "stack" means and ``harness/world.py`` for why scenarios go through a
world rather than touching it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from harness.stack import Stack, start_stack
from harness.world import World

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
        yield Stack(base_url=existing.rstrip("/"), redis_url="", database_url="")
        return
    yield from start_stack()


@pytest_asyncio.fixture
async def world(stack: Stack) -> AsyncIterator[World]:
    """A fresh world for one scenario, on the shared stack."""
    world = World(base_url=stack.base_url)
    try:
        yield world
    finally:
        await world.aclose()
