"""Real-worker cancellation e2e.

A streaq worker interrupted (SIGTERM) while an agent run is in flight must shut
down CLEANLY — without the anyio cancel-scope corruption that used to crash the
whole worker ("Attempted to exit a cancel scope that isn't the current task's
current cancel scope") — and the interrupted run must be finalized to a terminal
status rather than left stuck in RUNNING.

This validates:
  * PydanticAIHarness running agent.iter() in a child task so its anyio cancel
    scopes never corrupt streaq's, plus AgentRunnerService.execute finalizing in
    a same-task anyio shield and swallowing CancelledError, and
  * the worker grace_period that lets that finalization commit before the engine
    is disposed.

The worker here is FUNCTION-scoped and owned by the test so it can be SIGTERM'd
without affecting the shared session worker. Run in isolation, e.g.:

    uv run pytest app/modules/agent/tests/e2e/test_agent_run_cancellation_worker_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from streaq.task import TaskStatus

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.jobs.streaq_job_queue import create_streaq_client
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    MessageDraft,
    MessageRole,
    TERMINAL_AGENT_RUN_STATUSES,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
    system_lemma_env_overlay,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker, pytest.mark.slow]

DEFAULT_AGENT_RUNTIME = {"profile_id": "system:lemma"}

# Log fragments that mean the worker crashed on the cancel-scope corruption the
# fix targets. Their ABSENCE after a mid-run SIGTERM is the core regression guard.
_CANCEL_SCOPE_CRASH_MARKERS = (
    "Attempted to exit a cancel scope",
    "asynchronous generator is already running",
    "unhandled errors in a TaskGroup",
)


@pytest_asyncio.fixture(scope="function")
async def cancellable_worker(e2e_settings):
    """A real streaq worker owned by the test, so it can be SIGTERM'd mid-run.

    Mirrors the shared session ``worker`` fixture but function-scoped, and yields
    ``(proc, log_path)`` so the test drives the process lifecycle itself.

    Deliberately does NOT flush Redis: flushing would delete the shared session
    worker's consumer groups and trigger the very supervisor retry-storm this
    suite guards against. The run is targeted by a unique job id instead.
    """
    log_path = f"/tmp/lemma_cancel_worker_{uuid4().hex}.log"
    backend_root = Path(__file__).resolve().parents[5]
    log_file = open(log_path, "w+")
    proc = subprocess.Popen(
        [str(backend_root / ".venv/bin/streaq"), "run", "app.events:streaq_worker"],
        cwd=str(backend_root),
        env={
            **os.environ,
            **system_lemma_env_overlay(),
            "PYTHONPATH": ".",
            "DATABASE_URL": e2e_settings.database_url,
            "DATASTORE_DATABASE_URL": e2e_settings.datastore_database_url,
            "REDIS_URL": e2e_settings.redis_url,
            "API_URL": os.environ.get("API_URL", e2e_settings.api_url),
            "AGENTBOX_API_URL": e2e_settings.agentbox_api_url,
            "AGENTBOX_API_KEY": e2e_settings.agentbox_api_key,
            "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
            "ENVIRONMENT": "testing",
            "DEBUG": "true",
            "EMAIL_TRANSPORT": "filesystem",
            "EMAIL_OUTPUT_DIR": e2e_settings.email_output_dir,
            "GCS_STORAGE_BUCKET": "",
            "PUBLIC_BUCKET_NAME": "",
            "STORAGE_BACKEND": "local",
            "EMBEDDING_PROVIDER": "local",
            "LOCAL_OBJECT_STORAGE_ROOT": e2e_settings.local_object_storage_root,
            "LOCAL_FILE_STORAGE_ROOT": e2e_settings.local_file_storage_root,
            "COMPOSIO_CACHE_DIR": "/tmp/composio",
        },
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def _logs() -> str:
        log_file.flush()
        log_file.seek(0)
        return log_file.read()

    try:
        startup_ok = False
        for _ in range(200):
            if proc.poll() is not None:
                pytest.fail(
                    f"worker exited before startup (code={proc.returncode}).\n{_logs()}"
                )
            if "Worker starting..." in _logs():
                startup_ok = True
                break
            await asyncio.sleep(0.1)
        if not startup_ok:
            proc.terminate()
            pytest.fail(f"Timed out waiting for worker startup.\n{_logs()}")

        yield proc, log_path
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_file.close()


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Cancel Pod {uuid4().hex[:8]}",
            "description": "cancellation e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _start_real_agent_run(
    *,
    conversation_id: UUID,
    agent_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    content: str,
) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        run = await repo.create_agent_run(
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "e2e_cancellation"},
        )
        await repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text(content, role=MessageRole.USER),
        )
        repo.collect_events(
            [
                AgentRunStartedEvent(
                    conversation_id=conversation_id,
                    agent_run_id=run.id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=None,
                )
            ]
        )
        await uow.commit()
        return run.id


async def _wait_for_job_status(job_id: str, status: TaskStatus, attempts: int = 200) -> bool:
    async with create_streaq_client() as client:
        for _ in range(attempts):
            if await client.status_by_id(job_id) == status:
                return True
            await asyncio.sleep(0.1)
    return False


@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_sigterm_midrun_shuts_down_cleanly_and_finalizes_run(
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
    cancellable_worker,
):
    """SIGTERM while an agent run executes: worker exits clean, run goes terminal."""
    proc, log_path = cancellable_worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org)

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Cancellable Agent",
            "instruction": (
                "Answer in plain text. For a long essay, write one numbered "
                "sentence per line and keep going until the requested count."
            ),
            "agent_runtime": DEFAULT_AGENT_RUNTIME,
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent_id = create_agent.json()["id"]

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "cancellable_agent", "title": "Cancel", "type": "CHAT"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    run_id = await _start_real_agent_run(
        conversation_id=UUID(conversation_id),
        agent_id=UUID(agent_id),
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(pod_id),
        content="Write a 120 line numbered essay on the history of computing.",
    )

    # Wait until the worker is actually executing the run, then interrupt it
    # mid-flight (the harness is in an LLM call, with the anyio scope active).
    assert await _wait_for_job_status(
        f"agent-run:{run_id}", TaskStatus.RUNNING
    ), "run never reached RUNNING on the worker"
    await asyncio.sleep(0.5)

    proc.terminate()  # SIGTERM
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("worker did not exit within 30s of SIGTERM (possible hang)")

    logs = Path(log_path).read_text()

    # 1) Core regression guard: no cancel-scope corruption crash.
    for marker in _CANCEL_SCOPE_CRASH_MARKERS:
        assert marker not in logs, f"worker crashed on cancel scope: {marker!r}\n{logs[-3000:]}"
    # 2) Clean shutdown path ran.
    assert "Worker shutting down..." in logs, f"worker did not shut down cleanly\n{logs[-3000:]}"

    # 3) The interrupted run is finalized (not stuck RUNNING) — the grace_period
    #    lets the shielded finalization commit before engine disposal.
    terminal_values = {s.value for s in TERMINAL_AGENT_RUN_STATUSES}
    final_status = None
    for _ in range(100):
        db_session.expire_all()
        run_model = await db_session.get(AgentRunModel, run_id)
        final_status = run_model.status if run_model else None
        if final_status in terminal_values:
            break
        await asyncio.sleep(0.1)
    assert final_status in terminal_values, (
        f"run left non-terminal after SIGTERM: status={final_status}"
    )
