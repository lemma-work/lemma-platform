from __future__ import annotations

import io
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.modules.apps.domain.entities import (
    AppAssetDocument,
    AppEntity,
    AppReleaseEntity,
    AppStatus,
    AppUpdateEntity,
)
from app.modules.apps.domain.errors import (
    AppConflictError,
    AppNotFoundError,
    AppValidationError,
)
from app.core.runtime_config import runtime_config_token
from app.modules.apps.services.app_service import AppService
from app.modules.test_support.authz import allow_all_context


async def _get_app_asset(service, pod_id, name, user_id, *, asset_path, request_etag=None, ctx=None):
    """Serve an asset through the saga phases (the single-call convenience method
    was removed; production resolves in a short UoW then reads storage)."""
    resolved = await service.resolve_app_asset(
        pod_id, name, user_id, asset_path=asset_path, request_etag=request_etag, ctx=ctx
    )
    if isinstance(resolved, AppAssetDocument):
        return resolved
    return await service.read_app_asset(resolved)


async def _delete_app(service, pod_id, name, user_id, ctx=None):
    cleanup = await service.resolve_delete_app(pod_id, name, user_id, ctx=ctx)
    await service.cleanup_app_storage(cleanup)


async def _get_app_dist_archive(service, pod_id, name, user_id, ctx=None):
    app_id, archive_path = await service.resolve_dist_archive(pod_id, name, user_id, ctx=ctx)
    return await service.read_archive(app_id, archive_path)


def make_dist_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_create_app_conflict_raises():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage_factory = AsyncMock()
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    repo.get_by_name.return_value = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )
    repo.get_by_public_slug.return_value = None

    with pytest.raises(AppConflictError):
        await service.create_app_with_context(
            AppEntity(
                pod_id=pod_id,
                user_id=user_id,
                name="dashboard",
                public_slug="dashboard",
            ),
            user_id,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_create_app_duplicate_public_slug_raises():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage_factory = AsyncMock()
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    repo.get_by_name.return_value = None
    repo.get_by_public_slug.return_value = AppEntity(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=user_id,
        name="other-app",
        public_slug="dashboard",
    )

    with pytest.raises(AppConflictError):
        await service.create_app_with_context(
            AppEntity(
                pod_id=pod_id,
                user_id=user_id,
                name="dashboard",
                public_slug="dashboard",
            ),
            user_id,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_upload_bundle_requires_source_or_dist():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage_factory = AsyncMock()
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )
    repo.get_by_name.return_value = app

    with pytest.raises(AppValidationError):
        await service.upload_bundle(
            pod_id,
            "dashboard",
            user_id,
            source_archive_bytes=None,
            dist_archive_bytes=None,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_upload_bundle_sets_ready_and_persists_release_assets():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app_id = uuid4()
    release_id = uuid4()
    app = AppEntity(
        id=app_id,
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )
    dist_archive = make_dist_zip(
        {
            "index.html": "<html><body>ok</body></html>",
            "assets/app.js": "console.log('ok')",
        }
    )

    repo.get_by_name.return_value = app
    repo.update.side_effect = lambda entity: entity
    repo.get_release_by_version.return_value = None
    repo.create_release.return_value = AppReleaseEntity(
        id=release_id,
        app_id=app_id,
        version="version",
        dist_root_path="releases/version/dist/",
        dist_archive_path="releases/version/dist/archive.zip",
    )

    updated = await service.upload_bundle(
        pod_id,
        "dashboard",
        user_id,
        source_archive_bytes=b"source-zip",
        dist_archive_bytes=dist_archive,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert updated.status == AppStatus.READY
    assert updated.source_archive_path == "source/archive.zip"
    assert updated.current_release_id == release_id
    written_paths = [args.args[0] for args in storage.write_file.await_args_list]
    assert "source/archive.zip" in written_paths
    assert any(path.endswith("/dist/index.html") for path in written_paths)
    assert any(path.endswith("/dist/assets/app.js") for path in written_paths)
    assert any(path.endswith("/dist/archive.zip") for path in written_paths)


@pytest.mark.asyncio
async def test_upload_bundle_rejects_dist_without_root_index_html():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )

    repo.get_by_name.return_value = app

    with pytest.raises(AppValidationError):
        await service.upload_bundle(
            pod_id,
            "dashboard",
            user_id,
            source_archive_bytes=None,
            dist_archive_bytes=make_dist_zip({"nested/index.html": "<html></html>"}),
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )

    storage.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_app_asset_missing_release_raises_not_found():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage_factory = AsyncMock()
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )

    repo.get_by_name.return_value = app

    with pytest.raises(AppNotFoundError):
        await _get_app_asset(service, 
            pod_id,
            "dashboard",
            user_id,
            asset_path=None,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_get_app_asset_reads_release_contents():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        current_release_id=uuid4(),
    )
    release = AppReleaseEntity(
        id=app.current_release_id,
        app_id=app.id,
        version="version",
        dist_root_path="releases/version/dist/",
        dist_archive_path="releases/version/dist/archive.zip",
    )

    repo.get_by_name.return_value = app
    repo.get_release.return_value = release
    storage.read_file.return_value = "<html><head></head><body>public-ok</body></html>"

    asset = await _get_app_asset(service, 
        pod_id,
        "dashboard",
        user_id,
        asset_path=None,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    # Entrypoint HTML is served with the host-injected runtime config.
    body = asset.content.decode("utf-8")
    assert "public-ok" in body
    assert "data-lemma-runtime-config" in body
    assert str(pod_id) in body
    assert asset.media_type == "text/html"
    # ETag folds in the config hash so a pod/api/auth change busts the cache.
    expected_token = runtime_config_token(app.pod_id)
    assert asset.etag == f'"version.{expected_token}"'
    assert asset.is_entrypoint is True


@pytest.mark.asyncio
async def test_get_app_asset_serves_static_asset_without_fallback():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        current_release_id=uuid4(),
    )
    release = AppReleaseEntity(
        id=app.current_release_id,
        app_id=app.id,
        version="version",
        dist_root_path="releases/version/dist/",
    )

    repo.get_by_name.return_value = app
    repo.get_release.return_value = release
    storage.read_file.return_value = b"console.log('asset')"

    asset = await _get_app_asset(service, 
        pod_id,
        "dashboard",
        user_id,
        asset_path="assets/app.js",
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert asset.content == b"console.log('asset')"
    assert asset.media_type == "application/javascript"
    assert asset.is_entrypoint is False
    storage.read_file.assert_awaited_once_with("releases/version/dist/assets/app.js")


@pytest.mark.asyncio
async def test_read_archive_reads_storage_without_db_access():
    """Regression (DB pool exhaustion): ``read_archive`` runs after the resolving
    UoW closes, so it must not touch the repository — the (slow) archive transfer
    must hold no pooled DB connection."""
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage.read_file.return_value = b"ZIPBYTES"
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    app_id = uuid4()
    content = await service.read_archive(app_id, "releases/v1/source.zip")

    assert content == b"ZIPBYTES"
    storage_factory.assert_called_once_with(app_id)
    storage.read_file.assert_awaited_once_with("releases/v1/source.zip")
    repo.get_by_name.assert_not_awaited()
    repo.get_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_app_asset_missing_path_raises_without_fallback():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        current_release_id=uuid4(),
    )
    release = AppReleaseEntity(
        id=app.current_release_id,
        app_id=app.id,
        version="version",
        dist_root_path="releases/version/dist/",
    )

    repo.get_by_name.return_value = app
    repo.get_release.return_value = release
    storage.read_file.side_effect = FileNotFoundError("missing")

    with pytest.raises(AppNotFoundError):
        await _get_app_asset(service, 
            pod_id,
            "dashboard",
            user_id,
            asset_path="page",
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_get_app_asset_returns_not_modified_when_etag_matches():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app_id = uuid4()
    release_id = uuid4()
    app = AppEntity(
        id=app_id,
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        current_release_id=release_id,
    )
    release = AppReleaseEntity(
        id=release_id,
        app_id=app_id,
        version="version",
        dist_root_path="releases/version/dist/",
    )

    repo.get_by_name.return_value = app
    repo.get_release.return_value = release

    # The entrypoint ETag includes the config hash; a matching request 304s.
    config_token = runtime_config_token(app.pod_id)
    asset = await _get_app_asset(service, 
        pod_id,
        "dashboard",
        user_id,
        asset_path=None,
        request_etag=f'"version.{config_token}"',
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert asset.not_modified is True
    assert asset.content is None
    storage.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_app_removes_release_manifest_assets():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        source_archive_path="source/archive.zip",
    )
    release = AppReleaseEntity(
        id=uuid4(),
        app_id=app.id,
        version="version",
        dist_root_path="releases/version/dist/",
        dist_archive_path="releases/version/dist/archive.zip",
    )

    repo.get_by_name.return_value = app
    repo.list_releases.return_value = [release]
    await _delete_app(service, 
        pod_id,
        "dashboard",
        user_id,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    deleted_prefixes = [args.args[0] for args in storage.delete_prefix.await_args_list]
    assert deleted_prefixes == ["releases/version/dist/", ""]
    deleted_paths = {args.args[0] for args in storage.delete_file.await_args_list}
    assert deleted_paths == {"source/archive.zip"}


@pytest.mark.asyncio
async def test_delete_app_ignores_missing_source_archive():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        source_archive_path="source/archive.zip",
    )
    release = AppReleaseEntity(
        id=uuid4(),
        app_id=app.id,
        version="version",
        dist_root_path="releases/version/dist/",
        dist_archive_path="releases/version/dist/archive.zip",
    )

    repo.get_by_name.return_value = app
    repo.list_releases.return_value = [release]
    storage.delete_file.side_effect = [FileNotFoundError("missing source archive")]

    await _delete_app(service, 
        pod_id,
        "dashboard",
        user_id,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    deleted_prefixes = [args.args[0] for args in storage.delete_prefix.await_args_list]
    assert deleted_prefixes == ["releases/version/dist/", ""]
    repo.delete.assert_awaited_once_with(app.id)


@pytest.mark.asyncio
async def test_get_app_dist_archive_reads_current_release():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage = AsyncMock()
    storage_factory = Mock(return_value=storage)
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
        current_release_id=uuid4(),
    )
    release = AppReleaseEntity(
        id=app.current_release_id,
        app_id=app.id,
        version="version",
        dist_root_path="releases/version/dist/",
        dist_archive_path="releases/version/dist/archive.zip",
    )

    repo.get_by_name.return_value = app
    repo.get_release.return_value = release
    storage.read_file.return_value = b"zip-bytes"

    archive = await _get_app_dist_archive(service, 
        pod_id,
        "dashboard",
        user_id,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert archive == b"zip-bytes"
    storage.read_file.assert_awaited_once_with("releases/version/dist/archive.zip")


@pytest.mark.asyncio
async def test_update_app_public_slug():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage_factory = AsyncMock()
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )

    repo.get_by_name.return_value = app
    repo.update.side_effect = lambda entity: entity
    repo.get_by_public_slug.return_value = None

    updated = await service.update_app(
        pod_id,
        "dashboard",
        AppUpdateEntity(public_slug="sales dashboard"),
        user_id,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )
    assert updated.public_slug == "sales-dashboard"


@pytest.mark.asyncio
async def test_update_app_public_slug_conflict_raises():
    repo = AsyncMock()
    authorization_service = AsyncMock()
    storage_factory = AsyncMock()
    service = AppService(repo, storage_factory, authorization_service)

    pod_id = uuid4()
    user_id = uuid4()
    app = AppEntity(
        id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        name="dashboard",
        public_slug="dashboard",
    )

    repo.get_by_name.return_value = app
    repo.get_by_public_slug.return_value = AppEntity(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="taken",
        public_slug="sales-dashboard",
    )

    with pytest.raises(AppConflictError):
        await service.update_app(
            pod_id,
            "dashboard",
            AppUpdateEntity(public_slug="sales dashboard"),
            user_id,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )
