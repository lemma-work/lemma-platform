"""Unit: a workload (agent/function) authorizes a pod file via the file alone
(grant cascade covers ancestor-folder grants), while a human still walks the
ancestor chain (RESTRICTED-folder visibility) — except for a PUBLIC document,
which is authorized on its own so the folders above it are not a second gate."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceVisibility,
)
from app.core.authorization.service import Authorizer
from app.core.domain.errors import DomainError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.path_resolver import PathResolver

#: Signed in, but in no pod and no organization of the pod under test.
OUTSIDER_ID = uuid4()


def _file(pod_id, path: str) -> DatastoreFileEntity:
    return DatastoreFileEntity(
        id=uuid4(),
        pod_id=pod_id,
        owner_user_id=uuid4(),
        kind=FileKind.FILE,
        visibility="POD",
        path=path,
        name=path.rsplit("/", 1)[-1],
        description=None,
        mime_type="text/markdown",
        size_bytes=1,
        search_enabled=False,
        status=FileStatus.NOT_REQUIRED,
    )


def _folder(pod_id, path: str) -> DatastoreFileEntity:
    folder = _file(pod_id, path)
    folder.kind = FileKind.FOLDER
    return folder


@pytest.mark.asyncio
async def test_workload_authorizes_file_only_so_deep_folder_grant_works():
    pod_id = uuid4()
    user_id = uuid4()
    authz = AsyncMock()
    file_repo = AsyncMock()
    authorizer = FileAuthorizer(authz, file_repo, PathResolver())

    file_entity = _file(pod_id, "/docs/eng/runbooks/guide.md")
    ctx = SimpleNamespace(actor_type=ActorType.AGENT, user_id=user_id)
    await authorizer._ensure_pod_document_path_access(file_entity, user_id, ctx=ctx)

    # Exactly one check — the file itself. The grant cascade matches a grant on
    # /docs/eng/runbooks without a separate grant on /docs and /docs/eng.
    assert authz.require_document_read.await_count == 1
    _, kwargs = authz.require_document_read.await_args
    assert kwargs["resource_name"] == "/docs/eng/runbooks/guide.md"
    file_repo.get_by_paths.assert_not_awaited()


@pytest.mark.asyncio
async def test_human_still_walks_the_ancestor_chain():
    pod_id = uuid4()
    user_id = uuid4()
    authz = AsyncMock()
    file_repo = AsyncMock()
    file_repo.get_by_paths.return_value = [
        _folder(pod_id, "/docs"),
        _folder(pod_id, "/docs/eng"),
        _folder(pod_id, "/docs/eng/runbooks"),
    ]
    authorizer = FileAuthorizer(authz, file_repo, PathResolver())

    file_entity = _file(pod_id, "/docs/eng/runbooks/guide.md")
    ctx = SimpleNamespace(actor_type=ActorType.USER, user_id=user_id)
    await authorizer._ensure_pod_document_path_access(file_entity, user_id, ctx=ctx)

    # 3 ancestor folders + the file itself.
    assert authz.require_document_read.await_count == 4


class TestListingAgreesWithOpening:
    """The bulk path had the opposite rule to the single-file path above.

    `get_visible_file_ids_for_items` walked every ancestor for every actor, so a
    workload's row was hidden unless each folder above it was separately
    granted — re-deriving inheritance under a rule that cancels the grant
    cascade it was walking over. An agent could open a file by name and not see
    it in a listing. In production that was 241 of 241 files withheld from an
    agent holding a real `folder.read` grant on the folder containing 200 of
    them, because nobody had granted the folder *above* it.
    """

    @staticmethod
    def _authorizer(visible_paths: set[str], items):
        authz = AsyncMock()
        file_repo = AsyncMock()
        file_repo.get_by_paths.return_value = []
        file_repo.filter_visible_ids.return_value = {
            item.id for item in items if item.path in visible_paths
        }
        return FileAuthorizer(authz, file_repo, PathResolver())

    async def _visible(self, actor_type, visible_paths: set[str]):
        pod_id = uuid4()
        user_id = uuid4()
        bench = _folder(pod_id, "/bench")
        frames = _folder(pod_id, "/bench/frames")
        guide = _file(pod_id, "/bench/frames/guide.md")
        items = [bench, frames, guide]

        authorizer = self._authorizer(visible_paths, items)
        ctx = SimpleNamespace(actor_type=actor_type, user_id=user_id)
        visible = await authorizer.get_visible_file_ids_for_items(
            pod_id=pod_id, requester_user_id=user_id, items=items, ctx=ctx
        )
        return {item.path for item in items if item.id in visible}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "actor_type",
        [
            ActorType.AGENT,
            ActorType.FUNCTION,
            ActorType.DELEGATED_USER_WORKLOAD,
        ],
    )
    async def test_a_granted_subfolder_lists_even_though_its_parent_is_not_granted(
        self, actor_type
    ):
        """The regression. `/bench` is not granted and must not need to be."""
        visible = await self._visible(
            actor_type, visible_paths={"/bench/frames", "/bench/frames/guide.md"}
        )

        assert visible == {"/bench/frames", "/bench/frames/guide.md"}

    @pytest.mark.asyncio
    async def test_a_workload_still_sees_nothing_it_was_not_granted(self):
        """The other direction, which is the one that matters: dropping the walk
        must not turn "no grant" into access. The cascade lives in the SQL, so a
        row absent from `filter_visible_ids` was never authorized."""
        visible = await self._visible(ActorType.AGENT, visible_paths=set())

        assert visible == set()

    @pytest.mark.asyncio
    async def test_a_human_still_loses_a_file_under_a_folder_they_cannot_read(self):
        """Humans keep the ancestor walk: for them a RESTRICTED folder hides
        what is inside it, and that is not what the cascade is for."""
        visible = await self._visible(
            ActorType.USER, visible_paths={"/bench/frames", "/bench/frames/guide.md"}
        )

        assert visible == set()


class TestAPublicDocumentIsNotGatedByItsFolders:
    """A document opened to every signed-in account has to actually open.

    The ancestor walk asks for `folder.read` on each folder above the file. A
    non-member holds no pod permissions and a folder is POD by default, so the
    walk denied every PUBLIC document that was not sitting at the datastore root
    — while the share dialog promised "anyone with a Lemma account can open it",
    and `/pods/{id}/resources/document/preview`, which authorizes the document
    alone, said yes immediately before the download said no.
    """

    @staticmethod
    def _public(pod_id, path: str) -> DatastoreFileEntity:
        entity = _file(pod_id, path)
        entity.visibility = "PUBLIC"
        return entity

    @pytest.mark.asyncio
    async def test_only_the_document_itself_is_checked(self):
        pod_id = uuid4()
        user_id = uuid4()
        authz = AsyncMock()
        file_repo = AsyncMock()
        authorizer = FileAuthorizer(authz, file_repo, PathResolver())

        file_entity = self._public(pod_id, "/library/brand-directions.md")
        ctx = SimpleNamespace(actor_type=ActorType.USER, user_id=user_id)
        await authorizer._ensure_pod_document_path_access(file_entity, user_id, ctx=ctx)

        assert authz.require_document_read.await_count == 1
        _, kwargs = authz.require_document_read.await_args
        assert kwargs["resource_name"] == "/library/brand-directions.md"
        file_repo.get_by_paths.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_pod_document_still_walks_its_folders(self):
        """The other direction: only the document's OWN visibility short-circuits,
        so a RESTRICTED folder still contains what is inside it."""
        pod_id = uuid4()
        user_id = uuid4()
        authz = AsyncMock()
        file_repo = AsyncMock()
        file_repo.get_by_paths.return_value = [_folder(pod_id, "/library")]
        authorizer = FileAuthorizer(authz, file_repo, PathResolver())

        file_entity = _file(pod_id, "/library/brand-directions.md")
        ctx = SimpleNamespace(actor_type=ActorType.USER, user_id=user_id)
        await authorizer._ensure_pod_document_path_access(file_entity, user_id, ctx=ctx)

        # The folder and the file.
        assert authz.require_document_read.await_count == 2


class TestANonMemberActuallyGetsIn:
    """The same thing one layer down, against the real `Authorizer`.

    The shape tests above pin *which* resources are checked; these pin what the
    answer is, because the bug was a denial, not a call count.
    """

    @staticmethod
    def _ctx(rows) -> Context:
        """A signed-in user with no pod membership and no grants."""
        by_id = {row.id: row for row in rows}
        authorizer = Authorizer(session=None)

        async def _hydrate(resource):
            # Stands in for the DB read: `_require_document_action` builds a bare
            # ResourceRef, and visibility is exactly what this decision turns on.
            row = by_id.get(resource.resource_id)
            if row is None:
                return resource
            return replace(
                resource,
                visibility=ResourceVisibility(row.visibility),
                owner_user_id=row.owner_user_id,
                path=row.path,
            )

        authorizer._hydrate_resource = _hydrate
        return Context(
            actor_type=ActorType.USER,
            actor_id=str(OUTSIDER_ID),
            user_id=OUTSIDER_ID,
            organization_id=uuid4(),
            pod_id=rows[0].pod_id,
            permission_ids=frozenset(),
            principal_refs=frozenset(),
            authorizer=authorizer,
        )

    @staticmethod
    def _authorizer(rows) -> FileAuthorizer:
        file_repo = AsyncMock()
        file_repo.get_by_paths.side_effect = lambda pod_id, paths: [
            row for row in rows if row.path in paths
        ]
        return FileAuthorizer(
            DatastoreAuthorization(object()), file_repo, PathResolver()
        )

    @pytest.mark.asyncio
    async def test_a_public_document_inside_a_folder_opens(self):
        pod_id = uuid4()
        folder = _folder(pod_id, "/library")
        doc = _file(pod_id, "/library/brand-directions.md")
        doc.visibility = "PUBLIC"
        rows = [folder, doc]

        await self._authorizer(rows).ensure_file_path_access(
            doc, OUTSIDER_ID, ctx=self._ctx(rows)
        )

    @pytest.mark.asyncio
    async def test_a_pod_document_inside_the_same_folder_does_not(self):
        pod_id = uuid4()
        folder = _folder(pod_id, "/library")
        doc = _file(pod_id, "/library/brand-directions.md")
        rows = [folder, doc]

        with pytest.raises(DomainError):
            await self._authorizer(rows).ensure_file_path_access(
                doc, OUTSIDER_ID, ctx=self._ctx(rows)
            )
