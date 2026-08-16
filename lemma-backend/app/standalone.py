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

from anyio import create_task_group, sleep_forever
from fastapi import FastAPI



@dataclass(frozen=True)
class EmbeddedApp:
    """An ASGI sub-application whose lifespan belongs to the local process."""

    path: str
    app: FastAPI


async def _embedded_worker_signal_handler(scope) -> None:
    del scope
    await sleep_forever()


def _prepare_embedded_worker(worker):
    worker.handle_signals = False
    # streaq 6.3 still schedules signal_handler unconditionally. In local
    # standalone mode, uvicorn owns process signals and cancels this task group.
    worker.signal_handler = _embedded_worker_signal_handler
    return worker


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
