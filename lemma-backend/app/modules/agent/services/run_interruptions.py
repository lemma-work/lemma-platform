"""Which exceptions interrupt an agent turn rather than end its run."""

from __future__ import annotations

from app.core.infrastructure.db.transient_errors import is_transient_database_error
from app.core.infrastructure.work import LeaseLost


def run_is_not_over(exc: BaseException) -> bool:
    """Whether ``exc`` interrupts the run rather than ending it.

    A cancellation (a lost lease, a shutdown past its grace), a shutdown yield
    at a step boundary, and a transient database failure all leave the run
    RUNNING for its work item to resume from the last committed step. Anything
    else is the run going wrong, and it fails.
    """
    if isinstance(exc, LeaseLost):
        # Another worker owns the turn now; it finishes the run, not this one.
        return True
    return not isinstance(exc, Exception) or is_transient_database_error(exc)
