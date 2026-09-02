"""Whether readiness can see a worker that is not in this process.

`/health/ready` used to answer the worker question only in the single-process
desktop build, because the only liveness signal was a file on the worker's own
disk. In every split topology -- `python -m app.worker` beside `uvicorn`, which
is what the container stack and the documented Kubernetes layout run -- the API
answered 200 with the worker dead: the load balancer kept sending traffic, agent
runs queued behind nothing, and the UI showed "thinking".

So the watchdog publishes liveness where a second process can read it, and this
pins the three answers readiness has to tell apart:

* a worker refreshed the key recently -> ok;
* a worker was here and is not answering now -> stalled, and 503;
* no worker has ever run against this Redis -> the question does not apply, and
  an API-only deployment is not marked unready for a worker it never had.
"""

from __future__ import annotations

import pytest

from app.core.observability import worker_liveness


class _FakeRedis:
    """Enough of redis-py for SET ... EX and EXISTS, with no expiry clock."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        self.values[name] = value
        if ex is not None:
            self.expiries[name] = ex

    async def exists(self, *names: str) -> int:
        return sum(1 for name in names if name in self.values)

    def expire_key(self, name: str) -> None:
        """What the TTL does when nothing refreshes the key."""
        self.values.pop(name, None)


@pytest.fixture
def redis() -> _FakeRedis:
    return _FakeRedis()


async def test_a_worker_that_just_ticked_reads_as_ok(redis):
    await worker_liveness.publish_worker_liveness(redis)

    assert await worker_liveness.read_worker_liveness(redis) == "ok"


async def test_liveness_expires_faster_than_presence(redis):
    """The short key answers "now"; the long one answers "ever"."""
    await worker_liveness.publish_worker_liveness(redis)

    assert (
        redis.expiries[worker_liveness.WORKER_ALIVE_KEY]
        < redis.expiries[worker_liveness.WORKER_SEEN_KEY]
    )


async def test_a_worker_that_stopped_ticking_reads_as_stalled(redis):
    await worker_liveness.publish_worker_liveness(redis)
    redis.expire_key(worker_liveness.WORKER_ALIVE_KEY)

    assert await worker_liveness.read_worker_liveness(redis) == "stalled"


async def test_a_deployment_that_never_ran_a_worker_is_not_asked(redis):
    assert await worker_liveness.read_worker_liveness(redis) is None


async def test_a_redis_that_cannot_answer_is_not_a_dead_worker(redis):
    """Redis being down is already reported by the readiness Redis check.

    Reporting it a second time as a stalled worker would name the wrong
    component, and readiness is what an operator reads first.
    """

    async def _fail(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    redis.exists = _fail

    assert await worker_liveness.read_worker_liveness(redis) is None
