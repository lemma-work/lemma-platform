"""Reusable single-process (API + embedded worker) assembly.

Lives inside the `app` package (editable when installed as a library) so
lemma-cloud's standalone app can import it reliably. The top-level
standalone_app.py is the OSS run target; lemma_cloud/standalone_app.py is the
cloud one -- both call build_standalone_app with their own api_app and module
list.

The embedded app *is* the whole deployment -- desktop, the Docker local stack
and `make dev` all launch `uvicorn local_app:app` and nothing else -- so it runs
every work class (``WORK_CLASSES`` is for split deployments of the standalone
worker). uvicorn owns the process's signals: the lifespan's exit is the stop,
and the work runtime drains within its per-class grace before the API's own
teardown closes what its handlers use.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from app.core.registry.contract import LemmaModule


@dataclass(frozen=True)
class EmbeddedApp:
    """An ASGI sub-application whose lifespan belongs to the local process."""

    path: str
    app: FastAPI


def build_standalone_app(
    api_app: FastAPI,
    modules: Sequence[LemmaModule] | None = None,
    *,
    embedded_apps: Sequence[EmbeddedApp] = (),
) -> FastAPI:
    """Embed the work runtime into ``api_app`` for single-process deployments.

    ``modules`` is the list the worker half registers and runs (the open-source
    list when omitted); it must be the list ``api_app`` was built from.
    """
    if modules is not None and not isinstance(modules, (list, tuple)):
        # The second argument used to be the streaq worker. A caller still
        # passing one (an un-updated lemma-cloud wrapper) gets this rather than
        # a failure deep in module registration.
        raise TypeError(
            "build_standalone_app(api_app, modules): the second argument is the "
            "module list the work runtime registers (the one api_app was built "
            f"from), not a worker; got {type(modules).__name__}"
        )
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
        from app.core.infrastructure.work.process import worker_services
        from app.core.registry.installed import OSS_MODULES

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(api_lifespan(app))
            for item in embedded_apps:
                await stack.enter_async_context(
                    item.app.router.lifespan_context(item.app)
                )
            runtime = await stack.enter_async_context(
                worker_services(
                    OSS_MODULES if modules is None else modules,
                    service_name="lemma-api",
                    embedded=True,
                )
            )
            begin_stop = getattr(runtime, "begin_stop", None)
            if begin_stop is not None:
                stack.callback(_drain_on_signal(begin_stop))
            yield

    api_app.router.lifespan_context = standalone_lifespan
    return api_app


def _drain_on_signal(begin_stop: Callable[[], None]) -> Callable[[], None]:
    """Start the work runtime's drain the moment SIGTERM/SIGINT arrives.

    uvicorn handles the signal by waiting for open connections -- a chat's
    event stream among them -- before it runs the lifespan's exit, which is
    where the runtime stopped. So a turn was never handed back: it finished on
    the dying process, and shutdown waited on the stream for as long as it
    stayed open. uvicorn's own handler still runs after this one. Returns the
    undo, for the lifespan's exit.
    """
    loop = asyncio.get_running_loop()
    previous: dict[int, object] = {}

    def handle(signum: int, frame: object) -> None:
        loop.call_soon_threadsafe(begin_stop)
        prior = previous.get(signum)
        if callable(prior):
            prior(signum, frame)

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[signum] = signal.signal(signum, handle)
        except ValueError:  # not the main thread: no signals to watch here
            return lambda: None

    def restore() -> None:
        for signum, prior in previous.items():
            signal.signal(signum, prior)  # type: ignore[arg-type]

    return restore
