"""Pay the heavy imports at startup rather than on somebody's first request.

Three of the libraries this backend depends on are large enough that importing
them is visible work, and all three are imported lazily on purpose:
``check_import_budget.py`` and ``test_startup_import_laziness.py`` both exist to
keep them off the module graph of a process that only serves health checks.

Lazy is right, and it moves the cost rather than removing it. Measured against
this tree, the first agent request pays 0.5s to import
``runtime_model_factory`` (800 modules, pulling ``openai``, ``anthropic`` and
``pydantic_ai``), another 0.5s for ``metered_model`` (1,112 more), and about
three seconds the first time anything counts tokens, because ``tiktoken`` has to
fetch and build its vocabulary. On the event loop, in a request, that is a stall
every other request in the process shares.

So the modules stay lazily imported and a startup task imports them anyway --
which is the same trade `ensure_task_lanes_registered` already makes one line
below it in the lifespan.

Two things this is not. It is not a guarantee: ``importlib`` holds a per-module
lock, so a request that loses the race waits for the warm-up rather than
skipping it, and gains nothing. And it is not a correctness dependency -- every
failure here is logged and dropped, because a backend that cannot import
``tiktoken`` should still serve the requests that do not need it.
"""

from __future__ import annotations

import importlib

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: First-party, not the third-party libraries themselves: these are the modules
#: a request actually reaches for, and they pull the heavy trees transitively.
#: Naming them here rather than `openai`/`anthropic`/`pydantic_ai` keeps the
#: list honest when a provider is swapped out -- an import that no longer
#: happens is one that no longer needs warming.
WARM_MODULES: tuple[str, ...] = (
    "app.modules.agent.services.runtime_model_factory",
    "app.modules.usage.infrastructure.metered_model",
)


async def warm_lazy_imports() -> None:
    """Import the heavy modules and build the tokenizer, off the event loop.

    Each step is independent: one failing does not stop the next, because a
    provider SDK that will not import is a reason to lose *its* warm-up, not
    everyone else's.
    """
    for module_name in WARM_MODULES:
        await _warm(module_name, importlib.import_module, module_name)
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
