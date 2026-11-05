"""Every column that names a contact is moved when two contacts merge.

A merge keeps the older contact and points the other at it
(``merged_into_id``); everything that named the merged one must name the
survivor afterwards, or a row goes on naming somebody who is now only a
pointer -- the conversation, seat or question quietly lost to them. Nothing
finds such a column for you: so every foreign key to ``contacts.id`` in the
models is listed here with what moves it, and a new one fails this test until
it is given a merge -- and listed.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.infrastructure.db.base import Base
from app.modules.agent.application.channels.fold import FoldConversations
from app.modules.channels.infrastructure.contact_channels import (
    DEDUPED_ON_MERGE,
    RENAMED_ON_MERGE,
    ContactChannels,
)
from app.modules.contacts.infrastructure.repository import ContactRepository
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorSessionRepository,
)
from app.modules.test_support.e2e_base import _import_e2e_models

pytestmark = pytest.mark.unit

#: ``(table, column)`` -> what moves it on a merge. Channels' columns are not
#: here: ``contact_channels`` declares its own (below).
_MOVED_ELSEWHERE: dict[tuple[str, str], Callable[..., object]] = {
    ("contacts", "merged_into_id"): ContactRepository.merge,
    ("contact_identities", "contact_id"): ContactRepository.merge,
    ("visitor_sessions", "contact_id"): VisitorSessionRepository.repoint_contact,
    ("agent_conversations", "contact_id"): FoldConversations.merge_contact,
    # The merged contact's own channel folds into the survivor's.
    ("channels", "contact_id"): ContactChannels.fold,
}


def _channels_moved() -> set[tuple[str, str]]:
    columns = [*RENAMED_ON_MERGE, *(column for column, _ in DEDUPED_ON_MERGE)]
    return {(column.class_.__tablename__, column.key) for column in columns}


def _contact_keys() -> set[tuple[str, str]]:
    _import_e2e_models()
    return {
        (table.name, key.parent.name)
        for table in Base.metadata.tables.values()
        for key in table.foreign_keys
        if key.column.table.name == "contacts" and key.column.name == "id"
    }


def test_every_foreign_key_to_a_contact_has_a_merge():
    keys = _contact_keys()
    moved = set(_MOVED_ELSEWHERE) | _channels_moved()

    unmerged = keys - moved
    assert not unmerged, (
        f"{sorted(unmerged)} name a contact, and nothing moves them when two "
        "contacts merge. Move them in the merge (contacts' repository, a "
        "ContactMerged handler, or channels' RENAMED_ON_MERGE / "
        "DEDUPED_ON_MERGE) and list them here."
    )
    gone = moved - keys
    assert not gone, f"{sorted(gone)} are listed as merged but name no contact"
