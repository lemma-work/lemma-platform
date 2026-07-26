from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from uuid import uuid4
import zipfile

import pytest

from agentbox.function_runtime import runner


pytestmark = pytest.mark.asyncio


def _artifact() -> bytes:
    payload = BytesIO()
    with zipfile.ZipFile(payload, mode="w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "runtime_abi": "lemma-function-python-3.14-linux-x86_64-1",
                    "builder_digest": f"sha256:{'b' * 64}",
                    "source_path": "function.py",
                    "input_model": "Input",
                    "output_model": "Output",
                    "entrypoint": "execute",
                }
            ),
        )
        archive.writestr("function.py", "def execute(ctx, data): return data\n")
    return payload.getvalue()


class _Gateway:
    def __init__(self, artifact: bytes) -> None:
        self._artifact = artifact
        self.downloads = 0

    async def artifact(self, _token, **_kwargs) -> bytes:
        self.downloads += 1
        await asyncio.sleep(0.05)
        return self._artifact


async def _resolve(gateway, artifact):
    return await runner._resolve_artifact_root(
        gateway,
        "delegated-function-token",
        function_id=uuid4(),
        revision_hash=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )


async def test_warm_revision_skips_artifact_download(tmp_path, monkeypatch) -> None:
    artifact = _artifact()
    gateway = _Gateway(artifact)
    monkeypatch.setattr(runner, "_CACHE_ROOT", tmp_path)

    first = await _resolve(gateway, artifact)
    second = await _resolve(gateway, artifact)

    assert first == second
    assert gateway.downloads == 1
    assert (first / "manifest.json").is_file()


async def test_concurrent_cold_revision_downloads_once(tmp_path, monkeypatch) -> None:
    artifact = _artifact()
    gateway = _Gateway(artifact)
    monkeypatch.setattr(runner, "_CACHE_ROOT", tmp_path)

    roots = await asyncio.gather(
        _resolve(gateway, artifact),
        _resolve(gateway, artifact),
        _resolve(gateway, artifact),
    )

    assert roots[0] == roots[1] == roots[2]
    assert gateway.downloads == 1


async def test_invalid_cache_entry_is_replaced(tmp_path, monkeypatch) -> None:
    artifact = _artifact()
    digest = hashlib.sha256(artifact).hexdigest()
    target = tmp_path / digest
    target.mkdir()
    (target / ".ready").write_text(digest, encoding="utf-8")
    gateway = _Gateway(artifact)
    monkeypatch.setattr(runner, "_CACHE_ROOT", tmp_path)

    root = await _resolve(gateway, artifact)

    assert gateway.downloads == 1
    assert (root / "manifest.json").is_file()
