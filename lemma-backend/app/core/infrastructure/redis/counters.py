"""Windowed counters whose expiry cannot be lost.

The obvious shape -- ``INCR``, then ``EXPIRE`` when the count is 1 -- is two
commands. If the second one fails (a timeout, a failover, the process dying
between them) the key has no TTL and never gets another chance: every later
``INCR`` sees a count above 1 and skips the ``EXPIRE``. The counter then lives
in Redis forever and, for a rate limiter, keeps refusing after its window has
passed. Four counters in this codebase had that shape.

One script makes it atomic, and checks the TTL rather than the count, so a key
that already leaked is repaired the next time it is incremented.
"""

from __future__ import annotations

_INCR_WITH_TTL_LUA = """
local current = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


async def incr_with_ttl(client, key: str, ttl_seconds: int) -> int:
    """Increment ``key`` and make sure it expires within ``ttl_seconds``.

    The window starts at the first increment, as with INCR-then-EXPIRE; a key
    found without a TTL gets a fresh one.
    """
    return int(await client.eval(_INCR_WITH_TTL_LUA, 1, key, int(ttl_seconds)))
