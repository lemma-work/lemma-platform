from __future__ import annotations

from http import HTTPStatus

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


def _exec(
    manager, sandbox_id: str, session_id: str, cmd: str, *, timeout: int = 60
) -> dict:
    response = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": cmd,
            "timeout": timeout,
            "max_output_tokens": 20000,
        },
        timeout=timeout + 15,
    )
    assert response.status_code == HTTPStatus.OK, response.text
    payload = response.json()
    assert payload["success"] is True, payload
    return payload


def test_runtime_image_contains_lemma_browser_and_webpage_tools(
    agentbox_server,
    sandbox_id,
):
    manager = agentbox_server.client
    session_id = "runtime-tools"

    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text
    session = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}",
        body={"env": {}, "cwd": "/workspace/runtime-tools"},
        timeout=120,
    )
    assert session.status_code == HTTPStatus.OK, session.text

    binaries = _exec(
        manager,
        sandbox_id,
        session_id,
        "which lemma && which agent-browser && which save-webpage && node --version",
    )
    assert "/lemma" in binaries["stdout"]
    assert "agent-browser" in binaries["stdout"]
    assert "save-webpage" in binaries["stdout"]
    assert "v" in binaries["stdout"]

    lemma_help = _exec(
        manager,
        sandbox_id,
        session_id,
        "lemma --help | head -40",
        timeout=30,
    )
    assert "Usage:" in lemma_help["stdout"] or "usage:" in lemma_help["stdout"]

    _exec(
        manager,
        sandbox_id,
        session_id,
        "cat > /workspace/runtime-tools/page.html <<'HTML'\n"
        "<!doctype html><html><head><title>AgentBox Runtime Tools</title></head>"
        "<body><main><h1>AgentBox Runtime Tools</h1>"
        "<a href='https://example.com/runtime-tools'>Runtime tools link</a>"
        "<button>Refresh</button></main></body></html>\n"
        "HTML",
    )

    opened = _exec(
        manager,
        sandbox_id,
        session_id,
        "agent-browser open file:///workspace/runtime-tools/page.html",
        timeout=60,
    )
    assert opened["success"] is True

    snapshot = _exec(
        manager,
        sandbox_id,
        session_id,
        "agent-browser snapshot -i -u",
        timeout=60,
    )
    assert "AgentBox Runtime Tools" in snapshot["stdout"]
    assert "Runtime tools link" in snapshot["stdout"]
    assert "url=https://example.com/runtime-tools" in snapshot["stdout"]

    artifacts = _exec(
        manager,
        sandbox_id,
        session_id,
        "mkdir -p /workspace/runtime-tools/artifacts && "
        "agent-browser --max-output 500000 get html html "
        "> /workspace/runtime-tools/artifacts/page.html && "
        "save-webpage file:///workspace/runtime-tools/page.html "
        "--formats markdown --out /workspace/runtime-tools/artifacts "
        "--name page --wait-ms 250 && "
        "python - <<'PY'\n"
        "from pathlib import Path\n"
        "root = Path('/workspace/runtime-tools/artifacts')\n"
        "for name in ['page.html', 'page.md']:\n"
        "    path = root / name\n"
        "    print(name, path.exists(), path.stat().st_size if path.exists() else 0)\n"
        "    print(path.read_text(errors='replace')[:500] if path.exists() else '')\n"
        "PY",
        timeout=90,
    )
    assert "page.html True" in artifacts["stdout"]
    assert "page.md True" in artifacts["stdout"]
    assert artifacts["stdout"].count("AgentBox Runtime Tools") >= 2
    assert artifacts["stdout"].count("example.com/runtime-tools") >= 2
