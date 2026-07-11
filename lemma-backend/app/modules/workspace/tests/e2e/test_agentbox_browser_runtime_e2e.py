from __future__ import annotations

import json
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import status

from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)


pytestmark = [pytest.mark.e2e, pytest.mark.workspace]


async def _exec(
    session,
    cmd: str,
    *,
    yield_time_ms: int | None = None,
    timeout: int = 60,
    max_output_tokens: int = 12000,
) -> dict:
    result = await session.exec_command(
        cmd=cmd,
        yield_time_ms=yield_time_ms,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
    )
    if not result.get("success"):
        raise AssertionError(
            f"Command failed: {cmd}\nSTDOUT:\n{result.get('stdout')}\n"
            f"STDERR:\n{result.get('stderr')}\nERROR:\n{result.get('error')}"
        )
    return result


async def test_agentbox_runtime_supports_lemma_cli_browser_and_yielded_exec(
    authenticated_client,
    configure_workspace_api_url,
    fixed_test_org,
    fixed_test_user,
):
    del configure_workspace_api_url

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"AgentBox Browser E2E Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    service = WorkspaceSandboxService()
    session_id = f"agentbox-browser-e2e-{uuid4().hex}"
    session = await service.get_session(
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(pod["id"]),
        organization_id=UUID(fixed_test_org["id"]),
        session_id=session_id,
        close_on_exit=True,
    )

    async with session:
        binaries = await _exec(session, "which lemma && which agent-browser && which save-webpage")
        assert "/lemma" in (binaries["stdout"] or "")
        assert "agent-browser" in (binaries["stdout"] or "")
        assert "save-webpage" in (binaries["stdout"] or "")

        profile = await _exec(session, "lemma --output json profile get", timeout=30)
        profile_json = json.loads(profile["stdout"])
        assert profile_json["email"] == fixed_test_user["email"]
        assert profile_json["id"] == fixed_test_user["id"]

        sleep_started = time.monotonic()
        yielded_sleep = await _exec(
            session,
            "sleep 30",
            yield_time_ms=1000,
            timeout=60,
        )
        sleep_elapsed = time.monotonic() - sleep_started
        assert sleep_elapsed < 12
        assert yielded_sleep["completed"] is False
        assert yielded_sleep["process_id"]
        terminated = await session.terminate_process(yielded_sleep["process_id"])
        assert terminated["completed"] is True

        await _exec(
            session,
            "cat > /workspace/recent-matches.html <<'HTML'\n"
            "<!doctype html><html><head><title>Recent Cricket Matches</title></head>"
            "<body><main>"
            "<h1>Recent Cricket Matches</h1>"
            "<a href='https://www.cricbuzz.com/live-cricket-scores/129552/eng-vs-nz'>"
            "ENG vs NZ - Stumps</a>"
            "<button id='refresh'>Refresh scores</button>"
            "<a href='https://www.cricbuzz.com/cricket-match/live-scores/recent-matches'>"
            "Recent matches</a>"
            "</main></body></html>\n"
            "HTML",
        )

        await _exec(
            session,
            "agent-browser open file:///workspace/recent-matches.html",
            timeout=45,
        )

        snapshot_started = time.monotonic()
        snapshot = await _exec(
            session,
            "agent-browser snapshot -i -u",
            yield_time_ms=1000,
            timeout=60,
            max_output_tokens=14000,
        )
        snapshot_elapsed = time.monotonic() - snapshot_started
        assert snapshot_elapsed < 12
        assert snapshot["completed"] is True
        assert "ENG vs NZ - Stumps" in (snapshot["stdout"] or "")
        assert "url=https://www.cricbuzz.com/live-cricket-scores/129552/eng-vs-nz" in (
            snapshot["stdout"] or ""
        )
        assert "Recent matches" in (snapshot["stdout"] or "")

        await _exec(
            session,
            "mkdir -p /workspace/browser-artifacts && "
            "agent-browser --max-output 500000 get html html "
            "> /workspace/browser-artifacts/recent-matches.html && "
            "save-webpage file:///workspace/recent-matches.html "
            "--formats markdown --out /workspace/browser-artifacts "
            "--name recent-matches --wait-ms 250",
            timeout=60,
        )

        artifacts = await _exec(
            session,
            "python - <<'PY'\n"
            "from pathlib import Path\n"
            "root = Path('/workspace/browser-artifacts')\n"
            "for name in ['recent-matches.html', 'recent-matches.md']:\n"
            "    path = root / name\n"
            "    print(name, path.exists(), path.stat().st_size if path.exists() else 0)\n"
            "    if path.exists():\n"
            "        text = path.read_text(errors='replace')\n"
            "        print('contains_recent', 'Recent Cricket Matches' in text)\n"
            "        print('contains_cricbuzz', 'cricbuzz.com' in text)\n"
            "PY",
        )
        assert "recent-matches.html True" in (artifacts["stdout"] or "")
        assert "recent-matches.md True" in (artifacts["stdout"] or "")
        assert artifacts["stdout"].count("contains_recent True") == 2
        assert artifacts["stdout"].count("contains_cricbuzz True") == 2

        page_url = await _exec(session, "agent-browser get url", timeout=30)
        assert page_url["stdout"].strip() == "file:///workspace/recent-matches.html"


async def test_agentbox_manager_api_exercises_real_runtime_and_private_apps(
    authenticated_client,
    configure_workspace_api_url,
    fixed_test_org,
    fixed_test_user,
):
    manager_url = configure_workspace_api_url["manager_base_url"]
    manager_api_key = configure_workspace_api_url["api_key"]
    docker_base_url = configure_workspace_api_url["docker_base_url"]
    sandbox_id = f"mgr-e2e-{uuid4().hex[:12]}"
    session_id = f"conversation-{uuid4().hex[:12]}"
    headers = {"X-API-Key": manager_api_key}

    async with httpx.AsyncClient(
        base_url=manager_url,
        headers=headers,
        timeout=120,
        follow_redirects=False,
    ) as manager:
        health = await manager.get("/health")
        assert health.status_code == status.HTTP_200_OK, health.text
        assert health.json() == {"status": "ok"}

        async with httpx.AsyncClient(
            base_url=manager_url,
            timeout=120,
            follow_redirects=False,
        ) as public:
            missing_auth = await public.get(f"/sandboxes/{sandbox_id}")
            assert missing_auth.status_code in {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            }

        create = await manager.put(
            f"/sandboxes/{sandbox_id}",
            json={
                "env": {"LEMMA_BASE_URL": docker_base_url},
            },
        )
        assert create.status_code == status.HTTP_200_OK, create.text
        sandbox = create.json()["sandbox"]
        assert sandbox["id"] == sandbox_id
        assert sandbox["ready"] is True
        assert sandbox["status"] == "RUNNING"
        assert "runtime_url" not in sandbox
        assert "apps" not in sandbox

        fetched = await manager.get(f"/sandboxes/{sandbox_id}")
        assert fetched.status_code == status.HTTP_200_OK, fetched.text
        assert fetched.json()["id"] == sandbox_id
        assert fetched.json() == {
            "id": sandbox_id,
            "ready": True,
            "status": "RUNNING",
        }

        create_session = await manager.put(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}",
            json={
                "env": {"E2E_MARK": "stateful-session"},
                "cwd": "/workspace/e2e-manager",
            },
        )
        assert create_session.status_code == status.HTTP_200_OK, create_session.text
        assert create_session.json()["session_id"] == session_id
        assert create_session.json()["cwd"] == "/workspace/e2e-manager"
        assert "E2E_MARK" in create_session.json()["env_keys"]

        heartbeat = await manager.post(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/heartbeat"
        )
        assert heartbeat.status_code == status.HTTP_200_OK, heartbeat.text
        assert heartbeat.json()["active"] is True

        python_set = await manager.post(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
            json={
                "code": "value = 41\nvalue",
                "timeout_seconds": 30,
            },
        )
        assert python_set.status_code == status.HTTP_200_OK, python_set.text
        assert python_set.json()["result"] == "41"

        python_get = await manager.post(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
            json={
                "code": "value += 1\nvalue",
                "timeout_seconds": 30,
            },
        )
        assert python_get.status_code == status.HTTP_200_OK, python_get.text
        assert python_get.json()["result"] == "42"

        shell = await manager.post(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
            json={
                "cmd": "pwd && printf \"mark=%s\\n\" \"$E2E_MARK\"",
                "timeout": 30,
            },
        )
        assert shell.status_code == status.HTTP_200_OK, shell.text
        assert shell.json()["success"] is True
        assert "/workspace/e2e-manager" in shell.json()["stdout"]
        assert "mark=stateful-session" in shell.json()["stdout"]

        interactive = await manager.post(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
            json={
                "cmd": "read line; printf 'stdin:%s\\n' \"$line\"",
                "yield_time_ms": 500,
                "timeout": 30,
            },
        )
        assert interactive.status_code == status.HTTP_200_OK, interactive.text
        assert interactive.json()["completed"] is False
        process_id = interactive.json()["process_id"]
        assert process_id

        stdin = await manager.post(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/stdin",
            json={
                "process_id": process_id,
                "chars": "hello-manager\n",
                "yield_time_ms": 1000,
            },
        )
        assert stdin.status_code == status.HTTP_200_OK, stdin.text
        assert stdin.json()["success"] is True
        assert stdin.json()["completed"] is True
        assert "stdin:hello-manager" in stdin.json()["stdout"]

        async with httpx.AsyncClient(
            timeout=120,
            follow_redirects=False,
        ) as public:
            access = await manager.post(
                f"/sandboxes/{sandbox_id}/apps/browser/access",
                json={"ttl_seconds": 1800},
            )
            assert access.status_code == status.HTTP_200_OK, access.text
            access_payload = access.json()
            assert access_payload["app"] == "browser"
            assert f"{sandbox_id}-browser." in access_payload["url"]
            assert access_payload["expires_at"] > int(time.time())

            parsed_access_url = urlsplit(access_payload["url"])
            no_token_url = urlunsplit(
                (
                    parsed_access_url.scheme,
                    parsed_access_url.netloc,
                    parsed_access_url.path,
                    "",
                    "",
                )
            )
            forbidden_browser = await public.get(no_token_url)
            assert forbidden_browser.status_code == status.HTTP_403_FORBIDDEN

            first_browser = await public.get(access_payload["url"])
            assert first_browser.status_code == status.HTTP_200_OK
            set_cookie = first_browser.headers.get("set-cookie", "")
            assert "agentbox_app_access_browser" in set_cookie
            assert "Max-Age=1800" in set_cookie or "Max-Age=1799" in set_cookie

            second_browser = await public.get(
                urljoin(access_payload["url"], "/"),
                headers={"cookie": set_cookie},
            )
            assert second_browser.status_code != status.HTTP_403_FORBIDDEN, (
                second_browser.text
            )

        pod_response = await authenticated_client.post(
            "/pods",
            json={
                "name": f"AgentBox Function Executor E2E Pod {uuid4().hex[:8]}",
                "type": "ASSISTANT",
                "organization_id": fixed_test_org["id"],
            },
        )
        assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
        pod = pod_response.json()
        function_name = f"manager_function_{uuid4().hex[:8]}"
        code = f"""#input_type_name: ManagerInput
#output_type_name: ManagerOutput
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class ManagerInput(BaseModel):
    text: str

class ManagerOutput(BaseModel):
    result: str
    user_id: str
    base_url: str

async def {function_name}(ctx: FunctionContext, data: ManagerInput) -> ManagerOutput:
    return ManagerOutput(
        result=data.text.upper(),
        user_id=str(ctx.user_id),
        base_url=ctx.lemma_base_url,
    )
"""
        function_response = await authenticated_client.post(
            f"/pods/{pod['id']}/functions",
            json={
                "name": function_name,
                "description": "Manager private function executor e2e",
                "code": code,
            },
            follow_redirects=True,
        )
        assert function_response.status_code == status.HTTP_201_CREATED, (
            function_response.text
        )
        function = function_response.json()

        env_vars = await WorkspaceSandboxService().get_env_vars(
            user_id=UUID(fixed_test_user["id"]),
            pod_id=UUID(pod["id"]),
            organization_id=UUID(fixed_test_org["id"]),
            workload_type="function",
            workload_id=UUID(function["id"]),
            workload_name=function_name,
            session_id=f"function-api-{function['id']}",
        )
        lemma_token = env_vars["LEMMA_TOKEN"]
        run_id = str(uuid4())
        function_exec = await manager.post(
            f"/sandboxes/{sandbox_id}/apps/function_executor/"
            f"pods/{pod['id']}/functions/{function_name}/execute",
            headers={
                "X-API-Key": manager_api_key,
                "Authorization": f"Bearer {lemma_token}",
            },
            json={
                "run_id": run_id,
                "input_data": {"text": "private executor"},
                "async_job": False,
                "timeout_seconds": 120,
            },
        )
        assert function_exec.status_code == status.HTTP_200_OK, function_exec.text
        function_result = function_exec.json()
        assert function_result["status"] == "completed"
        assert function_result["output_data"]["result"] == "PRIVATE EXECUTOR"
        assert function_result["output_data"]["user_id"] == fixed_test_user["id"]
        assert function_result["output_data"]["base_url"] == docker_base_url

        delete_session = await manager.delete(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}"
        )
        assert delete_session.status_code == status.HTTP_200_OK, delete_session.text
        assert delete_session.json()["deleted"] is True

        delete_sandbox = await manager.delete(f"/sandboxes/{sandbox_id}")
        assert delete_sandbox.status_code == status.HTTP_200_OK, delete_sandbox.text
        assert delete_sandbox.json()["deleted"] is True
