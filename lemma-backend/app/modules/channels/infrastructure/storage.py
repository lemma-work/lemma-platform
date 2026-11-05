"""Storage parameters of the channels tables that churn.

``channels`` is rewritten with every line (its head) and keeps room on each
page for the new version, so the update stays on the page and touches no
index (HOT). Lines and deliveries are vacuumed at a small fraction of the
table rather than Postgres's fifth: they are large, and their dead rows are
what an inbound reads past. Migration ``0058_channels`` sets the same; the
two are compared by ``test_channel_storage_params``.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import DDL, Table, event

from app.modules.channels.infrastructure.delivery_models import (
    ChannelMessageDeliveryModel,
)
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import ChannelModel

#: Each table's ``ALTER TABLE … SET (…)``.
STORAGE: dict[str, str] = {
    "channels": "fillfactor = 80",
    "channel_messages": (
        "autovacuum_vacuum_scale_factor = 0.02, "
        "autovacuum_vacuum_insert_scale_factor = 0.02"
    ),
    "channel_message_deliveries": "autovacuum_vacuum_scale_factor = 0.02",
}


def _set_on_create(table: Table) -> None:
    event.listen(
        table,
        "after_create",
        DDL(f"ALTER TABLE {table.name} SET ({STORAGE[table.name]})"),
    )


for _model in (ChannelModel, ChannelMessageModel, ChannelMessageDeliveryModel):
    _set_on_create(cast(Table, _model.__table__))

__all__ = ["STORAGE"]
