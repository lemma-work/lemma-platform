"""Which module, if any, says what a deployment's plans allow.

Declared through `LemmaModule.plan_limits` and collected at assembly by
`configure_plan_limits`, the way `pod_liveness` is: a field on the module list,
not a setter someone has to remember to call. The open-source module list
declares none, so `build_plan_limits` answers ``None`` and nothing is limited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.ports.plan_limits import PlanLimits, PlanLimitsFactory

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

_plan_limits_factory: PlanLimitsFactory | None = None


def declare_plan_limits(factory: PlanLimitsFactory | None) -> None:
    """Register the module that answers for plans, or clear it with ``None``."""
    global _plan_limits_factory
    _plan_limits_factory = factory


def build_plan_limits(uow: SqlAlchemyUnitOfWork) -> PlanLimits | None:
    """The answerer for this unit of work, or ``None`` when nothing is limited."""
    if _plan_limits_factory is None:
        return None
    return _plan_limits_factory(uow)


def reset_plan_limits() -> None:
    """Forget the declared provider. For tests, which must not leak one."""
    declare_plan_limits(None)
