"""Where activation is remembered.

``pod.delivered`` fires once per pod, ever, so something has to know which pods
have already crossed. Two candidates were rejected before this one:

* **The inbox.** Keyed ``(consumer, event_id)``, not ``(consumer, pod_id)``. It
  dedupes redeliveries of one event; it cannot answer "has this pod ever".
* **Redis alone.** One ``SETNX`` per pod would be cheaper, and a flush or a
  failover would re-fire activation for every established pod on the platform --
  permanently corrupting the funnel this table exists to produce. This codebase
  has already been bitten by trusting Redis with state that cannot be rebuilt;
  see the consumer-group reconcile loop.

So Postgres is the truth and Redis is a negative cache in front of it. The hot
path is a cache hit for every pod that has already activated, which after the
first week is essentially all of them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class PodDeliveryMarker(Base):
    """One row per pod that has ever delivered an outcome."""

    __tablename__ = "analytics_pod_delivery"

    pod_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Which kind of outcome got there first, from the closed set in
    #: ``pod_delivery.DeliveryVia``. Kept so the activation *mix* stays visible:
    #: a platform where every pod activates by scheduled run is a different
    #: product from one where they activate by answering people.
    via: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: True for pods backfilled at migration time, which had already delivered
    #: before any of this existed. They emit no event -- dating their activation
    #: to the deploy would invent a spike that never happened -- and this flag
    #: lets analysis exclude the pre-instrumentation cohort explicitly rather
    #: than by guessing at a cutoff date.
    seeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
