"""`/me` names the requester's personal folder, so nothing may be created as it.

`lemma files upload note.md /me/` once created a shared file literally called
`/me` beside the personal folder: every path lookup reads `/me` as the folder,
so the file could not be opened, and deleting it by path would delete the folder.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.files.path_resolver import PathResolver


def test_a_child_named_me_at_the_root_is_refused():
    with pytest.raises(DatastoreValidationError, match="personal folder"):
        PathResolver()._join_child_path("/", "me")


@pytest.mark.parametrize(
    ("directory", "name", "path"),
    [
        ("/", "notes.md", "/notes.md"),
        ("/reports", "me", "/reports/me"),
        ("/", "meeting", "/meeting"),
    ],
)
def test_other_children_join_as_before(directory, name, path):
    assert PathResolver()._join_child_path(directory, name) == path
