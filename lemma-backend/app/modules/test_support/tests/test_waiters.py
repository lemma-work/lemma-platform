"""``eventually``/``wait_for_status`` are meant to be the ONE polling helper
the e2e suite uses instead of ~55 hand-rolled copies -- this pins their
contract so a future edit doesn't silently break every caller migrated onto
them.
"""

from __future__ import annotations

import pytest

from app.modules.test_support.e2e.waiters import eventually, wait_for_status

pytestmark = pytest.mark.unit


async def test_it_returns_as_soon_as_done_is_true() -> None:
    calls = []

    async def probe() -> int:
        calls.append(1)
        return len(calls)

    result = await eventually(
        label="counter reaches 3",
        probe=probe,
        done=lambda value: value >= 3,
        interval_seconds=0,
    )

    assert result == 3
    assert len(calls) == 3


async def test_it_fails_with_the_last_value_on_timeout() -> None:
    async def probe() -> int:
        return 1

    with pytest.raises(pytest.fail.Exception, match="never happens.*Last value: 1"):
        await eventually(
            label="never happens",
            probe=probe,
            done=lambda value: False,
            timeout_seconds=0,
            interval_seconds=0,
        )


async def test_fail_fast_stops_before_the_timeout() -> None:
    async def probe() -> dict:
        return {"status": "FAILED"}

    with pytest.raises(pytest.fail.Exception, match="stops early failed: FAILED"):
        await eventually(
            label="stops early",
            probe=probe,
            done=lambda value: value["status"] == "DONE",
            fail_fast=lambda value: value["status"]
            if value["status"] == "FAILED"
            else None,
            timeout_seconds=30,
            interval_seconds=0,
        )


async def test_retry_exceptions_are_treated_as_not_ready_yet() -> None:
    attempts = []

    async def probe() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("port not open yet")
        return "ready"

    result = await eventually(
        label="port opens",
        probe=probe,
        done=lambda value: value == "ready",
        retry_exceptions=(OSError,),
        interval_seconds=0,
    )

    assert result == "ready"
    assert len(attempts) == 3


async def test_an_exception_outside_retry_exceptions_still_propagates() -> None:
    async def probe() -> str:
        raise ValueError("not retryable")

    with pytest.raises(ValueError, match="not retryable"):
        await eventually(
            label="unused",
            probe=probe,
            done=lambda value: True,
            retry_exceptions=(OSError,),
            interval_seconds=0,
        )


async def test_timeout_after_only_errors_reports_the_last_error() -> None:
    async def probe() -> str:
        raise OSError("still not open")

    with pytest.raises(
        pytest.fail.Exception, match="never opens.*Last error:.*still not open"
    ):
        await eventually(
            label="never opens",
            probe=probe,
            done=lambda value: True,
            retry_exceptions=(OSError,),
            timeout_seconds=0,
            interval_seconds=0,
        )


async def test_wait_for_status_returns_the_payload_once_expected() -> None:
    payloads = iter([{"status": "PENDING"}, {"status": "RUNNING"}, {"status": "DONE"}])

    async def probe() -> dict:
        return next(payloads)

    result = await wait_for_status(
        label="job",
        probe=probe,
        expected={"DONE"},
        interval_seconds=0,
    )

    assert result == {"status": "DONE"}


async def test_wait_for_status_fails_fast_on_a_failed_status() -> None:
    async def probe() -> dict:
        return {"status": "FAILED"}

    with pytest.raises(pytest.fail.Exception, match="FAILED"):
        await wait_for_status(
            label="job",
            probe=probe,
            expected={"DONE"},
            timeout_seconds=30,
            interval_seconds=0,
        )


async def test_wait_for_status_an_explicit_empty_failed_set_disables_fail_fast() -> (
    None
):
    """A caller can legitimately want to wait FOR a status the default
    fail_fast set would otherwise treat as bad (e.g. a test driving an
    import to its own expected FAILED terminus)."""

    async def probe() -> dict:
        return {"status": "FAILED"}

    result = await wait_for_status(
        label="job",
        probe=probe,
        expected={"FAILED"},
        failed=set(),
        interval_seconds=0,
    )

    assert result == {"status": "FAILED"}
