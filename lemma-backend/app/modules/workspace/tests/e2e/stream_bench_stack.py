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

WebRTC needs a TURN server, so the stack starts a coturn container with the
bench user in the rollout (`--transports webrtc` in the runner). Its name goes
into the target as `turn_container`, and the runner shapes WebRTC's network
profiles with `tc netem` on it. `STREAM_BENCH_CPUS=2` gives the sandbox two
vCPUs -- and with them native density -- before the browser starts.
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
from pydantic import SecretStr
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.api.controllers import browser_view_stream
from app.modules.workspace.config import workspace_settings
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
TURN_SECRET = "stream-bench-turn"
NETEM_IMAGE = "lemma-bench-netem"


def _docker(*args: str) -> str:
    """One docker command, its stdout; raises with its stderr if it fails."""
    import subprocess

    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])}: {done.stderr.strip()}")
    return done.stdout.strip()


def _start_turn() -> tuple[str, int]:
    """coturn on a free host port, relay-only clients on both ends.

    NET_ADMIN is for the runner's netem sidecar, which shares this
    container's network namespace to shape the media.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    name = f"lemma-bench-turn-{uuid4().hex[:8]}"
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--cap-add",
        "NET_ADMIN",
        "-p",
        f"{port}:3478/udp",
        "-p",
        f"{port}:3478/tcp",
        "coturn/coturn:4.7.0",
        "-n",
        "--log-file=stdout",
        "--listening-port=3478",
        "--realm=lemma.test",
        "--use-auth-secret",
        f"--static-auth-secret={TURN_SECRET}",
        "--no-tls",
        "--no-dtls",
        "--fingerprint",
    )
    return name, port


def _netem_image() -> None:
    import subprocess

    subprocess.run(
        ["docker", "build", "-q", "-t", NETEM_IMAGE, "-"],
        input="FROM alpine:3.20\nRUN apk add --no-cache iproute2-tc\n",
        text=True,
        capture_output=True,
        check=True,
    )


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
    real_bridge = browser_view_stream.bridge

    async def observed_bridge(client, upstream, **kwargs):
        relay_leg.append(
            {
                "offered": upstream.request.headers.get("Sec-WebSocket-Extensions"),
                "negotiated": upstream.response.headers.get("Sec-WebSocket-Extensions"),
            }
        )
        return await real_bridge(client, upstream, **kwargs)

    monkeypatch.setattr(browser_view_stream, "bridge", observed_bridge)

    turn_name, turn_port = _start_turn()
    try:
        _netem_image()
        monkeypatch.setattr(workspace_settings, "turn_provider", "coturn")
        monkeypatch.setattr(
            workspace_settings,
            "coturn_urls",
            # Under both names: the sandbox reaches the host as
            # `host.docker.internal`, and the viewer -- a browser on the host
            # itself -- only as 127.0.0.1. Each end uses the one it can
            # resolve, and both allocate on the same coturn either way.
            [
                f"turn:host.docker.internal:{turn_port}?transport=udp",
                f"turn:host.docker.internal:{turn_port}?transport=tcp",
                f"turn:127.0.0.1:{turn_port}?transport=udp",
                f"turn:127.0.0.1:{turn_port}?transport=tcp",
            ],
        )
        monkeypatch.setattr(workspace_settings, "coturn_secret", SecretStr(TURN_SECRET))
        # `auto`, which is what a person gets: the runner's `?stream=` picks the
        # transport per run, and a WebRTC run that cannot connect falls back.
        monkeypatch.setattr(workspace_settings, "browser_stream", "auto")
        monkeypatch.setattr(
            workspace_settings, "browser_stream_users", [fixed_test_user["id"]]
        )

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

        cpus = os.getenv("STREAM_BENCH_CPUS")
        if cpus:
            # A running container's quota, raised in place, and the display
            # restarted under it: Xvfb's ceiling and Chrome's density are chosen
            # when they start (`lemma-ensure-display`).
            container = _docker(
                "ps",
                "--latest",
                "--filter",
                f"ancestor={os.environ.get('WORKSPACE_IMAGE', '')}",
                "--format",
                "{{.ID}}",
            ) or _docker("ps", "--latest", "--format", "{{.ID}}")
            _docker("update", "--cpus", cpus, container)
            await _run(
                ctx,
                "mkdir -p /tmp/lemma-relay && printf '{\"native_density\": true}' > /tmp/lemma-relay/display.json; "
                "agent-browser close --all >/dev/null 2>&1; pkill -x Xvfb; sleep 1; lemma-ensure-display >/dev/null 2>&1; "
                "cat /tmp/lemma-browser/density.active 2>/dev/null; xrandr --current | head -1",
                f"Give the sandbox {cpus} vCPUs and restart its display under them",
                timeout=180,
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
        machine = (await authenticated_client.get("/machines/me")).json()
        grant = (
            await WorkspaceSandboxService()
            ._get_manager_client()
            .create_port_access(
                None, UUID(machine["id"]), PROBE_PORT, expires_at=expires
            )
        )
        # The grant names the configured API host; the bench reaches this process.
        probe_url = (
            backend_server["host_base_url"]
            + "/public/machine-ports/"
            + grant.url.split("/public/machine-ports/", 1)[1]
        )

        target = {
            "name": "local-docker",
            "api_url": backend_server["host_base_url"],
            "token": fixed_test_user["token"],
            "pod_id": pod["id"],
            "probe_url": probe_url,
            "same_clock": False,
            "sandbox": f"docker, {cpus or 1} vCPU",
            "turn_container": turn_name,
            "netem_image": NETEM_IMAGE,
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
                timeout_seconds=float(
                    os.getenv("STREAM_BENCH_HOLD_SECONDS") or 24 * 3600
                ),
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
    finally:
        _docker("rm", "-f", turn_name)
