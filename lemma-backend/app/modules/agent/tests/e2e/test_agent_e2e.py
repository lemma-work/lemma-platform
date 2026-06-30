from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from asgiref.testing import ApplicationCommunicator as ConnectorCommunicator
from fastapi import status
from sqlalchemy import select
from streaq.task import TaskStatus

from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import create_streaq_client
from app.modules.agent.api.controllers.shared import (
    conversation_channel,
)
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunApprovalDecision,
    AgentRuntimeConfig,
    AgentRunStatus,
    ConversationStatus,
    HarnessKind,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.models import AgentRuntimeDaemonModel
from app.modules.agent.infrastructure.models import AgentRuntimeProfileModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
    system_lemma_default_model,
    system_lemma_model_names,
)
from app.modules.test_support.e2e_authz import (
    create_role_visibility_context,
    item_names,
)

pytestmark = pytest.mark.e2e

DEFAULT_AGENT_RUNTIME = {"profile_id": "system:lemma"}


async def _create_test_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Agent Pod {uuid4().hex[:8]}",
            "description": "Agent E2E pod",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _seed_paused_interaction(
    authenticated_client,
    fixed_test_org,
    *,
    tool_name: str,
    tool_args: dict,
):
    """Create pod/agent/conversation and a paused ``tool_name`` call.

    Mirrors the post-pause state: the harness persisted the interaction tool call
    and the run finished COMPLETED with the conversation in WAITING.
    """
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Interaction Agent",
            "instruction": "Ask the user when needed.",
            "toolsets": ["USER_INTERACTION"],
            "agent_runtime": DEFAULT_AGENT_RUNTIME,
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent = create_agent.json()
    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent["name"], "title": "Pause flow", "type": "CHAT"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = UUID(create_conversation.json()["id"])
    approval_id = f"functions.{tool_name}:e2e"

    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        paused_run = await repo.create_agent_run(
            conversation_id=conversation_id,
            agent_id=UUID(agent["id"]),
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "approval_e2e"},
        )
        await repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=paused_run.id,
            draft=MessageDraft.of_tool_call(
                tool_name=tool_name,
                tool_call_id=approval_id,
                tool_args=tool_args,
                role=MessageRole.ASSISTANT,
                metadata={"tool_name": tool_name},
            ),
        )
        # The pause shape: COMPLETED run, WAITING conversation.
        await repo.finish_agent_run(
            agent_run_id=paused_run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.WAITING,
        )
        await uow.commit()
    return pod_id, conversation_id, agent, paused_run, approval_id


async def _resume_run_and_tool_return(conversation_id: UUID, paused_run_id, approval_id):
    """Return (resume_run, tool_return_message) created by resolving the pause."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        runs = await repo.list_agent_runs_with_messages(conversation_id)
        messages, _ = await repo.list_messages(
            conversation_id=conversation_id, limit=500
        )
    resume_runs = [run for run in runs if run.id != paused_run_id]
    returns = [
        message
        for message in messages
        if message.kind == MessageKind.TOOL_RETURN
        and message.tool_call_id == approval_id
    ]
    resume_run = resume_runs[0] if resume_runs else None
    tool_return = returns[0] if returns else None
    return resume_run, tool_return


async def _seed_paused_interactions(
    authenticated_client,
    fixed_test_org,
    *,
    interactions: list[tuple[str, dict]],
):
    """Seed one paused run with MULTIPLE pausing tool calls (the multi-pause case)."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Interaction Agent",
            "instruction": "Ask the user when needed.",
            "toolsets": ["USER_INTERACTION"],
            "agent_runtime": DEFAULT_AGENT_RUNTIME,
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent = create_agent.json()
    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent["name"], "title": "Pause flow", "type": "CHAT"},
    )
    assert create_conversation.status_code == 201, create_conversation.text
    conversation_id = UUID(create_conversation.json()["id"])

    approval_ids: list[str] = []
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        paused_run = await repo.create_agent_run(
            conversation_id=conversation_id,
            agent_id=UUID(agent["id"]),
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "approval_e2e"},
        )
        for idx, (tool_name, tool_args) in enumerate(interactions):
            approval_id = f"functions.{tool_name}:e2e-{idx}"
            approval_ids.append(approval_id)
            await repo.append_message(
                conversation_id=conversation_id,
                agent_run_id=paused_run.id,
                draft=MessageDraft.of_tool_call(
                    tool_name=tool_name,
                    tool_call_id=approval_id,
                    tool_args=tool_args,
                    role=MessageRole.ASSISTANT,
                    metadata={"tool_name": tool_name},
                ),
            )
        await repo.finish_agent_run(
            agent_run_id=paused_run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.WAITING,
        )
        await uow.commit()
    return pod_id, conversation_id, agent, paused_run, approval_ids


async def _seed_gmail_connector(db_session) -> None:
    from app.modules.connectors.domain.connector import (
        AuthMethod,
        AuthProvider,
        LemmaProviderCapability,
    )
    from app.modules.connectors.infrastructure.models.connector import Connector
    from app.modules.connectors.infrastructure.models.connector_operation import (
        ConnectorOperation,
    )

    connector = Connector(
        id="gmail",
        title="Gmail",
        description="Gmail connector",
        provider_capabilities=[
            LemmaProviderCapability(
                provider=AuthProvider.LEMMA,
                auth_scheme=AuthMethod.NOAUTH,
            ).model_dump(mode="json")
        ],
        is_active=True,
    )
    operation = ConnectorOperation(
        id="gmail:send_email",
        connector_id="gmail",
        name="send_email",
        provider_operation_name="SEND_EMAIL",
        display_name="Send Email",
        description="Send an email message to one or more recipients.",
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        output_schema={"type": "object"},
    )
    db_session.add(connector)
    db_session.add(operation)
    await db_session.commit()


async def _collect_sse_lines(line_iterator) -> list[dict]:
    events: list[dict] = []
    async with asyncio.timeout(180):
        async for line in line_iterator:
            if not line.startswith("data: "):
                continue
            payload = json.loads(line.removeprefix("data: "))
            if payload["type"] == "token":
                assert set(payload) <= {"type", "kind", "data"}
                assert isinstance(payload["data"], str)
            events.append(payload)
            if payload["type"] in {"completed", "stopped", "error"}:
                break
    return events


async def _post_sse(client, url: str, payload: dict) -> list[dict]:
    async with client.stream("POST", url, json=payload, timeout=180) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise AssertionError(body.decode())
        return await _collect_sse_lines(response.aiter_lines())


async def _get_sse_after_publish(
    client,
    url: str,
    publish: Callable[[], Awaitable[None]],
) -> list[dict]:
    async def delayed_publish() -> None:
        await asyncio.sleep(0.5)
        await publish()

    publish_task = asyncio.create_task(delayed_publish())
    async with client.stream("GET", url, timeout=30) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise AssertionError(body.decode())
        events = await _collect_sse_lines(response.aiter_lines())
    await publish_task
    return events


async def _get_sse_until_closed(client, url: str) -> list[dict]:
    async with client.stream("GET", url, timeout=5) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise AssertionError(body.decode())
        async with asyncio.timeout(5):
            return await _collect_sse_lines(response.aiter_lines())


def _assert_completed_without_error(events: list[dict]) -> None:
    assert events, "SSE stream produced no events"
    assert not [event for event in events if event["type"] == "error"], events
    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events


def _split_concatenated_json(buffer: str) -> list[dict]:
    """Split a buffer of back-to-back JSON objects into a list.

    The tool-token stream emits one JSON object per tool call; when an agent makes
    several tool calls their objects arrive concatenated (``{...}{...}``), so a
    single ``json.loads`` raises "Extra data". Decode them one at a time."""
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    pos = 0
    length = len(buffer)
    while pos < length:
        while pos < length and buffer[pos].isspace():
            pos += 1
        if pos >= length:
            break
        obj, pos = decoder.raw_decode(buffer, pos)
        objects.append(obj)
    return objects


async def _create_active_run(
    *,
    conversation_id: UUID,
    agent_id: UUID | None,
) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await ConversationRepository(uow).create_agent_run(
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "e2e_stop"},
        )
        return run.id


async def _start_real_agent_run(
    *,
    conversation_id: UUID,
    agent_id: UUID | None,
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
            metadata={"source": "e2e_stop_running"},
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
                ),
            ]
        )
        await uow.commit()
        return run.id


async def _finish_agent_run(run_id: UUID, status: AgentRunStatus) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).finish_agent_run(
            agent_run_id=run_id,
            status=status,
        )


async def _append_messages(
    *,
    conversation_id: UUID,
    messages: list[tuple[str, str]],
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        for role, content in messages:
            await repo.append_message(
                conversation_id=conversation_id,
                agent_run_id=None,
                draft=MessageDraft.of_text(content, role=MessageRole(role)),
            )


async def _wait_for_run_status(
    db_session,
    run_id: UUID,
    status: str,
    *,
    attempts: int = 50,
    sleep_seconds: float = 0.1,
) -> None:
    expected = status.value if isinstance(status, AgentRunStatus) else status
    for _ in range(attempts):
        db_session.expire_all()
        run_model = await db_session.get(AgentRunModel, run_id)
        if run_model is not None and run_model.status == expected:
            return
        await asyncio.sleep(sleep_seconds)
    db_session.expire_all()
    run_model = await db_session.get(AgentRunModel, run_id)
    actual = run_model.status if run_model else None
    if actual == expected:
        return
    raise AssertionError(f"Expected run {run_id} to be {expected}, got {actual}")


async def _wait_for_streaq_job_status(job_id: str, status: TaskStatus) -> None:
    last_status: TaskStatus | None = None
    async with create_streaq_client() as worker:
        for _ in range(100):
            last_status = await worker.status_by_id(job_id)
            if last_status == status:
                return
            await asyncio.sleep(0.1)
    raise AssertionError(f"Expected job {job_id} to be {status}, got {last_status}")


async def _receive_daemon_ws_message(
    communicator: ConnectorCommunicator,
    *,
    message_type: str,
    timeout: float = 30,
) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            output = await communicator.receive_output(timeout=timeout)
            assert output["type"] == "websocket.send", output
            raw = output.get("text") or output.get("bytes")
            if isinstance(raw, bytes):
                raw = raw.decode()
            payload = json.loads(raw)
            if payload.get("type") == message_type:
                return payload


async def _wait_for_daemon_session(
    authenticated_client,
    *,
    pod_id,
    conversation_id,
    session_id: str,
    timeout: float = 15.0,
) -> None:
    """Wait until the daemon session id is durably persisted on the conversation.

    The session id is written by the worker while it processes the
    ``daemon.session.started`` status event; the next (resumption) run reads it
    cross-process, so the test must let that write land before triggering it.
    """
    async with asyncio.timeout(timeout):
        while True:
            response = await authenticated_client.get(
                f"/pods/{pod_id}/conversations/{conversation_id}"
            )
            assert response.status_code == 200, response.text
            metadata = response.json().get("metadata") or {}
            session = metadata.get("daemon_session") or {}
            if session.get("session_id") == session_id:
                return
            await asyncio.sleep(0.2)


async def _wait_for_daemon_harness(
    authenticated_client,
    *,
    harness_kind: str,
    process: subprocess.Popen[str] | None = None,
    timeout: float = 30,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    last_payload: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        if process is not None and process.poll() is not None:
            stdout, _ = process.communicate(timeout=2)
            raise AssertionError(
                f"Daemon process exited before {harness_kind} became available.\n{stdout}"
            )
        response = await authenticated_client.get("/agent-runtime/harnesses")
        assert response.status_code == 200, response.text
        last_payload = response.json()
        for item in last_payload["items"]:
            if item["harness_kind"] == harness_kind and item["daemon_status"] == "ONLINE":
                return item
        await asyncio.sleep(0.25)
    raise AssertionError(
        f"Timed out waiting for {harness_kind} daemon harness. Last payload: {last_payload}"
    )


class TestPodAgentLifecycle:
    @pytest.mark.real_llm
    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_file_creation_tool_call_streams_tool_json_tokens(
        self,
        authenticated_client,
        fixed_test_org,
        configure_workspace_api_url,
        worker,
    ):
        # configure_workspace_api_url starts the per-test AgentBox manager and
        # routes workspace calls to it. Without it the worker has no manager to
        # reach and every exec_command fails with ConnectError (the run only
        # "passed" when a previous test's manager happened to linger on the
        # pinned port).
        _ = worker
        _ = configure_workspace_api_url
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Tool Stream Agent",
                "instruction": (
                    "When asked to create the stream probe file, call exec_command "
                    "exactly once with a cmd that writes a file named "
                    "'stream_probe.md' whose content is exactly 20 lines, numbered "
                    "'line 01' through 'line 20', one per line. After the tool "
                    "succeeds, answer briefly."
                ),
                "toolsets": ["WORKSPACE_CLI"],
                "agent_runtime": DEFAULT_AGENT_RUNTIME,
            },
        )
        assert create_agent.status_code == 201, create_agent.text

        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": "tool_stream_agent",
                "title": "Tool token stream",
                "type": "CHAT",
            },
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation_id = create_conversation.json()["id"]

        events = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {
                "content": (
                    "Create the stream probe file now. Use exactly these 20 "
                    "lines as content: line 01, line 02, line 03, line 04, "
                    "line 05, line 06, line 07, line 08, line 09, line 10, "
                    "line 11, line 12, line 13, line 14, line 15, line 16, "
                    "line 17, line 18, line 19, line 20. Put each on its own line."
                )
            },
        )
        _assert_completed_without_error(events)

        tool_chunks = [
            event["data"]
            for event in events
            if event.get("type") == "token" and event.get("kind") == "tool"
        ]
        assert tool_chunks, events
        # A real agent may legitimately stream more than one tool call (e.g. write
        # the file, then a follow-up to verify it). The tool-token stream emits one
        # JSON object per call, concatenated, so split the buffer into successive
        # objects rather than assuming a single call.
        streamed_calls = _split_concatenated_json("".join(tool_chunks))
        assert streamed_calls, tool_chunks
        probe_call = next(
            (
                call
                for call in streamed_calls
                if call.get("tool_name") == "exec_command"
                and "stream_probe.md" in (call.get("args", {}).get("cmd") or "")
            ),
            None,
        )
        assert probe_call is not None, streamed_calls
        streamed_cmd = probe_call["args"]["cmd"]
        assert "line 01" in streamed_cmd and "line 20" in streamed_cmd

        messages = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == 200, messages.text
        message_items = messages.json()["items"]
        probe_tool_call = next(
            (
                item
                for item in message_items
                if item["kind"] == "TOOL_CALL"
                and item["tool_name"] == "exec_command"
                and "stream_probe.md" in (item["tool_args"].get("cmd") or "")
            ),
            None,
        )
        assert probe_tool_call is not None, message_items
        exec_returns = [
            item
            for item in message_items
            if item["kind"] == "TOOL_RETURN" and item["tool_name"] == "exec_command"
        ]
        assert exec_returns, message_items
        assert any(
            (item.get("tool_result") or {}).get("success") is True
            or (item.get("tool_result") or {}).get("exit_code") == 0
            for item in exec_returns
        ), exec_returns

    async def test_harness_text_message_between_tool_calls_persists_in_db(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Daemon Message Persistence Agent",
                "instruction": "Test harness message persistence.",
                "toolsets": ["WORKSPACE_CLI"],
                "agent_runtime": DEFAULT_AGENT_RUNTIME,
            },
        )
        assert create_agent.status_code == 201, create_agent.text
        agent = create_agent.json()

        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": agent["name"],
                "title": "Harness text persistence",
                "type": "CHAT",
            },
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation_id = UUID(create_conversation.json()["id"])

        async with create_uow_from_session_maker(async_session_maker) as uow:
            run = await ConversationRepository(uow).create_agent_run(
                conversation_id=conversation_id,
                agent_id=UUID(agent["id"]),
                agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
                metadata={"source": "daemon_message_persistence_test"},
            )
            await uow.commit()
        agent_run_id = run.id

        runner = AgentRunnerService(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            harness_registry=object(),  # type: ignore[arg-type]
        )

        await runner._handle_harness_event(
            event=AgentEvent(
                type=AgentEventType.MESSAGE,
                agent_run_id=agent_run_id,
                data=MessageDraft.of_text(
                    "Checking before tool.",
                    metadata={"is_final_answer": False, "harness_kind": "CODEX"},
                ),
            ),
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        await runner._handle_harness_event(
            event=AgentEvent(
                type=AgentEventType.MESSAGE,
                agent_run_id=agent_run_id,
                data=MessageDraft.of_tool_call(
                    tool_name="lemma_exec_command",
                    tool_call_id="call_db_check",
                    tool_args={"cmd": "printf ok"},
                    metadata={"tool_name": "lemma_exec_command"},
                ),
            ),
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        await runner._handle_harness_event(
            event=AgentEvent(
                type=AgentEventType.MESSAGE,
                agent_run_id=agent_run_id,
                data=MessageDraft.of_tool_return(
                    tool_name="lemma_exec_command",
                    tool_call_id="call_db_check",
                    tool_result={"stdout": "ok", "success": True},
                    metadata={"tool_name": "lemma_exec_command"},
                ),
            ),
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )
        await runner._handle_harness_event(
            event=AgentEvent(
                type=AgentEventType.MESSAGE,
                agent_run_id=agent_run_id,
                data=MessageDraft.of_text("Final answer."),
            ),
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )

        messages = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == 200, messages.text
        items = messages.json()["items"]
        nonfinal = next(
            item
            for item in items
            if item["kind"] == "TEXT" and item["text"] == "Checking before tool."
        )
        assert nonfinal["role"] == MessageRole.ASSISTANT.value
        assert nonfinal["metadata"]["is_final_answer"] is False
        assert any(item["kind"] == "TOOL_CALL" for item in items)
        assert any(item["kind"] == "TOOL_RETURN" for item in items)
        final = next(
            item
            for item in items
            if item["kind"] == "TEXT" and item["text"] == "Final answer."
        )
        assert final["metadata"]["is_final_answer"] is True

    async def test_ask_user_resolution_resumes_with_answers(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        """User scenario: the agent paused on ask_user; the user submits answers,
        which are recorded as the tool's synthesized return and a fresh run is
        started to resume the agent. The pending list clears and re-deciding the
        same call conflicts."""
        pod_id, conversation_id, _agent, paused_run, approval_id = (
            await _seed_paused_interaction(
                authenticated_client,
                fixed_test_org,
                tool_name="ask_user",
                tool_args={
                    "questions": [
                        {
                            "question": "Which auth method?",
                            "header": "Auth",
                            "options": [{"label": "OAuth"}, {"label": "API key"}],
                        }
                    ]
                },
            )
        )

        approvals = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/approvals"
        )
        assert approvals.status_code == 200, approvals.text
        assert [i["tool_call_id"] for i in approvals.json()["items"]] == [approval_id]

        decision = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_id}/decision",
            json={"decision": "APPROVE_ONCE", "response": {"answers": {"Auth": "OAuth"}}},
        )
        assert decision.status_code == 200, decision.text

        resume_run, tool_return = await _resume_run_and_tool_return(
            conversation_id, paused_run.id, approval_id
        )
        assert resume_run is not None
        assert resume_run.status == AgentRunStatus.RUNNING
        assert tool_return is not None
        # The synthesized return is persisted under the paused run that made the call.
        assert tool_return.agent_run_id == paused_run.id
        assert tool_return.tool_result["success"] is True
        assert tool_return.tool_result["answers"] == {"Auth": "OAuth"}

        # The conversation is RUNNING again (the resume run was started).
        conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert conversation.json()["status"] == ConversationStatus.RUNNING.value

        approvals_after = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/approvals"
        )
        assert approvals_after.json()["items"] == []

        duplicate = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_id}/decision",
            json={"decision": "DENY", "response": {}},
        )
        assert duplicate.status_code == status.HTTP_409_CONFLICT

    async def test_request_approval_denial_resumes_without_executing(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        """A denied request_approval resumes with a denial return and runs nothing."""
        pod_id, conversation_id, _agent, paused_run, approval_id = (
            await _seed_paused_interaction(
                authenticated_client,
                fixed_test_org,
                tool_name="request_approval",
                tool_args={
                    "tool_name": "exec_command",
                    "args": {"cmd": "lemma pods delete --all"},
                    "title": "Delete all pods?",
                    "reason": "Confirm destructive cleanup.",
                },
            )
        )

        decision = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_id}/decision",
            json={"decision": "DENY", "response": {"confirmed": False}},
        )
        assert decision.status_code == 200, decision.text
        ack = decision.json()
        assert ack["approval_id"] == approval_id
        assert ack["decision"] == "DENY"

        resume_run, tool_return = await _resume_run_and_tool_return(
            conversation_id, paused_run.id, approval_id
        )
        assert resume_run is not None
        assert tool_return is not None
        assert tool_return.tool_result["success"] is False
        assert tool_return.tool_result["executed"] is False
        assert tool_return.tool_result["decision"] == AgentRunApprovalDecision.DENY.value

        approvals_after = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/approvals"
        )
        assert approvals_after.json()["items"] == []

        duplicate = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_id}/decision",
            json={"decision": "APPROVE_ONCE", "response": {}},
        )
        assert duplicate.status_code == status.HTTP_409_CONFLICT

    async def test_request_approval_approval_runs_tool_as_user_on_resume(
        self,
        authenticated_client,
        fixed_test_org,
        monkeypatch,
    ):
        """An approved request_approval runs the wrapped tool as the user during
        resume and feeds its result back as the synthesized tool return."""
        captured: dict[str, object] = {}

        async def fake_execute_as_user(
            self, *, conversation, user_id, agent_run_id, tool_name, args
        ):  # noqa: ANN001 - test stub matching the service signature
            del self, conversation, user_id, agent_run_id
            captured["tool_name"] = tool_name
            captured["args"] = args
            return {"ok": True, "value": {"stdout": "deleted", "success": True}}

        monkeypatch.setattr(
            ConversationService,
            "_execute_approved_tool_as_user",
            fake_execute_as_user,
        )

        pod_id, conversation_id, _agent, paused_run, approval_id = (
            await _seed_paused_interaction(
                authenticated_client,
                fixed_test_org,
                tool_name="request_approval",
                tool_args={
                    "tool_name": "exec_command",
                    "args": {"cmd": "lemma records delete orders --id 42"},
                    "title": "Delete order 42?",
                    "reason": "Cleaning up a duplicate order.",
                },
            )
        )

        decision = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_id}/decision",
            json={"decision": "APPROVE_ONCE", "response": {}},
        )
        assert decision.status_code == 200, decision.text

        resume_run, tool_return = await _resume_run_and_tool_return(
            conversation_id, paused_run.id, approval_id
        )
        assert resume_run is not None
        assert tool_return is not None
        assert tool_return.tool_result["success"] is True
        assert tool_return.tool_result["executed"] is True
        assert tool_return.tool_result["result"] == {"stdout": "deleted", "success": True}
        assert captured["tool_name"] == "exec_command"
        assert captured["args"] == {"cmd": "lemma records delete orders --id 42"}

    async def test_multiple_pending_interactions_resume_only_after_all_resolved(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        """Two pausing tools in one turn: resolving the first must NOT resume (the
        sibling would be orphaned); only resolving the last starts one resume run
        whose history has a tool_return for both calls."""
        pod_id, conversation_id, _agent, paused_run, approval_ids = (
            await _seed_paused_interactions(
                authenticated_client,
                fixed_test_org,
                interactions=[
                    (
                        "request_approval",
                        {
                            "tool_name": "exec_command",
                            "args": {"cmd": "echo hi"},
                            "title": "Run it?",
                            "reason": "demo",
                        },
                    ),
                    (
                        "ask_user",
                        {
                            "questions": [
                                {
                                    "question": "Which auth?",
                                    "header": "Auth",
                                    "options": [{"label": "OAuth"}, {"label": "API key"}],
                                }
                            ]
                        },
                    ),
                ],
            )
        )
        approval_request, approval_ask = approval_ids

        approvals = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/approvals"
        )
        assert {i["tool_call_id"] for i in approvals.json()["items"]} == set(approval_ids)

        # Resolve the FIRST (deny) — the sibling is still pending, so NO resume run
        # and the conversation stays WAITING.
        first = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_request}/decision",
            json={"decision": "DENY", "response": {}},
        )
        assert first.status_code == 200, first.text

        resume_run, first_return = await _resume_run_and_tool_return(
            conversation_id, paused_run.id, approval_request
        )
        assert resume_run is None
        assert first_return is not None
        assert first_return.tool_result["decision"] == AgentRunApprovalDecision.DENY.value
        conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert conversation.json()["status"] == ConversationStatus.WAITING.value
        approvals_mid = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/approvals"
        )
        assert [i["tool_call_id"] for i in approvals_mid.json()["items"]] == [approval_ask]

        # Resolve the SECOND (last) — now one resume run starts with BOTH returns.
        second = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}"
            f"/approvals/{approval_ask}/decision",
            json={"decision": "APPROVE_ONCE", "response": {"answers": {"Auth": "OAuth"}}},
        )
        assert second.status_code == 200, second.text

        resume_run, ask_return = await _resume_run_and_tool_return(
            conversation_id, paused_run.id, approval_ask
        )
        assert resume_run is not None
        assert resume_run.status == AgentRunStatus.RUNNING
        assert ask_return is not None
        assert ask_return.tool_result["answers"] == {"Auth": "OAuth"}
        # Both interactions now have returns -> the resumed batch is complete.
        _, request_return = await _resume_run_and_tool_return(
            conversation_id, paused_run.id, approval_request
        )
        assert request_return is not None
        conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert conversation.json()["status"] == ConversationStatus.RUNNING.value
        approvals_done = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/approvals"
        )
        assert approvals_done.json()["items"] == []

    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_stopping_streaming_agent_run_does_not_wedge_worker(
        self,
        authenticated_client,
        fixed_test_user,
        fixed_test_org,
        db_session,
        worker,
    ):
        _ = worker
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Cancelable Agent",
                "instruction": (
                    "Answer directly in plain text. When asked for a long essay, "
                    "write one numbered line per line and continue until done."
                ),
                "agent_runtime": DEFAULT_AGENT_RUNTIME,
            },
        )
        assert create_agent.status_code == 201, create_agent.text
        agent_id = create_agent.json()["id"]

        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": "cancelable_agent",
                "title": "Cancelable stream",
                "type": "CHAT",
            },
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation_id = create_conversation.json()["id"]
        messages_url = f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        stop_url = f"/pods/{pod_id}/conversations/{conversation_id}/stop"

        stopped_run_id = await _start_real_agent_run(
            conversation_id=UUID(conversation_id),
            agent_id=UUID(agent_id),
            user_id=UUID(fixed_test_user["id"]),
            pod_id=UUID(pod_id),
            content=(
                "Write a 50 line essay on Gandhi ji. Use exactly one numbered "
                "sentence per line."
            ),
        )
        await _wait_for_streaq_job_status(
            f"agent-run:{stopped_run_id}",
            TaskStatus.RUNNING,
        )

        stopped = await authenticated_client.post(stop_url)
        assert stopped.status_code == 200, stopped.text
        await _wait_for_run_status(
            db_session,
            stopped_run_id,
            AgentRunStatus.STOPPED,
            attempts=300,
        )

        followup_events = await _post_sse(
            authenticated_client,
            messages_url,
            {"content": "Reply with exactly: worker alive"},
        )
        _assert_completed_without_error(followup_events)

    @pytest.mark.real_llm
    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_task_conversation_waits_then_completes_with_real_worker_model(
        self,
        authenticated_client,
        fixed_test_org,
        worker,
    ):
        _ = worker
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Human Input Agent",
                "instruction": (
                    "You are a task agent. Always finish by calling final_answer. "
                    "If the latest user request does not include a secret_code, "
                    "call final_answer with status WAITING and output exactly "
                    "'What is the secret_code?'. If the latest user message includes "
                    "a secret_code, call final_answer with status COMPLETED and output "
                    "exactly 'secret_code received'."
                ),
                "agent_runtime": DEFAULT_AGENT_RUNTIME,
            },
        )
        assert create_agent.status_code == 201, create_agent.text

        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": "human_input_agent",
                "title": "Human input task",
                "type": "TASK",
                "metadata": {
                    "source": "WORKFLOW_RUN",
                    "workflow_run_id": str(uuid4()),
                },
            },
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation = create_conversation.json()
        conversation_id = conversation["id"]
        assert conversation["type"] == "TASK"

        waiting_events = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {"content": "Please process this task."},
        )
        _assert_completed_without_error(waiting_events)
        assert (
            waiting_events[-1]["data"]["conversation_status"]
            == ConversationStatus.WAITING.value
        )

        waiting_conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert waiting_conversation.status_code == 200, waiting_conversation.text
        waiting_payload = waiting_conversation.json()
        assert waiting_payload["status"] == ConversationStatus.WAITING.value
        assert "secret_code" in str(waiting_payload["output"])

        listed_waiting = await authenticated_client.get(
            f"/pods/{pod_id}/conversations",
            params={
                "agent_name": "human_input_agent",
                "status": ConversationStatus.WAITING.value,
                "metadata.source": "WORKFLOW_RUN",
            },
        )
        assert listed_waiting.status_code == 200, listed_waiting.text
        assert conversation_id in [item["id"] for item in listed_waiting.json()["items"]]

        completed_events = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {"content": "The secret_code is 12345."},
        )
        _assert_completed_without_error(completed_events)
        assert (
            completed_events[-1]["data"]["conversation_status"]
            == ConversationStatus.COMPLETED.value
        )

        completed_conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert completed_conversation.status_code == 200, completed_conversation.text
        completed_payload = completed_conversation.json()
        assert completed_payload["status"] == ConversationStatus.COMPLETED.value
        assert "secret_code received" in str(completed_payload["output"])

    @pytest.mark.real_llm
    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_pod_agent_http_lifecycle_with_real_worker_model(
        self,
        authenticated_client,
        fixed_test_org,
        db_session,
        worker,
    ):
        _ = worker
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Lifecycle Agent",
                "description": "Agent with role-based runtime access",
                "icon_url": "https://example.com/agent.png",
                "instruction": (
                    "Answer briefly. For every request, produce a final answer "
                    "with ok=true and an answer string."
                ),
                "toolsets": ["WORKSPACE_CLI", "WEB_SEARCH"],
                "agent_runtime": DEFAULT_AGENT_RUNTIME,
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "ok": {"type": "boolean"},
                    },
                    "required": ["answer", "ok"],
                },
                "metadata": {"team": "ops"},
            },
        )
        assert create_agent.status_code == 201, create_agent.text
        agent = create_agent.json()
        assert agent["name"] == "lifecycle_agent"
        assert agent["toolsets"] == ["WORKSPACE_CLI", "WEB_SEARCH"]

        duplicate = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Lifecycle Agent",
                "instruction": "Duplicate should fail.",
            },
        )
        assert duplicate.status_code == 409, duplicate.text

        listed = await authenticated_client.get(f"/pods/{pod_id}/agents")
        assert listed.status_code == 200, listed.text
        assert [item["name"] for item in listed.json()["items"]] == ["lifecycle_agent"]

        fetched = await authenticated_client.get(
            f"/pods/{pod_id}/agents/lifecycle_agent"
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["metadata"] == {"team": "ops"}

        updated = await authenticated_client.patch(
            f"/pods/{pod_id}/agents/lifecycle_agent",
            json={
                "description": "Updated lifecycle agent",
                "toolsets": [],
                "metadata": {"team": "platform"},
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["description"] == "Updated lifecycle agent"
        assert updated.json()["toolsets"] == []

        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": "lifecycle_agent",
                "title": "Root task",
                "instructions": "Use lifecycle UI context when present.",
            },
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation = create_conversation.json()
        conversation_id = conversation["id"]
        assert conversation["pod_id"] == pod_id
        assert conversation["agent_id"] == agent["id"]
        assert conversation["instructions"] == "Use lifecycle UI context when present."

        create_child = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": "lifecycle_agent",
                "title": "Child branch",
                "parent_id": conversation_id,
            },
        )
        assert create_child.status_code == 201, create_child.text
        assert create_child.json()["parent_id"] == conversation_id

        conversations = await authenticated_client.get(
            f"/pods/{pod_id}/conversations",
            params={"agent_name": "lifecycle_agent"},
        )
        assert conversations.status_code == 200, conversations.text
        root_ids = [item["id"] for item in conversations.json()["items"]]
        assert conversation_id in root_ids
        assert create_child.json()["id"] not in root_ids

        get_conversation = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert get_conversation.status_code == 200, get_conversation.text

        update_conversation = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={
                "title": "Updated root task",
                "instructions": "Prefer the updated lifecycle screen state.",
                "agent_runtime": DEFAULT_AGENT_RUNTIME,
            },
        )
        assert update_conversation.status_code == 200, update_conversation.text
        assert update_conversation.json()["title"] == "Updated root task"
        assert (
            update_conversation.json()["instructions"]
            == "Prefer the updated lifecycle screen state."
        )
        assert update_conversation.json()["agent_runtime"] == DEFAULT_AGENT_RUNTIME

        events = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {
                "content": "Reply with ok true and mention lifecycle.",
                "metadata": {
                    "state": {
                        "screen": "agent_lifecycle",
                        "selected_agent": "lifecycle_agent",
                    }
                },
            },
        )
        _assert_completed_without_error(events)

        messages = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == 200, messages.text
        message_items = messages.json()["items"]
        assert [item["sequence"] for item in message_items] == sorted(
            [item["sequence"] for item in message_items]
        )
        first_user_message = next(
            item for item in message_items if item["role"] == "user"
        )
        assert first_user_message["text"] == (
            "Reply with ok true and mention lifecycle."
        )
        assert first_user_message["metadata"]["state"]["screen"] == "agent_lifecycle"
        assert any(item["role"] == "assistant" for item in message_items)
        final_messages = [
            item for item in message_items if item["metadata"].get("is_final_answer")
        ]
        assert final_messages
        assert final_messages[-1]["metadata"].get("structured_output")

        after_first = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            params={"after_sequence": 0},
        )
        assert after_first.status_code == 200, after_first.text
        assert all(item["sequence"] > 0 for item in after_first.json()["items"])

        idle_stream = await _get_sse_until_closed(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/stream",
        )
        assert idle_stream == []

        channel_service = await get_channel_service()
        replay_run_id = await _create_active_run(
            conversation_id=UUID(conversation_id),
            agent_id=UUID(agent["id"]),
        )

        async def publish_replay() -> None:
            await channel_service.publish(
                conversation_channel(UUID(conversation_id)),
                {
                    "type": "completed",
                    "agent_run_id": str(replay_run_id),
                    "data": {
                        "conversation_id": conversation_id,
                        "status": "completed",
                    },
                },
            )
            await _finish_agent_run(replay_run_id, AgentRunStatus.COMPLETED)

        streamed = await _get_sse_after_publish(
            authenticated_client,
            f"/pods/{pod_id}/conversations/"
            f"{conversation_id}/stream?agent_run_id={replay_run_id}",
            publish_replay,
        )
        assert streamed[0]["type"] == "completed"

        active_run_id = await _create_active_run(
            conversation_id=UUID(conversation_id),
            agent_id=UUID(agent["id"]),
        )
        stopped = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/stop"
        )
        assert stopped.status_code == 200, stopped.text
        await _wait_for_run_status(db_session, active_run_id, AgentRunStatus.STOPPED)

        deleted = await authenticated_client.delete(
            f"/pods/{pod_id}/agents/lifecycle_agent"
        )
        assert deleted.status_code == 200, deleted.text
        missing = await authenticated_client.get(
            f"/pods/{pod_id}/agents/lifecycle_agent"
        )
        assert missing.status_code == 404, missing.text


class TestAgentRoleVisibility:
    async def test_agent_list_and_access_respects_pod_roles(
        self,
        authenticated_client,
        async_client,
        fixed_test_org,
    ):
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="agent-visibility",
            custom_role="AGENT_REVIEWERS",
        )
        pod_id = ctx["pod_id"]
        default_name = f"default_agent_{uuid4().hex[:8]}"
        editor_name = f"editor_agent_{uuid4().hex[:8]}"
        custom_name = f"custom_agent_{uuid4().hex[:8]}"

        agents: dict[str, dict] = {}
        for name, visibility in [
            (default_name, None),
            (editor_name, "RESTRICTED"),
            (custom_name, "RESTRICTED"),
        ]:
            payload = {"name": name, "instruction": "Help with this pod."}
            if visibility is not None:
                payload["visibility"] = visibility
            response = await authenticated_client.post(
                f"/pods/{pod_id}/agents",
                json=payload,
            )
            assert response.status_code == status.HTTP_201_CREATED, response.text
            agents[name] = response.json()

        editor_grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/POD_EDITOR/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "agent",
                        "resource_name": agents[editor_name]["name"],
                        "permission_ids": ["agent.read", "agent.update"],
                    }
                ]
            },
        )
        assert editor_grant.status_code == status.HTTP_200_OK, editor_grant.text
        custom_grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "agent",
                        "resource_name": agents[custom_name]["name"],
                        "permission_ids": ["agent.read"],
                    }
                ]
            },
        )
        assert custom_grant.status_code == status.HTTP_200_OK, custom_grant.text

        viewer_list = await async_client.get(
            f"/pods/{pod_id}/agents",
            headers=ctx["viewer_headers"],
        )
        assert viewer_list.status_code == status.HTTP_200_OK, viewer_list.text
        assert item_names(viewer_list.json()) == {default_name}

        editor_list = await async_client.get(
            f"/pods/{pod_id}/agents",
            headers=ctx["editor_headers"],
        )
        assert editor_list.status_code == status.HTTP_200_OK, editor_list.text
        assert item_names(editor_list.json()) == {default_name, editor_name}
        editor_items = {item["name"]: item for item in editor_list.json()["items"]}
        assert set(editor_items[default_name]["allowed_actions"]) == {
            "agent.read",
            "agent.execute",
            "agent.update",
        }
        assert set(editor_items[editor_name]["allowed_actions"]) == {
            "agent.read",
            "agent.update",
        }
        editor_get_default = await async_client.get(
            f"/pods/{pod_id}/agents/{default_name}",
            headers=ctx["editor_headers"],
        )
        assert editor_get_default.status_code == status.HTTP_200_OK, (
            editor_get_default.text
        )
        assert set(editor_get_default.json()["allowed_actions"]) == {
            "agent.read",
            "agent.execute",
            "agent.update",
        }
        editor_get_restricted = await async_client.get(
            f"/pods/{pod_id}/agents/{editor_name}",
            headers=ctx["editor_headers"],
        )
        assert editor_get_restricted.status_code == status.HTTP_200_OK, (
            editor_get_restricted.text
        )
        assert set(editor_get_restricted.json()["allowed_actions"]) == {
            "agent.read",
            "agent.update",
        }

        custom_list = await async_client.get(
            f"/pods/{pod_id}/agents",
            headers=ctx["custom_headers"],
        )
        assert custom_list.status_code == status.HTTP_200_OK, custom_list.text
        assert item_names(custom_list.json()) == {default_name, custom_name}
        custom_items = {item["name"]: item for item in custom_list.json()["items"]}
        assert set(custom_items[default_name]["allowed_actions"]) == {"agent.read"}
        assert set(custom_items[custom_name]["allowed_actions"]) == {"agent.read"}
        custom_get_restricted = await async_client.get(
            f"/pods/{pod_id}/agents/{custom_name}",
            headers=ctx["custom_headers"],
        )
        assert custom_get_restricted.status_code == status.HTTP_200_OK, (
            custom_get_restricted.text
        )
        assert set(custom_get_restricted.json()["allowed_actions"]) == {"agent.read"}

        viewer_get_restricted = await async_client.get(
            f"/pods/{pod_id}/agents/{editor_name}",
            headers=ctx["viewer_headers"],
        )
        assert viewer_get_restricted.status_code == status.HTTP_403_FORBIDDEN

        viewer_edit_default = await async_client.patch(
            f"/pods/{pod_id}/agents/{default_name}",
            json={"description": "viewer edit"},
            headers=ctx["viewer_headers"],
        )
        assert viewer_edit_default.status_code == status.HTTP_403_FORBIDDEN

        custom_edit_custom = await async_client.patch(
            f"/pods/{pod_id}/agents/{custom_name}",
            json={"description": "custom viewer edit"},
            headers=ctx["custom_headers"],
        )
        assert custom_edit_custom.status_code == status.HTTP_403_FORBIDDEN

        editor_edit_restricted = await async_client.patch(
            f"/pods/{pod_id}/agents/{editor_name}",
            json={"description": "editor edit"},
            headers=ctx["editor_headers"],
        )
        assert editor_edit_restricted.status_code == status.HTTP_200_OK
        assert set(editor_edit_restricted.json()["allowed_actions"]) == {
            "agent.read",
            "agent.update",
        }


class TestPodAssistantLifecycle:
    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_pod_assistant_http_lifecycle_with_real_worker_model(
        self,
        authenticated_client,
        fixed_test_org,
        db_session,
        worker,
    ):
        _ = worker
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={"title": "Pod setup help", "agent_runtime": DEFAULT_AGENT_RUNTIME},
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation = create_conversation.json()
        conversation_id = conversation["id"]
        assert conversation["pod_id"] == pod_id
        assert conversation["agent_id"] is None

        listed = await authenticated_client.get(f"/pods/{pod_id}/conversations")
        assert listed.status_code == 200, listed.text
        assert conversation_id in [item["id"] for item in listed.json()["items"]]

        fetched = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert fetched.status_code == 200, fetched.text

        updated = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation_id}",
            json={"title": "Updated pod setup", "agent_runtime": DEFAULT_AGENT_RUNTIME},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["title"] == "Updated pod setup"
        assert updated.json()["agent_runtime"] == DEFAULT_AGENT_RUNTIME

        events = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {"content": "In one sentence, say this pod assistant e2e works."},
        )
        _assert_completed_without_error(events)

        messages = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == 200, messages.text
        roles = [item["role"] for item in messages.json()["items"]]
        assert "user" in roles
        assert "assistant" in roles
        assert [item["sequence"] for item in messages.json()["items"]] == sorted(
            [item["sequence"] for item in messages.json()["items"]]
        )
        for item in messages.json()["items"]:
            assert "author_user_id" not in (item["metadata"] or {})
            assert "agent_run_id" not in (item["metadata"] or {})

        idle_stream = await _get_sse_until_closed(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/stream",
        )
        assert idle_stream == []

        channel_service = await get_channel_service()
        replay_run_id = await _create_active_run(
            conversation_id=UUID(conversation_id),
            agent_id=None,
        )

        async def publish_replay() -> None:
            await channel_service.publish(
                conversation_channel(UUID(conversation_id)),
                {
                    "type": "completed",
                    "agent_run_id": str(replay_run_id),
                    "data": {
                        "conversation_id": conversation_id,
                        "status": "completed",
                    },
                },
            )
            await _finish_agent_run(replay_run_id, AgentRunStatus.COMPLETED)

        streamed = await _get_sse_after_publish(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/stream?"
            f"agent_run_id={replay_run_id}",
            publish_replay,
        )
        assert streamed[0]["type"] == "completed"

        active_run_id = await _create_active_run(
            conversation_id=UUID(conversation_id),
            agent_id=None,
        )
        stopped = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/stop"
        )
        assert stopped.status_code == 200, stopped.text
        await _wait_for_run_status(db_session, active_run_id, AgentRunStatus.STOPPED)


class TestConversationMessagePagination:
    async def test_messages_paginate_latest_window_chronologically_with_older_page_token(
        self,
        authenticated_client,
        fixed_test_org,
    ):
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={"title": "Pagination chat", "agent_runtime": DEFAULT_AGENT_RUNTIME},
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation_id = create_conversation.json()["id"]

        await _append_messages(
            conversation_id=UUID(conversation_id),
            messages=[
                ("user", "message 0"),
                ("assistant", "message 1"),
                ("user", "message 2"),
                ("assistant", "message 3"),
                ("user", "message 4"),
            ],
        )

        first_page = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            params={"limit": 2},
        )
        assert first_page.status_code == 200, first_page.text
        first_payload = first_page.json()
        assert [item["sequence"] for item in first_payload["items"]] == [3, 4]
        assert [item["text"] for item in first_payload["items"]] == [
            "message 3",
            "message 4",
        ]
        assert first_payload["next_page_token"] == "3"

        second_page = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            params={"limit": 2, "page_token": first_payload["next_page_token"]},
        )
        assert second_page.status_code == 200, second_page.text
        second_payload = second_page.json()
        assert [item["sequence"] for item in second_payload["items"]] == [1, 2]
        assert [item["text"] for item in second_payload["items"]] == [
            "message 1",
            "message 2",
        ]
        assert second_payload["next_page_token"] == "1"

        final_page = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            params={"limit": 2, "page_token": second_payload["next_page_token"]},
        )
        assert final_page.status_code == 200, final_page.text
        final_payload = final_page.json()
        assert [item["sequence"] for item in final_payload["items"]] == [0]
        assert final_payload["next_page_token"] is None

        invalid_page = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            params={"page_token": "not-a-sequence"},
        )
        assert invalid_page.status_code == 400, invalid_page.text


class TestAgentRuntimeConfigApis:
    async def test_runtime_discovery_and_null_agent_defaults(
        self,
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        db_session,
        monkeypatch,
    ):
        monkeypatch.setenv("LEMMA_OPENAI_API_KEY", "system-lemma-secret")
        # The system model profile has no built-in model default; the operator
        # must configure the catalog via env when the key is set.
        monkeypatch.setenv("LEMMA_OPENAI_MODEL_NAMES", "gpt-4o,gpt-4o-mini")
        monkeypatch.setenv("LEMMA_OPENAI_DEFAULT_MODEL", "gpt-4o")
        monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
        harnesses = await authenticated_client.get("/agent-runtime/harnesses")
        assert harnesses.status_code == 200, harnesses.text
        harness_payload = harnesses.json()
        assert harness_payload == {"items": []}
        assert "default_editable" not in harness_payload

        offline_daemon = AgentRuntimeDaemonModel(
            user_id=UUID(fixed_test_user["id"]),
            device_key=f"offline-device-{uuid4().hex[:8]}",
            display_name="Offline laptop",
            status="OFFLINE",
            device_info={"platform": "test"},
            harness_catalog={
                "CODEX": {
                    "available": True,
                    "display_name": "Offline Codex",
                    "models": ["gpt-5.5"],
                }
            },
        )
        db_session.add(offline_daemon)
        await db_session.commit()

        harnesses_with_offline_daemon = await authenticated_client.get(
            "/agent-runtime/harnesses"
        )
        assert harnesses_with_offline_daemon.status_code == 200
        assert harnesses_with_offline_daemon.json() == {"items": []}

        missing_tool_daemon = AgentRuntimeDaemonModel(
            user_id=UUID(fixed_test_user["id"]),
            device_key=f"missing-tool-device-{uuid4().hex[:8]}",
            display_name="Missing tool laptop",
            status="ONLINE",
            device_info={"platform": "test"},
            harness_catalog={
                "CLAUDE_CODE": {
                    "available": False,
                    "display_name": "Claude Code",
                    "models": [],
                }
            },
        )
        db_session.add(missing_tool_daemon)
        await db_session.flush()
        missing_tool_daemon_id = str(missing_tool_daemon.id)
        await db_session.commit()

        harnesses_with_missing_tool = await authenticated_client.get(
            "/agent-runtime/harnesses"
        )
        assert harnesses_with_missing_tool.status_code == 200
        assert harnesses_with_missing_tool.json() == {
            "items": [
                {
                    "harness_kind": "CLAUDE_CODE",
                    "display_name": "Claude Code",
                    "models": [],
                    "model_catalog": [],
                    "available": False,
                    "availability_status": "NOT_INSTALLED",
                    "daemon_id": missing_tool_daemon_id,
                    "daemon_display_name": "Missing tool laptop",
                    "daemon_status": "ONLINE",
                }
            ]
        }

        org_profile = AgentRuntimeProfileModel(
            organization_id=UUID(fixed_test_org["id"]),
            scope="ORGANIZATION",
            kind="MODEL_PROVIDER",
            protocol="OPENAI_COMPATIBLE",
            name=f"Org Runtime {uuid4().hex[:8]}",
            default_model_name="org-model",
            model_catalog=[
                {
                    "name": "org-model",
                    "display_name": "Org Model",
                    "provider_model_name": "provider/org-model",
                    "capabilities": ["TEXT", "TOOLS"],
                    "default_model_settings": {},
                    "metadata": {},
                }
            ],
            config={"base_url": "https://org-provider.test/v1"},
            credentials={"api_key": "org-secret"},
            status="ACTIVE",
            profile_metadata={"source": "e2e"},
        )
        disabled_profile = AgentRuntimeProfileModel(
            organization_id=UUID(fixed_test_org["id"]),
            scope="ORGANIZATION",
            kind="HARNESS",
            protocol="CODEX_APP_SERVER",
            name=f"Disabled Runtime {uuid4().hex[:8]}",
            default_model_name="default",
            model_catalog=[
                {
                    "name": "default",
                    "display_name": "default",
                    "provider_model_name": "default",
                    "capabilities": ["TEXT", "TOOLS"],
                    "default_model_settings": {},
                    "metadata": {},
                }
            ],
            config={"binary": "codex"},
            status="DISABLED",
            profile_metadata={"source": "e2e"},
        )
        db_session.add_all([org_profile, disabled_profile])
        await db_session.flush()
        org_profile_id = str(org_profile.id)
        disabled_profile_id = str(disabled_profile.id)
        await db_session.commit()

        profiles = await authenticated_client.get(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        )
        assert profiles.status_code == 200, profiles.text
        profile_payload = profiles.json()
        assert profile_payload["default_runtime"] == DEFAULT_AGENT_RUNTIME
        profile_ids = {item["id"] for item in profile_payload["items"]}
        assert org_profile_id in profile_ids
        assert disabled_profile_id not in profile_ids
        org_response = next(
            item for item in profile_payload["items"] if item["id"] == org_profile_id
        )
        assert org_response["has_credentials"] is True
        assert "credentials" not in org_response
        system_profile = next(
            item
            for item in profile_payload["items"]
            if item["id"] == "system:lemma"
        )
        assert system_profile["scope"] == "SYSTEM"
        assert system_profile["kind"] == "MODEL_PROVIDER"
        assert system_profile["protocol"] == "OPENAI_COMPATIBLE"
        assert system_profile["name"] == "Lemma"
        assert system_profile["default_model_name"] == system_lemma_default_model()
        assert [item["name"] for item in system_profile["model_catalog"]] == system_lemma_model_names()
        assert system_profile["derived_harness_kind"] == "LEMMA"

        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
        patched_pod = await authenticated_client.put(
            f"/pods/{pod_id}",
            json={"config": {"default_profile_id": org_profile_id}},
        )
        assert patched_pod.status_code == 200, patched_pod.text
        # PodConfig carries join_policy (default INVITE_ONLY) alongside the
        # default_profile_id, so the serialized config includes both.
        assert patched_pod.json()["config"] == {
            "default_profile_id": org_profile_id,
            "join_policy": "INVITE_ONLY",
        }

        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={"name": "Runtime Default Agent", "instruction": "Use defaults."},
        )
        assert create_agent.status_code == 201, create_agent.text
        agent = create_agent.json()
        assert agent["agent_runtime"] is None

        patched_agent = await authenticated_client.patch(
            f"/pods/{pod_id}/agents/runtime_default_agent",
            json={
                "agent_runtime": {
                    "profile_id": "system:lemma",
                    "model_name": "deepseek-v4-pro",
                }
            },
        )
        assert patched_agent.status_code == 200, patched_agent.text
        assert patched_agent.json()["agent_runtime"] == {
            "profile_id": "system:lemma",
            "model_name": "deepseek-v4-pro",
        }

        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={"agent_name": "runtime_default_agent", "title": "Runtime defaults"},
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation = create_conversation.json()
        assert conversation["agent_runtime"] is None

        patched_conversation = await authenticated_client.patch(
            f"/pods/{pod_id}/conversations/{conversation['id']}",
            json={
                "agent_runtime": {
                    "profile_id": "system:lemma",
                    "model_name": "deepseek-v4-flash",
                }
            },
        )
        assert patched_conversation.status_code == 200, patched_conversation.text
        assert patched_conversation.json()["agent_runtime"] == {
            "profile_id": "system:lemma",
            "model_name": "deepseek-v4-flash",
        }

    async def test_create_user_daemon_profiles_and_resolve_them(
        self,
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        db_session,
    ):
        daemon = AgentRuntimeDaemonModel(
            user_id=UUID(fixed_test_user["id"]),
            device_key=f"test-device-{uuid4().hex[:8]}",
            display_name="Test laptop",
            status="ONLINE",
            device_info={"platform": "test"},
            harness_catalog={
                "CODEX": {
                    "available": True,
                    "display_name": "Codex",
                    "models": ["gpt-5.5"],
                },
                "CLAUDE_CODE": {
                    "available": True,
                    "display_name": "Claude Code",
                    "models": ["sonnet"],
                },
                "OPENCODE": {
                    "available": True,
                    "display_name": "OpenCode",
                    "models": ["opencode/deepseek-v4-flash-free"],
                },
            },
        )
        db_session.add(daemon)
        await db_session.flush()
        daemon_id = str(daemon.id)
        await db_session.commit()

        harnesses = await authenticated_client.get("/agent-runtime/harnesses")
        assert harnesses.status_code == 200, harnesses.text
        daemon_items = [
            item
            for item in harnesses.json()["items"]
            if item.get("daemon_id") == daemon_id
        ]
        assert {(item["harness_kind"], tuple(item["models"])) for item in daemon_items} == {
            ("CODEX", ("gpt-5.5",)),
            ("CLAUDE_CODE", ("sonnet",)),
            ("OPENCODE", ("opencode/deepseek-v4-flash-free",)),
        }

        requested_profiles = [
            (HarnessKind.CODEX, "Codex daemon", "gpt-5.5"),
            (HarnessKind.CLAUDE_CODE, "Claude Code daemon", "sonnet"),
            (
                HarnessKind.OPENCODE,
                "OpenCode daemon",
                "opencode/deepseek-v4-flash-free",
            ),
        ]
        created_profiles = []
        for harness_kind, name, model_name in requested_profiles:
            response = await authenticated_client.post(
                f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
                json={
                    "source": "USER_DAEMON",
                    "daemon_id": daemon_id,
                    "harness_kind": harness_kind.value,
                    "name": f"{name} {uuid4().hex[:8]}",
                    "default_model_name": model_name,
                },
            )
            assert response.status_code == 201, response.text
            payload = response.json()
            assert payload["scope"] == "ORGANIZATION"
            assert payload["kind"] == "HARNESS"
            assert payload["user_id"] == fixed_test_user["id"]
            assert payload["daemon_id"] == daemon_id
            assert payload["derived_harness_kind"] == harness_kind.value
            assert payload["default_model_name"] == model_name
            assert payload["metadata"] == {"source": "USER_DAEMON"}
            assert {item["name"] for item in payload["model_catalog"]} == {
                "default",
                model_name,
            }
            assert payload["config"] == {}
            created_profiles.append((payload["id"], harness_kind, model_name))

        profiles = await authenticated_client.get(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        )
        assert profiles.status_code == 200, profiles.text
        listed_profile_ids = {item["id"] for item in profiles.json()["items"]}
        assert {profile_id for profile_id, _, _ in created_profiles}.issubset(
            listed_profile_ids
        )

        runner = AgentRunnerService(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            harness_registry=object(),  # type: ignore[arg-type]
        )
        for profile_id, harness_kind, model_name in created_profiles:
            resolved = await runner._resolve_agent_runtime(
                AgentRuntimeConfig(profile_id=profile_id),
                user_id=UUID(fixed_test_user["id"]),
                organization_id=UUID(fixed_test_org["id"]),
            )
            assert resolved.profile.id == profile_id
            assert resolved.harness_kind is harness_kind
            assert resolved.model_name_for_harness == model_name

        missing_model = await authenticated_client.post(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
            json={
                "source": "USER_DAEMON",
                "daemon_id": daemon_id,
                "harness_kind": "OPENCODE",
                "name": f"Bad OpenCode {uuid4().hex[:8]}",
                "default_model_name": "opencode/missing",
            },
        )
        assert missing_model.status_code == 400
        assert "detected model names" in missing_model.json()["message"]

    async def test_daemon_websocket_materializes_catalog_and_profile_flow(
        self,
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        db_session,
        test_app,
    ):
        device_key = f"real-ws-device-{uuid4().hex[:8]}"
        communicator = ConnectorCommunicator(
            test_app,
            {
                "type": "websocket",
                "path": "/me/agent-runtime/daemon/ws",
                "raw_path": b"/me/agent-runtime/daemon/ws",
                "query_string": b"",
                "headers": [
                    (
                        b"authorization",
                        f"Bearer {fixed_test_user['token']}".encode(),
                    ),
                    (b"host", b"testserver"),
                ],
                "scheme": "ws",
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
        )

        await communicator.send_input({"type": "websocket.connect"})
        accepted = await communicator.receive_output(timeout=5)
        assert accepted["type"] == "websocket.accept"

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "daemon.ready",
                        "payload": {
                            "device_key": device_key,
                            "display_name": "Real E2E laptop",
                            "device_info": {
                                "platform": "test",
                                "lemma_cli_version": "e2e",
                            },
                            "harness_catalog": {
                                "CODEX": {
                                    "available": True,
                                    "display_name": "Codex E2E",
                                    "models": ["gpt-5.5"],
                                }
                            },
                        },
                    }
                ),
            }
        )
        ready_ack = await communicator.receive_output(timeout=5)
        assert ready_ack["type"] == "websocket.send"
        ready_payload = json.loads(ready_ack["text"])
        assert ready_payload["type"] == "daemon.ready_ack"
        daemon_id = ready_payload["daemon_id"]

        db_session.expire_all()
        daemon = await db_session.scalar(
            select(AgentRuntimeDaemonModel).where(
                AgentRuntimeDaemonModel.user_id == UUID(fixed_test_user["id"]),
                AgentRuntimeDaemonModel.device_key == device_key,
            )
        )
        assert daemon is not None
        assert str(daemon.id) == daemon_id
        assert daemon.status == "ONLINE"

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "daemon.catalog",
                        "payload": {
                            "CODEX": {
                                "available": True,
                                "display_name": "Codex E2E",
                                "models": ["gpt-5.5", "gpt-5.5-mini"],
                            }
                        },
                    }
                ),
            }
        )
        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "daemon.ping"}),
            }
        )
        pong = await communicator.receive_output(timeout=5)
        assert pong["type"] == "websocket.send"
        assert json.loads(pong["text"]) == {"type": "daemon.pong"}

        harnesses = await authenticated_client.get("/agent-runtime/harnesses")
        assert harnesses.status_code == 200, harnesses.text
        daemon_items = [
            item
            for item in harnesses.json()["items"]
            if item.get("daemon_id") == daemon_id
        ]
        assert len(daemon_items) == 1
        daemon_item = daemon_items[0]
        # model_catalog is derived from the flat models list (structured entry
        # serialization); assert it by model name and check the rest exactly.
        model_catalog = daemon_item.pop("model_catalog")
        assert [entry["name"] for entry in model_catalog] == ["gpt-5.5", "gpt-5.5-mini"]
        assert daemon_item == {
            "harness_kind": "CODEX",
            "display_name": "Codex E2E",
            "models": ["gpt-5.5", "gpt-5.5-mini"],
            "available": True,
            "availability_status": "READY",
            "daemon_id": daemon_id,
            "daemon_display_name": "Real E2E laptop",
            "daemon_status": "ONLINE",
        }

        created = await authenticated_client.post(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
            json={
                "source": "USER_DAEMON",
                "daemon_id": daemon_id,
                "harness_kind": "CODEX",
                "name": f"Real WS Codex {uuid4().hex[:8]}",
                "default_model_name": "gpt-5.5-mini",
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()
        assert profile["user_id"] == fixed_test_user["id"]
        assert profile["daemon_id"] == daemon_id
        assert profile["metadata"] == {"source": "USER_DAEMON"}
        assert profile["config"] == {}

        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait(timeout=5)
        db_session.expire_all()
        offline_daemon = await db_session.get(
            AgentRuntimeDaemonModel,
            UUID(daemon_id),
        )
        assert offline_daemon is not None
        assert offline_daemon.status == "OFFLINE"

    async def test_daemon_websocket_rejects_invalid_auth_without_server_error(
        self,
        test_app,
    ):
        communicator = ConnectorCommunicator(
            test_app,
            {
                "type": "websocket",
                "path": "/me/agent-runtime/daemon/ws",
                "raw_path": b"/me/agent-runtime/daemon/ws",
                "query_string": b"",
                "headers": [
                    (b"authorization", b"Bearer invalid-token"),
                    (b"host", b"testserver"),
                ],
                "scheme": "ws",
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
        )

        await communicator.send_input({"type": "websocket.connect"})
        rejected = await communicator.receive_output(timeout=5)
        assert rejected["type"] == "websocket.close"
        assert rejected["code"] == status.WS_1008_POLICY_VIOLATION

    @pytest.mark.parametrize(
        ("harness_kind", "model_name"),
        [
            ("CODEX", "gpt-5.5"),
            ("CLAUDE_CODE", "sonnet"),
            ("OPENCODE", "opencode/deepseek-v4-flash-free"),
        ],
    )
    async def test_user_daemon_run_crosses_worker_and_websocket_via_redis(
        self,
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        test_app,
        worker,
        harness_kind,
        model_name,
    ):
        _ = worker
        device_key = f"run-ws-device-{uuid4().hex[:8]}"
        communicator = ConnectorCommunicator(
            test_app,
            {
                "type": "websocket",
                "path": "/me/agent-runtime/daemon/ws",
                "raw_path": b"/me/agent-runtime/daemon/ws",
                "query_string": b"",
                "headers": [
                    (
                        b"authorization",
                        f"Bearer {fixed_test_user['token']}".encode(),
                    ),
                    (b"host", b"testserver"),
                ],
                "scheme": "ws",
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        accepted = await communicator.receive_output(timeout=5)
        assert accepted["type"] == "websocket.accept"
        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "daemon.ready",
                        "payload": {
                            "device_key": device_key,
                            "display_name": "Worker bridge laptop",
                            "device_info": {"platform": "test"},
                            "harness_catalog": {
                                harness_kind: {
                                    "available": True,
                                    "display_name": f"{harness_kind} bridge",
                                    "models": [model_name],
                                }
                            },
                        },
                    }
                ),
            }
        )
        ready_ack = await _receive_daemon_ws_message(
            communicator,
            message_type="daemon.ready_ack",
        )
        daemon_id = ready_ack["daemon_id"]

        created_profile = await authenticated_client.post(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
            json={
                "source": "USER_DAEMON",
                "daemon_id": daemon_id,
                "harness_kind": harness_kind,
                "name": f"Worker Bridge {harness_kind} {uuid4().hex[:8]}",
                "default_model_name": model_name,
            },
        )
        assert created_profile.status_code == 201, created_profile.text
        profile_id = created_profile.json()["id"]

        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
        create_agent = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "Daemon Worker Agent",
                "instruction": "Reply through the daemon runtime.",
                "agent_runtime": {
                    "profile_id": profile_id,
                    "model_name": model_name,
                },
            },
        )
        assert create_agent.status_code == 201, create_agent.text
        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={
                "agent_name": "daemon_worker_agent",
                "title": "Daemon worker bridge",
            },
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation_id = create_conversation.json()["id"]

        post_task = asyncio.create_task(
            _post_sse(
                authenticated_client,
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                {"content": "Say hello from the daemon."},
            )
        )
        run_start = await _receive_daemon_ws_message(
            communicator,
            message_type="run.start",
            timeout=60,
        )
        agent_run_id = run_start["agent_run_id"]
        assert run_start["payload"]["runtime"] == {
            "profile_id": profile_id,
            "harness_kind": harness_kind,
            "model_name": model_name,
        }
        assert (
            run_start["payload"]["prompt"]["user_prompt"]
            == "USER:\nSay hello from the daemon."
        )
        assert "Reply through the daemon runtime." in run_start["payload"]["prompt"]["system_prompt"]
        assert "session_id" not in run_start["payload"]["prompt"]
        assert "text" not in run_start["payload"]["prompt"]
        assert "messages" not in run_start["payload"]
        first_local_session_id = f"local-session-{harness_kind}"

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": agent_run_id,
                        "event": {
                            "type": "status",
                            "data": {
                                "status": "daemon.session.started",
                                "local_session": {
                                    "harness_kind": harness_kind,
                                    "session_id": first_local_session_id,
                                },
                            },
                        },
                    }
                ),
            }
        )

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": agent_run_id,
                        "event": {
                            "type": "message",
                            "data": {
                                "role": "assistant",
                                "kind": "text",
                                "text": f"hello from redis daemon {harness_kind}",
                            },
                        },
                    }
                ),
            }
        )
        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": agent_run_id,
                        "event": {"type": "completed", "data": {}},
                    }
                ),
            }
        )
        events = await post_task
        _assert_completed_without_error(events)

        messages = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == 200, messages.text
        assistant_messages = [
            item
            for item in messages.json()["items"]
            if item["role"] == MessageRole.ASSISTANT.value
        ]
        assert any(
            item["text"] == f"hello from redis daemon {harness_kind}"
            for item in assistant_messages
        )

        # The worker persists the daemon session id while handling the
        # daemon.session.started event; wait for it to land before the
        # resumption run reads it cross-process.
        await _wait_for_daemon_session(
            authenticated_client,
            pod_id=pod_id,
            conversation_id=conversation_id,
            session_id=first_local_session_id,
        )

        followup_task = asyncio.create_task(
            _post_sse(
                authenticated_client,
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                {"content": "What did I ask first?"},
            )
        )
        followup_start = await _receive_daemon_ws_message(
            communicator,
            message_type="run.start",
            timeout=60,
        )
        followup_run_id = followup_start["agent_run_id"]
        assert followup_start["payload"]["conversation_id"] == conversation_id
        assert (
            followup_start["payload"]["mcp"]["conversation_id"]
            == conversation_id
        )
        assert (
            followup_start["payload"]["prompt"]["user_prompt"]
            == "USER:\nWhat did I ask first?"
        )
        assert followup_start["payload"]["prompt"]["session_id"] == first_local_session_id
        assert "system_prompt" not in followup_start["payload"]["prompt"]
        assert (
            "Reply through the daemon runtime."
            in followup_start["payload"]["prompt"]["recovery_system_prompt"]
        )
        assert "Say hello from the daemon." not in followup_start["payload"]["prompt"]["user_prompt"]
        assert (
            f"hello from redis daemon {harness_kind}"
            not in followup_start["payload"]["prompt"]["user_prompt"]
        )
        assert "text" not in followup_start["payload"]["prompt"]
        assert "messages" not in followup_start["payload"]
        replacement_local_session_id = f"replacement-local-session-{harness_kind}"

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": followup_run_id,
                        "event": {
                            "type": "status",
                            "data": {
                                "status": "daemon.session.invalid",
                                "local_session": {
                                    "harness_kind": harness_kind,
                                    "session_id": first_local_session_id,
                                },
                            },
                        },
                    }
                ),
            }
        )
        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": followup_run_id,
                        "event": {
                            "type": "status",
                            "data": {
                                "status": "daemon.session.started",
                                "local_session": {
                                    "harness_kind": harness_kind,
                                    "session_id": replacement_local_session_id,
                                },
                            },
                        },
                    }
                ),
            }
        )

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": followup_run_id,
                        "event": {
                            "type": "message",
                            "data": {
                                "role": "assistant",
                                "kind": "text",
                                "text": "you asked me to say hello",
                            },
                        },
                    }
                ),
            }
        )
        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": followup_run_id,
                        "event": {"type": "completed", "data": {}},
                    }
                ),
            }
        )
        followup_events = await followup_task
        _assert_completed_without_error(followup_events)

        repaired_task = asyncio.create_task(
            _post_sse(
                authenticated_client,
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                {"content": "Continue after repair."},
            )
        )
        repaired_start = await _receive_daemon_ws_message(
            communicator,
            message_type="run.start",
            timeout=60,
        )
        repaired_run_id = repaired_start["agent_run_id"]
        assert (
            repaired_start["payload"]["prompt"]["session_id"]
            == replacement_local_session_id
        )
        assert "system_prompt" not in repaired_start["payload"]["prompt"]
        assert (
            "Reply through the daemon runtime."
            in repaired_start["payload"]["prompt"]["recovery_system_prompt"]
        )

        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": repaired_run_id,
                        "event": {
                            "type": "message",
                            "data": {
                                "role": "assistant",
                                "kind": "text",
                                "text": "continued after repair",
                            },
                        },
                    }
                ),
            }
        )
        await communicator.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "run.event",
                        "agent_run_id": repaired_run_id,
                        "event": {"type": "completed", "data": {}},
                    }
                ),
            }
        )
        repaired_events = await repaired_task
        _assert_completed_without_error(repaired_events)

        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait(timeout=5)

    @pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI is not installed")
    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_cli_daemon_process_discovers_models_and_runs_real_harnesses(
        self,
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        backend_server,
        tmp_path,
        worker,
    ):
        _ = worker
        config_path = tmp_path / "lemma-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "active_server": "default",
                    "servers": {
                        "default": {
                            "base_url": backend_server["host_base_url"],
                            "token": fixed_test_user["token"],
                            "defaults": {},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        repo_root = Path(__file__).resolve().parents[6]
        env = os.environ.copy()
        python_paths = [str(repo_root / "lemma-cli"), str(repo_root / "lemma-python")]
        if env.get("PYTHONPATH"):
            python_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
        process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "lemma_cli.cli_core.app",
                "--config-file",
                str(config_path),
                "daemon",
                "start",
            ],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            harness = await _wait_for_daemon_harness(
                authenticated_client,
                harness_kind="CODEX",
                process=process,
                timeout=30,
            )
            daemon_id = harness["daemon_id"]
            catalog_response = await authenticated_client.get("/agent-runtime/harnesses")
            assert catalog_response.status_code == 200, catalog_response.text
            daemon_items = [
                item
                for item in catalog_response.json()["items"]
                if item.get("daemon_id") == daemon_id
            ]
            assert daemon_items
            for item in daemon_items:
                assert item["models"], item
                assert item["models"] != ["default"], item
            runnable_harnesses = {
                harness.strip()
                for harness in os.getenv(
                    "LEMMA_REAL_DAEMON_HARNESSES",
                    "CODEX",
                ).split(",")
                if harness.strip()
            }
            daemon_items = [
                item for item in daemon_items if item["harness_kind"] in runnable_harnesses
            ]
            assert daemon_items
            preferred_models = {
                "CODEX": os.getenv("LEMMA_REAL_DAEMON_CODEX_MODEL", "gpt-5.5"),
                "CLAUDE_CODE": os.getenv("LEMMA_REAL_DAEMON_CLAUDE_MODEL", "sonnet"),
                "OPENCODE": os.getenv(
                    "LEMMA_REAL_DAEMON_OPENCODE_MODEL",
                    "opencode/deepseek-v4-flash-free",
                ),
            }
            pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
            for item in daemon_items:
                harness_kind = item["harness_kind"]
                requested_model = preferred_models.get(harness_kind)
                model_name = (
                    requested_model
                    if requested_model in item["models"]
                    else item["models"][0]
                )
                created_profile = await authenticated_client.post(
                    f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
                    json={
                        "source": "USER_DAEMON",
                        "daemon_id": daemon_id,
                        "harness_kind": harness_kind,
                        "name": f"CLI Daemon {harness_kind} {uuid4().hex[:8]}",
                        "default_model_name": model_name,
                    },
                )
                assert created_profile.status_code == 201, created_profile.text
                profile_id = created_profile.json()["id"]

                agent_name = f"CLI Daemon {harness_kind} Agent"
                create_agent = await authenticated_client.post(
                    f"/pods/{pod_id}/agents",
                    json={
                        "name": agent_name,
                        "instruction": (
                            "Reply exactly with the marker requested by the user."
                        ),
                        "agent_runtime": {
                            "profile_id": profile_id,
                            "model_name": model_name,
                        },
                    },
                )
                assert create_agent.status_code == 201, create_agent.text
                create_conversation = await authenticated_client.post(
                    f"/pods/{pod_id}/conversations",
                    json={
                        "agent_name": create_agent.json()["name"],
                        "title": f"CLI daemon bridge {harness_kind}",
                    },
                )
                assert create_conversation.status_code == 201, create_conversation.text
                conversation_id = create_conversation.json()["id"]
                marker = f"REAL_DAEMON_{harness_kind}_E2E_OK"

                events = await _post_sse(
                    authenticated_client,
                    f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                    {"content": f"Reply exactly {marker}."},
                )
                _assert_completed_without_error(events)

                messages = await authenticated_client.get(
                    f"/pods/{pod_id}/conversations/{conversation_id}/messages"
                )
                assert messages.status_code == 200, messages.text
                assistant_messages = [
                    message
                    for message in messages.json()["items"]
                    if message["role"] == MessageRole.ASSISTANT.value
                ]
                assert any(
                    marker in (message["text"] or "")
                    for message in assistant_messages
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=10)

    async def test_create_provider_profiles_and_resolve_them(
        self,
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        monkeypatch,
    ):
        from app.modules.agent.services.runtime_profile_service import (
            DiscoveredModel,
        )

        async def fake_openai_discovery(*, base_url, **_kwargs):
            if base_url == "https://openrouter.ai/api/v1":
                return [
                    DiscoveredModel("openai/gpt-5.1", supports_vision=True),
                    DiscoveredModel("deepseek/deepseek-chat-v3.2"),
                ]
            return []

        monkeypatch.setattr(
            "app.modules.agent.services.runtime_profile_service._discover_openai_compatible_models",
            fake_openai_discovery,
        )

        openrouter = await authenticated_client.post(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
            json={
                "source": "OPENAI_COMPATIBLE",
                "name": f"OpenRouter {uuid4().hex[:8]}",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "openrouter-secret",
                "default_model_name": "deepseek/deepseek-chat-v3.2",
                "headers": {
                    "HTTP-Referer": "https://lemma.test",
                    "X-Title": "Lemma",
                },
            },
        )
        assert openrouter.status_code == 201, openrouter.text
        openrouter_payload = openrouter.json()
        assert openrouter_payload["protocol"] == "OPENAI_COMPATIBLE"
        assert openrouter_payload["derived_harness_kind"] == "LEMMA"
        assert openrouter_payload["has_credentials"] is True
        assert openrouter_payload["default_model_name"] == "deepseek/deepseek-chat-v3.2"
        assert {item["name"] for item in openrouter_payload["model_catalog"]} == {
            "openai/gpt-5.1",
            "deepseek/deepseek-chat-v3.2",
        }
        assert openrouter_payload["metadata"] == {
            "source": "openai_compatible",
            "catalog_discovered": True,
        }

        vendor = await authenticated_client.post(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
            json={
                "source": "OPENAI_COMPATIBLE",
                "name": f"Custom provider {uuid4().hex[:8]}",
                "base_url": "https://api.vendor.test/v1",
                "api_key": "vendor-secret",
                "default_model_name": "vendor/model-pro",
                "model_names": ["vendor/model-pro"],
            },
        )
        assert vendor.status_code == 201, vendor.text
        vendor_payload = vendor.json()
        assert vendor_payload["metadata"] == {
            "source": "openai_compatible",
            "catalog_discovered": False,
        }
        assert vendor_payload["default_model_name"] == "vendor/model-pro"

        runner = AgentRunnerService(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            harness_registry=object(),  # type: ignore[arg-type]
        )
        for payload in (openrouter_payload, vendor_payload):
            resolved = await runner._resolve_agent_runtime(
                AgentRuntimeConfig(profile_id=payload["id"]),
                user_id=UUID(fixed_test_user["id"]),
                organization_id=UUID(fixed_test_org["id"]),
            )
            assert resolved.harness_kind is HarnessKind.LEMMA
            assert resolved.model_name_for_harness == payload["default_model_name"]
            assert resolved.credentials == {
                "api_key": (
                    "openrouter-secret"
                    if payload["id"] == openrouter_payload["id"]
                    else "vendor-secret"
                )
            }

    @pytest.mark.parametrize(
        ("protocol", "harness_kind"),
        [
            ("CODEX_APP_SERVER", HarnessKind.CODEX),
            ("CLAUDE_CODE", HarnessKind.CLAUDE_CODE),
            ("OPENCODE", HarnessKind.OPENCODE),
        ],
    )
    async def test_runner_resolves_org_user_daemon_profile_rows(
        self,
        db_session,
        fixed_test_org,
        fixed_test_user,
        protocol,
        harness_kind,
    ):
        daemon = AgentRuntimeDaemonModel(
            user_id=UUID(fixed_test_user["id"]),
            device_key=f"resolver-daemon-{uuid4().hex[:8]}",
            display_name="Resolver daemon",
            status="ONLINE",
            device_info={"platform": "test"},
            harness_catalog={
                harness_kind.value: {
                    "available": True,
                    "display_name": harness_kind.value,
                    "models": ["default"],
                }
            },
        )
        db_session.add(daemon)
        await db_session.flush()
        profile = AgentRuntimeProfileModel(
            organization_id=UUID(fixed_test_org["id"]),
            user_id=UUID(fixed_test_user["id"]),
            daemon_id=daemon.id,
            scope="ORGANIZATION",
            kind="HARNESS",
            protocol=protocol,
            name=f"{harness_kind.value.lower()} e2e",
            default_model_name="default",
            model_catalog=[
                {
                    "name": "default",
                    "display_name": "Default",
                    "provider_model_name": "default",
                    "capabilities": ["TEXT", "TOOLS"],
                    "default_model_settings": {},
                    "metadata": {},
                }
            ],
            config={},
            status="ACTIVE",
            profile_metadata={"source": "USER_DAEMON"},
        )
        db_session.add(profile)
        await db_session.flush()
        profile_id = str(profile.id)
        await db_session.commit()

        runner = AgentRunnerService(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            harness_registry=object(),  # type: ignore[arg-type]
        )

        resolved = await runner._resolve_agent_runtime(
            AgentRuntimeConfig(profile_id=profile_id),
            user_id=UUID(fixed_test_user["id"]),
            organization_id=UUID(fixed_test_org["id"]),
        )

        assert resolved.profile.id == profile_id
        assert resolved.harness_kind is harness_kind
        assert resolved.model_name_for_harness == "default"


class TestAgentToolApis:
    async def test_agent_tool_http_apis(self, authenticated_client, db_session):
        await _seed_gmail_connector(db_session)

        web_search = await authenticated_client.post(
            "/tools/web-search",
            json={"query": "Lemma AI", "max_results": 1},
            timeout=60,
        )
        assert web_search.status_code == 200, web_search.text
        assert "success" in web_search.json()

        feedback = await authenticated_client.post(
            "/tools/report-feedback",
            json={
                "category": "TOOLING_ISSUE",
                "subject": "Tool e2e feedback",
                "issue_encountered": "The test needs to record feedback.",
                "expected_behavior": "Feedback is stored.",
                "actual_behavior": "Feedback route responded.",
                "suggested_next_steps": "Keep route healthy.",
            },
        )
        assert feedback.status_code == 201, feedback.text
        assert feedback.json()["success"] is True
        assert UUID(feedback.json()["feedback_id"])


class TestAgentOpenApi:
    async def test_agent_openapi_documents_current_routes(self, authenticated_client):
        response = await authenticated_client.get("/openapi.json")
        assert response.status_code == 200, response.text
        openapi = response.json()
        paths = openapi["paths"]
        schemas = openapi["components"]["schemas"]

        assert "/pods/{pod_id}/conversations" in paths
        assert "/agent-runtime/harnesses" in paths
        assert "/organizations/{org_id}/agent-runtime/profiles" in paths
        assert "/agent-runtime/profiles" not in paths
        assert "/agent-runtime/default" not in paths
        assert "/agent-runtime/harnesses/{harness_kind}/models" not in paths
        assert "/agent-runtime/config" not in paths
        assert "/pods/{pod_id}/agent-runtime/config" not in paths
        assert "/organizations/{organization_id}/agent-runtime/config" not in paths
        assert "/pods/{pod_id}/conversations/messages" not in paths
        assert "/pods/{pod_id}/conversations/{conversation_id}/messages" in paths
        assert "/lemma/conversations" not in paths
        assert (
            "/pods/{pod_id}/agents/{agent_name}/conversations/{conversation_id}"
            not in paths
        )
        assert "/agent/global/conversations" not in paths
        assert schemas["HarnessKind"]["enum"] == [
            "LEMMA",
            "CODEX",
            "CLAUDE_CODE",
            "OPENCODE",
            "CURSOR",
            "ANTIGRAVITY",
        ]
        assert "model_name" not in schemas["SendMessageRequest"]["properties"]
        assert "model_name" in schemas["AgentRuntimeConfig"]["properties"]
        assert "default_runtime" not in schemas["AgentHarnessListResponse"][
            "properties"
        ]
        assert schemas["AgentHarnessListResponse"]["required"] == ["items"]
        assert "metadata" in schemas["SendMessageRequest"]["properties"]
        assert "instructions" in schemas["CreateConversationRequest"]["properties"]
        assert "instructions" in schemas["UpdateConversationRequest"]["properties"]
        assert (
            paths["/pods/{pod_id}/conversations"]["get"]["operationId"]
            == "agent.conversation.list"
        )
        assert (
            paths["/pods/{pod_id}/conversations/{conversation_id}"]["patch"][
                "operationId"
            ]
            == "agent.conversation.update"
        )
        assert (
            paths["/pods/{pod_id}/conversations/{conversation_id}/messages"]["post"][
                "operationId"
            ]
            == "agent.conversation.message.send"
        )
        assert (
            paths["/agent-runtime/harnesses"]["get"]["operationId"]
            == "agent.runtime.harnesses.list"
        )
        assert (
            paths["/organizations/{org_id}/agent-runtime/profiles"]["get"][
                "operationId"
            ]
            == "agent.runtime.profiles.list"
        )
        assert (
            paths["/organizations/{org_id}/agent-runtime/profiles"]["post"][
                "operationId"
            ]
            == "agent.runtime.profiles.create"
        )
        create_profile_schema = paths[
            "/organizations/{org_id}/agent-runtime/profiles"
        ]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert create_profile_schema["discriminator"]["propertyName"] == "source"
        assert set(create_profile_schema["discriminator"]["mapping"]) == {
            "USER_DAEMON",
            "OPENAI_COMPATIBLE",
            "ANTHROPIC_COMPATIBLE",
        }
        assert schemas["CreateOpenAICompatibleRuntimeProfileRequest"]["properties"][
            "base_url"
        ]["format"] == "uri"
        assert (
            paths["/tools/report-feedback"]["post"]["operationId"]
            == "agent.tool.report_feedback"
        )


async def _wait_for_conversation_title(
    authenticated_client,
    pod_id,
    conversation_id,
    *,
    attempts: int = 160,
    sleep_seconds: float = 0.25,
) -> str:
    """Poll the conversation until the worker-generated title lands."""
    last: str | None = None
    for _ in range(attempts):
        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert response.status_code == 200, response.text
        last = response.json().get("title")
        if last:
            return last
        await asyncio.sleep(sleep_seconds)
    raise AssertionError(
        f"Conversation {conversation_id} title was not generated in time (last={last!r})"
    )


class TestConversationTitleGeneration:
    @pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
    async def test_first_run_generates_title_with_real_worker_model(
        self,
        authenticated_client,
        fixed_test_org,
        worker,
    ):
        """After the first run completes, the worker auto-generates a title from
        the opening exchange, and a second turn does not overwrite it."""
        _ = worker
        pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

        # Pod-assistant conversation created WITHOUT a title -> eligible.
        create_conversation = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={"agent_runtime": DEFAULT_AGENT_RUNTIME},
        )
        assert create_conversation.status_code == 201, create_conversation.text
        conversation = create_conversation.json()
        conversation_id = conversation["id"]
        assert conversation["title"] is None

        events = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {"content": "Help me plan a 3-day vegetarian food tour of Tokyo."},
        )
        _assert_completed_without_error(events)

        title = await _wait_for_conversation_title(
            authenticated_client, pod_id, conversation_id
        )
        assert title.strip()
        assert len(title) <= 80

        # Idempotent: a second turn must not change the established title.
        followup = await _post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {"content": "Actually, make it two days instead of three."},
        )
        _assert_completed_without_error(followup)
        await asyncio.sleep(2)
        after = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert after.status_code == 200, after.text
        assert after.json()["title"] == title
