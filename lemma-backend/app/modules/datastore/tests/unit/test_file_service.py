from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError
from app.modules.datastore.domain.errors import DatastoreConflictError
from app.modules.datastore.domain.errors import DatastoreFileNotFoundError
from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileSearchResult,
    DatastoreFileUpdateEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.services.file_service import DatastoreFileService


def _deny_admin_allow_other_actions(authorization_service_mock: AsyncMock) -> None:
    async def _require_user_action(*, action, **_kwargs):
        if action == Permissions.FOLDER_DELETE:
            raise DatastoreAccessDeniedError("not admin")

    authorization_service_mock.require_user_action.side_effect = _require_user_action


def _child_path(parent_path: str | None, name: str) -> str:
    if not parent_path or parent_path == "/":
        return f"/{name}"
    return f"{parent_path}/{name}"


def _personal_path(user_id, path: str) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"/{user_id}{suffix}"


def _ctx(user_id: UUID) -> SimpleNamespace:
    """A minimal ctx carrying ``user_id`` for the file-service facade.

    Deliberately exposes no ``require`` method so datastore authorization falls
    through to the legacy ``authorization_service`` mock the tests configure
    (mirroring the previous bare-``object()`` ctx behaviour)."""
    return SimpleNamespace(user_id=user_id)


async def _update_file_by_path(file_service, pod_id, update_entity, ctx):
    """Drive a full update through the saga phases (the production path; the old
    single-call convenience method was removed)."""
    plan = await file_service.resolve_update_file(pod_id, update_entity, ctx=ctx)
    await file_service.write_update_storage(plan, update_entity)
    updated = await file_service.persist_update_file(plan)
    await file_service.finalize_update_file(plan, updated)
    return updated


async def _delete_path_by_path(file_service, pod_id, path, ctx):
    """Drive a full delete through the saga phases (production path)."""
    cleanup = await file_service.resolve_delete_path(pod_id, path, ctx=ctx)
    await file_service.cleanup_deleted_paths(
        cleanup.pod_id,
        is_folder=cleanup.is_folder,
        folder_prefix=cleanup.folder_prefix,
        files=list(cleanup.files),
    )


def _make_folder(
    *,
    pod_id,
    name: str,
    parent_path: str | None = "/",
    file_id=None,
    visibility: str = "PERSONAL",
) -> DatastoreFileEntity:
    return DatastoreFileEntity(
        id=file_id or uuid4(),
        pod_id=pod_id,
        owner_user_id=uuid4(),
        kind=FileKind.FOLDER,
        path=_child_path(parent_path, name),
        name=name,
        visibility=visibility,
        description=None,
        mime_type="application/x-directory",
        size_bytes=0,
        search_enabled=False,
        status=FileStatus.NOT_REQUIRED,
    )


def _make_file(
    *,
    pod_id,
    name: str,
    owner_user_id,
    visibility: str,
    parent_path: str | None = "/",
    file_id=None,
) -> DatastoreFileEntity:
    resolved_file_id = file_id or uuid4()
    return DatastoreFileEntity(
        id=resolved_file_id,
        pod_id=pod_id,
        owner_user_id=owner_user_id,
        kind=FileKind.FILE,
        path=_child_path(parent_path, name),
        name=name,
        visibility=visibility,
        description=None,
        mime_type="text/plain",
        size_bytes=10,
        search_enabled=False,
        status=FileStatus.NOT_REQUIRED,
    )


@pytest.fixture
def file_repository_mock() -> AsyncMock:
    repository = AsyncMock()
    repository.filter_visible_ids.side_effect = lambda **kwargs: set(kwargs["file_ids"])
    return repository


@pytest.fixture
def storage_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def reindex_queue_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def file_service(
    file_repository_mock: AsyncMock,
    storage_mock: AsyncMock,
    reindex_queue_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
) -> DatastoreFileService:
    return DatastoreFileService(
        file_repository=file_repository_mock,
        storage=storage_mock,
        authorization_service=authorization_service_mock,
    )


@pytest.mark.asyncio
async def test_create_folder_duplicate_name_in_same_parent_raises_conflict(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
):
    user_id = uuid4()
    pod_id = uuid4()
    parent_path = "/parent"

    parent = _make_folder(
        pod_id=pod_id,
        name="parent",
        parent_path=f"/{user_id}",
        visibility="POD",
    )
    parent.path = parent_path
    parent.owner_user_id = None
    existing = _make_folder(
        pod_id=pod_id,
        name="research",
        parent_path=parent_path,
        visibility="POD",
    )
    existing.owner_user_id = None

    async def _file_lookup(*args, **kwargs):
        path = kwargs.get("path")
        if path is None and len(args) >= 3:
            path = args[2]
        if path == parent_path:
            return parent
        if path == f"{parent_path}/research":
            return existing
        return None

    file_repository_mock.get_by_path.side_effect = _file_lookup

    with pytest.raises(
        DatastoreConflictError,
        match=f"already exists at '{parent_path}/research'",
    ):
        await file_service.create_folder(
            pod_id=pod_id,
            path="/parent/research",
            ctx=_ctx(user_id),
        )

    file_repository_mock.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_pod_folder_rename_into_duplicate_raises_conflict(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
):
    user_id = uuid4()
    pod_id = uuid4()
    folder = _make_folder(pod_id=pod_id, name="drafts", visibility="POD")
    folder.owner_user_id = None

    file_repository_mock.get_by_path.return_value = folder
    file_repository_mock.get_by_path.side_effect = [
        folder,
        _make_folder(pod_id=pod_id, name="research", visibility="POD"),
    ]

    with pytest.raises(
        DatastoreConflictError,
        match="already exists at '/research'",
    ):
        await _update_file_by_path(file_service, 
            pod_id,
            DatastoreFileUpdateEntity(path=folder.path, new_path="/research"),
            ctx=_ctx(user_id),
        )

    file_repository_mock.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_pod_folder_move_into_duplicate_parent_raises_conflict(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
):
    user_id = uuid4()
    pod_id = uuid4()
    folder = _make_folder(pod_id=pod_id, name="research", visibility="POD")
    folder.owner_user_id = None
    destination_folder = _make_folder(
        pod_id=pod_id,
        name="destination",
        visibility="POD",
    )
    destination_folder.owner_user_id = None

    async def get_by_path(*args, **kwargs):
        path = kwargs.get("path")
        if path is None and len(args) >= 2:
            path = args[1]
        if path == folder.path:
            return folder
        if path == destination_folder.path:
            return destination_folder
        if path == f"{destination_folder.path}/research":
            return _make_folder(
                pod_id=pod_id,
                name="research",
                parent_path=destination_folder.path,
            )
        return None

    file_repository_mock.get_by_path.side_effect = get_by_path

    with pytest.raises(
        DatastoreConflictError,
        match=f"already exists at '{destination_folder.path}/research'",
    ):
        await _update_file_by_path(file_service, 
            pod_id,
            DatastoreFileUpdateEntity(
                path=folder.path,
                new_path=f"{destination_folder.path}/research",
            ),
            ctx=_ctx(user_id),
        )

    file_repository_mock.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_file_under_me_uses_personal_visibility(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    storage_mock: AsyncMock,
):
    user_id = uuid4()
    pod_id = uuid4()

    file_repository_mock.get_by_path.return_value = None

    async def create_file(entity):
        entity.id = uuid4()
        return entity

    file_repository_mock.create.side_effect = create_file
    file_repository_mock.update.side_effect = lambda entity: entity

    created = await file_service.create_file(
        pod_id=pod_id,
        name="artifact.txt",
        file_content=b"hello",
        ctx=_ctx(user_id),
        directory_path="/me",
        search_enabled=True,
    )

    assert created.visibility == "PERSONAL"
    assert created.search_enabled is True
    assert created.status == FileStatus.PENDING
    storage_mock.upload_file.assert_awaited_once_with(
        f"pods/{pod_id}/files/{user_id}/artifact.txt",
        b"hello",
    )


@pytest.mark.asyncio
async def test_list_files_defaults_to_pod_root_with_me_folder(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    other_user_id = uuid4()
    pod_id = uuid4()

    _deny_admin_allow_other_actions(authorization_service_mock)
    mine_private = _make_file(
        pod_id=pod_id,
        name="mine-private.txt",
        owner_user_id=requester_user_id,
        visibility="PERSONAL",
    )
    team_pod = _make_file(
        pod_id=pod_id,
        name="team-pod.txt",
        owner_user_id=other_user_id,
        visibility="POD",
    )
    other_private = _make_file(
        pod_id=pod_id,
        name="other-private.txt",
        owner_user_id=other_user_id,
        visibility="PERSONAL",
    )

    file_repository_mock.list_visible_by_datastore.return_value = ([team_pod], None)
    ctx = _ctx(requester_user_id)

    items, _ = await file_service.list_files(pod_id, ctx)

    names = {item.name for item in items}
    # The pod root surfaces both synthetic folders (/me and /skills) by default.
    assert names == {"me", "skills", "team-pod.txt"}
    personal_root = next(item for item in items if item.name == "me")
    assert personal_root.path == f"/{requester_user_id}"
    assert personal_root.visibility == "PERSONAL"
    skills_root = next(item for item in items if item.name == "skills")
    assert skills_root.path == "/skills"
    assert skills_root.kind == FileKind.FOLDER
    # Two synthetic folders occupy two slots, so the repository page is limit-2.
    file_repository_mock.list_visible_by_datastore.assert_awaited_once_with(
        pod_id=pod_id,
        ctx=ctx,
        directory_path="/",
        limit=98,
        cursor=None,
    )
    assert mine_private.name not in names
    assert other_private.name not in names


@pytest.mark.asyncio
async def test_list_files_personal_root_uuid_includes_me_folder(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    pod_id = uuid4()

    _deny_admin_allow_other_actions(authorization_service_mock)
    child = _make_file(
        pod_id=pod_id,
        name="notes.txt",
        owner_user_id=requester_user_id,
        visibility="PERSONAL",
        parent_path=f"/{requester_user_id}",
    )
    file_repository_mock.list_visible_by_datastore.return_value = ([child], None)
    ctx = _ctx(requester_user_id)

    items, _ = await file_service.list_files(
        pod_id,
        ctx,
        directory_path=f"/{requester_user_id}",
    )

    assert [item.name for item in items] == ["me", "notes.txt"]
    file_repository_mock.list_visible_by_datastore.assert_awaited_once_with(
        pod_id=pod_id,
        ctx=ctx,
        directory_path=f"/{requester_user_id}",
        limit=99,
        cursor=None,
    )


@pytest.mark.asyncio
async def test_list_files_me_cursor_does_not_skip_first_real_item(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    first_pod_item = _make_file(
        pod_id=pod_id,
        name="team-pod.txt",
        owner_user_id=uuid4(),
        visibility="POD",
    )

    _deny_admin_allow_other_actions(authorization_service_mock)
    file_repository_mock.list_visible_by_datastore.return_value = (
        [first_pod_item],
        None,
    )
    ctx = _ctx(requester_user_id)

    async def page(cursor):
        return await file_service.list_files(pod_id, ctx, limit=1, cursor=cursor)

    # limit=1 has to page through the two synthetic root folders (/me, /skills)
    # before reaching the first real item — without skipping it.
    first_page, first_cursor = await page(None)
    second_page, second_cursor = await page(first_cursor)
    third_page, third_cursor = await page(second_cursor)

    assert [item.name for item in first_page] == ["me"]
    assert [item.name for item in second_page] == ["skills"]
    assert [item.name for item in third_page] == ["team-pod.txt"]
    assert first_cursor is not None and second_cursor is not None
    assert third_cursor is None
    # The repository is only queried once the synthetic prefix is exhausted, and
    # it pages from the start (cursor=None) so the first real item is not lost.
    repo_calls = file_repository_mock.list_visible_by_datastore.await_args_list
    assert len(repo_calls) == 1
    assert repo_calls[0].kwargs["cursor"] is None


@pytest.mark.asyncio
async def test_list_files_under_me_hides_unshared_private_files_for_non_owner_non_admin(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    other_user_id = uuid4()
    pod_id = uuid4()

    _deny_admin_allow_other_actions(authorization_service_mock)
    listed_items = [
        _make_file(
            pod_id=pod_id,
            name="mine-private.txt",
            owner_user_id=requester_user_id,
            visibility="PERSONAL",
        ),
        _make_file(
            pod_id=pod_id,
            name="team-pod.txt",
            owner_user_id=other_user_id,
            visibility="POD",
        ),
        _make_file(
            pod_id=pod_id,
            name="other-private.txt",
            owner_user_id=other_user_id,
            visibility="PERSONAL",
        ),
    ]
    file_repository_mock.list_visible_by_datastore.return_value = (
        [listed_items[0]],
        None,
    )
    ctx = _ctx(requester_user_id)

    items, _ = await file_service.list_files(
        pod_id,
        ctx,
        directory_path="/me",
    )

    names = {item.name for item in items}
    assert names == {"mine-private.txt"}
    file_repository_mock.list_visible_by_datastore.assert_awaited_once_with(
        pod_id=pod_id,
        ctx=ctx,
        directory_path=f"/{requester_user_id}",
        limit=100,
        cursor=None,
    )


@pytest.mark.asyncio
async def test_get_file_denies_private_file_for_non_owner_non_admin(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    other_user_id = uuid4()
    pod_id = uuid4()
    private_file = _make_file(
        pod_id=pod_id,
        name="restricted.txt",
        owner_user_id=other_user_id,
        visibility="PERSONAL",
    )

    _deny_admin_allow_other_actions(authorization_service_mock)
    file_repository_mock.get.return_value = private_file
    file_repository_mock.get_by_path.return_value = private_file

    with pytest.raises(DatastoreAccessDeniedError, match="private file"):
        await file_service.get_file(private_file.id, ctx=_ctx(requester_user_id))


@pytest.mark.asyncio
async def test_create_file_in_private_parent_denied_for_non_owner_non_admin(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    parent_owner_id = uuid4()
    pod_id = uuid4()
    parent_folder = _make_folder(
        pod_id=pod_id,
        name="private-parent",
        parent_path=f"/{parent_owner_id}",
    )
    parent_folder.owner_user_id = parent_owner_id
    parent_folder.visibility = "PERSONAL"

    async def _folder_lookup(*args, **kwargs):
        path = kwargs.get("path")
        if path is None and len(args) >= 2:
            path = args[1]
        if path == parent_folder.path:
            return parent_folder
        return None

    file_repository_mock.get_by_path.side_effect = _folder_lookup
    _deny_admin_allow_other_actions(authorization_service_mock)

    with pytest.raises(DatastoreAccessDeniedError, match="under /me"):
        await file_service.create_file(
            pod_id=pod_id,
            name="nested.txt",
            file_content=b"hello",
            ctx=_ctx(requester_user_id),
            directory_path=parent_folder.path,
        )


@pytest.mark.asyncio
async def test_create_folder_in_private_parent_denied_for_non_owner_non_admin(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    parent_owner_id = uuid4()
    pod_id = uuid4()
    parent_folder = _make_folder(
        pod_id=pod_id,
        name="private-parent",
        parent_path=f"/{parent_owner_id}",
    )
    parent_folder.owner_user_id = parent_owner_id
    parent_folder.visibility = "PERSONAL"

    async def _file_lookup(*args, **kwargs):
        path = kwargs.get("path")
        if path is None and len(args) >= 2:
            path = args[1]
        if path == parent_folder.path:
            return parent_folder
        return None

    file_repository_mock.get_by_path.side_effect = _file_lookup
    _deny_admin_allow_other_actions(authorization_service_mock)

    with pytest.raises(DatastoreAccessDeniedError, match="under /me"):
        await file_service.create_folder(
            pod_id=pod_id,
            path=f"{parent_folder.path}/nested-folder",
            ctx=_ctx(requester_user_id),
        )


@pytest.mark.asyncio
async def test_create_folder_uses_pod_visibility_for_pod_parent(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    parent_folder = _make_folder(pod_id=pod_id, name="shared-root")
    parent_folder.visibility = "POD"
    parent_folder.owner_user_id = uuid4()

    async def _shared_folder_lookup(*args, **kwargs):
        path = kwargs.get("path")
        if path is None and len(args) >= 2:
            path = args[1]
        if path == parent_folder.path:
            return parent_folder
        return None

    file_repository_mock.get_by_path.side_effect = _shared_folder_lookup

    async def _create(entity):
        return entity

    file_repository_mock.create.side_effect = _create

    created = await file_service.create_folder(
        pod_id=pod_id,
        path=f"{parent_folder.path}/nested-folder",
        ctx=_ctx(requester_user_id),
        visibility="POD",
    )

    assert created.visibility == "POD"


@pytest.mark.asyncio
async def test_create_file_uses_pod_visibility_for_pod_parent(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    storage_mock: AsyncMock,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    created_file_id = uuid4()

    parent_folder = _make_folder(pod_id=pod_id, name="shared-root")
    parent_folder.visibility = "POD"
    parent_folder.owner_user_id = uuid4()

    async def _shared_file_lookup(*args, **kwargs):
        path = kwargs.get("path")
        if path is None and len(args) >= 2:
            path = args[1]
        if path == parent_folder.path:
            return parent_folder
        return None

    file_repository_mock.get_by_path.side_effect = _shared_file_lookup

    async def _create(entity):
        entity.id = created_file_id
        return entity

    async def _update(entity):
        return entity

    file_repository_mock.create.side_effect = _create
    file_repository_mock.update.side_effect = _update

    created = await file_service.create_file(
        pod_id=pod_id,
        name="nested.txt",
        file_content=b"hello world",
        ctx=_ctx(requester_user_id),
        directory_path=parent_folder.path,
        visibility="POD",
    )

    assert created.visibility == "POD"
    storage_mock.upload_file.assert_awaited()


@pytest.mark.asyncio
async def test_list_files_merges_system_skills_and_pod_created_skills(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    tmp_path,
    monkeypatch,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    skills_root = tmp_path / "lemma-skills"
    builtin = skills_root / "builtin-skill"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: builtin-skill\n---\n", encoding="utf-8"
    )

    file_service.system_skill_files.skills_root = skills_root

    custom = _make_folder(
        pod_id=pod_id,
        name="custom-skill",
        parent_path="/skills",
        visibility="POD",
    )
    custom.owner_user_id = None
    file_repository_mock.get_all_by_datastore.return_value = [custom]
    ctx = _ctx(requester_user_id)

    items, next_cursor = await file_service.list_files(
        pod_id,
        ctx,
        directory_path="/skills",
    )

    assert next_cursor is None
    assert {item.path for item in items} == {
        "/skills/builtin-skill",
        "/skills/custom-skill",
    }


@pytest.mark.asyncio
async def test_download_system_skill_reads_from_lemma_skills(
    file_service: DatastoreFileService,
    storage_mock: AsyncMock,
    tmp_path,
    monkeypatch,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    skills_root = tmp_path / "lemma-skills"
    builtin = skills_root / "builtin-skill"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text("system instructions", encoding="utf-8")

    file_service.system_skill_files.skills_root = skills_root
    ctx = AsyncMock()
    ctx.user_id = requester_user_id

    file_entity, content = await file_service.download_file_content_by_path(
        pod_id,
        "/skills/builtin-skill/SKILL.md",
        ctx=ctx,
    )

    assert file_entity.path == "/skills/builtin-skill/SKILL.md"
    assert content == b"system instructions"
    # Built-in skills are public read-only files: no document grant is required
    # and the bytes come from disk, never object storage.
    ctx.require.assert_not_awaited()
    storage_mock.download_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_skill_paths_are_read_only(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    tmp_path,
    monkeypatch,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    skills_root = tmp_path / "lemma-skills"
    builtin = skills_root / "builtin-skill"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text("system instructions", encoding="utf-8")

    file_service.system_skill_files.skills_root = skills_root

    with pytest.raises(DatastoreValidationError, match="System skills are read-only"):
        await _update_file_by_path(file_service, 
            pod_id,
            DatastoreFileUpdateEntity(
                path="/skills/builtin-skill/SKILL.md",
                content=b"changed",
            ),
            ctx=_ctx(requester_user_id),
        )

    with pytest.raises(DatastoreValidationError, match="System skills are read-only"):
        await file_service.create_file(
            pod_id=pod_id,
            name="notes.md",
            file_content=b"changed",
            ctx=_ctx(requester_user_id),
            directory_path="/skills/builtin-skill",
        )

    file_repository_mock.update.assert_not_awaited()
    file_repository_mock.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_can_create_pod_skill_folder_under_virtual_skills_root(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    tmp_path,
    monkeypatch,
):
    requester_user_id = uuid4()
    pod_id = uuid4()
    skills_root = tmp_path / "lemma-skills"
    skills_root.mkdir()
    file_service.system_skill_files.skills_root = skills_root

    async def _create(entity):
        return entity

    file_repository_mock.get_by_path.return_value = None
    file_repository_mock.create.side_effect = _create

    created = await file_service.create_folder(
        pod_id=pod_id,
        path="/skills/custom-skill",
        ctx=_ctx(requester_user_id),
    )

    assert created.visibility == "POD"
    assert created.path == "/skills/custom-skill"
    file_repository_mock.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_missing_blob_reports_file_not_found(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    storage_mock: AsyncMock,
):
    """A file whose stored blob is gone yields a clean 404, not a storage 500."""
    requester_user_id = uuid4()
    pod_id = uuid4()
    orphaned = _make_file(
        pod_id=pod_id,
        name="orphaned.txt",
        owner_user_id=requester_user_id,
        visibility="PERSONAL",
        parent_path=f"/{requester_user_id}",
    )
    file_repository_mock.get_by_path.return_value = orphaned
    storage_mock.download_file.side_effect = DatastoreObjectNotFoundError(
        "Storage object not found: pods/x/files/y"
    )

    with pytest.raises(DatastoreFileNotFoundError, match="content for .* unavailable"):
        await file_service.download_file_content_by_path(
            pod_id,
            orphaned.path,
            ctx=_ctx(requester_user_id),
        )


@pytest.mark.asyncio
async def test_list_root_includes_empty_skills_folder_without_bundled_skills(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    tmp_path,
):
    """`/skills` shows at the pod root even when no skills are bundled."""
    requester_user_id = uuid4()
    pod_id = uuid4()
    # Point the skills root at a non-existent directory: the folder still shows.
    file_service.system_skill_files.skills_root = tmp_path / "absent-skills"
    file_repository_mock.list_visible_by_datastore.return_value = ([], None)

    items, _ = await file_service.list_files(pod_id, _ctx(requester_user_id))

    by_name = {item.name: item for item in items}
    assert {"me", "skills"} <= set(by_name)
    assert by_name["skills"].path == "/skills"
    assert by_name["skills"].kind == FileKind.FOLDER


@pytest.mark.asyncio
async def test_tree_root_includes_me_and_skills_nodes(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    tmp_path,
):
    """The pod-root tree splices in /me (with personal files) and /skills."""
    requester_user_id = uuid4()
    pod_id = uuid4()
    skills_root = tmp_path / "lemma-skills"
    builtin = skills_root / "builtin-skill"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text("---\nname: builtin\n---\n", encoding="utf-8")
    file_service.system_skill_files.skills_root = skills_root

    team_file = _make_file(
        pod_id=pod_id, name="team.txt", owner_user_id=uuid4(), visibility="POD"
    )
    personal_note = _make_file(
        pod_id=pod_id,
        name="notes.txt",
        owner_user_id=requester_user_id,
        visibility="PERSONAL",
        parent_path=f"/{requester_user_id}",
    )
    file_repository_mock.get_all_by_datastore.return_value = [team_file, personal_note]
    file_repository_mock.get_by_paths.return_value = []
    file_repository_mock.get_by_path.return_value = None

    tree = await file_service.get_directory_tree(pod_id, _ctx(requester_user_id))

    children = {child["name"]: child for child in tree["children"]}
    assert {"me", "skills", "team.txt"} <= set(children)
    # /me carries the caller's personal files; /skills carries the bundled skill.
    assert children["me"]["path"] == f"/{requester_user_id}"
    assert any(c["name"] == "notes.txt" for c in children["me"]["children"])
    assert children["skills"]["path"] == "/skills"
    assert any(c["name"] == "builtin-skill" for c in children["skills"]["children"])


@pytest.mark.asyncio
async def test_tree_me_resolves_to_personal_root_not_pod_root(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
):
    """`tree /me` returns the personal subtree, not a pod-root fallback."""
    requester_user_id = uuid4()
    pod_id = uuid4()
    personal_note = _make_file(
        pod_id=pod_id,
        name="notes.txt",
        owner_user_id=requester_user_id,
        visibility="PERSONAL",
        parent_path=f"/{requester_user_id}",
    )
    pod_file = _make_file(
        pod_id=pod_id, name="shared.txt", owner_user_id=uuid4(), visibility="POD"
    )
    file_repository_mock.get_all_by_datastore.return_value = [personal_note, pod_file]
    file_repository_mock.get_by_paths.return_value = []
    file_repository_mock.get_by_path.return_value = None

    tree = await file_service.get_directory_tree(
        pod_id, _ctx(requester_user_id), root_path="/me"
    )

    assert tree["path"] == f"/{requester_user_id}"
    assert tree["name"] == "me"
    child_names = {child["name"] for child in tree["children"]}
    assert "notes.txt" in child_names
    assert "shared.txt" not in child_names


@pytest.mark.asyncio
async def test_search_files_excludes_private_or_private_ancestor_results_for_non_owner(
    file_repository_mock: AsyncMock,
    storage_mock: AsyncMock,
    reindex_queue_mock: AsyncMock,
    authorization_service_mock: AsyncMock,
):
    requester_user_id = uuid4()
    other_user_id = uuid4()
    pod_id = uuid4()
    pod_child_id = uuid4()
    pod_root_id = uuid4()
    seen_search_kwargs = {}

    private_parent = _make_folder(
        pod_id=pod_id,
        name="private-parent",
    )
    private_parent.visibility = "PERSONAL"
    private_parent.owner_user_id = other_user_id

    pod_child = _make_file(
        pod_id=pod_id,
        name="under-private-parent.txt",
        owner_user_id=other_user_id,
        visibility="POD",
        file_id=pod_child_id,
        parent_path=private_parent.path,
    )

    pod_root = _make_file(
        pod_id=pod_id,
        name="shared-root.txt",
        owner_user_id=other_user_id,
        visibility="POD",
        file_id=pod_root_id,
    )

    class _FakeSearchService:
        engine = None

        async def search(self, **kwargs):
            seen_search_kwargs.update(kwargs)
            return [
                DatastoreFileSearchResult(
                    file_id=pod_child_id,
                    path=pod_child.path,
                    chunk_index=0,
                    content="under private parent",
                    metadata={},
                    score=0.9,
                ),
                DatastoreFileSearchResult(
                    file_id=pod_root_id,
                    path=pod_root.path,
                    chunk_index=1,
                    content="shared root",
                    metadata={},
                    score=0.8,
                ),
            ]

    _deny_admin_allow_other_actions(authorization_service_mock)
    file_repository_mock.get_all_by_datastore.return_value = [pod_root]
    file_repository_mock.get_by_paths.return_value = [pod_root]

    service = DatastoreFileService(
        file_repository=file_repository_mock,
        storage=storage_mock,
        authorization_service=authorization_service_mock,
        search_service_factory=lambda _pod_id: _FakeSearchService(),
    )

    results = await service.search_files(
        pod_id=pod_id,
        query="shared",
        ctx=_ctx(requester_user_id),
    )

    assert [result.file_id for result in results] == [pod_root_id]
    assert seen_search_kwargs["visible_file_ids"] == {pod_root_id}


@pytest.mark.asyncio
async def test_delete_path_by_path_removes_folder_descendants_from_storage_and_search(
    file_service: DatastoreFileService,
    file_repository_mock: AsyncMock,
    storage_mock: AsyncMock,
):
    user_id = uuid4()
    pod_id = uuid4()

    root_folder = _make_folder(pod_id=pod_id, name="research")
    root_folder.owner_user_id = user_id
    nested_folder = _make_folder(
        pod_id=pod_id,
        name="notes",
        parent_path=root_folder.path,
    )
    nested_folder.owner_user_id = user_id
    nested_file = _make_file(
        pod_id=pod_id,
        name="draft.md",
        owner_user_id=user_id,
        visibility="PERSONAL",
        parent_path=nested_folder.path,
    )
    sibling_file = _make_file(
        pod_id=pod_id,
        name="summary.md",
        owner_user_id=user_id,
        visibility="PERSONAL",
        parent_path=root_folder.path,
    )

    file_repository_mock.get_by_path.return_value = root_folder
    file_repository_mock.get_descendants.return_value = [
        nested_folder,
        nested_file,
        sibling_file,
    ]
    file_repository_mock.delete_entity.return_value = True

    search_service = AsyncMock()
    search_service.engine = None
    file_service.search_service_factory = lambda _pod_id: search_service

    await _delete_path_by_path(file_service, pod_id, root_folder.path, ctx=_ctx(user_id))

    deleted_prefixes = [
        call.args[0] for call in storage_mock.delete_prefix.await_args_list
    ]
    # Derived child containers are colocated under the folder prefix, so the
    # single folder-storage delete already removes them.
    assert deleted_prefixes == [
        f"pods/{pod_id}/files/research/",
    ]
    storage_mock.delete_file.assert_not_awaited()
    assert search_service.remove_file.await_count == 2
    deleted_ids = {call.args[0] for call in search_service.remove_file.await_args_list}
    assert deleted_ids == {nested_file.id, sibling_file.id}
    assert file_repository_mock.delete_entity.await_count == 4
