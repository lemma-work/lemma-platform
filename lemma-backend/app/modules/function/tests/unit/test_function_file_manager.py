from uuid import uuid4

import pytest
from obstore.store import MemoryStore

from app.core.config import settings
from app.modules.function.api import dependencies
from app.modules.function.services.function_file_manager import FunctionFileManager


@pytest.mark.asyncio
async def test_function_file_manager_uses_functions_storage_prefix(tmp_path):
    function_id = uuid4()
    manager = FunctionFileManager(function_id, root_path=tmp_path)

    await manager.write_file("handler.py", "print('ok')")

    expected_path = tmp_path / "functions" / str(function_id) / "handler.py"
    assert expected_path.exists()
    assert expected_path.read_text(encoding="utf-8") == "print('ok')"

    content = await manager.read_file("handler.py")

    assert content == "print('ok')"


@pytest.mark.asyncio
async def test_function_file_manager_missing_file_raises(tmp_path):
    manager = FunctionFileManager(uuid4(), root_path=tmp_path)

    with pytest.raises(FileNotFoundError):
        await manager.read_file("handler.py")


@pytest.mark.asyncio
async def test_function_file_manager_accepts_any_obstore_adapter():
    manager = FunctionFileManager(uuid4(), store=MemoryStore())

    await manager.write_file("handler.py", "print('portable')")

    assert await manager.read_file("handler.py") == "print('portable')"


def test_function_storage_composition_uses_selected_cloud_adapter(monkeypatch):
    function_id = uuid4()
    captured: dict[str, object] = {}

    def build_store(**kwargs):
        captured.update(kwargs)
        return MemoryStore()

    monkeypatch.setattr(settings, "storage_backend", "azure")
    monkeypatch.setattr(settings, "storage_bucket", "documents")
    monkeypatch.setattr(dependencies, "build_object_store", build_store)

    manager = dependencies.get_function_storage_factory()(function_id)

    assert isinstance(manager, FunctionFileManager)
    assert captured["remote_prefix"] == f"functions/{function_id}"
