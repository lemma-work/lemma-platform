"""What the identity module announces when a person deletes their account.

``AccountDeleted`` is emitted in the transaction that turns the account into a
tombstone, so every module holding something that answers to the person -- a
paired computer, a connected MCP client, a proved chat identity, the name on
their messages -- lets go of it by work that commits with the deletion.

The person's organization memberships are not part of it: they are removed in
the same transaction through the ordinary departure, which announces
``OrganizationMemberRemoved`` and each pod's ``PodMemberRemoved`` as any
member leaving does.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import SYSTEM_NAMESPACE, Fact
from app.modules.identity.domain.user_entities import DELETED_USER_NAME


class AccountDeleted(Fact):
    user_id: UUID

    def work_namespace(self) -> str:
        # A person is not scoped to one pod or organization.
        return SYSTEM_NAMESPACE


__all__ = ["AccountDeleted", "DELETED_USER_NAME"]
