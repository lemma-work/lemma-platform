"""Stop calling a connector operation that is already failing.

Connector execution reaches a third party, and the contract is deliberately
generous: the use case documents a 1-45 second call and holds no pooled
connection across it. Production's worst was 12.2 seconds. So a duration cap is
the wrong instrument — it would fail the slow operations that are working.

What is worth avoiding is the *other* shape: a provider that is down, where
every caller waits the full timeout to be told the same thing. A breaker turns
that from a slow failure into an immediate one, and stops us adding load to
something already struggling.

**Only infrastructure failures count.** A 422 is a malformed request, a 401 is a
stale credential, a 404 is a wrong operation name — all of them the caller's
problem, and none of them evidence the provider is unwell. Counting those would
let one badly-formed integration trip the breaker for everyone using the same
connector.

**State lives in Redis, not the process.** The api runs more than one replica,
and a breaker each replica learns separately is not a breaker — it is N
independent guesses, each needing its own full threshold before it helps. This
is also the codebase's standing rule: in-process caching is reserved for object
singletons, and this is shared state.

Half-open falls out of the TTLs rather than needing its own state machine. When
the breaker opens, the failure counter is left one short of the threshold with a
lifetime longer than the cooldown, so the *next* failure — whenever it comes —
re-opens immediately instead of waiting for another full run.

Being precise about what that is and is not: it is not a single-probe half-open.
Nothing serialises the calls that arrive the moment the open key expires, so a
busy operation lets a burst through at once rather than one. That is a
deliberate trade, not an oversight — admitting one caller at a time needs a lock
with an owner and a lease, which is a second thing that can fail, and the burst
costs one round of timeouts before the first failure re-opens the breaker for
everyone. What it buys is that a *recovered* provider comes back at full rate
immediately instead of ramping one probe per cooldown.
"""

from __future__ import annotations

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import OperationExecutionCircuitOpenError

logger = get_logger(__name__)

_PREFIX = "lemma:connector:breaker"


def _keys(scope: str) -> tuple[str, str]:
    return f"{_PREFIX}:open:{scope}", f"{_PREFIX}:fail:{scope}"


def breaker_scope(
    connector_id: str, operation_name: str, organization_id: object | None = None
) -> str:
    """Identity of the thing that can be broken.

    Per operation, not per connector: a provider whose ``send_email`` is down
    usually still lists messages, and blocking the healthy half would turn a
    partial outage into a total one.

    **Per organization, though.** This used to be keyed on the catalog
    connector id alone, on the reasoning that an infrastructure failure is a
    property of the provider and every account would otherwise have to discover
    it separately. That reasoning holds only for a single shared SaaS endpoint,
    and it is false for the kinds where each install points somewhere different
    — an MCP server URL comes from the install's own ``connection_config``, and
    SQL and HTTP are the same. Two organizations pointing at two unrelated
    servers were sharing one breaker, so one of them going down stopped the
    other.

    It is still not per account. An account-level failure is a credential
    problem, which is classified as Unauthorized and never reaches the breaker.
    """
    if organization_id is None:
        return f"{connector_id}:{operation_name}"
    return f"{organization_id}:{connector_id}:{operation_name}"


async def guard(scope: str) -> None:
    """Raise if the breaker for *scope* is open. Fails open on a Redis error."""
    if not connector_settings.connector_breaker_enabled:
        return
    open_key, _ = _keys(scope)
    try:
        is_open = await get_redis().exists(open_key)
    except (RedisError, OSError):
        # A breaker that cannot reach Redis must not become an outage of its
        # own. Losing the protection is strictly better than refusing traffic
        # that would have worked.
        logger.debug("connectors.breaker.unavailable.diagnostic", scope=scope)
        return
    if is_open:
        raise OperationExecutionCircuitOpenError(
            f"Connector operation {scope} is temporarily disabled after repeated "
            "provider failures.",
            details={"scope": scope},
        )


async def record_success(scope: str) -> None:
    """Clear the streak. One good call is enough — the breaker guards outages."""
    if not connector_settings.connector_breaker_enabled:
        return
    open_key, fail_key = _keys(scope)
    try:
        await get_redis().delete(open_key, fail_key)
    except (RedisError, OSError):
        logger.debug("connectors.breaker.unavailable.diagnostic", scope=scope)


async def record_failure(scope: str) -> None:
    """Count an infrastructure failure, and open the breaker at the threshold."""
    if not connector_settings.connector_breaker_enabled:
        return
    open_key, fail_key = _keys(scope)
    threshold = connector_settings.connector_breaker_failure_threshold
    cooldown = connector_settings.connector_breaker_cooldown_seconds
    window = connector_settings.connector_breaker_failure_window_seconds
    try:
        redis = get_redis()
        failures = await redis.incr(fail_key)
        if failures == 1:
            await redis.expire(fail_key, window)
        if failures < threshold:
            return
        await redis.set(open_key, "1", ex=cooldown)
        # Left one short of the threshold, outliving the cooldown: the first
        # call after the breaker reopens is a probe, and a single failure
        # re-opens it rather than starting the count again.
        await redis.set(fail_key, threshold - 1, ex=cooldown + window)
    except (RedisError, OSError):
        logger.debug("connectors.breaker.unavailable.diagnostic", scope=scope)
        return
    logger.warning(
        "connectors.breaker.opened.degraded",
        scope=scope,
        failures=threshold,
        cooldown_seconds=cooldown,
    )
