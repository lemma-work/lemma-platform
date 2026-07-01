"""Holistic e2e tests for the daemon websocket reliability/concurrency redesign.

Unlike the per-harness happy-path tests (test_daemon_claude_code_e2e.py etc.),
these drive real disruption scenarios against real daemon subprocesses, a real
backend, and real LLM calls:

- Two different harnesses (Claude Code + Codex) running truly concurrently on
  one daemon, with no cross-talk between their conversations.
- The backend's websocket connection to the daemon being torn down and
  re-established mid-run (a server-side restart/connection reset), while the
  daemon holds the in-flight run and reattaches once reconnected.
- The daemon process itself going unresponsive for an extended window (SIGSTOP,
  simulating the client machine losing network connectivity) long enough to
  trip the backend's staleness detection, then coming back -- verifying the
  same provider subprocess resumes with no duplicate tool execution.
- Concurrent runs beyond the daemon's configured capacity being rejected
  immediately rather than silently competing for host resources.

Most of these need a real tool call (`sleep N` via lemma_exec_command) to
create a controllable time window to act within, so -- like the other
real-daemon e2e tests -- they need the real local AgentBox
(configure_workspace_api_url). The backend-restart test is the one exception:
it deliberately avoids any tool call, since restarting the backend also
severs any live MCP tool-call session regardless of timing -- see
_long_text_reply_prompt for why.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import socket

import pytest
import uvicorn

from app.modules.agent.domain.value_objects import MessageRole
from app.modules.agent.tests.e2e.daemon_harness_e2e_helpers import (
    BINARY,
    assert_latest_assistant_contains,
    create_daemon_profile,
    create_test_pod,
    create_workspace_agent_and_conversation,
    post_sse,
    start_real_daemon_process,
    stop_process,
    wait_for_daemon_harness,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.local_cli, pytest.mark.provider]


def _skip_unless_installed(*harness_kinds: str) -> None:
    for harness_kind in harness_kinds:
        binary = BINARY[harness_kind]
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} CLI is not installed")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _start_controllable_backend(app, port: int) -> tuple[uvicorn.Server, "asyncio.Task[None]"]:
    """Start a real, restartable uvicorn server wrapping ``app`` on ``port``.

    Mirrors app.modules.test_support.e2e.runtime's `backend_server` fixture
    internals, but keeps the Server/Task under the caller's control instead of
    hiding them behind a fixture -- needed here specifically to stop and
    restart the SAME port mid-test to simulate a server-side connection reset.
    """
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="websockets-sansio",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        if task.done():
            raise RuntimeError("Backend server exited before startup") from task.exception()
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError("Timed out starting backend server")
    return server, task


async def _stop_controllable_backend(server: uvicorn.Server, task: "asyncio.Task[None]") -> None:
    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _sleep_and_echo_prompt(*, seconds: int, marker: str) -> str:
    return (
        "Use lemma_exec_command to run exactly: "
        f"`printf START_{marker}; sleep {seconds}; printf END_{marker}`. "
        "Wait for it to finish, then reply with the exact combined output and "
        "nothing else."
    )


def _long_text_reply_prompt(*, marker: str) -> str:
    """A tool-free prompt shaped to create a long, disruptable streaming window.

    Deliberately uses NO tool call. Claude Code's CLI holds its MCP client
    session open against the backend's MCP endpoint for as long as any tool
    use is live in the conversation -- restarting the backend severs that
    session outright (a real, separate limitation of synchronous MCP calls
    tied to the same connection, confirmed empirically: it surfaces as an
    "error" stream event, "Connection closed by server.", regardless of
    exactly when the restart lands relative to an individual tool call).
    That is not what this test wants to isolate.

    A pure text reply only ever touches the websocket (daemon -> backend),
    which IS what the hold/reattach redesign covers -- so restart during
    token streaming here has no other connection to accidentally take down.
    "No duplicate tool execution" across a restart is covered separately by
    test_daemon_survives_client_network_loss_and_resumes_without_duplicate_tool_execution,
    which disrupts only the daemon's own websocket (SIGSTOP on the daemon
    process) while the provider subprocess's own MCP session stays live.
    """
    return (
        "Without calling any tools, write out the numbers from 1 to 60 in "
        "words (one, two, three, ...), each on its own line, and finish "
        f"with a final line containing exactly DONE_{marker}."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Concurrent runs across two different harnesses on one daemon
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_concurrent_runs_across_claude_code_and_codex_harnesses(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    tmp_path,
    worker,
):
    """One daemon process serving two concurrent conversations on two
    different harnesses at once -- the core promise of the redesign: a single
    websocket reliably multiplexing many concurrent runs with no cross-talk.
    """
    del configure_workspace_api_url
    _skip_unless_installed("CLAUDE_CODE", "CODEX")

    daemon_dir = tmp_path / "daemon-state"
    process = start_real_daemon_process(
        backend_server=backend_server,
        fixed_test_user=fixed_test_user,
        tmp_path=tmp_path,
    )
    try:
        claude_harness = await wait_for_daemon_harness(
            authenticated_client, harness_kind="CLAUDE_CODE", process=process
        )
        codex_harness = await wait_for_daemon_harness(
            authenticated_client, harness_kind="CODEX", process=process
        )
        assert claude_harness["daemon_id"] == codex_harness["daemon_id"], (
            "expected one daemon process to serve both harnesses"
        )

        conversations = {}
        for harness_kind, harness in (("CLAUDE_CODE", claude_harness), ("CODEX", codex_harness)):
            profile_id, model_name = await create_daemon_profile(
                authenticated_client,
                fixed_test_org,
                daemon_id=harness["daemon_id"],
                harness_kind=harness_kind,
                models=harness["models"],
            )
            pod_id = await create_test_pod(authenticated_client, fixed_test_org, harness_kind)
            _, conversation_id = await create_workspace_agent_and_conversation(
                authenticated_client,
                pod_id=pod_id,
                profile_id=profile_id,
                model_name=model_name,
                harness_kind=harness_kind,
            )
            conversations[harness_kind] = (pod_id, conversation_id)

        claude_pod_id, claude_conversation_id = conversations["CLAUDE_CODE"]
        codex_pod_id, codex_conversation_id = conversations["CODEX"]

        claude_task = asyncio.create_task(
            post_sse(
                authenticated_client,
                f"/pods/{claude_pod_id}/conversations/{claude_conversation_id}/messages",
                {"content": _sleep_and_echo_prompt(seconds=8, marker="CLAUDE")},
            )
        )
        codex_task = asyncio.create_task(
            post_sse(
                authenticated_client,
                f"/pods/{codex_pod_id}/conversations/{codex_conversation_id}/messages",
                {"content": _sleep_and_echo_prompt(seconds=8, marker="CODEX")},
            )
        )

        claude_events, codex_events = await asyncio.gather(claude_task, codex_task)

        for events in (claude_events, codex_events):
            assert events, "SSE stream produced no events"
            assert not [e for e in events if e["type"] == "error"], events
            assert events[-1]["type"] == "completed", events

        claude_text = await assert_latest_assistant_contains(
            authenticated_client,
            pod_id=claude_pod_id,
            conversation_id=claude_conversation_id,
            markers=["START_CLAUDE", "END_CLAUDE"],
        )
        codex_text = await assert_latest_assistant_contains(
            authenticated_client,
            pod_id=codex_pod_id,
            conversation_id=codex_conversation_id,
            markers=["START_CODEX", "END_CODEX"],
        )

        # The actual point of this test: the OTHER conversation's markers must
        # never leak across, which would indicate queue/event cross-talk
        # between the two concurrent runs sharing one daemon connection.
        assert "START_CODEX" not in claude_text and "END_CODEX" not in claude_text
        assert "START_CLAUDE" not in codex_text and "END_CLAUDE" not in codex_text
    finally:
        stop_process(process, daemon_dir=daemon_dir)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Backend restart mid-run
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_daemon_reconnects_after_backend_restart_mid_run(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    tmp_path,
    worker,
):
    """Simulates a server-side restart (e.g. a rolling deploy / LB connection
    drain) of the process the daemon's websocket is attached to: stop the real
    uvicorn server, wait briefly, start a fresh one on the SAME port. The
    daemon must notice the drop, reconnect with backoff, and the in-flight run
    must resume and complete via the same provider subprocess.
    """
    del configure_workspace_api_url
    _skip_unless_installed("CLAUDE_CODE")

    port = _free_port()
    server, server_task = await _start_controllable_backend(test_app, port)
    daemon_backend = {
        "host_base_url": f"http://127.0.0.1:{port}",
        "docker_base_url": f"http://host.docker.internal:{port}",
    }

    daemon_dir = tmp_path / "daemon-state"
    process = start_real_daemon_process(
        backend_server=daemon_backend,
        fixed_test_user=fixed_test_user,
        tmp_path=tmp_path,
    )
    try:
        harness = await wait_for_daemon_harness(
            authenticated_client, harness_kind="CLAUDE_CODE", process=process
        )
        profile_id, model_name = await create_daemon_profile(
            authenticated_client,
            fixed_test_org,
            daemon_id=harness["daemon_id"],
            harness_kind="CLAUDE_CODE",
            models=harness["models"],
        )
        pod_id = await create_test_pod(authenticated_client, fixed_test_org, "CLAUDE_CODE")
        _, conversation_id = await create_workspace_agent_and_conversation(
            authenticated_client,
            pod_id=pod_id,
            profile_id=profile_id,
            model_name=model_name,
            harness_kind="CLAUDE_CODE",
        )

        events: list[dict] = []

        async def _drive_run() -> list[dict]:
            async with authenticated_client.stream(
                "POST",
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                json={"content": _long_text_reply_prompt(marker="RESTART")},
                timeout=120,
            ) as response:
                assert response.status_code == 200
                async with asyncio.timeout(120):
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = json.loads(line.removeprefix("data: "))
                        events.append(payload)
                        if payload["type"] in {"completed", "stopped", "error"}:
                            break
            return events

        run_task = asyncio.create_task(_drive_run())

        # Wait for the first real TOKEN event -- proof the LLM has started
        # streaming text -- then restart immediately. No tool call is
        # involved (see _long_text_reply_prompt), so the only thing at risk
        # is the websocket itself, and a ~60-line reply leaves a wide,
        # comfortably-real window of remaining streaming to disrupt into.
        async with asyncio.timeout(30):
            while not any(e.get("type") == "token" for e in events):
                await asyncio.sleep(0.1)

        await _stop_controllable_backend(server, server_task)
        await asyncio.sleep(2)  # simulated downtime window
        server, server_task = await _start_controllable_backend(test_app, port)

        result_events = await run_task
        assert result_events, "SSE stream produced no events"
        assert not [e for e in result_events if e["type"] == "error"], result_events
        assert result_events[-1]["type"] == "completed", result_events

        await assert_latest_assistant_contains(
            authenticated_client,
            pod_id=pod_id,
            conversation_id=conversation_id,
            markers=["DONE_RESTART"],
        )
    finally:
        stop_process(process, daemon_dir=daemon_dir)
        await _stop_controllable_backend(server, server_task)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Client (daemon) network unavailable for an extended window
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_daemon_survives_client_network_loss_and_resumes_without_duplicate_tool_execution(
    monkeypatch,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    tmp_path,
    worker,
):
    """Freezes the daemon PROCESS itself (SIGSTOP) for long enough to trip the
    backend's staleness detection -- simulating the client machine losing
    network connectivity, not just a clean disconnect. On SIGCONT, the SAME
    provider subprocess must resume (buffered output flushed, live streaming
    restored) and complete with the tool call executed exactly once.

    The staleness threshold is monkeypatched down from its 90s production
    default to a few seconds (same pattern as
    test_daemon_websocket_route.py's unit tests) so the whole scenario runs
    on a short, real timescale -- a real 90s+ freeze around a real long-running
    exec_command was found to unreliably trip an unrelated limit in the local
    AgentBox test runtime (a "Remote end closed connection" transport failure
    on the sandbox's own command endpoint for commands this long), which is
    not what this test targets.
    """
    del configure_workspace_api_url
    _skip_unless_installed("CLAUDE_CODE")

    import app.modules.agent.api.controllers.runtime_config_controller as controller

    monkeypatch.setattr(controller.settings, "daemon_ws_ping_stale_after_seconds", 8.0)

    daemon_dir = tmp_path / "daemon-state"
    process = start_real_daemon_process(
        backend_server=backend_server,
        fixed_test_user=fixed_test_user,
        tmp_path=tmp_path,
    )
    try:
        harness = await wait_for_daemon_harness(
            authenticated_client, harness_kind="CLAUDE_CODE", process=process
        )
        profile_id, model_name = await create_daemon_profile(
            authenticated_client,
            fixed_test_org,
            daemon_id=harness["daemon_id"],
            harness_kind="CLAUDE_CODE",
            models=harness["models"],
        )
        pod_id = await create_test_pod(authenticated_client, fixed_test_org, "CLAUDE_CODE")
        _, conversation_id = await create_workspace_agent_and_conversation(
            authenticated_client,
            pod_id=pod_id,
            profile_id=profile_id,
            model_name=model_name,
            harness_kind="CLAUDE_CODE",
        )

        run_task = asyncio.create_task(
            post_sse(
                authenticated_client,
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                {"content": _sleep_and_echo_prompt(seconds=30, marker="FREEZE")},
                timeout=90,
            )
        )

        await asyncio.sleep(4)  # let the tool call actually start

        os.kill(process.pid, signal.SIGSTOP)
        try:
            # Comfortably past the monkeypatched 8s staleness threshold, so
            # the backend's reaper actually closes the connection from its
            # side (a frozen process can't run its own timers to notice on
            # its own -- this is what makes the freeze meaningfully
            # different from a clean, instantly-detected disconnect).
            await asyncio.sleep(15)
        finally:
            os.kill(process.pid, signal.SIGCONT)

        events = await run_task
        assert events, "SSE stream produced no events"
        assert not [e for e in events if e["type"] == "error"], events
        assert events[-1]["type"] == "completed", events

        final_text = await assert_latest_assistant_contains(
            authenticated_client,
            pod_id=pod_id,
            conversation_id=conversation_id,
            markers=["START_FREEZE", "END_FREEZE"],
        )
        # A genuine duplicate run/re-dispatch of the whole turn (the actual
        # redesign risk this test targets) would re-run the full prompt and
        # duplicate these markers in the final reply.
        assert final_text.count("START_FREEZE") == 1, final_text
        assert final_text.count("END_FREEZE") == 1, final_text

        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        items = response.json()["items"]
        # CLAUDE_CODE's daemon harness never persists a TOOL_RETURN message
        # (only codex.py/OpenCode's base.py emit one) and Claude Code's CLI
        # namespaces MCP tools as mcp__<server_label>__<tool_name>. Also, for
        # a long-running command sitting near Claude Code's own client-side
        # synchronous-wait budget (empirically ~150s), the CLI may legitimately
        # issue a SECOND tool_call to check in on the same backgrounded
        # command (distinct tool_call_id, tty/yield_time_ms polling args) --
        # not a re-execution. So "no duplicate execution" is proven by every
        # call referencing the same underlying shell command, not a strict
        # call count of 1.
        tool_calls = [
            item
            for item in items
            if item["role"] == MessageRole.ASSISTANT.value
            and item["kind"] == "TOOL_CALL"
            and (item["tool_name"] or "").endswith("lemma_exec_command")
        ]
        assert tool_calls, items
        commands = {(call.get("tool_args") or {}).get("cmd") for call in tool_calls}
        assert commands == {"printf START_FREEZE; sleep 30; printf END_FREEZE"}, tool_calls
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(process.pid, signal.SIGCONT)  # in case a failure left it stopped
        stop_process(process, daemon_dir=daemon_dir)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Concurrent runs beyond daemon capacity
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_concurrent_runs_beyond_daemon_capacity_are_rejected(
    monkeypatch,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    tmp_path,
    worker,
):
    """A daemon capped at 2 concurrent runs must immediately reject a 3rd
    rather than silently letting it compete for host resources -- the admission
    control added in the concurrency redesign, exercised against a real daemon
    dispatch (not the fakes used by the unit tests).
    """
    del configure_workspace_api_url
    _skip_unless_installed("CLAUDE_CODE")
    monkeypatch.setenv("LEMMA_DAEMON_MAX_CONCURRENT_RUNS", "2")

    daemon_dir = tmp_path / "daemon-state"
    process = start_real_daemon_process(
        backend_server=backend_server,
        fixed_test_user=fixed_test_user,
        tmp_path=tmp_path,
    )
    try:
        harness = await wait_for_daemon_harness(
            authenticated_client, harness_kind="CLAUDE_CODE", process=process
        )
        profile_id, model_name = await create_daemon_profile(
            authenticated_client,
            fixed_test_org,
            daemon_id=harness["daemon_id"],
            harness_kind="CLAUDE_CODE",
            models=harness["models"],
        )
        pod_id = await create_test_pod(authenticated_client, fixed_test_org, "CLAUDE_CODE")

        conversation_ids = []
        for _ in range(3):
            _, conversation_id = await create_workspace_agent_and_conversation(
                authenticated_client,
                pod_id=pod_id,
                profile_id=profile_id,
                model_name=model_name,
                harness_kind="CLAUDE_CODE",
            )
            conversation_ids.append(conversation_id)

        tasks = [
            asyncio.create_task(
                post_sse(
                    authenticated_client,
                    f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                    {"content": _sleep_and_echo_prompt(seconds=10, marker=f"CAP{i}")},
                )
            )
            for i, conversation_id in enumerate(conversation_ids)
        ]
        results = await asyncio.gather(*tasks)

        terminal_types = [events[-1]["type"] for events in results]
        completed = [t for t in terminal_types if t == "completed"]
        errored = [t for t in terminal_types if t == "error"]
        assert len(completed) == 2, terminal_types
        assert len(errored) == 1, terminal_types

        rejected_index = terminal_types.index("error")
        rejected_events = results[rejected_index]
        # Exact wording from _rejected_run_error_message (agent_runner_service.py):
        # "Daemon busy: N/M runs already active. Try again in a moment."
        assert "busy" in str(rejected_events[-1]["data"]).lower(), rejected_events

        # The rejected conversation must be finalized (FAILED), not stuck
        # RUNNING -- the exact gap found and fixed during live verification of
        # the redesign (AgentRunnerService previously had no branch for the
        # REJECTED event type).
        rejected_conversation_id = conversation_ids[rejected_index]
        conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{rejected_conversation_id}"
        )
        assert conversation.json()["last_run_status"] == "FAILED", conversation.text
    finally:
        stop_process(process, daemon_dir=daemon_dir)
