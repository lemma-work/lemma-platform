"""The other half of `test_startup_import_laziness`.

That file pins what a process serving a health check must *not* import. This one
pins that the cost was moved rather than forgotten: the same three libraries are
imported at startup by a background task, off the event loop, so the first agent
request does not pay ~1s of import and ~3s of tokenizer build inside a request
every other request in the process shares.

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

from app.warm_imports import WARM_MODULES, _warm

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]

_PROBE = """
import importlib, json, sys
for name in {names!r}:
    importlib.import_module(name)
print(json.dumps(sorted({{name.split(".")[0] for name in sys.modules}})))
"""


@pytest.mark.parametrize("module_name", WARM_MODULES)
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

    assert {"openai", "anthropic", "pydantic_ai"} <= loaded


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
