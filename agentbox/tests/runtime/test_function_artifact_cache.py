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
from agentbox.function_runtime.runtime_models import RunClaim, RuntimeIdentity


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


def _claim(artifact: bytes) -> RunClaim:
    return RunClaim(
        run_id=uuid4(),
        callback_token="callback-token-" + "x" * 32,
        artifact_url="/artifact",
        revision_hash=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        input_data={},
        config=None,
        identity=RuntimeIdentity(
            user_id=uuid4(),
            pod_id=uuid4(),
            function_id=uuid4(),
            function_name="cache_probe",
        ),
        lemma_token="lemma-token",
        lemma_base_url="https://api.lemma.test",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )


class _Gateway:
    def __init__(self, artifact: bytes) -> None:
        self._artifact = artifact
        self.downloads = 0

    async def artifact(self, _claim: RunClaim) -> bytes:
        self.downloads += 1
        await asyncio.sleep(0.05)
        return self._artifact


async def test_warm_revision_skips_artifact_download(tmp_path, monkeypatch) -> None:
    artifact = _artifact()
    claim = _claim(artifact)
    gateway = _Gateway(artifact)
    monkeypatch.setattr(runner, "_CACHE_ROOT", tmp_path)

    first = await runner._resolve_artifact_root(gateway, claim)
    second = await runner._resolve_artifact_root(gateway, claim)

    assert first == second
    assert gateway.downloads == 1
    assert (first / "manifest.json").is_file()


async def test_concurrent_cold_revision_downloads_once(tmp_path, monkeypatch) -> None:
    artifact = _artifact()
    claim = _claim(artifact)
    gateway = _Gateway(artifact)
    monkeypatch.setattr(runner, "_CACHE_ROOT", tmp_path)

    roots = await asyncio.gather(
        runner._resolve_artifact_root(gateway, claim),
        runner._resolve_artifact_root(gateway, claim),
        runner._resolve_artifact_root(gateway, claim),
    )

    assert roots[0] == roots[1] == roots[2]
    assert gateway.downloads == 1


async def test_invalid_cache_entry_is_replaced(tmp_path, monkeypatch) -> None:
    artifact = _artifact()
    claim = _claim(artifact)
    digest = claim.revision_hash.removeprefix("sha256:")
    target = tmp_path / digest
    target.mkdir()
    (target / ".ready").write_text(digest, encoding="utf-8")
    gateway = _Gateway(artifact)
    monkeypatch.setattr(runner, "_CACHE_ROOT", tmp_path)

    root = await runner._resolve_artifact_root(gateway, claim)

    assert gateway.downloads == 1
    assert (root / "manifest.json").is_file()
