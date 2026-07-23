from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import statistics
import time
from uuid import UUID, uuid4

import anyio
import httpx
import pytest
from fastapi import status
from mcp import ClientSession
from mcp.client.streamable_http import StreamableHTTPTransport

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecutePythonRequest,
    ListProcessesRequest,
    TerminateProcessRequest,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
    execute_python_internal,
    list_processes_internal,
    terminate_process_internal,
    write_stdin_internal,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
    reset_workspace_store_state,
)
import app.modules.workspace.services.workspace_tool_runtime as workspace_runtime


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.workspace,
]


async def test_agent_workspace_cli_tools_execute_through_real_agentbox(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    await workspace_runtime.close_workspace_tool_runtimes()

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Agent Workspace Tools Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="agentbox_workspace_tools_e2e",
    )

    python_set = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            comment="compute through AgentBox",
            code="agentbox_value = 6 * 7\nagentbox_value",
        ),
    )
    assert python_set.success is True, python_set
    assert python_set.result == "42"

    python_get = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            comment="verify persistent python session",
            code="agentbox_value += 1\nagentbox_value",
        ),
    )
    assert python_get.success is True, python_get
    assert python_get.result == "43"

    shell = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="verify shell env and Lemma CLI through AgentBox",
            cmd=(
                "pwd; "
                "printf 'pod=%s user=%s\\n' \"$LEMMA_POD_ID\" \"$LEMMA_USER_ID\"; "
                "lemma --output json profile get"
            ),
        ),
    )
    assert shell.success is True, shell.stdout or shell
    assert shell.completed is True
    assert f"/workspace/conversations/{ctx.conversation_id}" in (shell.stdout or "")
    assert f"pod={pod['id']}" in (shell.stdout or "")
    assert f"user={fixed_test_user['id']}" in (shell.stdout or "")
    assert fixed_test_user["email"] in (shell.stdout or "")

    interactive = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="start interactive shell command",
            cmd="read line; printf 'agentbox-stdin:%s\\n' \"$line\"",
            tty=True,
            yield_time_ms=500,
        ),
    )
    assert interactive.success is True, interactive
    assert interactive.completed is False
    assert interactive.process_id

    stdin = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            comment="finish interactive shell command",
            process_id=interactive.process_id,
            chars="hello-agent\n",
            yield_time_ms=1000,
        ),
    )
    assert stdin.success is True, stdin
    assert stdin.completed is True
    assert "agentbox-stdin:hello-agent" in (stdin.stdout or "")

    tty_check = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="verify real tty allocation",
            cmd=(
                "python -c 'import sys; "
                "print(f\"stdin={sys.stdin.isatty()} stdout={sys.stdout.isatty()}\")'"
            ),
            tty=True,
            yield_time_ms=1000,
        ),
    )
    assert tty_check.success is True, tty_check
    assert tty_check.completed is True
    assert "stdin=True stdout=True" in (tty_check.stdout or "")

    long_running = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="start long-running command without explicit tty",
            cmd="python -c 'import time; print(\"server-ready\", flush=True); time.sleep(60)'",
        ),
    )
    assert long_running.success is True, long_running
    assert long_running.completed is False
    assert long_running.process_id
    assert "server-ready" in (long_running.stdout or "")

    processes = await list_processes_internal(ctx, ListProcessesRequest())
    assert processes.success is True, processes
    assert any(
        process.process_id == long_running.process_id and not process.completed
        for process in processes.processes
    )

    terminated = await terminate_process_internal(
        ctx,
        TerminateProcessRequest(
            comment="stop long-running command",
            process_id=long_running.process_id,
        ),
    )
    assert terminated.success is True, terminated
    assert terminated.completed is True


@asynccontextmanager
async def _mcp_client_session(url: str, token: str):
    async with httpx.AsyncClient(
        timeout=None,
        headers={"Authorization": f"Bearer {token}"},
    ) as http_client:
        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
        transport = StreamableHTTPTransport(url)

        async with anyio.create_task_group() as task_group:
            try:
                async with AsyncExitStack() as stack:
                    stack.push_async_callback(read_stream.aclose)
                    stack.push_async_callback(read_stream_writer.aclose)
                    stack.push_async_callback(write_stream.aclose)
                    stack.push_async_callback(write_stream_reader.aclose)

                    def start_get_stream() -> None:
                        task_group.start_soon(
                            transport.handle_get_stream,
                            http_client,
                            read_stream_writer,
                        )

                    task_group.start_soon(
                        transport.post_writer,
                        http_client,
                        write_stream_reader,
                        read_stream_writer,
                        write_stream,
                        start_get_stream,
                        task_group,
                    )

                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session

                    if transport.session_id:
                        await transport.terminate_session(http_client)
            finally:
                task_group.cancel_scope.cancel()


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "avg_ms": round(statistics.fmean(values) * 1000, 2),
        "min_ms": round(min(values) * 1000, 2),
        "max_ms": round(max(values) * 1000, 2),
    }


@pytest.mark.slow
async def test_workspace_cli_tools_execute_over_real_mcp_with_latency_summary(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    record_property,
):
    del configure_workspace_api_url
    await workspace_runtime.close_workspace_tool_runtimes()

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Agent Workspace MCP Tools Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    create_agent = await authenticated_client.post(
        f"/pods/{pod['id']}/agents",
        json={
            "name": f"Workspace MCP Latency Agent {uuid4().hex[:8]}",
            "instruction": "Expose workspace tools for the MCP latency test.",
            "toolsets": ["WORKSPACE_CLI"],
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    create_conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "title": "Workspace MCP latency",
            "type": "CHAT",
        },
    )
    assert create_conversation.status_code == status.HTTP_201_CREATED
    conversation_id = UUID(create_conversation.json()["id"])

    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await ConversationRepository(uow).create_agent_run(
            conversation_id=conversation_id,
            agent_id=UUID(agent["id"]),
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "workspace_mcp_latency_e2e"},
        )
        await uow.commit()

    mcp_url = (
        f"{backend_server['host_base_url']}"
        f"/agent-runtime/conversations/{conversation_id}/mcp"
    )
    workspace_service = WorkspaceSandboxService()
    try:
        token = (
            await workspace_service.get_env_vars(
                user_id=UUID(fixed_test_user["id"]),
                pod_id=UUID(pod["id"]),
                organization_id=UUID(fixed_test_org["id"]),
                workload_type="agent",
                workload_id=UUID(agent["id"]),
                workload_name=agent["name"],
                session_id=str(run.id),
            )
        )["LEMMA_TOKEN"]
    finally:
        await workspace_service.close()

    try:
        async with _mcp_client_session(
            mcp_url, token
        ) as shell_session, _mcp_client_session(
            mcp_url, token
        ) as python_session:
            tools = await shell_session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert {"lemma_exec_command", "lemma_execute_python"} <= tool_names

            startup_shell, startup_python = await asyncio.gather(
                shell_session.call_tool(
                    "lemma_exec_command",
                    {
                        "cmd": "printf 'MCP_STARTUP\\n'",
                        "timeout_seconds": 10,
                    },
                ),
                python_session.call_tool(
                    "lemma_execute_python",
                    {
                        "code": "mcp_startup_value = 21 * 2\nmcp_startup_value",
                        "timeout_seconds": 10,
                    },
                ),
            )
            assert startup_shell.structuredContent["success"] is True
            assert "MCP_STARTUP" in startup_shell.structuredContent["stdout"]
            assert startup_python.structuredContent["success"] is True
            assert startup_python.structuredContent["result"] == "42"

            shell_latencies: list[float] = []
            python_latencies: list[float] = []

            async def call_shell(index: int):
                started = time.perf_counter()
                result = await shell_session.call_tool(
                    "lemma_exec_command",
                    {
                        "cmd": f"printf 'SHELL_MCP_{index}\\n'",
                        "timeout_seconds": 10,
                    },
                )
                shell_latencies.append(time.perf_counter() - started)
                return result

            async def call_python(index: int):
                started = time.perf_counter()
                result = await python_session.call_tool(
                    "lemma_execute_python",
                    {
                        "code": f"mcp_latency_value = {index} * {index}\nmcp_latency_value",
                        "timeout_seconds": 10,
                    },
                )
                python_latencies.append(time.perf_counter() - started)
                return result

            for index in range(10):
                shell_result, python_result = await asyncio.gather(
                    call_shell(index),
                    call_python(index),
                )
                assert shell_result.structuredContent["success"] is True
                assert f"SHELL_MCP_{index}" in shell_result.structuredContent["stdout"]
                assert python_result.structuredContent["success"] is True
                assert python_result.structuredContent["result"] == str(index * index)

            shell_summary = _latency_summary(shell_latencies)
            python_summary = _latency_summary(python_latencies)
            combined_summary = _latency_summary(shell_latencies + python_latencies)
            record_property("mcp_shell_latency", shell_summary)
            record_property("mcp_python_latency", python_summary)
            record_property("mcp_combined_latency", combined_summary)
            print(
                "MCP latency summary "
                f"shell={shell_summary} "
                f"python={python_summary} "
                f"combined={combined_summary}"
            )

            assert shell_summary["avg_ms"] < 15000
            assert python_summary["avg_ms"] < 15000
    finally:
        await workspace_runtime.close_workspace_tool_runtimes()
        await reset_workspace_store_state()
