"""A local target for the stream bench: a real API and a real Docker workspace.

Not a test. The name does not start with `test_`, so no lane collects it; it
is run by naming it, and it borrows the e2e fixtures because they are the
one place that already knows how to stand up an API with a live sandbox
behind it:

    cd lemma-backend
    uv run pytest -s app/modules/workspace/tests/e2e/stream_bench_stack.py

It brings the stack up, puts the probe page (`sandbox-images/bench/`) in the
sandbox's browser, and writes what the runner needs to
`out/stream-bench/target.json`. Then it either runs the runner itself, if
`STREAM_BENCH_RUN` holds a command (it is run with `--target <that file>`
appended), or holds the stack open until `out/stream-bench/stop` appears so
the runner can be run by hand, as often as needed:

    node scripts/stream-bench.mjs --target out/stream-bench/target.json

Before it finishes it also answers, from both ends, which WebSocket legs
actually negotiate permessage-deflate, and writes that to `legs.json`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.api.controllers import browser_view_controller
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]

REPO = Path(__file__).resolve().parents[6]
BENCH_SOURCES = REPO / "lemma-backend" / "sandbox-images" / "bench"
OUT = Path(os.getenv("STREAM_BENCH_DIR") or REPO / "out" / "stream-bench")
#: 4848 because the Docker provider publishes only 4848, 4850 and 8080, so only
#: those reach the API's port proxy; 4850 is the relay, 8080 the workspace
#: runtime, and 4848 is agent-browser's own stream port, which nothing on the
#: VNC path listens on. On E2B any port would do.
PROBE_PORT = int(os.getenv("STREAM_BENCH_PROBE_PORT") or 4848)
SANDBOX_DIR = "/tmp/stream-bench"


async def _run(ctx: BaseAgentContext, cmd: str, comment: str, timeout: int = 60):
    result = await exec_command_internal(
        ctx, ExecCommandRequest(cmd=cmd, timeout_seconds=timeout, comment=comment)
    )
    assert result.success, result
    return result


async def _copy_in(ctx: BaseAgentContext, name: str) -> None:
    payload = base64.b64encode((BENCH_SOURCES / name).read_bytes()).decode()
    await _run(
        ctx,
        f"mkdir -p {SANDBOX_DIR} && echo '{payload}' | base64 -d > {SANDBOX_DIR}/{name}",
        f"Copy the stream bench's {name} into the sandbox",
    )


_WEBSOCKIFY_LEG = """
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://127.0.0.1:5901/", open_timeout=10) as ws:
        print(json.dumps({
            "offered": ws.request.headers.get("Sec-WebSocket-Extensions"),
            "negotiated": ws.response.headers.get("Sec-WebSocket-Extensions"),
        }))
asyncio.run(main())
"""


# A bench holds the stack for as long as the runner needs it, which is far
# past the suite's per-test timeout.
@pytest.mark.timeout(0)
async def test_stream_bench_stack(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    monkeypatch,
):
    del configure_workspace_api_url
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stop").unlink(missing_ok=True)

    # The API's own end of the API <-> relay leg, observed rather than changed:
    # the handshake headers of the upstream socket every view opens.
    relay_leg: list[dict[str, str | None]] = []
    real_bridge = browser_view_controller.bridge

    async def observed_bridge(client, upstream, **kwargs):
        relay_leg.append(
            {
                "offered": upstream.request.headers.get("Sec-WebSocket-Extensions"),
                "negotiated": upstream.response.headers.get("Sec-WebSocket-Extensions"),
            }
        )
        return await real_bridge(client, upstream, **kwargs)

    monkeypatch.setattr(browser_view_controller, "bridge", observed_bridge)

    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Stream bench {uuid4().hex[:8]}",
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
        agent_name="stream_bench",
        workload_type="agent",
    )

    async def warmup():
        return await exec_command_internal(
            ctx, ExecCommandRequest(cmd="true", timeout_seconds=180)
        )

    await eventually(
        label="the workspace sandbox starting",
        probe=warmup,
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )

    await _copy_in(ctx, "stream-probe.html")
    await _copy_in(ctx, "stream-probe-server.py")
    await _run(
        ctx,
        f"cd {SANDBOX_DIR} && PY=/opt/lemma-python/bin/python; [ -x $PY ] || PY=python3; "
        f"setsid nohup $PY stream-probe-server.py {PROBE_PORT} > server.log 2>&1 < /dev/null & "
        f"for i in $(seq 50); do curl -sf -o /dev/null http://127.0.0.1:{PROBE_PORT}/now && exit 0; sleep 0.1; done; "
        f"cat {SANDBOX_DIR}/server.log; exit 1",
        "Start the stream bench's probe server",
    )
    opened = await _run(
        ctx,
        f"agent-browser open http://127.0.0.1:{PROBE_PORT}/",
        "Open the probe page in the browser the live view shows",
    )
    assert "Stream probe" in (opened.stdout or ""), opened

    expires = datetime.now(timezone.utc) + timedelta(hours=6)
    grant = (
        await WorkspaceSandboxService()
        ._get_manager_client()
        .create_port_access(
            None, UUID(fixed_test_user["id"]), PROBE_PORT, expires_at=expires
        )
    )
    # The grant names the configured API host; the bench reaches this process.
    probe_url = (
        backend_server["host_base_url"]
        + "/public/workspace-ports/"
        + grant.url.split("/public/workspace-ports/", 1)[1]
    )

    target = {
        "name": "local-docker",
        "api_url": backend_server["host_base_url"],
        "token": fixed_test_user["token"],
        "pod_id": pod["id"],
        "probe_url": probe_url,
        "same_clock": False,
        "sandbox": "docker",
    }
    target_path = OUT / "target.json"
    target_path.write_text(json.dumps(target, indent=2))
    print(f"\nstream bench target written to {target_path}", flush=True)

    command = os.getenv("STREAM_BENCH_RUN")
    if command:
        process = await asyncio.create_subprocess_shell(
            f"{command} --target {target_path}", cwd=REPO
        )
        exit_code = await process.wait()
    else:
        print(f"holding the stack open; touch {OUT / 'stop'} to finish", flush=True)
        stop = OUT / "stop"

        async def stopped() -> bool:
            return stop.exists()

        await eventually(
            label="somebody releasing the bench stack",
            probe=stopped,
            done=bool,
            timeout_seconds=float(os.getenv("STREAM_BENCH_HOLD_SECONDS") or 24 * 3600),
            interval_seconds=1.0,
        )
        exit_code = 0

    websockify = await _run(
        ctx,
        # The bridge is idempotent; started here in case no viewer ever came.
        "start-vnc-bridge && "
        f"/opt/lemma-python/bin/python - <<'EOF'\n{_WEBSOCKIFY_LEG}\nEOF",
        "Ask websockify which extensions it negotiates",
    )
    legs = {
        "api_to_relay": relay_leg[-1] if relay_leg else None,
        "api_to_relay_observed": len(relay_leg),
        "relay_to_websockify": json.loads(
            (websockify.stdout or "").strip().splitlines()[-1]
        ),
    }
    (OUT / "legs.json").write_text(json.dumps(legs, indent=2))
    print(json.dumps(legs, indent=2), flush=True)
    assert exit_code == 0, f"the runner exited {exit_code}"
