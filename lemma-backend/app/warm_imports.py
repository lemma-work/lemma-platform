"""Pay the heavy imports at startup rather than on somebody's first request.

The model clients and the tokenizer are large enough that loading them is
visible work, and they are imported lazily on purpose:
``check_import_budget.py`` and ``test_startup_import_laziness.py`` both exist to
keep them off the module graph of a process that only serves health checks.

Lazy is right, and it moves the cost rather than removing it. Whatever first
reaches ``runtime_model_factory`` imports ``openai`` and ``pydantic_ai``, and
whatever first counts tokens builds ``tiktoken``'s vocabulary -- fetching it
over the network first, unless the image carries it. On the event loop that is
a stall everything else in the process shares: every request in the API, every
turn running beside the first one in a worker.

So the modules stay lazily imported and each process imports them at startup
anyway: the API in its lifespan (``app/app.py``), and a worker that runs agent
turns through the agent module's worker lifespan, before it claims one.

Two things this is not. It is not a guarantee: ``importlib`` holds a per-module
lock, so a request that loses the race waits for the warm-up rather than
skipping it, and gains nothing. And it is not a correctness dependency -- every
failure here is logged and dropped, because a backend that cannot import
``tiktoken`` should still serve the requests that do not need it.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

logger = get_logger(__name__)

#: First-party, not the third-party libraries themselves: these are the modules
#: a request actually reaches for, and they pull the heavy trees transitively.
#: Naming them here rather than `openai`/`pydantic_ai` keeps the list honest
#: when a provider is swapped out -- an import that no longer happens is one
#: that no longer needs warming.
WARM_MODULES: tuple[str, ...] = (
    "app.modules.agent.services.runtime_model_factory",
    "app.modules.usage.infrastructure.metered_model",
)
#: A worker's list: the API's, plus the runner a turn starts from, which no
#: request reaches. The rest of a turn's imports hang off these, so the first
#: turn a fresh worker claims finds them loaded.
TURN_WARM_MODULES: tuple[str, ...] = (
    *WARM_MODULES,
    "app.modules.agent.services.agent_runner_service",
)
#: How long a stopping worker waits for a tokenizer warm-up it cancelled.
_TOKENIZER_SHUTDOWN_WAIT_SECONDS = 2.0


async def warm_lazy_imports() -> None:
    """Import the heavy modules and build the tokenizer, off the event loop.

    Each step is independent: one failing does not stop the next, because a
    provider SDK that will not import is a reason to lose *its* warm-up, not
    everyone else's.
    """
    await warm_modules()
    await warm_tokenizer()


async def warm_modules(modules: Sequence[str] = WARM_MODULES) -> None:
    """The imports alone. The lifespan awaits these before serving.

    Backgrounded, they raced the first requests: ``importlib``'s per-module lock
    makes a request that reaches ``runtime_model_factory`` mid-warm-up wait for
    it *on the event loop thread*, and the loop-stall sampler caught exactly
    that, inside ``from openai import AsyncOpenAI``. About a second in a thread
    before the port opens costs less than a stall every request shares.
    """
    for module_name in modules:
        await _warm(module_name, importlib.import_module, module_name)


@asynccontextmanager
async def warmed_for_turns() -> AsyncIterator[None]:
    """A worker's warm-up, held open for as long as the worker runs.

    The API's split, for the same reasons. The imports are awaited, so the
    worker claims nothing until they are done: a turn claimed mid-import would
    wait for the module lock on the event loop thread, which is the stall this
    removes. The tokenizer builds in the background, since its vocabulary may
    come over the network, and is cancelled if the worker stops first.
    """
    await warm_modules(TURN_WARM_MODULES)
    tokenizer = create_background_task(warm_tokenizer(), name="worker-warm-tokenizer")
    try:
        yield
    finally:
        tokenizer.cancel()
        # `wait` rather than `await`: a warm-up that failed has already been
        # logged by the task's done callback, and must not fail the shutdown.
        # Bounded, because cancelling does not stop the thread the build runs
        # in, and tiktoken's download has no timeout: a stalled fetch would
        # otherwise hold the worker's shutdown open until it is killed.
        await asyncio.wait({tokenizer}, timeout=_TOKENIZER_SHUTDOWN_WAIT_SECONDS)


async def warm_tokenizer() -> None:
    """The tokenizer, which may fetch its vocabulary over the network.

    Stays in the background: a network fetch must not hold the port closed, and
    the tokenizer's ``lru_cache`` is filled in a thread, with no import lock for
    a request to wait on.
    """
    await _warm("tiktoken vocabulary", _build_tokenizer)


def _build_tokenizer() -> None:
    """Populate the `lru_cache` behind the token counter.

    Through the public counter rather than the private `_encoder`, so this warms
    whatever that function decides the tokenizer is.
    """
    from app.modules.agent.services.history_tokens import count_text_tokens

    count_text_tokens("warm")


async def _warm(what: str, fn, *args) -> None:
    """Run one warm-up, absorbing the ways a warm-up is allowed to fail.

    The three named here are the environment being unhelpful rather than the
    code being wrong: an optional dependency that is not installed
    (`ImportError`), a tokenizer download that cannot reach the network or write
    its cache (`OSError` -- `requests.RequestException` is an `OSError`), and a
    vocabulary name the library does not know (`ValueError`).

    Anything else propagates. A provider SDK that raises at import is a bug, and
    `create_background_task` logs an unhandled task exception with its
    traceback; swallowing it here would turn a broken deployment into a silently
    slow one.
    """
    try:
        await run_blocking(fn, *args, limiter="cpu_bound")
    except ImportError, OSError, ValueError:
        logger.warning("api.warm_import.failed", target=what, exc_info=True)
    else:
        logger.debug("api.warm_import.ready", target=what)
