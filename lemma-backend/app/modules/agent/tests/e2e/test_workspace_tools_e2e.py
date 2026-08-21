from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import json
import shlex
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
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    e2e_real_llm,
    system_lemma_available,
)
from app.modules.agent.tests.e2e.test_agent_e2e import (
    _assert_completed_without_error,
    _post_sse,
)
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
from app.modules.test_support.e2e.worker_process import production_worker_process
import app.modules.workspace.services.workspace_tool_runtime as workspace_runtime


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.workspace,
]

_ACCEPTANCE_MODEL = "accounts/fireworks/models/minimax-m3"

# Some assertions here require the *sandbox* to call back into the backend this
# test just started on 127.0.0.1 -- the workspace token check and anything
# driving the `lemma` CLI. A local Docker sandbox reaches it over the host
# gateway; a sandbox running in someone else's cloud cannot reach a laptop, so
# those assertions are about the developer's network rather than about the
# provider. Everything that runs *inside* the sandbox is still exercised in
# every mode.
def _sandbox_runs_locally() -> bool:
    """Whether the sandbox shares a host with the backend under test.

    Keyed off the provider actually in use rather than the harness's sandbox
    mode, because the two can differ: the workspace module may be provisioning
    on E2B while the harness still configures the sandbox side for Docker.
    """
    if os.getenv("WORKSPACE_OWNS_SANDBOXES", "").lower() in {"1", "true", "yes"}:
        return os.getenv("WORKSPACE_PROVIDER", "docker").lower() != "e2b"
    return os.getenv("E2E_SANDBOX_MODE", "docker").lower() in {"", "docker"}


_SANDBOX_CAN_REACH_TEST_BACKEND = _sandbox_runs_locally()

requires_sandbox_callback = pytest.mark.skipif(
    not _SANDBOX_CAN_REACH_TEST_BACKEND,
    reason=(
        "the sandbox must be able to reach the test backend on 127.0.0.1; "
        "a cloud sandbox cannot"
    ),
)


@requires_sandbox_callback
async def test_fresh_workspace_token_authenticates_over_backend_http(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
):
    """A workspace token must survive both HTTP and sandbox process boundaries."""

    service = WorkspaceSandboxService()
    try:
        env_vars = await service.get_env_vars(
            UUID(fixed_test_user["id"]),
            None,
            organization_id=UUID(fixed_test_org["id"]),
            workload_type="agent",
            workload_id=uuid4(),
            workload_name="sandbox_token_preflight",
            scope=["pod.read"],
            session_id=str(uuid4()),
        )

        headers = {"Authorization": f"Bearer {env_vars['LEMMA_TOKEN']}"}
        in_process = await authenticated_client.get(
            "/users/me/profile", headers=headers
        )
        assert in_process.status_code == status.HTTP_200_OK, in_process.text

        async with httpx.AsyncClient(
            base_url=backend_server["host_base_url"],
            headers=headers,
        ) as client:
            over_http = await client.get("/users/me/profile")
        assert over_http.status_code == status.HTTP_200_OK, over_http.text
        assert over_http.json()["email"] == fixed_test_user["email"]

        expected_token_hash = hashlib.sha256(
            env_vars["LEMMA_TOKEN"].encode()
        ).hexdigest()[:16]
        session = await service.get_session(
            UUID(fixed_test_user["id"]),
            None,
            session_id=str(uuid4()),
            env_vars=env_vars,
        )
        token_probe = await session.exec_command(
            cmd=(
                "python -c 'import hashlib, os; "
                'print(hashlib.sha256(os.environ["LEMMA_TOKEN"].encode()).hexdigest()[:16])\''
            )
        )
        assert token_probe["exit_code"] == 0, token_probe
        assert token_probe["stdout"].strip() == expected_token_hash, token_probe

        direct_http_script = (
            "import httpx, os; "
            "response = httpx.get("
            "os.environ['LEMMA_BASE_URL'] + '/users/me/profile', "
            "headers={'Authorization': 'Bearer ' + os.environ['LEMMA_TOKEN']}"
            "); "
            "print(response.status_code); "
            "print(response.text)"
        )
        direct_http_probe = await session.exec_command(
            cmd=f"python -c {shlex.quote(direct_http_script)}"
        )
        assert direct_http_probe["exit_code"] == 0, direct_http_probe
        assert direct_http_probe["stdout"].splitlines()[0] == "200", direct_http_probe
        assert fixed_test_user["email"] in direct_http_probe["stdout"], (
            direct_http_probe
        )

        cli_probe = await session.exec_command(cmd="lemma --output json profile get")
        assert cli_probe["exit_code"] == 0, cli_probe
        assert fixed_test_user["email"] in cli_probe["stdout"], cli_probe
    finally:
        await service.close()


@pytest.mark.slow
@pytest.mark.provider
@pytest.mark.real_llm
@pytest.mark.real_sandbox
@pytest.mark.skipif(
    not e2e_real_llm(),
    reason="set E2E_LLM_MODE=real to run the live sandbox agent acceptance test",
)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_agent_uses_lemma_cli_through_selected_workspace_provider(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    db_manager,
    e2e_settings,
):
    """A real agent must choose the workspace tool and authenticate the real CLI."""

    del db_manager
    await workspace_runtime.close_workspace_tool_runtimes()
    provider = configure_workspace_api_url["provider"]

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Sandbox Lemma CLI Agent Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    create_agent = await authenticated_client.post(
        f"/pods/{pod['id']}/agents",
        json={
            "name": f"Sandbox CLI Acceptance Agent {uuid4().hex[:8]}",
            "instruction": (
                "You verify the installed Lemma CLI. When asked, you must call "
                "exec_command through the WORKSPACE_CLI toolset and use the exact "
                "command supplied by the user. Never invent or infer the command "
                "output. After the tool succeeds, report the returned profile email "
                "and the exact marker LEMMA_CLI_SANDBOX_OK."
            ),
            "toolsets": ["WORKSPACE_CLI"],
            "agent_runtime": {
                "profile_id": "system:lemma",
                "model_name": _ACCEPTANCE_MODEL,
            },
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    create_conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "title": f"{provider} Lemma CLI acceptance",
            "type": "CHAT",
        },
    )
    assert create_conversation.status_code == status.HTTP_201_CREATED, (
        create_conversation.text
    )
    conversation_id = create_conversation.json()["id"]

    async with production_worker_process(
        e2e_settings,
        log_prefix=f"sandbox_{provider}_lemma_cli_agent",
    ) as acceptance_worker:
        events = await _post_sse(
            authenticated_client,
            f"/pods/{pod['id']}/conversations/{conversation_id}/messages",
            {
                "content": (
                    "Use exec_command now to run exactly: "
                    "`lemma --output json profile get`. "
                    "Do not answer from memory. After it succeeds, reply with the "
                    "profile email and LEMMA_CLI_SANDBOX_OK."
                )
            },
        )
        if any(event.get("type") == "error" for event in events):
            pytest.fail(
                "Agent acceptance run failed.\nWorker log:\n"
                + acceptance_worker.read_log_tail()
            )
    _assert_completed_without_error(events)

    messages = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]

    cli_calls = [
        item
        for item in items
        if item["kind"] == "TOOL_CALL"
        and item["tool_name"] == "exec_command"
        and "lemma --output json profile get"
        in ((item.get("tool_args") or {}).get("cmd") or "")
    ]
    assert cli_calls, items

    cli_call_ids = {item["tool_call_id"] for item in cli_calls}
    cli_returns = [
        item
        for item in items
        if item["kind"] == "TOOL_RETURN"
        and item["tool_name"] == "exec_command"
        and item["tool_call_id"] in cli_call_ids
    ]
    assert cli_returns, items
    serialized_returns = json.dumps(
        [item.get("tool_result") for item in cli_returns],
        sort_keys=True,
    )
    assert fixed_test_user["email"] in serialized_returns, serialized_returns
    assert any(
        (item.get("tool_result") or {}).get("success") is True for item in cli_returns
    ), cli_returns

    assistant_text = " ".join(
        item.get("text") or ""
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT"
    )
    assert "LEMMA_CLI_SANDBOX_OK" in assistant_text.upper(), assistant_text
    assert fixed_test_user["email"] in assistant_text, assistant_text


@pytest.mark.slow
@pytest.mark.provider
@pytest.mark.real_llm
@pytest.mark.real_sandbox
@pytest.mark.skipif(
    not e2e_real_llm(),
    reason="set E2E_LLM_MODE=real to run the live two-tool directory acceptance test",
)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_a_real_agent_finds_with_the_shell_what_it_wrote_with_python(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    db_manager,
    e2e_settings,
):
    """The failure as a person actually hits it, with a real model driving.

    `execute_python` ran in the sandbox image's default directory while
    `exec_command` ran in the conversation's own, so a relative path meant two
    different files and an agent watched its own work disappear between tools.
    The scripted journeys pin the contract; this pins the experience -- nobody
    tells this agent a directory, it just uses both tools the way a model does.

    Deliberately no absolute paths in the prompt: an absolute path passes even
    with the interpreter sitting in the wrong place, which is the whole bug.
    """
    del db_manager
    await workspace_runtime.close_workspace_tool_runtimes()
    provider = configure_workspace_api_url["provider"]

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Two Tool Directory Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    create_agent = await authenticated_client.post(
        f"/pods/{pod['id']}/agents",
        json={
            "name": f"Two Tool Directory Agent {uuid4().hex[:8]}",
            "instruction": (
                "You do small workspace tasks with the tools you are given. "
                "Use execute_python for Python and exec_command for shell "
                "commands. Never answer from memory -- run the tools."
            ),
            "toolsets": ["WORKSPACE_CLI"],
            "agent_runtime": {
                "profile_id": "system:lemma",
                "model_name": _ACCEPTANCE_MODEL,
            },
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    create_conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "title": f"{provider} two-tool directory acceptance",
            "type": "CHAT",
        },
    )
    assert create_conversation.status_code == status.HTTP_201_CREATED, (
        create_conversation.text
    )
    conversation = create_conversation.json()
    conversation_id = conversation["id"]
    recorded_cwd = conversation["metadata"]["cwd"]

    async with production_worker_process(
        e2e_settings,
        log_prefix=f"sandbox_{provider}_two_tool_directory",
    ) as acceptance_worker:
        events = await _post_sse(
            authenticated_client,
            f"/pods/{pod['id']}/conversations/{conversation_id}/messages",
            {
                "content": (
                    "Use execute_python to write the text TWO_TOOL_PROOF into a "
                    "file called handoff.txt, using that relative filename and "
                    "no directory. Then use exec_command to `cat handoff.txt`, "
                    "also with no directory. Reply with what cat printed."
                )
            },
        )
        if any(event.get("type") == "error" for event in events):
            pytest.fail(
                "Two-tool directory acceptance run failed.\nWorker log:\n"
                + acceptance_worker.read_log_tail()
            )
    _assert_completed_without_error(events)

    messages = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]

    def _returns(tool_name: str) -> list[dict]:
        return [
            item
            for item in items
            if item["kind"] == "TOOL_RETURN" and item["tool_name"] == tool_name
        ]

    python_returns = _returns("execute_python")
    shell_returns = _returns("exec_command")
    assert python_returns, items
    assert shell_returns, items

    # The handoff itself: the shell found, by relative name, the file Python
    # wrote by relative name. This is the assertion that fails when the two
    # tools are in different directories, and it fails for the reason the
    # person experiences rather than on an internal detail.
    shell_output = json.dumps(
        [item.get("tool_result") for item in shell_returns], sort_keys=True
    )
    assert "TWO_TOOL_PROOF" in shell_output, shell_output
    assert "No such file" not in shell_output, shell_output

    assistant_text = " ".join(
        item.get("text") or ""
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT"
    )
    assert "TWO_TOOL_PROOF" in assistant_text, assistant_text

    # And the file is in the conversation's own directory, so the handoff
    # worked because both tools were there -- not because they happened to
    # share the image's default.
    probe = await exec_command_internal(
        BaseAgentContext(
            user_id=UUID(fixed_test_user["id"]),
            org_id=UUID(fixed_test_org["id"]),
            pod_id=UUID(pod["id"]),
            conversation_id=UUID(conversation_id),
            agent_name="two_tool_directory_probe",
            workspace_cwd=recorded_cwd,
        ),
        ExecCommandRequest(
            cmd=f"cat {shlex.quote(recorded_cwd)}/handoff.txt", timeout_seconds=30
        ),
    )
    assert probe.success is True, probe
    assert "TWO_TOOL_PROOF" in (probe.stdout or ""), probe


@pytest.mark.slow
@pytest.mark.provider
@pytest.mark.real_llm
@pytest.mark.real_sandbox
@pytest.mark.skipif(
    not e2e_real_llm(),
    reason="set E2E_LLM_MODE=real to run the live task-list acceptance test",
)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_a_real_agent_picks_up_its_plan_in_a_later_turn_and_finishes_it(
    authenticated_client,
    fixed_test_org,
    configure_workspace_api_url,
    db_manager,
    e2e_settings,
):
    """A live agent working a plan across two turns, end to end.

    Read this for what it is: a guard, not a proof. The reported failure -- a
    checklist written at the start of a session and never updated again -- did
    not reproduce here against the pre-fix code (3/3 passed on the acceptance
    model, 2/3 on the weaker default, and that one failure was the model never
    calling `write_todos` at all rather than never updating it). The mechanism
    the prompt section addresses needs a conversation long enough for
    summarization to bite: it keeps the last 40 messages, and past that the
    `write_todos` return carrying the list is simply gone from history. Getting
    there costs ~70k tokens of conversation, which no e2e should spend, so the
    section is pinned at unit level instead.

    What this does hold: the whole loop works with a real model across a turn
    boundary, and the outcome the person sees is right -- the stored list ends
    ticked off, holding the tasks that were planned rather than a re-plan or
    paraphrases beside them.
    """
    del db_manager
    await workspace_runtime.close_workspace_tool_runtimes()
    provider = configure_workspace_api_url["provider"]

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Task List Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    create_agent = await authenticated_client.post(
        f"/pods/{pod['id']}/agents",
        json={
            "name": f"Task List Agent {uuid4().hex[:8]}",
            "instruction": (
                "You do multi-step workspace tasks. Track work that takes "
                "several steps with write_todos, and keep the list current as "
                "you go. Do the work with the workspace tools -- never answer "
                "from memory."
            ),
            "toolsets": ["WORKSPACE_CLI", "TODO"],
            "agent_runtime": {
                "profile_id": "system:lemma",
                "model_name": _ACCEPTANCE_MODEL,
            },
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    create_conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "title": f"{provider} task list acceptance",
            "type": "CHAT",
        },
    )
    assert create_conversation.status_code == status.HTTP_201_CREATED, (
        create_conversation.text
    )
    conversation_id = create_conversation.json()["id"]
    messages_url = f"/pods/{pod['id']}/conversations/{conversation_id}/messages"

    async with production_worker_process(
        e2e_settings,
        log_prefix=f"sandbox_{provider}_task_list",
    ) as acceptance_worker:
        first = await _post_sse(
            authenticated_client,
            messages_url,
            {
                "content": (
                    "Four steps in the workspace, please: (1) write ALPHA into "
                    "alpha.txt, (2) write the number of characters in alpha.txt "
                    "into beta.txt, (3) join both files into gamma.txt, (4) "
                    "print gamma.txt. Plan all four before you start, then do "
                    "only the first two and stop -- I will tell you when to "
                    "carry on."
                )
            },
        )
        if any(event.get("type") == "error" for event in first):
            pytest.fail(
                "Task list acceptance run failed.\nWorker log:\n"
                + acceptance_worker.read_log_tail()
            )
        _assert_completed_without_error(first)

        second = await _post_sse(
            authenticated_client,
            messages_url,
            {"content": "Carry on with the rest of the plan."},
        )
        if any(event.get("type") == "error" for event in second):
            pytest.fail(
                "Task list continuation run failed.\nWorker log:\n"
                + acceptance_worker.read_log_tail()
            )
        _assert_completed_without_error(second)

    messages = await authenticated_client.get(messages_url)
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]

    todo_calls = [
        item
        for item in items
        if item["kind"] == "TOOL_CALL" and item["tool_name"] == "write_todos"
    ]
    # More than once is the whole point: writing the plan was never the part
    # that failed.
    assert len(todo_calls) >= 2, [item.get("tool_args") for item in todo_calls]

    persisted = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    stored = persisted.json()["metadata"]["todos"]
    assert stored, persisted.json()["metadata"]
    assert all(item["done"] for item in stored), stored

    # The list the person reads is the plan, not the plan plus paraphrases of
    # it: a check-off in different words used to append beside the task it
    # meant. Compare against what the agent actually planned first.
    planned = (todo_calls[0].get("tool_args") or {}).get("todos") or []
    assert len(stored) <= max(len(planned), 1) + 1, (planned, stored)

    # And the work itself really happened.
    assistant_text = " ".join(
        item.get("text") or ""
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT"
    )
    assert "ALPHA" in assistant_text.upper(), assistant_text


async def test_agent_workspace_cli_tools_execute_through_a_real_sandbox(
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
        agent_name="workspace_tools_e2e",
    )

    python_set = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            comment="compute through a sandbox",
            code="sandbox_value = 6 * 7\nsandbox_value",
        ),
    )
    assert python_set.success is True, python_set
    assert python_set.result == "42"

    python_get = await execute_python_internal(
        ctx,
        ExecutePythonRequest(
            comment="verify persistent python session",
            code="sandbox_value += 1\nsandbox_value",
        ),
    )
    assert python_get.success is True, python_get
    assert python_get.result == "43"

    # The cwd and the injected identity are provider behaviour and are checked
    # everywhere. The `lemma` CLI call additionally needs the sandbox to reach
    # the backend this test started on 127.0.0.1, which a cloud sandbox cannot,
    # so only that part is conditional.
    shell = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="verify shell env and Lemma CLI through a sandbox",
            cmd=(
                "pwd; "
                'printf \'pod=%s user=%s\\n\' "$LEMMA_POD_ID" "$LEMMA_USER_ID"; '
                + (
                    "lemma --output json profile get"
                    if _SANDBOX_CAN_REACH_TEST_BACKEND
                    else "true"
                )
            ),
        ),
    )
    assert shell.success is True, shell.stdout or shell
    assert shell.completed is True
    assert f"/workspace/conversations/{ctx.conversation_id}" in (shell.stdout or "")
    assert f"pod={pod['id']}" in (shell.stdout or "")
    assert f"user={fixed_test_user['id']}" in (shell.stdout or "")
    if _SANDBOX_CAN_REACH_TEST_BACKEND:
        assert fixed_test_user["email"] in (shell.stdout or "")

    interactive = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="start interactive shell command",
            cmd="read line; printf 'sandbox-stdin:%s\\n' \"$line\"",
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
    assert "sandbox-stdin:hello-agent" in (stdin.stdout or "")

    tty_check = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="verify real tty allocation",
            cmd=(
                "python -c 'import sys; "
                'print(f"stdin={sys.stdin.isatty()} stdout={sys.stdout.isatty()}")\''
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
        async with (
            _mcp_client_session(mcp_url, token) as shell_session,
            _mcp_client_session(mcp_url, token) as python_session,
        ):
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
