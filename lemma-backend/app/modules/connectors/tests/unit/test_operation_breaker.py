"""Stop calling a provider that is already failing — and only for its own faults.

Connector execution reaches a third party under a deliberately generous
contract: 1-45 seconds, no pooled connection held. Production's worst was 12.2s.
A duration cap would fail the slow calls that are working, so the instrument is
a breaker: when the provider is down, stop making every caller wait the full
timeout to learn the same thing.

The risk a breaker introduces is worse than the problem it solves if it trips on
the wrong thing. A 422 is a malformed request, a 401 a stale credential, a 404 a
wrong operation name — all the caller's fault. Counting those would let one
badly-formed integration disable a working connector for every other tenant.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionCircuitOpenError,
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
    OperationExecutionTimeoutError,
    OperationExecutionUnauthorizedError,
    OperationExecutionValidationError,
)
from app.modules.connectors.infrastructure import operation_breaker


class _FakeRedis:
    """Enough of Redis to exercise the counter/TTL logic."""

    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.broken = broken

    def _check(self):
        if self.broken:
            raise ConnectionError("redis is unreachable")

    async def exists(self, key):
        self._check()
        return 1 if key in self.store else 0

    async def incr(self, key):
        self._check()
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        self._check()
        self.ttls[key] = seconds

    async def set(self, key, value, ex=None):
        self._check()
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, *keys):
        self._check()
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(operation_breaker, "get_redis", lambda: fake)
    return fake


SCOPE = "gmail:send_email"


@pytest.mark.anyio
async def test_a_healthy_operation_is_not_blocked(redis) -> None:
    await operation_breaker.guard(SCOPE)  # does not raise


@pytest.mark.anyio
async def test_it_opens_only_at_the_threshold(redis) -> None:
    threshold = connector_settings.connector_breaker_failure_threshold

    for _ in range(threshold - 1):
        await operation_breaker.record_failure(SCOPE)
    await operation_breaker.guard(SCOPE)  # still closed

    await operation_breaker.record_failure(SCOPE)
    with pytest.raises(OperationExecutionCircuitOpenError):
        await operation_breaker.guard(SCOPE)


@pytest.mark.anyio
async def test_one_success_clears_the_streak(redis) -> None:
    for _ in range(connector_settings.connector_breaker_failure_threshold - 1):
        await operation_breaker.record_failure(SCOPE)

    await operation_breaker.record_success(SCOPE)
    await operation_breaker.record_failure(SCOPE)

    await operation_breaker.guard(SCOPE)  # the streak restarted, so still closed


@pytest.mark.anyio
async def test_a_success_reopens_an_open_breaker(redis) -> None:
    for _ in range(connector_settings.connector_breaker_failure_threshold):
        await operation_breaker.record_failure(SCOPE)

    await operation_breaker.record_success(SCOPE)

    await operation_breaker.guard(SCOPE)


@pytest.mark.anyio
async def test_the_probe_after_a_cooldown_re_opens_on_one_failure(redis) -> None:
    """Half-open, without a state machine.

    Opening leaves the counter one short of the threshold with a lifetime
    outliving the cooldown, so the first call after the breaker expires is a
    probe: if it fails the breaker re-opens at once rather than waiting for
    another full run of failures against a provider still known to be down.
    """
    threshold = connector_settings.connector_breaker_failure_threshold
    for _ in range(threshold):
        await operation_breaker.record_failure(SCOPE)

    open_key, fail_key = operation_breaker._keys(SCOPE)
    assert redis.store[fail_key] == threshold - 1
    assert redis.ttls[fail_key] > redis.ttls[open_key], (
        "the counter must outlive the open window, or the probe starts from zero"
    )

    # Cooldown elapses: Redis expires the open key.
    del redis.store[open_key]

    await operation_breaker.record_failure(SCOPE)
    with pytest.raises(OperationExecutionCircuitOpenError):
        await operation_breaker.guard(SCOPE)


@pytest.mark.anyio
async def test_a_redis_outage_does_not_become_an_outage_of_its_own(monkeypatch) -> None:
    """Losing the protection beats refusing traffic that would have worked."""
    monkeypatch.setattr(operation_breaker, "get_redis", lambda: _FakeRedis(broken=True))

    await operation_breaker.guard(SCOPE)
    await operation_breaker.record_failure(SCOPE)
    await operation_breaker.record_success(SCOPE)
    await operation_breaker.guard(SCOPE)


@pytest.mark.anyio
async def test_it_can_be_switched_off(redis, monkeypatch) -> None:
    monkeypatch.setattr(connector_settings, "connector_breaker_enabled", False)

    for _ in range(connector_settings.connector_breaker_failure_threshold * 3):
        await operation_breaker.record_failure(SCOPE)

    await operation_breaker.guard(SCOPE)


@pytest.mark.anyio
async def test_operations_break_independently(redis) -> None:
    """A provider whose send is down usually still lists.

    Breaking the whole connector would turn a partial outage into a total one.
    """
    other = operation_breaker.breaker_scope("gmail", "list_messages")
    for _ in range(connector_settings.connector_breaker_failure_threshold):
        await operation_breaker.record_failure(SCOPE)

    with pytest.raises(OperationExecutionCircuitOpenError):
        await operation_breaker.guard(SCOPE)
    await operation_breaker.guard(other)


def test_the_scope_is_per_connector_and_operation() -> None:
    assert operation_breaker.breaker_scope("gmail", "send_email") == "gmail:send_email"
    assert operation_breaker.breaker_scope("gmail", "send_email") != (
        operation_breaker.breaker_scope("gmail", "list_messages")
    )


# --- which failures count ----------------------------------------------------


def test_only_provider_faults_are_breaker_worthy() -> None:
    """The taxonomy the use case switches on, pinned.

    If a new error class lands on the wrong side of this line, one tenant's bad
    request starts disabling a connector for everyone.
    """
    counts = (OperationExecutionInfrastructureError, OperationExecutionTimeoutError)
    never = (
        OperationExecutionValidationError,
        OperationExecutionUnauthorizedError,
        OperationExecutionAccessDeniedError,
        OperationExecutionNotFoundError,
    )

    for error in never:
        assert not issubclass(error, counts), (
            f"{error.__name__} is the caller's fault and must not trip a breaker"
        )
    # The open error descends from the infrastructure error, so a caller that
    # already handles "provider unavailable" keeps working unchanged.
    assert issubclass(
        OperationExecutionCircuitOpenError, OperationExecutionInfrastructureError
    )


def test_the_open_error_is_distinguishable_from_a_provider_failure() -> None:
    """"We stopped asking" and "it failed" want different responses."""
    opened = OperationExecutionCircuitOpenError("x")
    failed = OperationExecutionInfrastructureError("x")

    assert opened.code != failed.code
    assert opened.status_code == 503
