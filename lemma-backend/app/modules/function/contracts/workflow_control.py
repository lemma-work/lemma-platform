"""How `workflow` runs a function.

One factory, and the same exception to "operations, not classes" that
`agent/contracts/workflow_control.py` is: `FunctionPort` is a port with three
methods the engine holds for the length of a run, so publishing three free
functions would only make the caller reassemble them.

The class it returns is still not published -- the caller names the port, not
the implementation, and cannot reach past it to `FunctionUseCases` or to
`FunctionRunRepository`, which is what `app/composition/workflow_function.py`
left in reach.

A submodule for the same reason as its siblings in `agent`, `schedule` and
`datastore`: this reaches the repository layer, and `contracts/__init__` is
imported by anything that wants any contract at all.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.infrastructure.workflow_control import (
    FunctionControlAdapter,
)
from app.modules.workflow.contracts import FunctionPort


def build_function_control_adapter(uow: SqlAlchemyUnitOfWork) -> FunctionPort:
    """A function driver bound to this transaction."""
    return FunctionControlAdapter(uow)


__all__ = ["build_function_control_adapter"]
