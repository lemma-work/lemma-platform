"""How a dispatch target reports back to the schedule ledger.

Value objects only, and no imports past the standard library, so a module that
implements the read pays nothing for the rest of `schedule`.

Two fields because that is all the ledger reconciles against: whether the
target reached a terminal state, and when. The sweep that consumes this reads a
hundred targets a tick, and a workflow run carries four JSONB columns including
`step_history`, which grows with every step it took -- so asking for the whole
row to learn a status and a timestamp is the difference between a projection
and an unbounded payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TargetRunOutcome:
    """One target's state. Its *absence* from a lookup means the row is gone.

    `status` is the target module's own status name, not a schedule status:
    translating it is the ledger's job, and a provider that guessed would have
    to know what a schedule does with a cancellation.
    """

    status: str | None
    ended_at: datetime | None


__all__ = ["TargetRunOutcome"]
