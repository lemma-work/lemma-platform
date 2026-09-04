"""The once-only marker a pod carries for the first outcome it produced.

The fact belongs to analytics; the row belongs here. A pod delivers once, ever,
and that is recorded in the pod's own ``config`` JSONB under an ``analytics``
key -- a dedicated table would have been a migration, a model and an
infrastructure package to store one timestamp per pod, and the pod row is
already the thing the fact is about.

Which is exactly why the write is pod's rather than the consumer's. The claim is
not a convenience wrapper around an ``UPDATE``: the conditional ``WHERE NOT (...
? 'delivered_at')`` *is* the lock, so two workers processing two outcomes for the
same pod at the same instant still produce exactly one event. A second module
writing this column would be a second module holding that guarantee.

What ``via`` means is not pod's business -- it is a funnel dimension the caller
names -- so it arrives as a string and is stored as one.

A submodule rather than ``contracts/__init__``, which is a leaf: this reaches
the ORM layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text

from app.modules.pod.infrastructure.models.pod_models import Pod

#: Claim delivery for a pod, exactly once.
#:
#: The casts are load-bearing, not decoration. ``jsonb_build_object`` declares
#: its arguments as ``"any"``, so PostgreSQL cannot infer a type for a bound
#: parameter and asyncpg raises ``IndeterminateDatatypeError`` before the
#: statement runs at all. Without them this never wrote the marker, so the guard
#: never became false and every later outcome retried it forever.
#:
#: ``CAST(x AS text)`` rather than ``x::text``: SQLAlchemy's ``text()`` scans for
#: ``:name`` and reads the second colon of ``::text`` as a bind parameter called
#: ``text``, which turns the statement into a syntax error. The ``'{}'::jsonb``
#: literals below are unaffected because no bind parameter precedes them.
#:
#: Kept as a module constant so the e2e test binds the same statement production
#: does. A copy in the test would have passed while this failed.
DELIVERY_CLAIM_SQL = """
UPDATE pods
SET config = jsonb_set(
    coalesce(config, '{}'::jsonb),
    '{analytics}',
    coalesce(config->'analytics', '{}'::jsonb)
        || jsonb_build_object('delivered_at', CAST(:now AS text), 'via', CAST(:via AS text)),
    true
)
WHERE id = :pod_id
  AND NOT (coalesce(config->'analytics', '{}'::jsonb) ? 'delivered_at')
RETURNING id
"""

_SEEDED_MARKER_SQL = """
UPDATE pods
SET config = jsonb_set(config, '{analytics,seeded}', 'true'::jsonb, true)
WHERE id = :pod_id
"""


@dataclass(frozen=True, slots=True)
class PodProvenance:
    """Who built the pod, and when."""

    creator_user_id: UUID
    created_at: datetime


async def claim_first_delivery(uow, pod_id: UUID, *, at: datetime, via: str) -> bool:
    """Stamp the marker, and say whether this caller is the one that set it.

    ``False`` for every caller after the first, including a redelivery of the
    same outcome -- which is what makes calling this on every outcome safe.
    """
    claimed = await uow.session.scalar(
        text(DELIVERY_CLAIM_SQL),
        {"pod_id": pod_id, "now": at.isoformat(), "via": via},
    )
    return claimed is not None


async def mark_delivery_seeded(uow, pod_id: UUID) -> None:
    """Record that the marker was backdated rather than earned under observation."""
    await uow.session.execute(text(_SEEDED_MARKER_SQL), {"pod_id": pod_id})


async def pod_provenance(uow, pod_id: UUID) -> PodProvenance | None:
    """The pod's builder and birthday, or ``None`` if there is no such pod."""
    row = (
        await uow.session.execute(
            select(Pod.user_id, Pod.created_at).where(Pod.id == pod_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return PodProvenance(creator_user_id=row[0], created_at=row[1])


__all__ = [
    "DELIVERY_CLAIM_SQL",
    "PodProvenance",
    "claim_first_delivery",
    "mark_delivery_seeded",
    "pod_provenance",
]
