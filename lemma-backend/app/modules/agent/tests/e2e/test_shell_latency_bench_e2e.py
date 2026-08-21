"""Latency benchmark: a real agent running real shell commands.

Not a correctness test — it drives real runs against a real model and a real
Docker sandbox so the per-tool-call cost can be attributed from spans. Gated
behind LEMMA_BENCH=1 on top of the usual real-model gates so it never runs in
CI.

Reproduce:

    # 1. span sink
    uv run python otlp_sink.py 4318 /tmp/spans.jsonl &

    # 2. benchmark
    LEMMA_BENCH=1 E2E_LLM_MODE=real LEMMA_RUN_PROVIDER_E2E=1 \
    LEMMA_OPENAI_API_KEY=... \
    LEMMA_OPENAI_BASE_URL=https://api.fireworks.ai/inference/v1/ \
    OBSERVABILITY_ENABLED=true \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    LLM_OTEL_ENABLED=true \
    LLM_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces \
    LLM_OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    uv run pytest app/modules/agent/tests/e2e/test_shell_latency_bench_e2e.py -q -s
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    e2e_real_llm,
    system_lemma_available,
)
from app.modules.test_support.e2e.worker_process import production_worker_process
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
import app.modules.workspace.services.workspace_tool_runtime as workspace_runtime

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.workspace,
    pytest.mark.slow,
    pytest.mark.provider,
    pytest.mark.real_llm,
    pytest.mark.real_sandbox,
    pytest.mark.skipif(
        os.getenv("LEMMA_BENCH") != "1", reason="set LEMMA_BENCH=1 to run the benchmark"
    ),
    pytest.mark.skipif(not e2e_real_llm(), reason="set E2E_LLM_MODE=real"),
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
]

BENCH_MODEL = os.getenv(
    "LEMMA_BENCH_MODEL", "accounts/fireworks/models/deepseek-v4-flash-0731"
)
RESULTS_PATH = os.getenv("LEMMA_BENCH_OUT", "/tmp/bench_runs.jsonl")

# Each command is trivial in the sandbox (a few ms of real work), so whatever a
# call costs beyond that is ours or the model's.
COMMANDS = ["echo AAA", "echo BBB", "echo CCC", "echo DDD", "echo EEE", "echo FFF"]


def _record(row: dict) -> None:
    with open(RESULTS_PATH, "a") as fh:
        fh.write(json.dumps(row) + "\n")


async def _make_agent(client, pod_id: str, *, toolsets: list[str]) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"Bench Agent {uuid4().hex[:8]}",
            "instruction": (
                "You run shell commands exactly as instructed, one per "
                "exec_command call, in order. Never combine commands. After the "
                "last one reply DONE."
            ),
            "toolsets": toolsets,
            "agent_runtime": {
                "profile_id": "system:lemma",
                "model_name": BENCH_MODEL,
            },
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _conversation(client, pod_id: str, agent_name: str) -> str:
    response = await client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent_name, "title": "bench", "type": "CHAT"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _run(client, pod_id, conversation_id, content, *, label, n_expected):
    """Start a run and wait for it to reach a terminal state.

    Deliberately does NOT consume the SSE stream to completion. The run is
    queued server-side before the response headers are sent, so closing the
    stream early cannot cancel it — and the realtime channel proved flaky
    enough locally to abort a benchmark that depended on it. Timing comes from
    spans; this wall clock is only the outer envelope.
    """
    started = time.time()
    async with client.stream(
        "POST",
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": content},
        timeout=300,
    ) as response:
        assert response.status_code == 200, (await response.aread()).decode()

    status_value = None
    deadline = started + 300
    while time.time() < deadline:
        probe = await client.get(f"/pods/{pod_id}/conversations/{conversation_id}")
        assert probe.status_code == 200, probe.text
        status_value = probe.json().get("status")
        if status_value in ("COMPLETED", "WAITING", "FAILED", "CANCELLED"):
            break
        await asyncio.sleep(0.2)
    elapsed = time.time() - started

    messages = await client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    items = messages.json().get("items", []) if messages.status_code == 200 else []
    tool_calls = [i for i in items if i.get("kind") == "TOOL_CALL"]

    row = {
        "label": label,
        "n_expected": n_expected,
        "wall_s": elapsed,
        "conversation_id": str(conversation_id),
        "status": status_value,
        "tool_calls": len(tool_calls),
        "messages": len(items),
        "started_epoch": started,
        "model": BENCH_MODEL,
    }
    _record(row)
    print(
        f"\n[bench] {label} n={n_expected} wall={elapsed:.2f}s "
        f"status={status_value} tool_calls={len(tool_calls)}",
        flush=True,
    )
    return row


async def test_shell_tool_call_cost_without_model(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    db_manager,
    e2e_settings,
):
    """Per-call cost of the shell tool with the model taken out of the loop.

    The agent runs measure model + tool together and give a handful of calls per
    run. This drives the same production tool body directly, so per-call
    overhead is measurable at a useful sample size and without the model's
    variance sitting on top of it.
    """
    del db_manager
    await workspace_runtime.close_workspace_tool_runtimes()

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Bench Direct Pod {uuid4().hex[:8]}",
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
        agent_name="shell_latency_bench",
    )

    iterations = int(os.getenv("LEMMA_BENCH_CALLS", "25"))

    # First call pays container start; recorded separately so it does not skew
    # the steady-state number.
    cold_started = time.time()
    first = await exec_command_internal(
        ctx, ExecCommandRequest(comment="warm", cmd="echo WARM")
    )
    cold_s = time.time() - cold_started
    assert first.success is True, first
    _record({"label": "direct_cold", "wall_s": cold_s, "n_expected": 1})
    print(f"\n[bench] direct cold call: {cold_s:.2f}s", flush=True)

    seq = []
    for i in range(iterations):
        t0 = time.time()
        result = await exec_command_internal(
            ctx, ExecCommandRequest(comment="bench", cmd=f"echo N{i}")
        )
        seq.append(time.time() - t0)
        assert result.success is True, result
    _record(
        {
            "label": "direct_sequential",
            "n_expected": iterations,
            "wall_s": sum(seq),
            "per_call_s": seq,
        }
    )
    seq_sorted = sorted(seq)
    print(
        f"\n[bench] direct sequential n={len(seq)} "
        f"p50={seq_sorted[len(seq) // 2] * 1000:.0f}ms "
        f"min={seq_sorted[0] * 1000:.0f}ms max={seq_sorted[-1] * 1000:.0f}ms "
        f"total={sum(seq):.2f}s",
        flush=True,
    )

    # Same work, issued concurrently: shows whether the per-call tax is a
    # serialized queue or genuinely parallel work.
    conc_started = time.time()
    results = await asyncio.gather(
        *[
            exec_command_internal(
                ctx, ExecCommandRequest(comment="bench", cmd=f"echo C{i}")
            )
            for i in range(iterations)
        ]
    )
    conc_s = time.time() - conc_started
    assert all(r.success for r in results), results
    _record({"label": "direct_concurrent", "n_expected": iterations, "wall_s": conc_s})
    print(
        f"\n[bench] direct concurrent n={iterations} total={conc_s:.2f}s "
        f"(sequential was {sum(seq):.2f}s)",
        flush=True,
    )


@pytest.mark.parametrize("n_commands", [0, 1, 3, 6])
async def test_shell_tool_call_latency(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    db_manager,
    e2e_settings,
    n_commands,
):
    """Drive N sequential shell tool calls and record wall clock per run."""
    del db_manager, fixed_test_user
    await workspace_runtime.close_workspace_tool_runtimes()

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Bench Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod = pod_response.json()

    agent = await _make_agent(
        authenticated_client, pod["id"], toolsets=["WORKSPACE_CLI"]
    )
    conversation_id = await _conversation(
        authenticated_client, pod["id"], agent["name"]
    )

    repeats = int(os.getenv("LEMMA_BENCH_REPEATS", "3"))

    # The agent run is executed by the production worker subprocess, not
    # in-process — without one the run is queued and never picked up.
    async with production_worker_process(
        e2e_settings, log_prefix=f"bench_n{n_commands}"
    ):
        # Warm the sandbox once so container cold start is not charged to the
        # measured runs. Recorded separately under the "warmup" label.
        await _run(
            authenticated_client,
            pod["id"],
            conversation_id,
            "Run exactly this one shell command: echo WARMUP",
            label="warmup",
            n_expected=1,
        )

        for i in range(repeats):
            if n_commands == 0:
                content = (
                    "Do not call any tool at all. Reply with exactly the word DONE."
                )
            else:
                listed = "; ".join(
                    f"{k + 1}) {c}" for k, c in enumerate(COMMANDS[:n_commands])
                )
                content = (
                    f"Run these {n_commands} shell commands, each as its own "
                    f"separate exec_command call, in this order: {listed}. "
                    "Do not combine them into one command. Then reply DONE."
                )
            conversation_id = await _conversation(
                authenticated_client, pod["id"], agent["name"]
            )
            await _run(
                authenticated_client,
                pod["id"],
                conversation_id,
                content,
                label=f"n{n_commands}_rep{i}",
                n_expected=n_commands,
            )
