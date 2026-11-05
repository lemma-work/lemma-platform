"""The measure check behind ``OutcomeMeasures``, for the modules that set one.

``pod`` keeps what a pod owns and its API sets it; the agent's tool and a
Lemma started from a template set it too. Each holds the port
(``app/core/ports/outcome_measures.py``) and binds it with this factory, so
none of them names the datastore service that reads the query.

A submodule rather than ``contracts/__init__``: it reaches the service layer.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.ports.outcome_measures import OutcomeMeasures
from app.modules.datastore.services.wiring import build_outcome_measure_service


def build_outcome_measures(uow: SqlAlchemyUnitOfWork) -> OutcomeMeasures:
    """A measure check bound to this transaction's authorization."""
    return build_outcome_measure_service(uow)


__all__ = ["build_outcome_measures"]
