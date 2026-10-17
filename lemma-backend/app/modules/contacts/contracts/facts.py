"""What the contacts module announces.

``ContactMerged`` is emitted in the transaction that found two contacts to be
one person. This module has already moved what it keeps -- handles, web
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


__all__ = ["ContactMerged"]
