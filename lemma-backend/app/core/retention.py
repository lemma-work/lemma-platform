"""The retention rule for versioned build history (app releases, function revisions).

Nothing has ever deleted a release or a revision, so storage grows with every
deploy forever. This is the decision about what to keep, kept pure and in one
place so both modules apply the same rule and it can be argued with in one test
file rather than two SQL queries.

The rule has three knobs because two are not enough:

* ``keep_last`` is a FLOOR. The N newest survive whatever their age, so an app
  nobody has deployed in a year can still be rolled back the day a bad deploy
  lands. Age alone as the rule would delete exactly the build you need.
* ``keep_days`` keeps recent work around while it is still being iterated on.
* ``max_keep`` is a CEILING, and it is what makes the whole thing bounded.
  Without it, fifty deploys in one afternoon means fifty retained builds for the
  next thirty days -- "keep anything recent" has no upper limit on its own.

The live entry is never a candidate, at any age or rank: deleting the bytes an
app or function is currently serving is the one outcome retention must never
produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID


class RetainableVersion(Protocol):
    """The three facts the rule needs. Both entity types already have them."""

    id: UUID | None
    created_at: datetime | None
    pruned_at: datetime | None


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    keep_last: int = 10
    keep_days: int = 30
    max_keep: int = 20

    def __post_init__(self) -> None:
        if self.max_keep < self.keep_last:
            # A ceiling below the floor would silently delete inside the range
            # the floor promises to protect.
            raise ValueError(
                f"max_keep ({self.max_keep}) must be >= keep_last ({self.keep_last})"
            )


def select_prunable(
    versions: list,
    *,
    policy: RetentionPolicy,
    live_id: UUID | None,
    now: datetime,
) -> list:
    """Return the entries whose bytes may be deleted, newest-ranked first.

    ``versions`` may arrive in any order; ranking is by ``created_at``
    descending, so rank 1 is the newest build. Entries already pruned are not
    returned again -- pruning is idempotent, and a sweep that keeps re-selecting
    the same rows would re-delete objects that are already gone on every tick.

    Ties in ``created_at`` break on ``id``, which is load-bearing rather than
    arbitrary: both tables key on uuid7, so id order IS creation order. Two
    builds recorded in the same timestamp tick therefore still rank in the order
    they happened, and never in an order that could keep an older build while
    deleting a newer one.
    """
    cutoff = now - timedelta(days=policy.keep_days)
    ranked = sorted(
        versions,
        key=lambda version: (version.created_at or now, version.id),
        reverse=True,
    )

    prunable = []
    for rank, version in enumerate(ranked, start=1):
        if version.pruned_at is not None:
            continue
        if live_id is not None and version.id == live_id:
            continue
        if rank <= policy.keep_last:
            continue
        if rank <= policy.max_keep and (version.created_at or now) >= cutoff:
            continue
        prunable.append(version)
    return prunable
