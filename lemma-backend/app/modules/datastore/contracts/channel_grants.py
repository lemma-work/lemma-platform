"""Tables a group's people outside the pod may read, or add rows to.

What other modules need of ``services/channel_grants``: the channels API that
grants and lists them, and the guest's run in a group that reads and adds
through them. The refusals name neither the table nor a value.
"""

from __future__ import annotations

from app.modules.datastore.domain.public_rows import (
    PublicRowRefused,
    PublicRowsClosed,
)
from app.modules.datastore.services.channel_grants import (
    MAX_GRANTED_ROWS,
    ChannelGrant,
    add_granted_row,
    channel_grants,
    grant_table,
    read_granted_rows,
    revoke_table,
)
from app.modules.datastore.services.public_rows import OpeningRefused

__all__ = [
    "MAX_GRANTED_ROWS",
    "ChannelGrant",
    "OpeningRefused",
    "PublicRowRefused",
    "PublicRowsClosed",
    "add_granted_row",
    "channel_grants",
    "grant_table",
    "read_granted_rows",
    "revoke_table",
]
