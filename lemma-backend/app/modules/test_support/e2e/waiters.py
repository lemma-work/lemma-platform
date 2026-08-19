"""Shared polling helpers for asynchronous E2E behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest

T = TypeVar("T")


async def eventually(
    *,
    label: str,
    probe: Callable[[], Awaitable[T]],
    done: Callable[[T], bool],
    timeout_seconds: float = 30.0,
    interval_seconds: float = 0.25,
    fail_fast: Callable[[T], str | None] | None = None,
    retry_exceptions: tuple[type[BaseException], ...] = (),
) -> T:
    """Poll ``probe`` until ``done`` is true or fail with useful context.

    ``retry_exceptions`` treats a matching exception from ``probe`` as "not
    ready yet" rather than a hard failure -- e.g. ``OSError`` while a port
    isn't listening yet, or ``httpx.HTTPError`` during a health check before
    the server has bound. Anything not in this tuple still propagates
    immediately, same as before.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_value: T | None = None
    last_error: BaseException | None = None
    while True:
        try:
            last_value = await probe()
        except retry_exceptions as exc:
            last_error = exc
        else:
            last_error = None
            if fail_fast is not None:
                failure = fail_fast(last_value)
                if failure:
                    pytest.fail(f"{label} failed: {failure}. Last value: {last_value!r}")
            if done(last_value):
                return last_value
        # Checked after every attempt (not before) so a zero/tiny timeout
        # still gets exactly one real probe instead of silently skipping it.
        if loop.time() >= deadline:
            break
        await asyncio.sleep(interval_seconds)

    if last_error is not None:
        pytest.fail(f"Timed out waiting for {label}. Last error: {last_error!r}")
    pytest.fail(f"Timed out waiting for {label}. Last value: {last_value!r}")


async def wait_for_status(
    *,
    label: str,
    probe: Callable[[], Awaitable[dict]],
    status_field: str = "status",
    expected: set[str],
    failed: set[str] | None = None,
    timeout_seconds: float = 30.0,
    interval_seconds: float = 0.25,
) -> dict:
    # `failed or {...}` would silently ignore an explicitly-passed empty set
    # (falsy) and fall back to the default anyway -- a real trap for a
    # caller that legitimately wants no fail-fast statuses at all (e.g.
    # waiting for a status that overlaps the default "bad" set, like
    # `expected={"FAILED"}`, where {"FAILED", "ERROR"} would fail-fast the
    # instant it becomes true).
    failed = {"FAILED", "ERROR"} if failed is None else failed
    return await eventually(
        label=label,
        probe=probe,
        done=lambda payload: str(payload.get(status_field)) in expected,
        fail_fast=lambda payload: (
            str(payload.get(status_field))
            if str(payload.get(status_field)) in failed
            else None
        ),
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )

