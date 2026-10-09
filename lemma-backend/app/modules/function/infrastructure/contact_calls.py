"""What a contact's function calls have used up, and which calls already ran.

Two things in Redis, both failing **closed**, for the reason
``agent_surfaces/services/outsider_limits`` gives: a contact's call not running
while Redis is away costs them a retry, and running without the ledger would
let one contact spend the pod's sandbox without limit or run a payment twice.

* A fixed daily window per contact per function.
* One claim per tool call. A model retrying the same call (the same tool call
  id) finds the run the first attempt started instead of starting another, so
  "book the slot" is booked once however often the turn is replayed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger

logger = get_logger(__name__)

_DAY = 86_400
#: Set before the run exists, replaced by its id once it does.
_PENDING = "pending"


class ContactCallsUnavailable(Exception):
    """The ledger could not be read or written, so the call must not run."""


@dataclass(frozen=True, slots=True)
class PriorCall:
    """A call with this key was already made. ``run_id`` is ``None`` while the
    first attempt is still creating its run."""

    run_id: UUID | None


class ContactCallLedger:
    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    def _client(self):
        return self._redis or get_redis()

    async def count_call(
        self, *, pod_id: UUID, contact_id: UUID, function_name: str, limit: int
    ) -> bool:
        """Count one call and say whether it is within today's allowance."""
        key = f"contact-function:day:{pod_id}:{contact_id}:{function_name}"
        try:
            count = await incr_with_ttl(self._client(), key, _DAY)
        except (RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        return count <= limit

    async def claim(self, key: str) -> PriorCall | None:
        """Claim ``key`` for a new call, or say which call already holds it."""
        try:
            claimed = await self._client().set(
                self._claim_key(key), _PENDING, nx=True, ex=_DAY
            )
            if claimed:
                return None
            held = await self._client().get(self._claim_key(key))
        except (RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        if held is None or held == _PENDING:
            return PriorCall(run_id=None)
        return PriorCall(run_id=UUID(str(held)))

    async def record(self, key: str, run_id: UUID) -> None:
        """Name the run a claimed call started."""
        try:
            await self._client().set(self._claim_key(key), str(run_id), ex=_DAY)
        except (RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

    async def release(self, key: str) -> None:
        """Give a claim back when its call started nothing, so a retry may run.

        Best effort: a claim that cannot be released expires within the day,
        and until then a retry is told the call is still in progress.
        """
        try:
            await self._client().delete(self._claim_key(key))
        except (RedisError, OSError) as exc:
            self._unavailable(exc)

    @staticmethod
    def _claim_key(key: str) -> str:
        return f"contact-function:call:{key}"

    @staticmethod
    def _unavailable(exc: BaseException) -> ContactCallsUnavailable:
        logger.warning(
            "function.contact_calls.unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return ContactCallsUnavailable("Contact function calls are unavailable")
