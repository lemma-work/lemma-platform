"""Named rate limits, enforced at claim time as token buckets in Postgres.

A kind declares which buckets each of its items draws one token from
(``rates=[RateUse("llm_filter", per_key=True), RateUse("llm_filter_pod")]``).
A bucket is ``(namespace, name, key)``: per namespace, or per namespace *and*
the item's rate key (a schedule id) when the use says ``per_key``.

The claim spends tokens in its own transaction, so a token is spent exactly
when an item is taken and a rolled-back claim spends nothing. An item whose
bucket is empty is not taken and not charged: its ``run_at`` moves to the
moment a token will be there, so it stops being probed until then and the
rest of the namespace's work -- other kinds, other keys -- carries on.

Two claims never contend for a bucket: each takes a transaction-scoped
*try* lock on it first, and a claim that cannot get the lock leaves those
items for its next round. Nothing waits on a bucket, so buckets cannot
deadlock with each other, with queue rows, or with retention. No state lives
in the process; a missing row is a full bucket.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work.config import work_settings
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Rate:
    """``limit`` tokens per ``per_seconds``, refilled continuously; burst = limit."""

    limit: int
    per_seconds: float

    @property
    def per_second(self) -> float:
        return self.limit / self.per_seconds

    def as_json(self) -> dict[str, JsonValue]:
        return {"limit": self.limit, "per_seconds": self.per_seconds}


@dataclass(frozen=True, slots=True)
class RateUse:
    """One bucket a kind's item draws a token from when it is claimed.

    ``per_key``: one bucket per rate key (``WorkWriter.add(rate_key=...)``,
    falling back to the item's concurrency key) instead of one per namespace.
    """

    name: str
    per_key: bool = False


#: Defaults per bucket name, from the trigger caps decided for the work
#: runtime. A deployment changes them with WORK_RATES_JSON; a pod or org with
#: WORK_LIMIT_OVERRIDES_JSON or a plan provider.
DEFAULT_RATES: Mapping[str, Rate] = MappingProxyType(
    {
        # Fires per trigger (keyed by schedule id).
        "trigger_fire": Rate(600, 3600),
        # LLM filter calls per trigger (keyed by schedule id) ...
        "llm_filter": Rate(60, 3600),
        # ... and per pod.
        "llm_filter_pod": Rate(300, 3600),
        # Outbound messages per pod.
        "email_out": Rate(200, 3600),
        # Fires per trigger per day. Not a bucket any kind draws from: it is
        # the ceiling past which a trigger pauses itself (schedule module), kept
        # here so the same override seam reaches it.
        "trigger_fire_day": Rate(2000, 86400),
        # Webhook deliveries per MCP event subscription (keyed by its id): a
        # bulk write reaches the receiver at this pace instead of in one burst.
        "mcp_delivery": Rate(120, 60),
    }
)


def parse_rates(raw: JsonValue) -> tuple[tuple[str, Rate], ...]:
    """``{"name": {"limit": n, "per_seconds": s}}``; malformed entries are dropped."""
    if not isinstance(raw, dict):
        return ()
    rates: list[tuple[str, Rate]] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        limit, per = spec.get("limit"), spec.get("per_seconds")
        if (
            isinstance(limit, int)
            and not isinstance(limit, bool)
            and limit >= 1
            and isinstance(per, (int, float))
            and per > 0
        ):
            rates.append((name, Rate(limit, float(per))))
    return tuple(rates)


@lru_cache(maxsize=8)
def _configured(raw: str) -> tuple[tuple[str, Rate], ...]:
    if not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("work.config.unparseable", setting="WORK_RATES_JSON")
        return ()
    rates = parse_rates(parsed)
    if not isinstance(parsed, dict) or len(rates) != len(parsed):
        logger.warning(
            "work.config.entry_ignored", setting="WORK_RATES_JSON", entry="*"
        )
    return rates


def default_rates() -> dict[str, Rate]:
    """The deployment's rates: built-in defaults overlaid by WORK_RATES_JSON."""
    return {**DEFAULT_RATES, **dict(_configured(work_settings.work_rates_json))}


def is_known_rate(name: str) -> bool:
    return name in default_rates()


def rates_json(rates: Mapping[str, Rate]) -> str:
    return json.dumps({name: rate.as_json() for name, rate in sorted(rates.items())})


def rates_from_json(raw: JsonValue) -> dict[str, Rate]:
    """A queue row's stored rates, over the deployment defaults for any it lacks."""
    return {**default_rates(), **dict(parse_rates(raw))}


@dataclass(frozen=True, slots=True, order=True)
class BucketId:
    """A bucket within the namespace being claimed."""

    name: str
    key: str


def bucket_ids(
    uses: Sequence[RateUse], rate_key: str | None, concurrency_key: str | None
) -> tuple[BucketId, ...]:
    key = rate_key or concurrency_key or ""
    return tuple(BucketId(use.name, key if use.per_key else "") for use in uses)


@dataclass(frozen=True, slots=True)
class BucketState:
    """Tokens in a bucket after refill, as this claim found it."""

    tokens: float
    rate: Rate


def wait_for_token(owed: float, rate: Rate) -> float:
    """Seconds until ``owed`` more tokens than are there now have refilled.

    ``owed`` is ``k - tokens`` for the k-th item queued behind an empty
    bucket, so items behind one bucket come due one refill apart instead of
    all at the same instant.
    """
    return max(owed, 0.0) / rate.per_second


# The lock key folds in everything that names the bucket. A hash collision only
# makes two buckets share a lock: the loser skips a round, nothing is wrong.
# The bucket row carries its rate, so a changed limit takes effect at the next
# refill (and clamps what is already there).
_REFILL = text(
    """
    WITH locked AS (
        SELECT w.name, w.key, w.cap, w.per_second
          FROM unnest(CAST(:names AS text[]), CAST(:keys AS text[]),
                      CAST(:caps AS float8[]), CAST(:rates AS float8[]))
               AS w(name, key, cap, per_second)
         WHERE pg_try_advisory_xact_lock(
                   hashtextextended('work_rate|' || :ns || '|' || w.name || '|' || w.key, 0))
    )
    INSERT INTO work_rate_buckets AS b
           (ns, name, key, cap, per_second, tokens, updated_at, full_at)
    SELECT :ns, l.name, l.key, l.cap, l.per_second, l.cap,
           clock_timestamp(), clock_timestamp()
      FROM locked l
    ON CONFLICT (ns, name, key) DO UPDATE
       SET tokens = LEAST(
               EXCLUDED.cap,
               b.tokens + GREATEST(0, EXTRACT(EPOCH FROM clock_timestamp() - b.updated_at))
                          * EXCLUDED.per_second),
           cap = EXCLUDED.cap,
           per_second = EXCLUDED.per_second,
           updated_at = GREATEST(b.updated_at, clock_timestamp())
    RETURNING b.name, b.key, b.tokens
    """
)
# ``full_at`` is when the bucket refills completely; past it the row says
# nothing a missing row would not, so retention may drop it.
_SPEND = text(
    """
    UPDATE work_rate_buckets b
       SET tokens = b.tokens - s.spent,
           full_at = b.updated_at
                     + make_interval(secs => (b.cap - (b.tokens - s.spent)) / b.per_second)
      FROM unnest(CAST(:names AS text[]), CAST(:keys AS text[]),
                  CAST(:spent AS float8[])) AS s(name, key, spent)
     WHERE b.ns = :ns AND b.name = s.name AND b.key = s.key
    """
)
_THROTTLE = text(
    """
    UPDATE work_items w
       SET run_at = clock_timestamp() + make_interval(secs => t.wait)
      FROM unnest(CAST(:ids AS uuid[]), CAST(:waits AS float8[])) AS t(id, wait)
     WHERE w.id = t.id AND w.state = 'queued'
    """
)
#: Same try-lock as the claim, so retention never waits on a claim or makes one wait.
DELETE_FULL_BUCKETS = text(
    """
    DELETE FROM work_rate_buckets b
     WHERE b.full_at < now()
       AND pg_try_advisory_xact_lock(
               hashtextextended('work_rate|' || b.ns || '|' || b.name || '|' || b.key, 0))
    """
)


async def open_buckets(
    session: AsyncSession, ns: str, wanted: Mapping[BucketId, Rate]
) -> dict[BucketId, BucketState]:
    """Lock and refill the wanted buckets; a bucket another claim holds is absent."""
    if not wanted:
        return {}
    ordered = sorted(wanted)
    rows = await session.execute(
        _REFILL,
        {
            "ns": ns,
            "names": [b.name for b in ordered],
            "keys": [b.key for b in ordered],
            "caps": [float(wanted[b].limit) for b in ordered],
            "rates": [wanted[b].per_second for b in ordered],
        },
    )
    return {
        BucketId(row.name, row.key): BucketState(
            float(row.tokens), wanted[BucketId(row.name, row.key)]
        )
        for row in rows
    }


async def spend(session: AsyncSession, ns: str, spent: Mapping[BucketId, int]) -> None:
    used = sorted(b for b, n in spent.items() if n > 0)
    if not used:
        return
    await session.execute(
        _SPEND,
        {
            "ns": ns,
            "names": [b.name for b in used],
            "keys": [b.key for b in used],
            "spent": [float(spent[b]) for b in used],
        },
    )


async def throttle(session: AsyncSession, waits: Mapping[UUID, float]) -> None:
    """Move rate-limited items to when their token will be there. Nothing charged."""
    if not waits:
        return
    ids = list(waits)
    await session.execute(
        _THROTTLE, {"ids": ids, "waits": [waits[item_id] for item_id in ids]}
    )


def wanted_buckets(
    needed: Iterable[BucketId], rates: Mapping[str, Rate]
) -> dict[BucketId, Rate]:
    """Each needed bucket with its rate; a name with no rate is unlimited."""
    return {b: rates[b.name] for b in needed if b.name in rates}
