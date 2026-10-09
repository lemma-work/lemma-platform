"""Tables people outside the pod may read, for the module that serves them.

``agent_surfaces`` reads a table's open columns for a visitor's page, and learns
nothing else about the pod's tables. See ``domain/public_reads`` for the rules
and ``services/public_reads`` for how the rows are read.
"""

from __future__ import annotations

from app.modules.datastore.domain.public_reads import (
    MAX_PUBLIC_READ_ROWS,
    PublicReadsClosed,
    PublicValue,
    ReadableTable,
    ReadColumn,
)
from app.modules.datastore.services.public_reads import visitor_rows

__all__ = [
    "MAX_PUBLIC_READ_ROWS",
    "PublicReadsClosed",
    "PublicValue",
    "ReadColumn",
    "ReadableTable",
    "visitor_rows",
]
