"""What the contacts module announces.

``ContactMerged`` is emitted in the transaction that found two contacts to be
one person; ``ContactBecameMember`` in the one that found a contact to be one of
the pod's members. This module has already moved what it keeps -- handles, web
sessions -- by then; subscribers move what they keep that names the merged
contact: conversations, contact-owned rows. A submodule rather than
``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace


class ContactMerged(Fact):
    pod_id: UUID
    #: The contact that stays.
    survivor_id: UUID
    #: The contact that turned out to be the survivor, and now points at it.
    merged_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


class ContactBecameMember(Fact):
    """A contact proved to be one of the pod's members: their handle is the
    member's now, and the contact holds nothing. Subscribers move what they
    keep that says the contact spoke -- a group's lines and seats -- onto the
    member; what was done *for* the contact (answers made from what is Public)
    stays as it was."""

    pod_id: UUID
    contact_id: UUID
    user_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["ContactBecameMember", "ContactMerged"]
