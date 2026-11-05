"""Why a record write happened, as triggers see it.

Recorded on every change in a pod's change log. A trigger decides what each
intent means to it (``bulk_writes`` / ``import_writes``); the writer only says
which it is. Bulk endpoints default to ``bulk``; connectors, pod imports and
syncs write ``import``; a caller may pass ``normal`` to make a bulk write fire
triggers like single writes do.
"""

from __future__ import annotations

from enum import StrEnum


class WriteIntent(StrEnum):
    NORMAL = "normal"
    BULK = "bulk"
    IMPORT = "import"


__all__ = ["WriteIntent"]
