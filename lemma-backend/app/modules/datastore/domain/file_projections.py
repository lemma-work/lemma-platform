"""Minimal file projections used by datastore scheduling.

In the domain layer rather than beside the query that builds it, because it is
the return type of ``DatastoreFileRepositoryPort`` — the scheduling services
depend on this shape, not on the ORM row it happens to be read from.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.modules.datastore.domain.file_entities import FileStatus


@dataclass(frozen=True, slots=True)
class DispatchableFileRef:
    """Everything the dispatch and recovery sweeps read from a file row.

    Four columns out of nineteen. The sweeps used to hydrate the full entity,
    which meant every tick dragged ``description``, ``path``, ``content_sha256``
    and -- worst -- ``last_processing_error``, an unbounded ``Text`` column
    holding extraction stack traces, through TOAST for rows it only ever
    identified and re-queued.

    Deferring those columns with ``load_only`` would not have worked:
    ``to_entity()`` reads every attribute, so each deferred one would lazy-load
    a SELECT per row. Projecting past the entity is what actually avoids the
    read.
    """

    id: UUID
    pod_id: UUID
    status: FileStatus
    metadata: dict[str, Any] | None
