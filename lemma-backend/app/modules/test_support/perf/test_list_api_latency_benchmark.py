"""API latency benchmark for the pod list endpoints the frontend loads on a
pod open (workflows, agents, datastore tables, datastore files, org pods).

Run explicitly (not part of normal CI):

    PYTHONPATH=. uv run pytest \
      app/modules/test_support/perf/test_list_api_latency_benchmark.py \
      -m e2e -s -p no:randomly

What it measures, per endpoint, across a volume sweep (rows in the pod/org):

  * e2e_ms      -- full request through the FastAPI app via the httpx ASGI
                   client (routing + auth dependency + context build + service
                   + repo + SQL + Pydantic serialization). EXCLUDES network and
                   prod DB hardware, so it is the pure application+DB cost.
  * cold_ms     -- first request for a fresh pod (role-snapshot cache miss:
                   pays the org-member / pod-member / role-load queries).
  * db_ms       -- the list SQL executed directly against the DB with a real
                   authorization Context (SQL exec + ORM row hydration only).
  * serialize   -- e2e_ms (warm) - db_ms ~= Pydantic build + JSON encode.
  * bytes       -- size of the JSON response body at the given volume.
  * EXPLAIN     -- ANALYZE/BUFFERS plan of the list SQL (seq scan? index?).

Numbers are printed as Markdown tables and saved to /tmp/lemma_latency_bench.json.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreFile,
    DatastoreTable,
)
from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.workflow.infrastructure.models import WorkflowModel

# `slow`: this is a multi-volume latency *benchmark* (creates rows, runs EXPLAIN
# ANALYZE, times list endpoints). It only asserts basic correctness (200s) and is
# load-sensitive, so it must not run in the parallel fast gate where contention
# makes it flaky. It still runs in the full/runtime suites.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

# Volume sweep: number of rows in the pod (or pods in the org). Picked to show
# where latency starts to bend. 1000 is the API max page size for datastore.
VOLUMES = [50, 200, 1000]
WARMUP = 3
ITERS = 12
DB_ITERS = 10

RESULTS_PATH = "/tmp/lemma_latency_bench.json"


# --------------------------------------------------------------------------- #
# Timing helpers
# --------------------------------------------------------------------------- #
@dataclass
class Stat:
    samples_ms: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.samples_ms.append(ms)

    @property
    def min(self) -> float:
        return min(self.samples_ms) if self.samples_ms else float("nan")

    @property
    def p50(self) -> float:
        return statistics.median(self.samples_ms) if self.samples_ms else float("nan")

    @property
    def p95(self) -> float:
        if not self.samples_ms:
            return float("nan")
        ordered = sorted(self.samples_ms)
        idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[idx]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples_ms) if self.samples_ms else float("nan")


async def _time_async(fn: Callable[[], Awaitable[Any]], iters: int) -> Stat:
    stat = Stat()
    for _ in range(iters):
        start = time.perf_counter()
        await fn()
        stat.add((time.perf_counter() - start) * 1000.0)
    return stat


# --------------------------------------------------------------------------- #
# Seed payload builders (realistic shapes)
# --------------------------------------------------------------------------- #
def _workflow_graph(n_function_nodes: int = 6) -> tuple[list[dict], list[dict], str]:
    """A moderately rich, valid workflow graph (FORM -> N*FUNCTION -> AGENT -> END)."""
    nodes: list[dict] = [
        {
            "id": "intake",
            "type": "FORM",
            "label": "Intake",
            "config": {
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "merchant": {"type": "string"},
                        "amount": {"type": "number"},
                        "category": {"type": "string"},
                    },
                }
            },
        }
    ]
    edges: list[dict] = []
    prev = "intake"
    for i in range(n_function_nodes):
        node_id = f"fn_{i}"
        nodes.append(
            {
                "id": node_id,
                "type": "FUNCTION",
                "label": f"Step {i}",
                "config": {
                    "function_name": f"some-function-{i}",
                    "input_mapping": {
                        "merchant": {"type": "expression", "value": "intake.merchant"},
                        "amount": {"type": "expression", "value": "intake.amount"},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            }
        )
        edges.append({"id": f"e_{prev}_{node_id}", "source": prev, "target": node_id})
        prev = node_id
    nodes.append(
        {
            "id": "agent",
            "type": "AGENT",
            "label": "Summarize",
            "config": {
                "agent_name": "summarizer",
                "input_mapping": {
                    "context": {"type": "expression", "value": f"{prev}.result"}
                },
            },
        }
    )
    edges.append({"id": f"e_{prev}_agent", "source": prev, "target": "agent"})
    nodes.append({"id": "end", "type": "END", "label": "Done"})
    edges.append({"id": "e_agent_end", "source": "agent", "target": "end"})
    return nodes, edges, "intake"


_INSTRUCTION = (
    "You are a meticulous operations assistant for a finance pod. Follow the "
    "company's expense policy precisely. When given a ticket, classify it, pull "
    "the relevant records from the datastore, and draft a concise summary for a "
    "human approver. Always cite the record ids you used. Never invent figures. "
    "If a field is missing, say so explicitly rather than guessing. Prefer "
    "short, scannable bullet points over prose. " * 4
)


def _json_schema(n_props: int = 8) -> dict:
    return {
        "type": "object",
        "properties": {
            f"field_{i}": {
                "type": "string",
                "description": f"Description for field {i} used by the agent.",
            }
            for i in range(n_props)
        },
        "required": [f"field_{i}" for i in range(n_props // 2)],
    }


def _columns(n: int = 12) -> list[dict]:
    cols = [{"name": "id", "type": "UUID", "auto": True, "system": True}]
    for i in range(n - 1):
        cols.append(
            {
                "name": f"col_{i}",
                "type": "TEXT",
                "description": f"Free-text column number {i} for benchmark seeding.",
                "required": i % 3 == 0,
                "max_length": 512,
            }
        )
    return cols


# --------------------------------------------------------------------------- #
# Seeders (direct DB insert, fast)
# --------------------------------------------------------------------------- #
async def _seed(session: AsyncSession, rows: list[Any]) -> None:
    session.add_all(rows)
    await session.commit()


async def _seed_workflows(session, pod_id: UUID, user_id: UUID, n: int) -> None:
    nodes, edges, entry = _workflow_graph()
    rows = [
        WorkflowModel(
            id=uuid7(),
            pod_id=pod_id,
            user_id=user_id,
            name=f"wf-{i:05d}",
            description="Benchmark workflow",
            nodes=nodes,
            edges=edges,
            entry_node_id=entry,
            start={"type": "MANUAL", "config": None},
            mode="GLOBAL",
            is_active=True,
            visibility="POD",
        )
        for i in range(n)
    ]
    await _seed(session, rows)


async def _seed_agents(session, pod_id: UUID, user_id: UUID, n: int) -> None:
    rows = [
        AgentModel(
            id=uuid7(),
            pod_id=pod_id,
            user_id=user_id,
            name=f"agent-{i:05d}",
            description="Benchmark agent",
            visibility="POD",
            instruction=_INSTRUCTION,
            agent_runtime={"profile_id": "system:lemma"},
            toolsets=["WEB_SEARCH"],
            input_schema=_json_schema(),
            output_schema=_json_schema(),
            agent_metadata={"team": "finance", "tier": "gold"},
        )
        for i in range(n)
    ]
    await _seed(session, rows)


async def _seed_tables(session, pod_id: UUID, user_id: UUID, n: int) -> None:
    cols = _columns()
    rows = [
        DatastoreTable(
            id=uuid7(),
            pod_id=pod_id,
            user_id=user_id,
            table_name=f"table_{i:05d}",
            primary_key_column="id",
            columns=cols,
            config={"description": "benchmark table"},
            enable_rls=True,
            visibility="POD",
        )
        for i in range(n)
    ]
    await _seed(session, rows)


async def _seed_files(session, pod_id: UUID, user_id: UUID, n: int) -> None:
    rows = [
        DatastoreFile(
            id=uuid7(),
            pod_id=pod_id,
            owner_user_id=user_id,
            kind="FILE",
            visibility="POD",
            path=f"/perf_file_{i:05d}.txt",
            name=f"perf_file_{i:05d}.txt",
            description="Benchmark file",
            mime_type="text/plain",
            size_bytes=4096,
            search_enabled=True,
            status="COMPLETED",
            file_metadata={"source": "benchmark", "pages": 3},
        )
        for i in range(n)
    ]
    await _seed(session, rows)


async def _seed_pods(session, org_id: UUID, user_id: UUID, n: int) -> None:
    batch = uuid7().hex[-6:]  # unique per call: pod name is unique per (org, name)
    rows = [
        Pod(
            id=uuid7(),
            user_id=user_id,
            organization_id=org_id,
            name=f"perf-pod-{batch}-{i:05d}",
            description="Benchmark pod",
            config={},
            is_deleted=False,
        )
        for i in range(n)
    ]
    await _seed(session, rows)


# --------------------------------------------------------------------------- #
# Direct list SQL (mirrors each repository's list_visible_by_* statement)
# --------------------------------------------------------------------------- #
def _stmt_workflows(ctx, pod_id, limit):
    actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.WORKFLOW,
        resource_id_col=WorkflowModel.id,
        pod_id_col=WorkflowModel.pod_id,
        owner_user_id_col=WorkflowModel.user_id,
        visibility_col=WorkflowModel.visibility,
    )
    return (
        select(WorkflowModel, actions)
        .where(
            WorkflowModel.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.WORKFLOW_READ),
        )
        .order_by(WorkflowModel.id.desc())
        .limit(limit + 1)
    )


def _stmt_agents(ctx, pod_id, limit):
    actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.AGENT,
        resource_id_col=AgentModel.id,
        pod_id_col=AgentModel.pod_id,
        owner_user_id_col=AgentModel.user_id,
        visibility_col=AgentModel.visibility,
    )
    return (
        select(AgentModel, actions)
        .where(
            AgentModel.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.AGENT_READ),
        )
        .order_by(AgentModel.id.desc())
        .limit(limit + 1)
    )


def _stmt_tables(ctx, pod_id, limit):
    actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id_col=DatastoreTable.id,
        pod_id_col=DatastoreTable.pod_id,
        owner_user_id_col=DatastoreTable.user_id,
        visibility_col=DatastoreTable.visibility,
    )
    return (
        select(DatastoreTable, actions)
        .where(
            DatastoreTable.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.DATASTORE_TABLE_READ),
        )
        .order_by(DatastoreTable.id)
        .limit(limit + 1)
    )


def _stmt_files(ctx, pod_id, limit):
    actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=DatastoreFile.id,
        pod_id_col=DatastoreFile.pod_id,
        owner_user_id_col=DatastoreFile.owner_user_id,
        visibility_col=DatastoreFile.visibility,
        resource_path_col=DatastoreFile.path,
    )
    return (
        select(DatastoreFile, actions)
        .where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like("/%", escape="!"),
            ~DatastoreFile.path.like("/%/%", escape="!"),
            allowed_actions_contains(actions, Permissions.FOLDER_READ),
        )
        .order_by(DatastoreFile.id)
        .limit(limit + 1)
    )


def _stmt_pods(ctx, org_id, limit):
    return (
        select(Pod)
        .where(Pod.organization_id == org_id, Pod.is_deleted.is_(False))
        .order_by(Pod.id)
        .limit(limit + 1)
    )


async def _explain(session: AsyncSession, stmt) -> str:
    try:
        compiled = stmt.compile(
            dialect=session.bind.dialect,
            compile_kwargs={"literal_binds": True},
        )
        res = await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {compiled}"))
        return "\n".join(r[0] for r in res)
    except Exception as exc:  # noqa: BLE001 - EXPLAIN is best-effort
        return f"<explain unavailable: {type(exc).__name__}: {exc}>"


def _explain_summary(plan: str) -> str:
    """One-line digest: scan type + execution time."""
    scan = "seq-scan" if "Seq Scan" in plan else ("index" if "Index" in plan else "?")
    exec_ms = "?"
    for line in plan.splitlines():
        if "Execution Time:" in line:
            exec_ms = line.split("Execution Time:")[1].strip()
    return f"{scan}, exec={exec_ms}"


# --------------------------------------------------------------------------- #
# Endpoint registry
# --------------------------------------------------------------------------- #
@dataclass
class Endpoint:
    key: str
    http: str  # "{scope}" substituted with pod_id or org_id
    seeder: Callable
    stmt: Callable
    scope: str  # "pod" or "org"
    max_limit: int = 100000


ENDPOINTS = [
    Endpoint("workflows", "/pods/{scope}/workflows", _seed_workflows, _stmt_workflows, "pod"),
    Endpoint("agents", "/pods/{scope}/agents", _seed_agents, _stmt_agents, "pod"),
    Endpoint(
        "datastore_tables",
        "/pods/{scope}/datastore/tables",
        _seed_tables,
        _stmt_tables,
        "pod",
        max_limit=1000,
    ),
    Endpoint(
        "datastore_files",
        "/pods/{scope}/datastore/files",
        _seed_files,
        _stmt_files,
        "pod",
        max_limit=1000,
    ),
    Endpoint("org_pods", "/pods/organization/{scope}", _seed_pods, _stmt_pods, "org"),
]


async def _create_pod(client, org_id: str) -> str:
    resp = await client.post(
        "/pods",
        json={
            "name": f"perf-{uuid7().hex[-12:]}",
            "description": "latency benchmark",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_org(client) -> str:
    """Fresh org so org-pods scaling is measured against an exact pod count
    (the requester is provisioned ORG_OWNER -> list_by_org fast path)."""
    resp = await client.post(
        "/organizations", json={"name": f"perf-org-{uuid7().hex[-12:]}"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_list_api_latency_benchmark(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_manager,
):
    org_id = fixed_test_org["id"]
    user_id = UUID(fixed_test_user["id"])
    report: dict[str, Any] = {}

    for ep in ENDPOINTS:
        report[ep.key] = {}
        for volume in VOLUMES:
            limit = min(volume, ep.max_limit)

            # --- choose scope + seed -------------------------------------- #
            if ep.scope == "pod":
                scope_id = await _create_pod(authenticated_client, org_id)
                scope_uuid = UUID(scope_id)
                async with db_manager.session_factory() as s:
                    await ep.seeder(s, scope_uuid, user_id, volume)
            else:  # org -- reuse the requester's org (org is unique per email
                # domain, so a second org for the same user 409s). Pods
                # accumulate across volumes; we report the actual returned count.
                scope_id = org_id
                scope_uuid = UUID(org_id)
                async with db_manager.session_factory() as s:
                    await ep.seeder(s, scope_uuid, user_id, volume)

            url = ep.http.format(scope=scope_id)
            params = {"limit": limit}
            if ep.key == "datastore_files":
                params["directory_path"] = "/"

            async def _call():
                r = await authenticated_client.get(url, params=params)
                assert r.status_code == 200, r.text
                return r

            # --- cold (cache-miss) first hit ------------------------------ #
            t0 = time.perf_counter()
            first = await _call()
            cold_ms = (time.perf_counter() - t0) * 1000.0
            returned = len(first.json()["items"])
            payload_bytes = len(first.content)

            # --- warm e2e ------------------------------------------------- #
            for _ in range(WARMUP):
                await _call()
            e2e = await _time_async(_call, ITERS)

            # --- direct DB SQL ------------------------------------------- #
            async with db_manager.session_factory() as s:
                if ep.scope == "pod":
                    ctx = await AuthorizationDataService(s).build_user_context(
                        user_id=user_id, pod_id=scope_uuid
                    )
                else:
                    ctx = await AuthorizationDataService(s).build_user_context(
                        user_id=user_id, organization_id=scope_uuid
                    )
                stmt = ep.stmt(ctx, scope_uuid, limit)

                async def _db():
                    res = await s.execute(stmt)
                    return res.all()

                # warm the connection/plan
                await _db()
                db = await _time_async(_db, DB_ITERS)
                plan = await _explain(s, stmt)

            report[ep.key][volume] = {
                "limit": limit,
                "returned": returned,
                "payload_bytes": payload_bytes,
                "cold_ms": round(cold_ms, 1),
                "e2e_p50_ms": round(e2e.p50, 1),
                "e2e_p95_ms": round(e2e.p95, 1),
                "e2e_min_ms": round(e2e.min, 1),
                "db_p50_ms": round(db.p50, 1),
                "serialize_p50_ms": round(e2e.p50 - db.p50, 1),
                "explain": _explain_summary(plan),
                "explain_full": plan,
            }
            print(
                f"[{ep.key:18s} vol={volume:5d}] "
                f"cold={cold_ms:7.1f}  e2e_p50={e2e.p50:7.1f}  e2e_p95={e2e.p95:7.1f}  "
                f"db_p50={db.p50:6.1f}  ser={e2e.p50 - db.p50:6.1f}  "
                f"bytes={payload_bytes:8d}  rows={returned:5d}  [{_explain_summary(plan)}]"
            )

    # ----------------------------------------------------------------- #
    # Markdown report
    # ----------------------------------------------------------------- #
    lines: list[str] = []
    lines.append("\n\n================ LATENCY BENCHMARK (ASGI, no network) ================\n")
    for ep in ENDPOINTS:
        lines.append(f"\n### {ep.key}  (`GET {ep.http}`)\n")
        lines.append(
            "| rows | returned | payload KB | cold ms | e2e p50 ms | e2e p95 ms | "
            "db p50 ms | serialize ms | plan |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for volume in VOLUMES:
            r = report[ep.key][volume]
            lines.append(
                f"| {volume} | {r['returned']} | {r['payload_bytes'] / 1024:.1f} | "
                f"{r['cold_ms']} | {r['e2e_p50_ms']} | {r['e2e_p95_ms']} | "
                f"{r['db_p50_ms']} | {r['serialize_p50_ms']} | {r['explain']} |"
            )
    report_md = "\n".join(lines)
    print(report_md)

    # Full EXPLAIN plans at the top volume
    print("\n\n================ EXPLAIN (ANALYZE) @ max volume ================\n")
    for ep in ENDPOINTS:
        top = report[ep.key][VOLUMES[-1]]
        print(f"\n--- {ep.key} (vol={VOLUMES[-1]}, limit={top['limit']}) ---")
        print(top["explain_full"])

    with open(RESULTS_PATH, "w") as f:
        # strip the big explain blobs from the JSON for readability
        slim = {
            k: {
                vol: {kk: vv for kk, vv in data.items() if kk != "explain_full"}
                for vol, data in v.items()
            }
            for k, v in report.items()
        }
        json.dump(slim, f, indent=2)
    print(f"\nSaved JSON results to {RESULTS_PATH}")
