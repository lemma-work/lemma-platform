"""Function revision retention, against real Postgres and real storage.

Deliberately NOT in ``test_function_e2e.py``: that module carries a module-level
``workspace`` marker, so every test in it is deselected by the fast CI lane and
only runs under ``E2E_REAL=1``. Retention needs no sandbox -- it walks rows and
deletes objects -- so keeping it here is what gets it run on every PR.

Revisions are minted through the repository rather than by saving code, for the
same reason: compiling code is the only part that needs a sandbox.
"""

from __future__ import annotations

from app.modules.function.config import function_settings, revision_settings

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status

from app.core.config import settings

pytestmark = pytest.mark.e2e


def _hash(seed: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


async def _create_function(client, pod_id: str, name: str) -> str:
    res = await client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "retention e2e"},
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text
    return res.json()["id"]


async def _mint_revisions(db_session, function_id, *, count: int, age_days: int):
    """Insert revisions and write the bytes they name, as a real build would."""
    from app.modules.function.api.dependencies import get_function_storage_factory
    from app.modules.function.domain.entities import FunctionRevisionEntity
    from app.modules.function.infrastructure.models import FunctionRevisionModel
    from app.modules.function.infrastructure.repositories import FunctionRepository
    from sqlalchemy import update

    repository = FunctionRepository.__new__(FunctionRepository)
    repository.session = db_session
    storage = get_function_storage_factory()(function_id)

    minted = []
    for index in range(count):
        revision_hash = _hash(f"{function_id}-{index}")
        bare = revision_hash.removeprefix("sha256:")
        entity = await repository.record_revision(
            FunctionRevisionEntity(
                function_id=function_id,
                revision_number=0,
                revision_hash=revision_hash,
                code_path=f"revisions/{bare}/function.py",
            )
        )
        await storage.write_file(f"artifacts/{bare}.zip", b"ARTIFACT")
        await storage.write_file(f"revisions/{bare}/function.py", b"def run(): ...")
        minted.append(entity)

    # Backdated so `_drop_in_flight`'s execution-deadline grace does not protect
    # everything -- a freshly minted revision is never prunable, by design.
    await db_session.execute(
        update(FunctionRevisionModel)
        .where(FunctionRevisionModel.function_id == function_id)
        .values(created_at=datetime.now(timezone.utc) - timedelta(days=age_days))
    )
    await db_session.commit()
    return minted


def _stored_artifacts(function_id) -> set[str]:
    root = (
        Path(settings.local_file_storage_root)
        / "common"
        / "functions"
        / str(function_id)
        / "artifacts"
    )
    return {path.name for path in root.rglob("*") if path.is_file()}


async def test_the_sweep_reaches_functions_beyond_the_first_page(
    authenticated_client, test_pod, db_session, monkeypatch
):
    """The head-of-table regression, at the tier where the SQL is real.

    The old query took the lowest `batch_size` ids with no cursor and no filter.
    With a page size of one it touched exactly one function, every tick, forever
    -- and the functions it never reached are precisely the ones that stopped
    being edited, which is the only case this cron exists for.
    """
    from app.core.infrastructure.db.session import get_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.events.handlers import _sweep_function_revisions

    monkeypatch.setattr(revision_settings, "function_revision_keep_last", 1)
    monkeypatch.setattr(revision_settings, "function_revision_max_keep", 1)
    monkeypatch.setattr(revision_settings, "function_revision_keep_days", 0)
    monkeypatch.setattr(function_settings, "function_job_deadline_seconds", 1)

    pod_id = test_pod["id"]
    function_ids = []
    for index in range(3):
        name = f"fn_sweep_{index}_{uuid4().hex[:8]}"
        function_id = await _create_function(authenticated_client, pod_id, name)
        await _mint_revisions(db_session, function_id, count=3, age_days=10)
        function_ids.append(function_id)

    before = {fid: _stored_artifacts(fid) for fid in function_ids}
    assert all(len(names) == 3 for names in before.values())

    outcome = await _sweep_function_revisions(
        SessionUnitOfWorkFactory(get_session_maker()),
        page_size=1,  # one function per round trip: the drain has to do the rest
    )

    assert outcome.examined >= 3, "every candidate function was looked at"
    assert outcome.pruned_functions >= 3
    for function_id in function_ids:
        remaining = _stored_artifacts(function_id)
        assert len(remaining) < 3, f"{function_id} kept all its artifacts"


async def test_a_settled_install_examines_nothing(
    authenticated_client, test_pod, db_session, monkeypatch
):
    """The candidate set drains, which is why this needs no cursor column: a
    second tick over an install with nothing to do does no work at all."""
    from app.core.infrastructure.db.session import get_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.events.handlers import _sweep_function_revisions

    monkeypatch.setattr(revision_settings, "function_revision_keep_last", 1)
    monkeypatch.setattr(revision_settings, "function_revision_max_keep", 1)
    monkeypatch.setattr(revision_settings, "function_revision_keep_days", 0)
    monkeypatch.setattr(function_settings, "function_job_deadline_seconds", 1)

    pod_id = test_pod["id"]
    name = f"fn_settled_{uuid4().hex[:8]}"
    function_id = await _create_function(authenticated_client, pod_id, name)
    await _mint_revisions(db_session, function_id, count=3, age_days=10)

    factory = SessionUnitOfWorkFactory(get_session_maker())
    first = await _sweep_function_revisions(factory, page_size=10)
    assert first.pruned_functions >= 1

    second = await _sweep_function_revisions(factory, page_size=10)
    assert second.examined == 0, "the pruned rows left the candidate set"
    assert second.pruned_functions == 0


async def test_delayed_deletion_cannot_remove_a_redeployed_digest(
    authenticated_client,
    test_pod,
    db_session,
):
    from uuid import UUID
    from app.core.infrastructure.db.session import get_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.core.retention import RetentionPolicy
    from app.modules.function.api.dependencies import (
        build_function_service,
        get_function_storage_factory,
    )
    from app.modules.function.domain.entities import (
        FunctionArtifact,
        FunctionRevisionEntity,
    )
    from app.modules.function.services.function_revision_retention import (
        FunctionRevisionRetention,
    )

    function_id = UUID(
        await _create_function(
            authenticated_client, test_pod["id"], f"retry_{uuid4().hex[:8]}"
        )
    )
    old, current = await _mint_revisions(db_session, function_id, count=2, age_days=10)
    factory = SessionUnitOfWorkFactory(get_session_maker())
    async with factory() as uow:
        service = build_function_service(uow)
        function = await service.repository.activate_revision(function_id, current)
        await uow.commit()
    async with factory() as uow:
        service = build_function_service(uow)
        retention = FunctionRevisionRetention(
            service.repository, service.storage_factory
        )
        plan = await retention.plan(
            function, policy=RetentionPolicy(keep_last=1, max_keep=1, keep_days=0)
        )
        await uow.commit()
    assert plan.revision_numbers == (old.revision_number,)

    artifact = FunctionArtifact(revision_hash=old.revision_hash, generation=uuid4())
    storage = get_function_storage_factory()(function_id)
    await storage.write_file(artifact.artifact_path, b"REDEPLOYED")
    await storage.write_file(artifact.code_path, b"NEW GENERATION SOURCE")
    async with factory() as uow:
        service = build_function_service(uow)
        redeployed = await service.repository.record_revision(
            FunctionRevisionEntity(
                function_id=function_id,
                revision_number=0,
                revision_hash=old.revision_hash,
                generation=artifact.generation,
                code_path=artifact.code_path,
            )
        )
        await service.repository.activate_revision(function_id, redeployed)
        await uow.commit()
    assert redeployed.revision_number > current.revision_number
    # A worker can resume its saved plan after a new upload, or repeat it after a crash.
    await retention.execute(plan)
    await retention.execute(plan)
    assert await storage.read_bytes(artifact.artifact_path) == b"REDEPLOYED"
    assert await storage.read_file(artifact.code_path) in (
        b"NEW GENERATION SOURCE",
        "NEW GENERATION SOURCE",
    )
    async with factory() as uow:
        service = build_function_service(uow)
        assert (
            await service.repository.get_revision_by_number(
                function_id, old.revision_number
            )
        ).is_pruned
        assert not (
            await service.repository.get_revision_by_number(
                function_id, redeployed.revision_number
            )
        ).is_pruned


async def test_pending_deletion_is_retried_below_the_retention_floor(
    authenticated_client,
    test_pod,
    db_session,
    monkeypatch,
):
    from uuid import UUID
    from sqlalchemy import update
    from app.core.infrastructure.db.session import get_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.function.api.dependencies import get_function_storage_factory
    from app.modules.function.events.handlers import _sweep_function_revisions
    from app.modules.function.infrastructure.models import FunctionRevisionModel

    monkeypatch.setattr(revision_settings, "function_revision_keep_last", 1)
    monkeypatch.setattr(revision_settings, "function_revision_max_keep", 1)
    monkeypatch.setattr(revision_settings, "function_revision_keep_days", 0)
    function_id = UUID(
        await _create_function(
            authenticated_client, test_pod["id"], f"pending_{uuid4().hex[:8]}"
        )
    )
    old, _current = await _mint_revisions(db_session, function_id, count=2, age_days=10)
    await db_session.execute(
        update(FunctionRevisionModel)
        .where(FunctionRevisionModel.id == old.id)
        .values(
            pruned_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
    )
    await db_session.commit()
    outcome = await _sweep_function_revisions(
        SessionUnitOfWorkFactory(get_session_maker()), page_size=1
    )
    assert outcome.examined >= 1
    storage = get_function_storage_factory()(function_id)
    with pytest.raises(FileNotFoundError):
        await storage.read_bytes(old.artifact_path)
    await db_session.refresh(await db_session.get(FunctionRevisionModel, old.id))
    assert (await db_session.get(FunctionRevisionModel, old.id)).purged_at is not None
