"""Identity's published schema: the two mapped classes another module may join.

This is a deliberate exception to "a module reaches another module only through
contracts or a domain event", and it is the only kind of exception that rule
takes. `pod_members.organization_member_id` is a foreign key to
`organization_members.id`, `PodMember.to_entity()` fills `user_email` and
`user_name` from the row behind it, and the query does that in one statement:

    joinedload(PodMember.organization_member).joinedload(OrganizationMember.user)

SQLAlchemy 2.0 removed string-based loader options, so that hop needs the class.
Everything else considered costs more than it removes — denormalising the email
onto `pod_members` buys a migration, a backfill, a dual-write and a permanent
staleness bug; a database view is a second schema to keep in step and does not
help `joinedload` at all; and a `list_pod_members` operation in identity inverts
ownership, since pod owns pod membership.

**What another module may do with these.** `select` and `join`. Not construct,
mutate, flush or delete, and not follow a relationship off them. Renaming a
column here is a breaking change to the modules listed below, which is the cost
of publishing them and the reason the list is short.

Consumers: `pod/infrastructure/pod_repositories.py`.
"""

from __future__ import annotations

from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User

__all__ = ["OrganizationMember", "User"]
