"""A function runtime on loopback, and a dispatcher pointed at it.

The sandbox is the one seam replaced: endpoint acquisition (what the provider
does) answers with this server's URL, and the server speaks the runtime's wire
contract (``contracts/runtime.py``). Everything between -- the start, the pod
gate, the deadline item, completion, the facts -- is the shipping code.

Used in-process (``fake_dispatcher_factory``) and by the worker subprocess
(``function_work_entrypoint``), which reads the server's URL from
``E2E_FAKE_FUNCTION_RUNTIME_URL``.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid7

import httpx
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
)
from app.modules.function.domain.entities import (
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.models import FunctionModel
from app.modules.function.infrastructure.run_repository import FunctionRunRepository
from sandbox_runtime.errors import SandboxUnavailable

REVISION_HASH = f"sha256:{'5' * 64}"
#: What the server does with an invocation, chosen per function name.
#: complete -- 200 with a terminal result; accept -- 202, then nothing (a JOB
#: whose callback never comes); fail -- 200 with a runtime error; hold -- wait
#: until released, then complete.
MODES = ("complete", "accept", "fail", "hold")


@dataclass
class FakeRuntime:
    url: str
    invocations: Counter[str] = field(default_factory=Counter)
    cancels: Counter[str] = field(default_factory=Counter)
    prepares: int = 0
    #: Endpoint acquisition answers "the sandbox is not up" while set.
    unavailable: bool = False
    #: Endpoint acquisition blocks until released while set (a cold start).
    hold_prepare: bool = False
    released: asyncio.Event = field(default_factory=asyncio.Event)
    in_flight: int = 0
    max_in_flight: int = 0
    #: What the runtime's terminal callback does for a run it accepted
    #: asynchronously; None means the callback never comes.
    on_accepted: Callable[[str, str], Awaitable[None]] | None = None

    def release(self) -> None:
        self.released.set()


async def _respond(runtime: FakeRuntime, request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    if run_id.endswith(":cancel"):
        runtime.cancels[run_id.removesuffix(":cancel")] += 1
        return web.json_response({"accepted": True})
    runtime.invocations[run_id] += 1
    body = await request.json()
    mode = str(body["identity"]["function_name"]).split("-", 1)[0]
    runtime.in_flight += 1
    runtime.max_in_flight = max(runtime.max_in_flight, runtime.in_flight)
    try:
        if mode == "hold":
            await runtime.released.wait()
        if request.headers.get("Prefer") == "respond-async":
            # Queued runs are always asynchronous: the runtime acknowledges,
            # and reports the end on its callback -- which never comes for
            # "accept".
            if mode != "accept" and runtime.on_accepted is not None:
                asyncio.get_running_loop().create_task(
                    runtime.on_accepted(run_id, mode)
                )
            return web.json_response({"accepted": True, "run_id": run_id}, status=202)
        if mode == "fail":
            return web.json_response(
                {
                    "status": "failed",
                    "error": {"name": "ValueError", "message": "boom"},
                    "stdout": "",
                    "stderr": "",
                }
            )
        return web.json_response(
            {
                "status": "completed",
                "output_data": {"echo": body["input"]},
                "stdout": "",
                "stderr": "",
            }
        )
    finally:
        runtime.in_flight -= 1


async def _prepare(runtime: FakeRuntime, request: web.Request) -> web.Response:
    runtime.prepares += 1
    if runtime.unavailable:
        return web.Response(status=503)
    if runtime.hold_prepare:
        await runtime.released.wait()
    return web.json_response({"ok": True})


@asynccontextmanager
async def fake_runtime() -> AsyncIterator[FakeRuntime]:
    holder: list[FakeRuntime] = []

    async def respond(request: web.Request) -> web.Response:
        return await _respond(holder[0], request)

    async def prepare(request: web.Request) -> web.Response:
        return await _prepare(holder[0], request)

    app = web.Application()
    app.router.add_post("/functions/{function_id}/runs/{run_id}", respond)
    app.router.add_get("/prepare", prepare)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    holder.append(FakeRuntime(url=f"http://127.0.0.1:{port}/"))
    try:
        yield holder[0]
    finally:
        holder[0].release()
        await runner.cleanup()


class LoopbackDispatcher(FunctionDispatcher):
    """The shipping dispatcher with the provider's endpoint lease replaced."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        url: str,
        cap: int,
        client: httpx.AsyncClient,
    ) -> None:
        self._client = client
        super().__init__(
            uow_factory=uow_factory,
            sandbox_client_factory=_no_sandbox_client,
            token_minter=_mint,
            token_cache=FunctionSessionTokenCache(),
            endpoint_cache=FunctionRuntimeEndpointCache(),
            runtime_http_client_factory=lambda: self._client,
            organization_resolver=_no_organization,
            delegated_tokens_enabled=False,
            max_running_per_pod=cap,
        )
        self._url = url

    async def _runtime_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        execution_ends_at: datetime,
        wait_until: datetime | None = None,
    ) -> FunctionRuntimeEndpoint:
        del execution_ends_at, wait_until
        response = await self._client.get(f"{self._url}prepare", timeout=None)
        if response.status_code == 503:
            raise SandboxUnavailable("the pod's function sandbox is still starting")
        return FunctionRuntimeEndpoint(
            url=self._url,
            request_headers=(),
            allocation_id=uuid4(),
            allocation_epoch=1,
            profile_digest=f"sha256:{'2' * 64}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def _best_effort_cancel(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        endpoint: FunctionRuntimeEndpoint | None = None,
    ) -> None:
        await self._invoker.cancel(
            dispatch,
            endpoint=endpoint
            or await self._runtime_endpoint(
                dispatch, execution_ends_at=datetime.now(timezone.utc)
            ),
        )


def _no_sandbox_client():  # pragma: no cover - the endpoint seam never asks
    raise AssertionError("the loopback dispatcher never provisions a sandbox")


async def _mint(**_kwargs: object) -> FunctionSessionToken:
    return FunctionSessionToken(
        value="loopback-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )


async def _no_organization(_pod_id: UUID | None) -> str | None:
    return None


def loopback_dispatcher_factory(url: str, *, cap: int = 32):
    # One client for every job, as the deployment shares one: a client per job
    # built an SSL context each time, which was a visible share of the CPU in
    # the workflow load test -- the test double's cost, not the product's.
    client = httpx.AsyncClient()

    def build(uow_factory: UnitOfWorkFactory) -> FunctionDispatcher:
        return LoopbackDispatcher(uow_factory, url=url, cap=cap, client=client)

    return build


async def seed_function(
    session: AsyncSession, *, pod_id: UUID, user_id: UUID, mode: str, job: bool = False
) -> FunctionModel:
    """A READY function whose name tells the loopback runtime what to do."""
    assert mode in MODES
    function = FunctionModel(
        id=uuid7(),
        pod_id=pod_id,
        user_id=user_id,
        name=f"{mode}-{uuid4().hex[:10]}",
        input_schema={},
        output_schema={},
        type=FunctionType.JOB if job else FunctionType.API,
        status=FunctionStatus.READY,
        visibility="POD",
        revision_hash=REVISION_HASH,
    )
    session.add(function)
    await session.flush()
    return function


async def create_queued_run(
    uow,
    *,
    function_id: UUID,
    user_id: UUID,
    start_window: timedelta = timedelta(minutes=30),
) -> FunctionRunEntity:
    """A PENDING asynchronous run, written the way every creator writes one."""
    run_id = uuid7()
    return await FunctionRunRepository(uow).create_run(
        FunctionRunEntity(
            id=run_id,
            function_id=function_id,
            revision_hash=REVISION_HASH,
            user_id=user_id,
            input_data={"n": str(run_id)},
            status=FunctionRunStatus.PENDING,
            deadline_at=datetime.now(timezone.utc) + start_window,
            job_id=f"function:{run_id}",
        )
    )


def callback_settler(session_maker):
    """The runtime's terminal callback, as the gateway applies it."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.function.infrastructure.execution_repository import (
        FunctionExecutionRepository,
    )
    from app.modules.function.infrastructure.models import FunctionRunModel

    async def settle(run_id: str, mode: str) -> None:
        async with session_maker() as session:
            run = await session.get(FunctionRunModel, UUID(run_id))
            function = await session.get(FunctionModel, run.function_id)
            uow = SqlAlchemyUnitOfWork(session)
            await FunctionExecutionRepository(uow).complete(
                SimpleNamespace(run_id=run.id, pod_id=function.pod_id),  # type: ignore[arg-type]
                completed=mode != "fail",
                output_data={"echo": run.input_data} if mode != "fail" else None,
                error="ValueError: boom" if mode == "fail" else None,
                logs=None,
            )
            await uow.commit()

    return settle
