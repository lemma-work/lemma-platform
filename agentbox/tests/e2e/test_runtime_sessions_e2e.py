from __future__ import annotations

from http import HTTPStatus

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


def _ensure_session(agentbox_server, sandbox_id: str, session_id: str) -> None:
    created = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": "https://api.test.invalid"}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text
    session = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}",
        body={
            "env": {"SESSION_MARK": "session-env"},
            "cwd": "/workspace/e2e-session",
        },
        timeout=120,
    )
    assert session.status_code == HTTPStatus.OK, session.text
    assert session.json()["session_id"] == session_id
    assert session.json()["cwd"] == "/workspace/e2e-session"
    assert "SESSION_MARK" in session.json()["env_keys"]


def test_stateful_python_shell_cwd_env_stdin_and_process_lifecycle(
    agentbox_server,
    sandbox_id,
):
    manager = agentbox_server.client
    session_id = "conversation-main"
    _ensure_session(agentbox_server, sandbox_id, session_id)

    heartbeat = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/heartbeat",
    )
    assert heartbeat.status_code == HTTPStatus.OK
    assert heartbeat.json() == {
        "sandbox_id": sandbox_id,
        "session_id": session_id,
        "active": True,
    }

    set_python_state = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
        body={"code": "counter = 41\ncounter", "timeout_seconds": 30},
    )
    assert set_python_state.status_code == HTTPStatus.OK, set_python_state.text
    assert set_python_state.json()["status"] == "completed"
    assert set_python_state.json()["result"] == "41"

    get_python_state = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
        body={"code": "counter += 1\ncounter", "timeout_seconds": 30},
    )
    assert get_python_state.status_code == HTTPStatus.OK, get_python_state.text
    assert get_python_state.json()["result"] == "42", get_python_state.text

    isolated_session_id = "conversation-isolated"
    _ensure_session(agentbox_server, sandbox_id, isolated_session_id)
    isolated = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{isolated_session_id}/python",
        body={"code": "'counter' in globals()", "timeout_seconds": 30},
    )
    assert isolated.status_code == HTTPStatus.OK, isolated.text
    assert isolated.json()["result"] == "False"

    failed_python = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
        body={"code": "raise ValueError('boom')", "timeout_seconds": 30},
    )
    assert failed_python.status_code == HTTPStatus.OK, failed_python.text
    assert failed_python.json()["status"] == "error"
    assert failed_python.json()["error_name"] == "ValueError"
    assert "ValueError: boom" in failed_python.json()["stderr"]

    shell = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": (
                "pwd; printf 'session=%s concurrency=%s\\n' "
                '"$SESSION_MARK" "$AGENTBOX_FUNCTION_MAX_CONCURRENCY"'
            ),
            "timeout": 30,
        },
    )
    assert shell.status_code == HTTPStatus.OK, shell.text
    assert shell.json()["success"] is True
    assert "/workspace/e2e-session" in shell.json()["stdout"]
    assert "session=session-env concurrency=8" in shell.json()["stdout"]

    changed_cwd = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": "mkdir -p nested && cd nested && pwd",
            "timeout": 30,
        },
    )
    assert changed_cwd.status_code == HTTPStatus.OK, changed_cwd.text
    assert changed_cwd.json()["success"] is True
    assert "/workspace/e2e-session/nested" in changed_cwd.json()["stdout"]

    persisted_cwd = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={"cmd": "pwd", "timeout": 30},
    )
    assert persisted_cwd.status_code == HTTPStatus.OK, persisted_cwd.text
    assert persisted_cwd.json()["stdout"].strip() == "/workspace/e2e-session/nested"

    interactive = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": "read line; printf 'stdin:%s\\n' \"$line\"",
            "yield_time_ms": 500,
            "timeout": 30,
        },
    )
    assert interactive.status_code == HTTPStatus.OK, interactive.text
    assert interactive.json()["completed"] is False
    process_id = interactive.json()["process_id"]
    assert process_id

    stdin = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/stdin",
        body={
            "process_id": process_id,
            "chars": "hello-runtime\n",
            "yield_time_ms": 1000,
        },
    )
    assert stdin.status_code == HTTPStatus.OK, stdin.text
    assert stdin.json()["success"] is True
    assert stdin.json()["completed"] is True
    assert "stdin:hello-runtime" in stdin.json()["stdout"]

    tty_check = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": (
                "python -c 'import sys; "
                'print(f"stdin={sys.stdin.isatty()} stdout={sys.stdout.isatty()}")\''
            ),
            "tty": True,
            "yield_time_ms": 1000,
            "timeout": 30,
        },
    )
    assert tty_check.status_code == HTTPStatus.OK, tty_check.text
    assert tty_check.json()["success"] is True
    assert tty_check.json()["completed"] is True
    assert "stdin=True stdout=True" in tty_check.json()["stdout"]

    sleeper = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={"cmd": "sleep 30", "yield_time_ms": 300, "timeout": 60},
    )
    assert sleeper.status_code == HTTPStatus.OK, sleeper.text
    assert sleeper.json()["completed"] is False
    sleeper_process_id = sleeper.json()["process_id"]
    assert sleeper_process_id

    processes = manager.request_json(
        "GET",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/processes",
    )
    assert processes.status_code == HTTPStatus.OK, processes.text
    assert any(
        process["process_id"] == sleeper_process_id
        and process["cmd"] == "sleep 30"
        and process["completed"] is False
        for process in processes.json()["processes"]
    )

    terminated = manager.request_json(
        "DELETE",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/processes/{sleeper_process_id}",
    )
    assert terminated.status_code == HTTPStatus.OK, terminated.text
    assert terminated.json()["success"] is True
    assert terminated.json()["completed"] is True

    delete_session = manager.request_json(
        "DELETE",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}",
    )
    assert delete_session.status_code == HTTPStatus.OK
    assert delete_session.json()["deleted"] is True


def test_exec_command_rejects_removed_tool_fields(agentbox_server, sandbox_id):
    manager = agentbox_server.client
    session_id = "conversation-contract"
    _ensure_session(agentbox_server, sandbox_id, session_id)

    response = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": "echo nope",
            "justification": "tool-only field",
            "login": True,
            "process_id": "proc-old-shape",
            "sandbox_permissions": "require_escalated",
            "shell": "/bin/sh",
        },
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
