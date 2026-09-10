"""The widget store behind a conversation's rendered HTML.

One factory, and the same exception to "operations, not classes" that
`agent/contracts/workflow_control.py` is: `WidgetContentReader` is a port the
consumer declares in `app/core/ports/widget_content.py` and holds, and a free
function would only make it reassemble one.

`WidgetAssetService` is still not published -- `apps` names the port, not the
implementation, and cannot reach past it.

This is the last of the four files in `docs/engineering/design.md`'s canonical
cross-module example, and the only one that was wrong: the port and the consumer
were always right, and the binding sat in `app/composition/widget_content.py`
because there was nowhere in `agent` to publish it from.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.ports.widget_content import WidgetContentReader
from app.modules.agent.services.widget_asset_service import WidgetAssetService


def build_widget_content_reader(uow: SqlAlchemyUnitOfWork) -> WidgetContentReader:
    """A reader for widget HTML, bound to this transaction."""
    return WidgetAssetService(uow)


__all__ = ["build_widget_content_reader"]
