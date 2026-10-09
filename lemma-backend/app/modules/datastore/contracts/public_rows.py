"""Tables open to people outside the pod, for the modules that serve them.

``agent_surfaces`` asks what a visitor's page may fill and adds their row;
``agent`` tells a visitor's run which tables it may help fill. Neither learns
anything else about the pod's tables. See ``domain/public_rows`` for the rules
and ``services/public_rows`` for how a row is added.
"""

from __future__ import annotations

from app.modules.datastore.domain.public_rows import (
    OpenTable,
    PublicAudience,
    PublicColumn,
    PublicRowRefused,
    PublicRowsClosed,
    public_values,
)
from app.modules.datastore.services.public_rows import (
    add_visitor_row,
    visitor_table,
)
from app.modules.datastore.services.public_rows import (
    open_tables as open_table_names,
)

__all__ = [
    "OpenTable",
    "PublicAudience",
    "PublicColumn",
    "PublicRowRefused",
    "PublicRowsClosed",
    "add_visitor_row",
    "open_table_names",
    "public_values",
    "visitor_table",
]
