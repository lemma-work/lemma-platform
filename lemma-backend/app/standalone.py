"""Reusable single-process (API + embedded worker) assembly.

Lives inside the `app` package (editable when installed as a library) so
lemma-cloud's standalone app can import it reliably. The top-level
standalone_app.py is the OSS run target; lemma_cloud/standalone_app.py is the
cloud one — both call build_standalone_app with their own api_app + worker.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from anyio import TASK_STATUS_IGNORED, create_task_group, sleep_forever
from anyio.abc import TaskStatus
from fastapi import FastAPI



@dataclass(frozen=True)
class EmbeddedApp:
    """An ASGI sub-application whose lifespan belongs to the local process."""

    path: str
    app: FastAPI


async def _embedded_worker_signal_handler(scope) -> None:
    del scope
    await sleep_forever()


class _EmbeddedLanes:
    """Runs every worker lane inside this process.

    Deliberately `list(Lane)` rather than `enabled_lanes()`. The embedded app
    *is* the whole deployment — desktop, the Docker local stack and `make dev`
    all launch `uvicorn local_app:app` and nothing else — so there is no second
    process a lane could be delegated to, and honouring `WORKER_LANES` here
    would let one env var silently leave a queue unconsumed.

    Which is what used to happen without the env var. This embedded exactly one
    Worker, the interactive one, so nothing anywhere consumed `default-bulk`.
    Document processing lives on that lane, so every uploaded file sat at
    PENDING with `processing_attempts` 0 forever — no consumer, no retries, and
    the two recovery crons that would have noticed are on the same lane.
    """

    async def run_async(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED) -> None:
        from app.core.infrastructure.jobs.streaq_runtime import Lane, run_worker_lanes

        await run_worker_lanes(list(Lane), task_status=task_status)


def _prepare_embedded_worker(worker):
    """Silence signals on every lane this process runs, and return its runner.

    uvicorn owns process signals here, and streaq 6.3 schedules
    `signal_handler` regardless of `handle_signals`. Non-primary lanes are
    already silenced where they are constructed; doing it for all of them keeps
    this correct if a lane is ever added.
    """
    from app.core.infrastructure.jobs.streaq_runtime import LANE_WORKERS

    for lane_worker in LANE_WORKERS.values():
        lane_worker.handle_signals = False
        lane_worker.signal_handler = _embedded_worker_signal_handler
    del worker
    return _EmbeddedLanes()


def build_standalone_app(
    api_app: FastAPI,
    worker,
    *,
    embedded_apps: Sequence[EmbeddedApp] = (),
) -> FastAPI:
    """Embed the streaq worker + scheduler into ``api_app`` for single-process
    local dev. The caller passes the composed api_app (OSS or cloud) and the
    matching streaq worker."""
    api_app.state.embedded_worker = True
    api_app.state.embedded_apps = tuple(item.path for item in embedded_apps)
    api_lifespan = api_app.router.lifespan_context

    for item in embedded_apps:
        path = item.path.rstrip("/")
        if not path.startswith("/") or path == "":
            raise ValueError(f"embedded app path must be absolute: {item.path!r}")
        api_app.mount(path, item.app)

    @asynccontextmanager
    async def standalone_lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(api_lifespan(app))

            for item in embedded_apps:
                await stack.enter_async_context(
                    item.app.router.lifespan_context(item.app)
                )

            embedded_worker = _prepare_embedded_worker(worker)
            async with create_task_group() as task_group:
                await task_group.start(embedded_worker.run_async)
                try:
                    yield
                finally:
                    task_group.cancel_scope.cancel()

    api_app.router.lifespan_context = standalone_lifespan

    # No scheduler to start, and no job API to mount. Schedules are fired by the
    # poller inside the embedded worker, so standalone gets scheduling by
    # running a worker rather than by assembling a second control plane and
    # calling it over loopback HTTP.

    return api_app
