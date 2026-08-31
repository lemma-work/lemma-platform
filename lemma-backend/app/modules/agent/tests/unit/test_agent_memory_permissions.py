"""Who can read an agent's memory.

An agent writes durable facts to four fixed locations, and two of them are
shared while two are private. That split is the whole point: `/memory` is what
the pod knows and every member should see, `/me` is what the agent knows about
*one person* and nobody else should. Both are ordinary datastore files, so the
split is enforced by path-derived visibility rather than by anything the memory
code does itself — which is exactly why it is worth pinning here, next to the
paths, rather than trusting it holds.

The rule: a path under the requester's own `/{user_id}` root is PERSONAL, and
everything else is POD.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization.context import ResourceVisibility
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.datastore.contracts import DatastoreAccessDeniedError
from app.modules.datastore.services.files.path_resolver import PathResolver

pytestmark = pytest.mark.unit


def _visibility_of(path: str, *, user_id) -> str:
    """What visibility a write to `path` lands with, as the service resolves it.

    `/me/...` is expanded to the requester's own root first; that expansion is
    what makes the personal tree personal, so the two steps have to be measured
    together.
    """
    resolver = PathResolver()
    resolved = resolver._resolve_api_path(path, requester_user_id=user_id)
    return resolver._default_visibility_for_path(resolved, user_id)


class TestPodMemoryIsSharedWithThePod:
    def test_the_pod_index_is_pod_visible(self) -> None:
        paths = agent_memory_paths_for_name("butler")

        assert _visibility_of(paths.pod_index, user_id=uuid4()) == (
            ResourceVisibility.POD.value
        )

    def test_the_agents_shared_index_is_pod_visible(self) -> None:
        paths = agent_memory_paths_for_name("butler")

        assert _visibility_of(paths.pod_agent_index, user_id=uuid4()) == (
            ResourceVisibility.POD.value
        )

    def test_two_members_resolve_the_shared_paths_identically(self) -> None:
        """Shared memory has to be the same file for everyone, or it is not
        shared -- each member would quietly get their own copy."""
        paths = agent_memory_paths_for_name("butler")
        resolver = PathResolver()
        first, second = uuid4(), uuid4()

        for path in (paths.pod_index, paths.pod_agent_index):
            assert resolver._resolve_api_path(
                path, requester_user_id=first
            ) == resolver._resolve_api_path(path, requester_user_id=second)


class TestPersonalMemoryIsPerUser:
    def test_the_personal_index_is_personal(self) -> None:
        paths = agent_memory_paths_for_name("butler")

        assert _visibility_of(paths.personal_index, user_id=uuid4()) == (
            ResourceVisibility.PERSONAL.value
        )

    def test_the_agents_personal_index_is_personal(self) -> None:
        paths = agent_memory_paths_for_name("butler")

        assert _visibility_of(paths.personal_agent_index, user_id=uuid4()) == (
            ResourceVisibility.PERSONAL.value
        )

    def test_two_members_resolve_personal_paths_to_different_files(self) -> None:
        """The same `/me/...` string must mean a different file per person."""
        paths = agent_memory_paths_for_name("butler")
        resolver = PathResolver()
        first, second = uuid4(), uuid4()

        assert resolver._resolve_api_path(
            paths.personal_agent_index, requester_user_id=first
        ) != resolver._resolve_api_path(
            paths.personal_agent_index, requester_user_id=second
        )

    def test_a_personal_path_expands_to_the_requesters_own_root(self) -> None:
        paths = agent_memory_paths_for_name("butler")
        user_id = uuid4()

        resolved = PathResolver()._resolve_api_path(
            paths.personal_index, requester_user_id=user_id
        )

        assert resolved.startswith(f"/{user_id}/")


class TestOneUserCannotWriteIntoAnothersTree:
    def test_writing_under_another_users_root_is_denied(self) -> None:
        """`/me` is sugar for `/{user_id}`, so the raw form is spellable. An
        agent that got hold of another member's id must not be able to drop a
        file into their private memory by writing the expanded path."""
        someone_else = uuid4()
        paths = agent_memory_paths_for_name("butler")
        raw = f"/{someone_else}{paths.personal_agent_index.removeprefix('/me')}"

        with pytest.raises(DatastoreAccessDeniedError):
            PathResolver()._ensure_personal_write_path(
                path=raw, requester_user_id=uuid4()
            )

    def test_writing_under_your_own_root_is_allowed(self) -> None:
        user_id = uuid4()
        paths = agent_memory_paths_for_name("butler")
        raw = f"/{user_id}{paths.personal_agent_index.removeprefix('/me')}"
        resolver = PathResolver()

        resolver._ensure_personal_write_path(path=raw, requester_user_id=user_id)

        # And it lands where `/me` would have put it, personal to them.
        assert resolver._default_visibility_for_path(raw, user_id) == (
            ResourceVisibility.PERSONAL.value
        )

    def test_a_shared_path_is_not_mistaken_for_a_personal_one(self) -> None:
        paths = agent_memory_paths_for_name("butler")
        user_id = uuid4()
        resolver = PathResolver()

        resolver._ensure_personal_write_path(
            path=paths.pod_index, requester_user_id=user_id
        )

        assert resolver._default_visibility_for_path(paths.pod_index, user_id) == (
            ResourceVisibility.POD.value
        )


def test_every_memory_path_is_covered_by_one_of_the_two_rules() -> None:
    """If a fifth scope is ever added, it must land on purpose, not by
    defaulting to POD because nobody looked."""
    paths = agent_memory_paths_for_name("butler")
    user_id = uuid4()

    assert {
        path: _visibility_of(path, user_id=user_id)
        for path in (
            paths.pod_index,
            paths.pod_agent_index,
            paths.personal_index,
            paths.personal_agent_index,
        )
    } == {
        paths.pod_index: ResourceVisibility.POD.value,
        paths.pod_agent_index: ResourceVisibility.POD.value,
        paths.personal_index: ResourceVisibility.PERSONAL.value,
        paths.personal_agent_index: ResourceVisibility.PERSONAL.value,
    }
