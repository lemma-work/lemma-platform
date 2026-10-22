"""The other half of `test_startup_import_laziness`.

That file pins what a process serving a health check must *not* import. This one
pins that the cost was moved rather than forgotten: the model clients and the
tokenizer are loaded at startup, off the event loop -- by the API, and by a
worker before it claims its first agent turn -- so neither the first request nor
the first turn pays for them inside a stall everything else in the process
shares.

The pair matters more than either half. Warming the wrong module names is
silent -- a rename leaves a warm-up that logs a failure at every boot and a
request that still pays the import -- so the names are checked against the tree
rather than trusted.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.warm_imports import TURN_WARM_MODULES, WARM_MODULES, _warm

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]

_PROBE = """
import importlib, json, sys
for name in {names!r}:
    importlib.import_module(name)
print(json.dumps(sorted({{name.split(".")[0] for name in sys.modules}})))
"""

# Boots the agent module's worker lifespans the way `worker_services` does, three
# times over, and reports what each boot left loaded. The order matters: a boot
# that warms cannot be followed by one that proves it did not.
_WORKER_PROBE = """
import asyncio, json, sys
from contextlib import AsyncExitStack

from app.core.config import settings
from app.core.infrastructure.work.config import work_settings
from app.core.infrastructure.work.process import WorkerContext, register_worker_modules
from app.core.registry.assembly import enter_worker_lifespans
from app.modules.agent.module import module as agent

register_worker_modules([agent])
WATCHED = {watched!r}


async def boot(environment, work_classes):
    settings.environment = environment
    work_settings.work_classes = work_classes
    async with AsyncExitStack() as stack:
        await enter_worker_lifespans(stack, [agent], WorkerContext(None))
        tokenizer = [
            task for task in asyncio.all_tasks()
            if task.get_name() == "worker-warm-tokenizer"
        ]
        if tokenizer:
            await asyncio.wait(tokenizer)
        return {{
            "loaded": sorted(name for name in WATCHED if name in sys.modules),
            "tokenizer": bool(tokenizer),
        }}


async def main():
    print(json.dumps({{
        "local": await boot("local", ""),
        "ingestion_only": await boot("development", "ingestion"),
        "runs_turns": await boot("development", ""),
    }}))


asyncio.run(main())
"""


@pytest.mark.parametrize("module_name", TURN_WARM_MODULES)
def test_every_warmed_name_still_exists(module_name: str) -> None:
    """A renamed module would leave the warm-up warming nothing, quietly.

    `find_spec` rather than an import: this asks whether the name resolves,
    which is the thing that rots, without paying the second of import the
    warm-up exists to move.
    """
    assert importlib.util.find_spec(module_name) is not None


def test_warming_is_what_pulls_the_heavy_libraries() -> None:
    """The modules named are the ones that bring in the expensive trees.

    If this fails while `test_startup_import_laziness` still passes, the cost
    has not moved to startup -- it has moved to whichever request first needs
    it, which is the situation the warm-up was added to end.

    A subprocess for the same reason that file gives: `app` is already imported
    by the time any test runs, and purging trees this large from `sys.modules`
    mid-process is not a faithful fresh interpreter.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE.format(names=list(WARM_MODULES))],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    loaded = set(json.loads(completed.stdout.strip().splitlines()[-1]))

    # Not `anthropic`: only an Anthropic profile needs that SDK, and the model
    # factory imports it when it builds one.
    assert {"openai", "pydantic_ai"} <= loaded


def test_a_worker_that_runs_turns_loads_them_before_it_claims_one() -> None:
    """The first turn on a fresh worker finds a turn's imports already paid.

    Left to that turn, they ran on the event loop, stalling every other turn on
    the worker while they did. The two skips are pinned too: local mode warms
    nothing, as the API's lifespan does not, and neither does a worker whose
    classes hold no turns -- an ingestion-only one has no use for model clients.

    A subprocess for the same reason as above, and one boot after another in it
    so the interpreter is paid for once.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER_PROBE.format(watched=list(TURN_WARM_MODULES))],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    boots = json.loads(completed.stdout.strip().splitlines()[-1])

    assert boots["local"] == {"loaded": [], "tokenizer": False}
    assert boots["ingestion_only"] == {"loaded": [], "tokenizer": False}
    assert boots["runs_turns"] == {
        "loaded": sorted(TURN_WARM_MODULES),
        "tokenizer": True,
    }


async def test_an_unreachable_tokenizer_does_not_cost_the_other_warm_ups() -> None:
    """The environment being unhelpful is absorbed, one warm-up at a time.

    `tiktoken` fetches its vocabulary over the network on first use, so an
    offline or egress-restricted deployment raises here. That costs its own
    warm-up and nothing else -- the backend still serves every request that does
    not need a tokenizer.
    """
    done: list[str] = []

    def _offline() -> None:
        raise OSError("no route to host")

    await _warm("tiktoken vocabulary", _offline)
    await _warm("succeeds", lambda: done.append("ran"))

    assert done == ["ran"]


async def test_a_provider_sdk_that_is_broken_at_import_is_not_swallowed() -> None:
    """The other direction, which is why the catch is not `Exception`.

    A module that raises at import is a bug in this deployment, and a warm-up
    that quietly absorbed it would turn a broken install into a permanently slow
    one with nothing in the logs but a warning nobody reads.
    `create_background_task` logs an unhandled task exception with its
    traceback, which is where this belongs.
    """

    def _bug() -> None:
        raise RuntimeError("provider SDK exploded at import")

    with pytest.raises(RuntimeError):
        await _warm("explodes", _bug)
