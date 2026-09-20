"""`RateLimitExceeded` has to survive the trip out of an identity lease.

This is a regression test for a bug that no amount of reading the handler would
have found, because the handler was correct -- it simply never ran. The
exception was a frozen dataclass, and a frozen dataclass generates a
``__setattr__`` that refuses every field including ``__traceback__``. Raising
one inside ``async with identity_lease(...)`` therefore produced
``FrozenInstanceError`` on the way out, and the ``except RateLimitExceeded``
that was supposed to turn it into "too many attempts, try later" was bypassed
entirely. Chat onboarding and the browser sign-in path both sit behind it.

The shape below is the part that matters: a naive ``raise``/``except`` in a
plain function does *not* reproduce it. The traceback assignment that trips the
frozen setattr only happens when the exception is thrown into an async
generator, which is exactly what an ``@asynccontextmanager`` lease is.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

from app.modules.identity.services.auth_abuse import RateLimitExceeded


@asynccontextmanager
async def _lease_shaped_like_identity_lease() -> AsyncIterator[str]:
    """`identity_lease` in miniature: a TaskGroup renewing around a yield."""

    async def _renew() -> None:
        while True:
            await asyncio.sleep(3600)

    try:
        async with asyncio.TaskGroup() as tasks:
            renewal = tasks.create_task(_renew())
            try:
                yield "lease"
            finally:
                renewal.cancel()
    except BaseExceptionGroup as failures:
        if len(failures.exceptions) == 1:
            raise failures.exceptions[0] from failures
        raise


async def test_it_reaches_its_handler_through_a_lease() -> None:
    with pytest.raises(RateLimitExceeded) as raised:
        async with _lease_shaped_like_identity_lease():
            raise RateLimitExceeded(42)
    assert raised.value.retry_after_seconds == 42


async def test_the_retry_hint_survives_the_trip() -> None:
    """The number is the whole payload: without it the caller cannot say when."""
    caught: RateLimitExceeded | None = None
    try:
        async with _lease_shaped_like_identity_lease():
            raise RateLimitExceeded(900)
    except RateLimitExceeded as exc:
        caught = exc
    assert caught is not None and caught.retry_after_seconds == 900


def test_assigning_a_traceback_is_allowed() -> None:
    """The specific thing freezing forbade, asserted directly."""
    error = RateLimitExceeded(1)
    error.__traceback__ = None  # would raise FrozenInstanceError if frozen again
    assert error.retry_after_seconds == 1
