"""Pod's published schema: the two mapped classes another module may join.

This is a deliberate exception to "a module reaches another module only through
contracts or a domain event", and it is the only kind of exception that rule
takes -- the same one `identity/contracts/orm.py` takes, for the same reason.
Three statements in `agent_surfaces` need the class rather than an operation,
because SQLAlchemy 2.0 removed string-based loader and join targets:

- `surface_repository.py` joins `Pod` to keep surfaces in live pods, and reads
  `Pod.organization_id` as a scalar subquery to scope a lookup to one org.
- `connection_owner_adapter.py` and `routing_resolution_adapter.py` join
  `PodMember` to identity's `OrganizationMember` in a single statement.

An operation in pod would invert ownership for the first two -- a surface query
filtered by pod liveness is agent_surfaces' query -- and for the third it would
have to return a row already joined across two modules' tables, which is the
join we are trying not to hide.

**What another module may do with these.** `select` and `join`. Not construct,
mutate, flush or delete, and not follow a relationship off them. Renaming a
column here is a breaking change to the consumers below, which is the cost of
publishing them and the reason the list is short.

Consumers: `agent_surfaces/infrastructure/repositories/surface_repository.py`,
`agent_surfaces/infrastructure/adapters/connection_owner_adapter.py`,
`agent_surfaces/infrastructure/adapters/routing_resolution_adapter.py`.
"""

from __future__ import annotations

from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember

__all__ = ["Pod", "PodMember"]
