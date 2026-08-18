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

from uuid import uuid4

import pytest

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionCircuitOpenError,
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
    OperationExecutionRateLimitedError,
    OperationExecutionTimeoutError,
    OperationExecutionUnauthorizedError,
    OperationExecutionValidationError,
)
from app.modules.connectors.infrastructure import operation_breaker
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)
from app.modules.connectors.infrastructure.operation_breaker import breaker_scope


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

    async def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def ttl(self, key):
        self._check()
        if key not in self.store:
            return -2
        return self.ttls.get(key, -1)

    async def delete(self, *keys):
        self._check()
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
            self.ttls.pop(key, None)
        return removed

    def pipeline(self, transaction=False):
        del transaction
        return _FakePipeline(self)


class _FakePipeline:
    """Queues calls and replays them, like redis-py's async pipeline.

    Faithful enough to matter: the real one returns one result *per queued
    command*, which is the whole reason the breaker can tell an open key from a
    failure counter.
    """

    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._queued: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def ttl(self, key):
        self._queued.append(("ttl", (key,), {}))

    def set(self, key, value, ex=None, nx=False):
        self._queued.append(("set", (key, value), {"ex": ex, "nx": nx}))

    def delete(self, *keys):
        self._queued.append(("delete", keys, {}))

    async def execute(self):
        results = []
        for name, args, kwargs in self._queued:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        self._queued.clear()
        return results


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


# -- what may open the breaker, and what may not ------------------------------
#
# The breaker exists for one thing: a provider that hangs holds an OS thread per
# call, and refusing to *start* calls is the only mechanism that stops those
# accumulating (see composio_operation_gateway, which measured a limiter of 2
# serving 4 live threads). Nothing else it does is worth an outage.
#
# Production found the cost of getting the boundary wrong. Composio answered
# `413 Upstream_PayloadTooLarge` five times in 25 seconds -- an agent asking a
# tool for more data than it could return -- each was reported as "provider
# temporarily unavailable", and the fifth opened the breaker on a provider that
# was healthy and answering in 3.4s. Seven later calls were refused.


def _classify(status: int | None, error: str = "boom"):
    return ComposioOperationGateway._classify_failure(
        "GMAIL_SEND_EMAIL", status, error, {}
    )


@pytest.mark.parametrize(
    "status",
    [400, 404, 409, 413, 422, 429, 402, 451, 418],
    ids=["bad-request", "not-found", "conflict", "payload-too-large",
         "unprocessable", "rate-limited", "payment-required", "legal", "unknown-4xx"],
)
def test_a_provider_4xx_never_opens_the_breaker(status):
    """Whatever the provider rejected, it answered — so it is up, and this is
    the caller's problem. 413 is the one that reached production; the rest are
    here so the next unfamiliar status does not have to."""
    error = _classify(status)

    assert not isinstance(error, OperationExecutionInfrastructureError), (
        f"{status} would count toward the breaker and disable the operation"
    )


@pytest.mark.parametrize(
    "status",
    [500, 502, 503, 504, None],
    ids=["internal", "bad-gateway", "unavailable", "gateway-timeout", "no-status"],
)
def test_a_provider_5xx_or_a_silent_failure_does_open_the_breaker(status):
    """The other half. These say something about the provider's health, which
    is the only thing the breaker is for."""
    assert isinstance(_classify(status), OperationExecutionInfrastructureError)


def test_a_rate_limit_is_reported_as_one():
    """429 asks the caller to slow down. Reporting it as "temporarily
    unavailable" tells them to retry, which is the opposite."""
    error = _classify(429)

    assert isinstance(error, OperationExecutionRateLimitedError)
    assert error.status_code == 429


def test_payload_too_large_is_reported_as_a_rejected_request():
    error = _classify(413, "The tool response payload is too large.")

    assert isinstance(error, OperationExecutionValidationError)
    assert error.status_code == 422


# -- one tenant may not break another -----------------------------------------


def test_two_organizations_do_not_share_a_breaker():
    """The scope used to be the *catalog* connector id, so a breaker opened by
    one tenant's traffic refused every other tenant's. For MCP, SQL and HTTP
    kinds the endpoint is per install, so the two organizations were not even
    talking to the same server."""
    org_a = breaker_scope("mcp", "list_tools", uuid4())
    org_b = breaker_scope("mcp", "list_tools", uuid4())

    assert org_a != org_b


def test_the_same_organization_shares_one_breaker_per_operation():
    org = uuid4()

    assert breaker_scope("gmail", "send", org) == breaker_scope("gmail", "send", org)
    assert breaker_scope("gmail", "send", org) != breaker_scope("gmail", "list", org)


# -- a refused call must say what refused it ----------------------------------


def test_the_circuit_open_error_names_the_connector_and_when_to_retry():
    """Seven of these in one production incident, attributable to nothing.

    The breaker builds a message naming the scope and passes `details={"scope":
    ...}`. The error class shadowed the message with a fixed string, and the
    details allowlist dropped "scope" because it was not on it — so the caller
    received `details: null` and "a connector is disabled", with no way to learn
    which one or when to come back.
    """
    error = OperationExecutionCircuitOpenError(
        "Connector operation org-1:gmail:send is temporarily disabled after "
        "repeated provider failures.",
        details={"scope": "org-1:gmail:send", "retry_after": 60},
    )

    assert "org-1:gmail:send" in error.message
    assert error.details is not None, "details were dropped by the allowlist"
    assert error.details["scope"] == "org-1:gmail:send"
    assert error.details["retry_after"] == 60


def test_the_scope_splits_into_fields_a_log_query_can_group_by():
    """One opaque compound string could only be grouped by string surgery, and
    the organization — which matters *because* the key is per-tenant — was not
    visible at all."""
    assert operation_breaker._fields("org-1:gmail:send") == (
        "org-1",
        "gmail",
        "send",
    )
    # Legacy two-part keys still parse rather than raising.
    assert operation_breaker._fields("gmail:send") == ("", "gmail", "send")


@pytest.mark.anyio
async def test_clearing_a_failure_streak_does_not_announce_a_recovery(
    redis, caplog
) -> None:
    """A recovery is the end of an incident, and there was no incident.

    ``delete(open_key, fail_key)`` returns how many of the *two* keys existed,
    so a success that merely cleared a partial failure streak — the ordinary
    case, since most streaks never reach the threshold — reported a recovery
    from an outage that never happened. An incident log that fires without a
    matching opening makes the pair useless for reading what occurred.
    """
    for _ in range(connector_settings.connector_breaker_failure_threshold - 1):
        await operation_breaker.record_failure(SCOPE)

    with caplog.at_level("INFO"):
        await operation_breaker.record_success(SCOPE)

    assert "connectors.breaker.recovered" not in caplog.text, (
        "a success that ended no incident announced a recovery"
    )


@pytest.mark.anyio
async def test_a_real_recovery_is_still_announced(redis, caplog) -> None:
    """The counterweight: silencing the false positive must not silence the
    true one, or an incident starts in the logs and never ends there."""
    for _ in range(connector_settings.connector_breaker_failure_threshold):
        await operation_breaker.record_failure(SCOPE)
    with pytest.raises(OperationExecutionCircuitOpenError):
        await operation_breaker.guard(SCOPE)

    with caplog.at_level("INFO"):
        await operation_breaker.record_success(SCOPE)

    assert "connectors.breaker.recovered" in caplog.text
    await operation_breaker.guard(SCOPE)  # and the breaker really is closed


@pytest.mark.anyio
async def test_retry_after_counts_down_rather_than_repeating_the_cooldown(
    redis,
) -> None:
    """Telling a caller to wait the full cooldown every time overstates it.

    Someone arriving fifty seconds into a sixty-second cooldown would wait
    twice as long as they needed to.
    """
    for _ in range(connector_settings.connector_breaker_failure_threshold):
        await operation_breaker.record_failure(SCOPE)

    open_key, _ = operation_breaker._keys(SCOPE)
    redis.ttls[open_key] = 7  # most of the cooldown has already elapsed

    with pytest.raises(OperationExecutionCircuitOpenError) as raised:
        await operation_breaker.guard(SCOPE)

    assert raised.value.details["retry_after"] == 7, (
        "retry_after reported the whole cooldown instead of what remains of it"
    )


@pytest.mark.anyio
async def test_a_retry_loop_does_not_flood_the_log(redis, caplog) -> None:
    """One warning per incident, not one per refused call.

    A client retrying a down provider in a loop is the normal case, and it is
    exactly when a per-call warning turns one incident into pages of identical
    lines.
    """
    for _ in range(connector_settings.connector_breaker_failure_threshold):
        await operation_breaker.record_failure(SCOPE)

    with caplog.at_level("WARNING"):
        for _ in range(25):
            with pytest.raises(OperationExecutionCircuitOpenError):
                await operation_breaker.guard(SCOPE)

    refusals = caplog.text.count("connectors.breaker.rejected.degraded")
    assert refusals == 1, (
        f"25 refused calls produced {refusals} warnings; the breaker should "
        "announce an incident once per cooldown, not once per caller"
    )


@pytest.mark.anyio
async def test_reopening_announces_itself_again(redis, caplog) -> None:
    """Each opening is its own incident and deserves its own line."""
    threshold = connector_settings.connector_breaker_failure_threshold
    for _ in range(threshold):
        await operation_breaker.record_failure(SCOPE)
    with pytest.raises(OperationExecutionCircuitOpenError):
        await operation_breaker.guard(SCOPE)

    await operation_breaker.record_success(SCOPE)
    for _ in range(threshold):
        await operation_breaker.record_failure(SCOPE)

    caplog.clear()  # the first incident's line is not what this asserts on
    with caplog.at_level("WARNING"):
        with pytest.raises(OperationExecutionCircuitOpenError):
            await operation_breaker.guard(SCOPE)

    assert caplog.text.count("connectors.breaker.rejected.degraded") == 1, (
        "a breaker that re-opened refused silently — the announcement token "
        "from the previous incident was still alive"
    )
