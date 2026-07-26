from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from agentbox.function_runtime.runtime_models import (
    FunctionArtifactManifest,
    RuntimeIdentity,
    WorkerRequest,
)
from agentbox.function_runtime.worker_pool import (
    RevisionWorker,
    RevisionWorkerPool,
    RevisionWorkerRegistry,
    RuntimeOverloaded,
    WorkerBudget,
)


def _artifact(root: Path) -> tuple[Path, FunctionArtifactManifest]:
    artifact = root / ("a" * 64)
    artifact.mkdir()
    manifest = FunctionArtifactManifest(
        runtime_abi="lemma-function-python-3.14-linux-x86_64-1",
        builder_digest="test",
        source_path="function.py",
        input_model="Input",
        output_model="Output",
        entrypoint="execute",
    )
    (artifact / "manifest.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    (artifact / "function.py").write_text(
        """
import asyncio
from pydantic import BaseModel

counter = 0

class Input(BaseModel):
    value: int
    hold_ms: int = 0

class Output(BaseModel):
    value: int
    counter: int

async def execute(ctx, data):
    global counter
    if data.hold_ms:
        await asyncio.sleep(data.hold_ms / 1000)
    counter += 1
    return Output(value=data.value, counter=counter)
""".strip(),
        encoding="utf-8",
    )
    return artifact, manifest


def _request(
    artifact: Path,
    manifest: FunctionArtifactManifest,
    *,
    function_id,
    value: int,
    hold_ms: int = 0,
) -> WorkerRequest:
    return WorkerRequest(
        artifact_root=str(artifact),
        manifest=manifest,
        run_id=uuid4(),
        input_data={"value": value, "hold_ms": hold_ms},
        config=None,
        identity=RuntimeIdentity(
            user_id=uuid4(),
            pod_id=uuid4(),
            function_id=function_id,
            function_name="cached",
        ),
        lemma_token="test-token",
        lemma_base_url="https://api.example.test",
    )


def _legacy_artifact(root: Path) -> tuple[Path, FunctionArtifactManifest]:
    artifact = root / ("b" * 64)
    artifact.mkdir()
    manifest = FunctionArtifactManifest(
        runtime_abi="lemma-function-python-3.14-linux-x86_64-1",
        builder_digest="test",
        source_path="function.py",
        input_model="Input",
        output_model="Output",
        entrypoint="execute",
    )
    (artifact / "manifest.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    (artifact / "function.py").write_text(
        """
import os
import threading
from pydantic import BaseModel
from lemma_sdk import Pod

class Input(BaseModel):
    value: int

class Output(BaseModel):
    pod_id: str
    token: str
    base_url: str
    user_id: str
    user_email: str | None
    organization_id: str | None
    threaded_pod_id: str

def execute(ctx, data):
    del data
    result = {}

    def resolve_from_legacy_environment():
        result["pod_id"] = Pod.from_env().pod_id

    thread = threading.Thread(target=resolve_from_legacy_environment)
    thread.start()
    thread.join()
    return Output(
        pod_id=Pod.from_env().pod_id,
        token=os.environ["LEMMA_TOKEN"],
        base_url=os.environ["LEMMA_BASE_URL"],
        user_id=os.environ["LEMMA_USER_ID"],
        user_email=os.environ.get("LEMMA_USER_EMAIL"),
        organization_id=os.environ.get("LEMMA_ORG_ID"),
        threaded_pod_id=result["pod_id"],
    )
""".strip(),
        encoding="utf-8",
    )
    return artifact, manifest


@pytest.mark.asyncio
async def test_revision_worker_is_reused_for_same_hash(tmp_path: Path) -> None:
    artifact, manifest = _artifact(tmp_path)
    registry = RevisionWorkerRegistry(max_workers=4)
    function_id = uuid4()
    digest = f"sha256:{'a' * 64}"
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    try:
        first = await registry.execute(
            function_id=function_id,
            revision_hash=digest,
            artifact_root=artifact,
            run_id=uuid4(),
            request=_request(
                artifact,
                manifest,
                function_id=function_id,
                value=1,
            ),
            deadline_at=deadline,
        )
        second = await registry.execute(
            function_id=function_id,
            revision_hash=digest,
            artifact_root=artifact,
            run_id=uuid4(),
            request=_request(
                artifact,
                manifest,
                function_id=function_id,
                value=2,
            ),
            deadline_at=deadline,
        )
    finally:
        await registry.close()

    assert first.output_data == {"value": 1, "counter": 1}
    assert second.output_data == {"value": 2, "counter": 2}


@pytest.mark.asyncio
async def test_schema_inspection_prewarms_the_first_execution_worker(
    tmp_path: Path,
) -> None:
    artifact, manifest = _artifact(tmp_path)
    registry = RevisionWorkerRegistry(max_workers=1)
    function_id = uuid4()
    digest = f"sha256:{'a' * 64}"
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    key = (function_id, digest)
    try:
        schemas = await registry.inspect_schemas(
            function_id=function_id,
            revision_hash=digest,
            artifact_root=artifact,
            deadline_at=deadline,
        )
        pool = registry._pools[key]
        schema_worker = next(iter(pool._workers))
        result = await registry.execute(
            function_id=function_id,
            revision_hash=digest,
            artifact_root=artifact,
            run_id=uuid4(),
            request=_request(
                artifact,
                manifest,
                function_id=function_id,
                value=1,
            ),
            deadline_at=deadline,
        )
        assert next(iter(pool._workers)) is schema_worker
    finally:
        await registry.close()

    assert schemas.input["title"] == "Input"
    assert result.output_data == {"value": 1, "counter": 1}


@pytest.mark.asyncio
async def test_reused_worker_preserves_legacy_function_environment(
    tmp_path: Path,
) -> None:
    artifact, manifest = _legacy_artifact(tmp_path)
    registry = RevisionWorkerRegistry(max_workers=1)
    function_id = uuid4()
    digest = f"sha256:{'b' * 64}"
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    first = _request(
        artifact,
        manifest,
        function_id=function_id,
        value=1,
    )
    first = first.model_copy(
        update={
            "identity": first.identity.model_copy(
                update={
                    "organization_id": uuid4(),
                    "user_email": "first@example.test",
                }
            ),
            "lemma_token": "first-token",
        }
    )
    second = _request(
        artifact,
        manifest,
        function_id=function_id,
        value=2,
    ).model_copy(update={"lemma_token": "second-token"})
    try:
        first_result = await registry.execute(
            function_id=function_id,
            revision_hash=digest,
            artifact_root=artifact,
            run_id=first.run_id,
            request=first,
            deadline_at=deadline,
        )
        second_result = await registry.execute(
            function_id=function_id,
            revision_hash=digest,
            artifact_root=artifact,
            run_id=second.run_id,
            request=second,
            deadline_at=deadline,
        )
    finally:
        await registry.close()

    assert first_result.output_data == {
        "pod_id": str(first.identity.pod_id),
        "token": "first-token",
        "base_url": "https://api.example.test",
        "user_id": str(first.identity.user_id),
        "user_email": "first@example.test",
        "organization_id": str(first.identity.organization_id),
        "threaded_pod_id": str(first.identity.pod_id),
    }
    assert second_result.output_data == {
        "pod_id": str(second.identity.pod_id),
        "token": "second-token",
        "base_url": "https://api.example.test",
        "user_id": str(second.identity.user_id),
        "user_email": None,
        "organization_id": None,
        "threaded_pod_id": str(second.identity.pod_id),
    }


@pytest.mark.asyncio
async def test_idle_revision_caches_are_evicted_by_lru(tmp_path: Path) -> None:
    artifact, manifest = _artifact(tmp_path)
    registry = RevisionWorkerRegistry(max_workers=3, max_cached_revisions=2)
    function_ids = (uuid4(), uuid4(), uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=20)

    async def invoke(function_id):
        return await registry.execute(
            function_id=function_id,
            revision_hash=f"sha256:{str(function_id).replace('-', '') * 2}",
            artifact_root=artifact,
            run_id=uuid4(),
            request=_request(
                artifact,
                manifest,
                function_id=function_id,
                value=1,
            ),
            deadline_at=deadline,
        )

    try:
        first = await invoke(function_ids[0])
        await invoke(function_ids[1])
        await invoke(function_ids[2])
        first_after_eviction = await invoke(function_ids[0])
    finally:
        await registry.close()

    assert first.output_data == {"value": 1, "counter": 1}
    assert first_after_eviction.output_data == {"value": 1, "counter": 1}


@pytest.mark.asyncio
async def test_idle_workers_are_reclaimed_across_revision_pools(
    tmp_path: Path,
) -> None:
    artifact, manifest = _artifact(tmp_path)
    registry = RevisionWorkerRegistry(max_workers=2, max_cached_revisions=4)
    first_function = uuid4()
    second_function = uuid4()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=20)

    async def invoke(function_id, *, hold_ms: int = 0):
        return await registry.execute(
            function_id=function_id,
            revision_hash=f"sha256:{str(function_id).replace('-', '') * 2}",
            artifact_root=artifact,
            run_id=uuid4(),
            request=_request(
                artifact,
                manifest,
                function_id=function_id,
                value=1,
                hold_ms=hold_ms,
            ),
            deadline_at=deadline,
        )

    try:
        await asyncio.gather(
            invoke(first_function, hold_ms=200),
            invoke(first_function, hold_ms=200),
        )
        second_results = await asyncio.gather(
            invoke(second_function, hold_ms=200),
            invoke(second_function, hold_ms=200),
        )
        first_after_reclamation = await invoke(first_function)
    finally:
        await registry.close()

    assert [result.output_data for result in second_results] == [
        {"value": 1, "counter": 1},
        {"value": 1, "counter": 1},
    ]
    assert first_after_reclamation.output_data == {"value": 1, "counter": 1}


@pytest.mark.asyncio
async def test_active_workers_are_never_reclaimed(tmp_path: Path) -> None:
    artifact, manifest = _artifact(tmp_path)
    registry = RevisionWorkerRegistry(max_workers=1, max_cached_revisions=4)
    first_function = uuid4()
    second_function = uuid4()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=20)

    async def invoke(function_id, *, hold_ms: int = 0):
        return await registry.execute(
            function_id=function_id,
            revision_hash=f"sha256:{str(function_id).replace('-', '') * 2}",
            artifact_root=artifact,
            run_id=uuid4(),
            request=_request(
                artifact,
                manifest,
                function_id=function_id,
                value=1,
                hold_ms=hold_ms,
            ),
            deadline_at=deadline,
        )

    active = asyncio.create_task(invoke(first_function, hold_ms=500))
    try:
        for _ in range(100):
            async with registry._lock:
                if registry._active:
                    break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("first worker never became active")

        with pytest.raises(RuntimeOverloaded):
            await invoke(second_function)
        await active
    finally:
        if not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        await registry.close()


@pytest.mark.asyncio
async def test_failed_initial_bootstrap_does_not_poison_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = 0

    class _Worker:
        healthy = True

        async def close(self) -> None:
            return None

    async def start(_root, *, budget, deadline_at):
        nonlocal calls
        del budget, deadline_at
        calls += 1
        if calls == 1:
            raise RuntimeError("transient bootstrap failure")
        return _Worker()

    monkeypatch.setattr(RevisionWorker, "start", staticmethod(start))
    pool = RevisionWorkerPool(tmp_path, budget=WorkerBudget(2))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=5)
    try:
        with pytest.raises(RuntimeError, match="transient bootstrap failure"):
            await pool.acquire(deadline_at=deadline)
        worker = await pool.acquire(deadline_at=deadline)
        await pool.release(worker)
    finally:
        await pool.close()

    assert calls == 2


@pytest.mark.asyncio
async def test_expired_deadline_does_not_start_revision_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    called = False

    async def create_subprocess(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("expired work must not start a process")

    monkeypatch.setattr(
        "agentbox.function_runtime.worker_pool.asyncio.create_subprocess_exec",
        create_subprocess,
    )

    with pytest.raises(TimeoutError, match="deadline elapsed"):
        await RevisionWorker.start(
            tmp_path,
            budget=WorkerBudget(1),
            deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    assert called is False
