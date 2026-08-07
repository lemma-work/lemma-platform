"""Task spawning for the sandbox runtimes.

These two were the only part of the manager's observability module the runtimes
ever used. They live here now so what ships inside a sandbox image does not
depend on 566 lines of manager logging that image never loaded.

The distinction between them is the whole point: a background task must not
inherit the contextvars of whatever request happened to spawn it, or a
long-lived worker keeps that request's correlation id for its entire life. An
inherited task should keep them, because it is doing the caller's work.
"""

from __future__ import annotations

import asyncio
from contextvars import Context
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def create_background_task(
    coroutine: Coroutine[Any, Any, T], *, name: str | None = None
) -> asyncio.Task[T]:
    """Spawn a task with a fresh context, detached from its spawner's."""

    return asyncio.create_task(coroutine, name=name, context=Context())


def create_inherited_task(
    coroutine: Coroutine[Any, Any, T], *, name: str | None = None
) -> asyncio.Task[T]:
    """Spawn a task that keeps the spawner's context, because it is its work."""

    return asyncio.create_task(coroutine, name=name)
