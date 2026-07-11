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
    assert captured["remote_prefix"] == f"apps/{app_id}"
