from uuid import uuid4

import pytest
from obstore.store import MemoryStore

from app.core.config import settings
from app.modules.apps.api import dependencies
from app.modules.apps.services.app_file_manager import AppFileManager


@pytest.mark.asyncio
async def test_app_file_manager_uses_apps_storage_prefix(tmp_path):
    app_id = uuid4()
    manager = AppFileManager(app_id, root_path=tmp_path)

    await manager.write_file("build/index.html", "<html>ok</html>")

    expected_path = tmp_path / "apps" / str(app_id) / "build" / "index.html"
    assert expected_path.exists()
    assert expected_path.read_text(encoding="utf-8") == "<html>ok</html>"

    content = await manager.read_file("build/index.html")

    assert content == "<html>ok</html>"


@pytest.mark.asyncio
async def test_app_file_manager_missing_file_raises(tmp_path):
    manager = AppFileManager(uuid4(), root_path=tmp_path)

    with pytest.raises(FileNotFoundError):
        await manager.read_file("build/index.html")


@pytest.mark.asyncio
async def test_app_file_manager_delete_prefix_removes_nested_tree(tmp_path):
    app_id = uuid4()
    manager = AppFileManager(app_id, root_path=tmp_path)

    await manager.write_file("releases/v1/dist/index.html", "<html>ok</html>")
    await manager.write_file("releases/v1/dist/assets/app.js", "console.log('ok')")

    await manager.delete_prefix("releases/v1/dist/")

    expected_dir = tmp_path / "apps" / str(app_id) / "releases" / "v1" / "dist"
    assert not expected_dir.exists()


@pytest.mark.asyncio
async def test_app_file_manager_delete_prefix_without_path_removes_app_root(tmp_path):
    app_id = uuid4()
    manager = AppFileManager(app_id, root_path=tmp_path)

    await manager.write_file("source/archive.zip", b"source")
    await manager.write_file("releases/v1/dist/index.html", "<html>ok</html>")

    await manager.delete_prefix("")

    expected_root = tmp_path / "apps" / str(app_id)
    assert not expected_root.exists()


@pytest.mark.asyncio
async def test_app_file_manager_accepts_any_obstore_adapter():
    manager = AppFileManager(uuid4(), store=MemoryStore())

    await manager.write_file("build/index.html", "<html>portable</html>")

    assert await manager.read_file("build/index.html") == "<html>portable</html>"
    await manager.delete_prefix("build")
    with pytest.raises(FileNotFoundError):
        await manager.read_file("build/index.html")


def test_app_storage_composition_uses_selected_cloud_adapter(monkeypatch):
    """The cloud store is built without the app id in it.

    The app id used to be passed as ``remote_prefix``, which made the store part
    of the app's identity — one store, and once stores were cached one cache
    entry, per tenant. The id belongs in the key instead; see
    ``test_app_storage_is_shared_across_apps`` for the property that buys.
    """
    app_id = uuid4()
    captured: dict[str, object] = {}

    def build_store(**kwargs):
        captured.update(kwargs)
        return MemoryStore()

    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "storage_bucket", "documents")
    monkeypatch.setattr(dependencies, "build_object_store", build_store)

    manager = dependencies._get_app_storage_factory()(app_id)

    assert isinstance(manager, AppFileManager)
    assert "remote_prefix" not in captured
    assert manager._key("build/index.html") == f"apps/{app_id}/build/index.html"


def test_app_storage_is_shared_across_apps(monkeypatch):
    """Two apps resolve to the same store object, not one store each."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "storage_bucket", "documents")

    store = MemoryStore()
    monkeypatch.setattr(dependencies, "build_object_store", lambda **_: store)

    build = dependencies._get_app_storage_factory()
    first, second = build(uuid4()), build(uuid4())

    assert first.store is second.store
    assert first._key("x.js") != second._key("x.js")


@pytest.mark.asyncio
async def test_apps_sharing_one_store_cannot_delete_each_other(tmp_path):
    """``delete_prefix("")`` clears its own app and leaves the neighbour alone.

    With a per-app store this was structurally impossible. On a shared store an
    unscoped ``list()`` would walk the whole bucket, so the scoping is now load-
    bearing rather than incidental.
    """
    store = MemoryStore()
    mine = AppFileManager(uuid4(), store=store)
    theirs = AppFileManager(uuid4(), store=store)

    await mine.write_file("build/index.html", "<html>mine</html>")
    await theirs.write_file("build/index.html", "<html>theirs</html>")

    await mine.delete_prefix("")

    with pytest.raises(FileNotFoundError):
        await mine.read_file("build/index.html")
    assert await theirs.read_file("build/index.html") == "<html>theirs</html>"
