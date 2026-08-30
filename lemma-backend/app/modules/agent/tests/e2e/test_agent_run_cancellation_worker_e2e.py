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
without affecting the shared session worker.

Determinism in a shared session: the shared session ``worker`` also runs
``app.events:streaq_worker`` and would otherwise compete for the agent-run job
off the shared ``default`` streaq queue — whichever worker won the race would
run (and finalize) the job, so SIGTERMing this test's worker would be a no-op
and the run could be left RUNNING. To make this test the SOLE consumer of its
run, the ``cancellable_worker`` is given a DEDICATED streaq queue
(``WORKER_QUEUE_NAME``) and the run is dispatched straight onto that queue,
bypassing the ``agent-events`` event path so the shared worker never sees it.
That also keeps the SIGTERM'd worker's leftover pending entries confined to its
throwaway queue instead of starving the shared ``default`` queue.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from streaq.task import TaskStatus

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.jobs.streaq_job_queue import create_streaq_client
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
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
from app.modules.test_support.e2e.waiters import eventually, wait_for_status

pytestmark = [pytest.mark.e2e, pytest.mark.worker, pytest.mark.slow]

DEFAULT_AGENT_RUNTIME = {"profile_id": "system:lemma"}

# Log fragments that mean the worker crashed on the cancel-scope corruption the
# fix targets. Their ABSENCE after a mid-run SIGTERM is the core regression guard.
_CANCEL_SCOPE_CRASH_MARKERS = (
    "Attempted to exit a cancel scope",
    "asynchronous generator is already running",
    "unhandled errors in a TaskGroup",
)


@pytest.fixture
def mock_llm_latency_ms() -> int:
    """How long the mock model sits inside one request. Overridden per test."""
    return 0


@pytest_asyncio.fixture(scope="function")
async def cancellable_worker(e2e_settings, mock_llm_latency_ms):
    """A real streaq worker owned by the test, so it can be SIGTERM'd mid-run.

    Mirrors the shared session ``worker`` fixture but function-scoped, and yields
    ``(proc, log_path, queue_name)`` so the test drives the process lifecycle and
    dispatches its run onto the worker's dedicated queue.

    Runs on a DEDICATED streaq queue (``WORKER_QUEUE_NAME``) rather than the
    shared ``default`` queue, so the session worker never competes for — or
    finalizes — the run this test SIGTERMs. The run is dispatched straight onto
    this queue (see ``_dispatch_agent_run``), never via the shared ``agent-events``
    event path.

    Deliberately does NOT flush Redis: flushing would delete the shared session
    worker's consumer groups and trigger the very supervisor retry-storm this
    suite guards against. Any pending entries the SIGTERM leaves behind stay
    confined to this throwaway queue.
    """
    queue_name = f"cancel-test-{uuid4().hex[:8]}"
    log_path = f"/tmp/lemma_cancel_worker_{uuid4().hex}.log"
    backend_root = Path(__file__).resolve().parents[5]
    log_file = open(log_path, "w+")
    proc = subprocess.Popen(
        [str(backend_root / ".venv/bin/python"), "-m", "app.worker"],
        cwd=str(backend_root),
        env={
            **os.environ,
            **system_lemma_env_overlay(),
            "PYTHONPATH": ".",
            "WORKER_QUEUE_NAME": queue_name,
            "WORKER_LANES": os.environ.get("_CANCEL_TEST_LANES", ""),
            "DATABASE_URL": e2e_settings.database_url,
            "DATASTORE_DATABASE_URL": e2e_settings.datastore_database_url,
            "REDIS_URL": e2e_settings.redis_url,
            "API_URL": os.environ.get("API_URL", e2e_settings.api_url),
            "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
            "ENVIRONMENT": "testing",
            "DEBUG": "true",
            "EMAIL_TRANSPORT": "filesystem",
            "EMAIL_OUTPUT_DIR": e2e_settings.email_output_dir,
            "GCS_STORAGE_BUCKET": "",
            "STORAGE_BUCKET": "",
            "PUBLIC_BUCKET_NAME": "",
            "STORAGE_BACKEND": "local",
            "EMBEDDING_PROVIDER": "local",
            "LOCAL_OBJECT_STORAGE_ROOT": e2e_settings.local_object_storage_root,
            "LOCAL_FILE_STORAGE_ROOT": e2e_settings.local_file_storage_root,
            "COMPOSIO_CACHE_DIR": "/tmp/composio",
            # Unbuffered, because this fixture's whole diagnostic value is the
            # log it leaves behind — and the failure path SIGKILLs the worker,
            # which discards anything still sitting in a block buffer. A hang
            # used to produce a log that stopped at `service.started`.
            "PYTHONUNBUFFERED": "1",
            # So a hung worker can be made to print where every thread is
            # stuck, rather than dying silently on SIGKILL.
            "PYTHONFAULTHANDLER": "1",
            # Lets a test hold the mock model inside one request for as long as
            # it likes. A model request is a place the driver makes no stop
            # checks at all, so this is how "a stop reaches a busy run" is
            # asserted without needing a real slow tool.
            "E2E_MOCK_LLM_LATENCY_MS": str(mock_llm_latency_ms),
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

        async def probe() -> dict:
            return {
                "logs": _logs(),
                "exited": proc.poll() is not None,
                "returncode": proc.returncode,
            }

        await eventually(
            label="worker startup",
            probe=probe,
            done=lambda v: (
                '"logger": "app.core.infrastructure.jobs.streaq_runtime"' in v["logs"]
                and '"event": "service.started"' in v["logs"]
            ),
            fail_fast=lambda v: (
                f"worker exited before startup (code={v['returncode']}).\n{v['logs']}"
                if v["exited"]
                else None
            ),
            timeout_seconds=20.0,
            interval_seconds=0.1,
        )

        yield proc, log_path, queue_name
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
    content: str,
) -> UUID:
    """Create the run + user message, WITHOUT emitting AgentRunStartedEvent.

    Skipping the event keeps the run off the shared ``agent-events`` path so the
    session worker never enqueues (or runs) it; the test dispatches the run onto
    the cancellable_worker's dedicated queue itself via ``_dispatch_agent_run``.
    """
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
        await uow.commit()
        return run.id


async def _dispatch_agent_run(
    *,
    run_id: UUID,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    queue_name: str,
) -> None:
    """Enqueue ``process_agent_run`` directly onto the worker's dedicated queue.

    Mirrors the payload that ``enqueue_agent_run`` builds from an
    AgentRunStartedEvent (the same ``agent-run:{run_id}`` job id), but targets the
    cancellable_worker's private queue so it is the sole consumer.
    """
    async with create_streaq_client(queue_name=queue_name) as client:
        task = client.enqueue_unsafe(
            "process_agent_run",
            context={
                "agent_run_id": str(run_id),
                "conversation_id": str(conversation_id),
                "user_id": str(user_id),
                "pod_id": str(pod_id),
                "agent_name": None,
            },
        )
        task.id = f"agent-run:{run_id}"
        await task


async def _wait_for_job_status(
    job_id: str,
    status: TaskStatus,
    *,
    queue_name: str,
    timeout_seconds: float = 20.0,
) -> None:
    async with create_streaq_client(queue_name=queue_name) as client:
        await eventually(
            label=f"job {job_id} to reach {status}",
            probe=lambda: client.status_by_id(job_id),
            done=lambda current: current == status,
            timeout_seconds=timeout_seconds,
            interval_seconds=0.1,
        )


@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_sigterm_midrun_shuts_down_cleanly_and_finalizes_run(
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
    cancellable_worker,
):
    """SIGTERM while an agent run executes: worker exits clean, run goes terminal."""
    proc, log_path, queue_name = cancellable_worker
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
        content="Write a 120 line numbered essay on the history of computing.",
    )

    # Dispatch straight onto this worker's dedicated queue so it — and only it —
    # runs the job. The shared session worker consumes the `default` queue and
    # never sees this run.
    await _dispatch_agent_run(
        run_id=run_id,
        conversation_id=UUID(conversation_id),
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(pod_id),
        queue_name=queue_name,
    )

    # Wait until the worker is actually executing the run, then interrupt it
    # mid-flight (the harness is in an LLM call, with the anyio scope active).
    await _wait_for_job_status(
        f"agent-run:{run_id}", TaskStatus.RUNNING, queue_name=queue_name
    )
    await asyncio.sleep(0.5)

    proc.terminate()  # SIGTERM
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        # SIGABRT with PYTHONFAULTHANDLER makes the worker print every thread's
        # stack before it dies, so a hang names the line it is stuck on instead
        # of leaving a log that stops at startup.
        proc.send_signal(signal.SIGQUIT)  # dump pending coroutines first
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Expected: SIGQUIT only asks for a dump, it does not end the
            # process. Fall through to SIGABRT, which does.
            pass
        proc.send_signal(signal.SIGABRT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.fail(
            "worker did not exit within 30s of SIGTERM (possible hang)\n"
            f"{Path(log_path).read_text()[-6000:]}"
        )

    logs = Path(log_path).read_text()

    # 1) Core regression guard: no cancel-scope corruption crash.
    for marker in _CANCEL_SCOPE_CRASH_MARKERS:
        assert marker not in logs, (
            f"worker crashed on cancel scope: {marker!r}\n{logs[-3000:]}"
        )
    # 2) Clean shutdown path ran.
    assert (
        '"logger": "app.core.infrastructure.jobs.streaq_runtime"' in logs
        and '"event": "service.stopped"' in logs
    ), f"worker did not shut down cleanly\n{logs[-3000:]}"

    # 3) The interrupted run is finalized (not stuck RUNNING) — the grace_period
    #    lets the shielded finalization commit before engine disposal.
    terminal_values = {s.value for s in TERMINAL_AGENT_RUN_STATUSES}

    async def probe() -> dict:
        db_session.expire_all()
        run_model = await db_session.get(AgentRunModel, run_id)
        return {"status": run_model.status if run_model else None}

    # failed=set(): a SIGTERM mid-run very plausibly finalizes to FAILED, which
    # is just as much a legitimate terminal outcome here as COMPLETED/STOPPED --
    # the whole point of this probe is "did it reach ANY terminal status", not
    # a particular one. wait_for_status's default fail-fast on FAILED would
    # break the common (SIGTERM -> FAILED) case instead of accepting it as done.
    await wait_for_status(
        label=f"run {run_id} to leave RUNNING after SIGTERM",
        probe=probe,
        expected=terminal_values,
        failed=set(),
        timeout_seconds=10.0,
        interval_seconds=0.1,
    )


@pytest.mark.parametrize("mock_llm_latency_ms", [60_000])
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_stop_reaches_a_run_that_is_busy_in_the_model(
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
    cancellable_worker,
):
    """Stop must land while the driver is somewhere that never checks for one.

    Every stop check the harness makes sits between streamed chunks, so none of
    them runs during a model request or a tool call — and `exec_command` may
    hold for 300 seconds. Pressing Stop therefore did nothing at all until
    whatever was in flight returned, while the client went on showing the run as
    live. That is the "Stop does nothing" report.

    The mock model is held inside a single request for 20 seconds, which is the
    same blind spot as a long tool call and needs no sandbox. The assertion is
    the latency: the stop has to land in a small fraction of that window. Before
    the consumer raced the queue against a stop poll, nothing could have
    observed it until the 20 seconds were up.

    Runs on this file's dedicated worker and queue, so the shared session worker
    never competes for the run.
    """
    proc, _log_path, queue_name = cancellable_worker
    del proc
    pod_id = await _create_pod(authenticated_client, fixed_test_org)

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Stoppable Agent",
            "instruction": "Answer in plain text.",
            "agent_runtime": DEFAULT_AGENT_RUNTIME,
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent_id = create_agent.json()["id"]

    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "stoppable_agent", "title": "Stop", "type": "CHAT"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = create_conversation.json()["id"]

    run_id = await _start_real_agent_run(
        conversation_id=UUID(conversation_id),
        agent_id=UUID(agent_id),
        content="Write something long.",
    )
    await _dispatch_agent_run(
        run_id=run_id,
        conversation_id=UUID(conversation_id),
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(pod_id),
        queue_name=queue_name,
    )
    await _wait_for_job_status(
        f"agent-run:{run_id}", TaskStatus.RUNNING, queue_name=queue_name
    )
    # Long enough that the run is unambiguously inside the model request and
    # not still doing setup, where stop checks do exist.
    await asyncio.sleep(6.0)

    requested_at = asyncio.get_running_loop().time()
    stopped = await authenticated_client.post(
        f"/pods/{pod_id}/conversations/{conversation_id}/stop"
    )
    assert stopped.status_code == 200, stopped.text

    # Deliberately NOT the agent_runs row. The stop event also reaches the
    # shared session worker, which looks this job up on its own queue, does not
    # find it (this run was dispatched to a dedicated one), concludes there is
    # no job to wait for and finalizes the row itself. A row assertion
    # therefore goes green while the worker actually executing the run is still
    # asleep in the model — it passed against the unfixed code, which is how
    # this was caught. The job on *this* worker's queue is the honest signal.
    await _wait_for_job_status(
        f"agent-run:{run_id}",
        TaskStatus.DONE,
        queue_name=queue_name,
        timeout_seconds=30.0,
    )
    # The latency IS the assertion, and it is measured rather than left to a
    # generous timeout. The model is holding its request for 60s; anything near
    # that means nothing observed the stop until the request returned.
    elapsed = asyncio.get_running_loop().time() - requested_at
    assert elapsed < 15.0, (
        f"the run took {elapsed:.1f}s to end while the model held its request "
        "for 60s — the stop was not observed until the request returned"
    )

    async def probe() -> dict:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            row = await uow.session.get(AgentRunModel, run_id)
            return {"status": None if row is None else str(row.status)}

    await wait_for_status(
        label=f"run {run_id} to settle as STOPPED",
        probe=probe,
        expected={AgentRunStatus.STOPPED.value},
        failed=set(),
        timeout_seconds=15.0,
        interval_seconds=0.1,
    )
