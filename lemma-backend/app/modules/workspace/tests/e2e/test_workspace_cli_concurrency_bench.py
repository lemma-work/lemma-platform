"""Benchmark: do parallel agent tool calls block in the backend workspace layer?

Drives the *real* tool entrypoint (`workspace_cli.exec_command_internal`) the way
pydantic-ai does — multiple concurrent calls sharing one BaseAgentContext (hence
one conversation-scoped shell session) — against a local Docker sandbox manager.
Because the manager/runtime are local (no gVisor, no remote network), any
serialization observed here is in OUR backend layer, not the sandbox runtime.

The headline probe is ``N`` parallel ``sleep 1`` calls: with true concurrency the
wall time is ~1s; if the backend serializes them it is ~Ns.

Run:
    WORKSPACE_E2E_IMAGE=lemma-runtime:perf-test \
    uv run pytest -s app/modules/workspace/tests/e2e/test_workspace_cli_concurrency_bench.py
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]

# The semantic regression this probe guards against is a completed command
# waiting for the default 30-second yield window before returning. Shared CI
# runners can add several seconds of Docker and scheduler latency even while
# all calls are genuinely concurrent, so do not use this integration test as a
# tight end-user latency SLO. Returning within two-thirds of the yield window
# still distinguishes the broken path with substantial runner headroom.
DEFAULT_YIELD_WINDOW_SECONDS = 30.0
FAST_YIELD_COMPLETION_LIMIT_SECONDS = DEFAULT_YIELD_WINDOW_SECONDS * (2 / 3)


async def _run(ctx: BaseAgentContext, cmd: str, *, timeout: int | None = 60):
    # timeout=None -> agent default path (yield mode, yield_time_ms=30000).
    started = time.perf_counter()
    request = (
        ExecCommandRequest(cmd=cmd, timeout_seconds=timeout)
        if timeout is not None
        else ExecCommandRequest(cmd=cmd)
    )
    result = await workspace_cli.exec_command_internal(ctx, request)
    return time.perf_counter() - started, result


def _fmt(xs) -> str:
    return "[" + ", ".join(f"{x:.2f}" for x in xs) + "]"


async def test_workspace_cli_parallel_tool_calls_bench(
    authenticated_client,
    configure_workspace_api_url,
    fixed_test_org,
    fixed_test_user,
):
    del configure_workspace_api_url

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"WS CLI Concurrency Bench {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    # One shared context == one conversation == one default shell session, exactly
    # like an agent turn that emits several tool calls at once.
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="concurrency-bench",
        workload_type="agent",
    )

    print("\n========== WORKSPACE CLI CONCURRENCY BENCH ==========")

    # --- COLD PARALLEL BURST: first ops are N concurrent calls hitting a
    #     not-yet-created sandbox. Exercises acquire_creation_lock +
    #     _ensure_running_sandbox/_wait_for_creator contention (the most likely
    #     "gets stuck"/"fails on transient terminal" path when an agent opens a
    #     turn with several parallel tool calls). ---
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    with contextlib.suppress(Exception):
        await WorkspaceSandboxService().stop_sandbox(UUID(fixed_test_user["id"]))

    n = 4
    t0 = time.perf_counter()
    cold = await asyncio.gather(
        *[_run(ctx, "echo cold") for _ in range(n)], return_exceptions=True
    )
    wall = time.perf_counter() - t0
    lat = [c[0] for c in cold if isinstance(c, tuple)]
    oks = sum(1 for c in cold if isinstance(c, tuple) and c[1].success)
    excs = [repr(c)[:120] for c in cold if not isinstance(c, tuple)]
    print(
        f"COLD parallel x{n} (sandbox create under contention): "
        f"wall={wall:6.2f}s  per-call {_fmt(lat)}  ok={oks}/{n}"
    )
    for c in cold:
        if isinstance(c, tuple) and not c[1].success:
            print(f"    FAILURE: {c[1].error}")
    for e in excs:
        print(f"    EXCEPTION: {e}")

    # --- cold start (first call creates the sandbox) ---
    dt0, r0 = await _run(ctx, "echo warm")
    print(f"cold start (first exec):      {dt0:6.2f}s  ok={r0.success}")
    assert r0.success, r0.error

    # --- sequential baselines (warm) ---
    echo_seq = []
    for _ in range(5):
        dt, r = await _run(ctx, "echo hi")
        assert r.success, r.error
        echo_seq.append(dt)
    print(f"sequential echo  x5:          per-call {_fmt(echo_seq)}  mean={sum(echo_seq)/len(echo_seq):.2f}s")

    dt_sleep1, r = await _run(ctx, "sleep 1")
    assert r.success
    print(f"single 'sleep 1':             {dt_sleep1:6.2f}s  (backend overhead = {dt_sleep1 - 1:.2f}s)")

    # --- SERIALIZATION PROBE: N parallel `sleep 1` ---
    for n in (3, 4):
        t0 = time.perf_counter()
        results = await asyncio.gather(*[_run(ctx, "sleep 1") for _ in range(n)])
        wall = time.perf_counter() - t0
        lat = [d for d, _ in results]
        oks = sum(1 for _, r in results if r.success)
        factor = wall / max(lat) if lat else 0
        print(
            f"parallel x{n} 'sleep 1':        wall={wall:6.2f}s  per-call {_fmt(lat)}  "
            f"ok={oks}/{n}  serialization={factor:.2f}x "
            f"({'CONCURRENT' if factor < 1.5 else 'SERIALIZED' if factor > n - 1 else 'PARTIAL'})"
        )
        for _, r in results:
            if not r.success:
                print(f"    FAILURE: {r.error}")

    # --- parallel echo (pure backend overhead under concurrency) ---
    for n in (3, 4):
        t0 = time.perf_counter()
        results = await asyncio.gather(*[_run(ctx, "echo hi") for _ in range(n)])
        wall = time.perf_counter() - t0
        lat = [d for d, _ in results]
        oks = sum(1 for _, r in results if r.success)
        print(f"parallel x{n} echo:             wall={wall:6.2f}s  per-call {_fmt(lat)}  ok={oks}/{n}")

    # --- AGENT DEFAULT PATH: yield mode (no explicit timeout -> yield 30s). A
    #     fast command must still return instantly, not wait the yield window. ---
    dt, r = await _run(ctx, "echo hi", timeout=None)
    print(f"YIELD-mode single echo:       {dt:6.2f}s  completed={r.completed}  (must be ~0s, not ~30s)")
    assert r.success, r.error
    assert r.completed, "fast command unexpectedly returned a background process"
    assert dt < FAST_YIELD_COMPLETION_LIMIT_SECONDS, (
        f"fast command entered the {DEFAULT_YIELD_WINDOW_SECONDS:.0f}s yield window "
        f"and blocked for {dt:.1f}s"
    )

    for n in (3, 4):
        t0 = time.perf_counter()
        results = await asyncio.gather(*[_run(ctx, "echo hi", timeout=None) for _ in range(n)])
        wall = time.perf_counter() - t0
        lat = [d for d, _ in results]
        oks = sum(1 for _, r in results if r.success)
        print(f"YIELD-mode parallel x{n} echo:  wall={wall:6.2f}s  per-call {_fmt(lat)}  ok={oks}/{n}")
        failures = [r.error for _, r in results if not r.success]
        assert oks == n, f"yield-mode parallel commands failed: {failures}"
        assert all(r.completed for _, r in results), (
            "fast parallel commands unexpectedly returned background processes"
        )
        assert wall < FAST_YIELD_COMPLETION_LIMIT_SECONDS, (
            f"yield-mode parallel calls entered the "
            f"{DEFAULT_YIELD_WINDOW_SECONDS:.0f}s yield window and blocked for {wall:.1f}s"
        )

    print("=====================================================\n")
