"""Real user journeys through every workspace execution surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
from sandbox_runtime.protocol import PortProtocol, WorkloadKind
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecutePythonRequest,
    ListProcessesRequest,
    ResizeTerminalRequest,
    TerminateProcessRequest,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
    execute_python_internal,
    list_processes_internal,
    resize_terminal_internal,
    terminate_process_internal,
    write_stdin_internal,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(600)]


async def _context(authenticated_client, fixed_test_org, fixed_test_user):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Holistic Workspace Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod = response.json()
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="holistic_workspace_e2e",
        workload_type="agent",
    )

    async def warmup():
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(cmd="true", timeout_seconds=180),
        )

    await eventually(
        label="real workspace sandbox warmup",
        probe=warmup,
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    return ctx


async def test_shell_python_file_and_lemma_cli_round_trip(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    shell = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd=(
                "mkdir -p artifacts && "
                "printf 'workspace-file-proof\\n' > artifacts/proof.txt && "
                "cat artifacts/proof.txt"
            ),
            comment="Create and read a workspace file",
            timeout_seconds=30,
        ),
    )
    assert shell.success, shell
    assert "workspace-file-proof" in (shell.stdout or "")
    assert shell.exit_code == 0

    python_first = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            code="from pathlib import Path\nvalue = Path('artifacts/proof.txt').read_text().strip()\nprint(value)",
            comment="Read shell output from the shared Python session",
        ),
    )
    assert python_first.success, python_first
    assert "workspace-file-proof" in (python_first.stdout or "")

    python_second = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            code="value = value.upper()\nprint(value)",
            comment="Prove Python state survives the next tool call",
        ),
    )
    assert python_second.success, python_second
    assert "WORKSPACE-FILE-PROOF" in (python_second.stdout or "")

    python_failure = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            code="raise RuntimeError('intentional workspace python failure')",
            comment="Return user-code failures as tool results",
        ),
    )
    assert python_failure.success is False
    assert "intentional workspace python failure" in (python_failure.error or "")

    cli = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="lemma --output json profile get",
            comment="Use the installed Lemma CLI from the sandbox",
            timeout_seconds=60,
        ),
    )
    assert cli.success, cli
    assert cli.exit_code == 0
    assert fixed_test_user["email"] in (cli.stdout or ""), cli

    service = WorkspaceSandboxService()
    session = await service.get_session(
        ctx.user_id,
        ctx.pod_id,
        session_id=str(ctx.conversation_id),
        organization_id=ctx.org_id,
        workload_type="agent",
        workload_id=ctx.pod_id,
        workload_name=ctx.agent_name,
        scope=["pod.read", "pod.write"],
    )
    async with session:
        await session.write_file("artifacts/api.txt", b"written through file API")
        assert await session.read_file("artifacts/api.txt") == b"written through file API"
        listed = await session.list_files("artifacts")
        assert {item.path.rsplit("/", 1)[-1] for item in listed} >= {
            "proof.txt",
            "api.txt",
        }
        await session.delete_file("artifacts/api.txt")
        with pytest.raises(Exception):
            await session.read_file("artifacts/api.txt")
    await service.close()


async def test_tty_process_input_resize_listing_and_termination(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="printf 'TTY_READY\\n'; cat",
            tty=True,
            cols=100,
            rows=30,
            yield_time_ms=500,
            timeout_seconds=30,
            comment="Start an interactive process",
        ),
    )
    assert started.success, started
    assert started.completed is False
    assert started.process_id
    process_id = started.process_id
    assert "TTY_READY" in (started.stdout or "")

    listed = await list_processes_internal(ctx, ListProcessesRequest())
    assert listed.success, listed
    assert any(item.process_id == process_id and item.tty for item in listed.processes)

    resized = await resize_terminal_internal(
        ctx,
        ResizeTerminalRequest(process_id=process_id, cols=140, rows=50),
    )
    assert resized.success, resized
    assert resized.completed is False

    sent = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            process_id=process_id,
            chars="tty-input-proof\n",
            yield_time_ms=500,
            comment="Send input to the interactive process",
        ),
    )
    assert sent.success, sent
    assert "tty-input-proof" in (sent.stdout or "")
    assert sent.completed is False

    terminated = await terminate_process_internal(
        ctx,
        TerminateProcessRequest(
            process_id=process_id,
            comment="Terminate the interactive process",
        ),
    )
    assert terminated.success, terminated
    assert terminated.completed is True

    after = await list_processes_internal(ctx, ListProcessesRequest())
    assert after.success, after
    assert not any(item.process_id == process_id and not item.completed for item in after.processes)


async def test_signed_port_proxy_and_browser_access_reach_the_sandbox(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    del configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)

    server = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            cmd="python3 -m http.server 8765 --directory /workspace",
            timeout_seconds=10,
            comment="Start a sandbox HTTP service",
        ),
    )
    assert server.success, server
    assert server.completed is False
    assert server.process_id

    service = WorkspaceSandboxService()
    client = LocalSandboxClient(service)
    grant = await client.create_port_access(
        WorkloadKind.WORKSPACE,
        ctx.user_id,
        8765,
        protocol=PortProtocol.HTTP,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    proxied = await authenticated_client.get(urlparse(grant.url).path + "health")
    assert proxied.status_code == status.HTTP_200_OK, proxied.text

    browser = await authenticated_client.post(
        "/workspace/apps/browser/access",
        json={"ttl_seconds": 300},
    )
    assert browser.status_code == status.HTTP_200_OK, browser.text
    browser_payload = browser.json()
    assert browser_payload["app"] == "browser"
    assert browser_payload["url"].startswith("http")
    assert browser_payload["expires_at"]

    stopped = await terminate_process_internal(
        ctx,
        TerminateProcessRequest(process_id=server.process_id),
    )
    assert stopped.success, stopped
    await service.close()
