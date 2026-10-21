"""Shared polling helpers for asynchronous E2E behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest

T = TypeVar("T")


#: First poll gap. Everything after it doubles until the caller's
#: ``interval_seconds`` ceiling, so a condition that is already true costs one
#: probe and a condition that takes a while is not polled in a hot loop.
_INITIAL_POLL_SECONDS = 0.02


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
    delay = _INITIAL_POLL_SECONDS
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
                    pytest.fail(
                        f"{label} failed: {failure}. Last value: {last_value!r}"
                    )
            if done(last_value):
                return last_value
        # Checked after every attempt (not before) so a zero/tiny timeout
        # still gets exactly one real probe instead of silently skipping it.
        if loop.time() >= deadline:
            break
        # Ramp rather than a flat interval. Most waits here resolve on the first
        # or second poll against an in-process ASGI probe that answers in single
        # -digit milliseconds, so a flat 0.25s default overshot by 5-15x every
        # time it missed -- and 51 of 77 call sites take the default, while
        # every site that thought about it chose tighter. Backing off to the
        # caller's interval keeps a genuinely slow condition from being polled
        # in a hot loop.
        await asyncio.sleep(min(delay, interval_seconds))
        delay *= 2

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


def _libpq_url(url: str) -> str:
    scheme, rest = url.split("://", 1)
    return f"{scheme.split('+', 1)[0]}://{rest}"


async def _work_in_flight(database_url: str, datastore_database_url: str) -> int:
    """Wake rows not yet handed off, plus work items due or running."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(
        _libpq_url(datastore_database_url), autocommit=True
    ) as connection:
        cursor = await connection.execute(
            "SELECT CASE WHEN to_regclass('public.lemma_change_pending') IS NULL "
            "THEN 0 ELSE (SELECT count(*) FROM public.lemma_change_pending) END"
        )
        row = await cursor.fetchone()
        pending = int(row[0]) if row else 0
    async with await psycopg.AsyncConnection.connect(
        _libpq_url(database_url), autocommit=True
    ) as connection:
        # Receivers are perpetual pollers: running is their resting state.
        cursor = await connection.execute(
            "SELECT count(*) FROM work_items WHERE class <> 'receivers' AND "
            "(state = 'running' OR (state = 'queued' AND run_at <= now()))"
        )
        row = await cursor.fetchone()
        due = int(row[0]) if row else 0
    return pending + due


async def wait_for_work_to_settle(
    *,
    label: str,
    database_url: str,
    datastore_database_url: str,
    timeout_seconds: float = 30.0,
) -> None:
    """Wait until the worker has nothing left to do for what was already committed.

    The barrier for a negative assertion -- "this write must *not* start a
    run" -- which otherwise has nothing to wait on but the clock. Each step of
    the asynchronous chain stays a row until the next step's row commits: a
    datastore write's wake row stays until its delivery items are committed, and
    a work item stays queued or running until whatever it causes commits with
    it. Once there is neither, everything the earlier writes set in motion has
    happened, and what has not happened will not.
    """
    await eventually(
        label=f"{label}: worker settled",
        probe=lambda: _work_in_flight(database_url, datastore_database_url),
        done=lambda count: count == 0,
        timeout_seconds=timeout_seconds,
        interval_seconds=0.1,
    )
