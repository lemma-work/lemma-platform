"""Tests for the I/O hygiene gate (scripts/check_io_hygiene.py).

It had none. Its sibling `check_session_scope.py` has twenty-one, and the
difference showed: this gate shipped with an offload it did not recognise and a
timeout rule that a `timeout=None` walked straight through. Both are the same
failure mode -- a gate that is green because it is not looking.

So these tests are as much about the *negative* space as the positive: what the
checker must stay quiet about matters, because a gate that cries wolf gets
baselined into irrelevance, which is worse than no gate at all.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest


def _load_checker():
    script = Path(__file__).resolve().parents[4] / "scripts" / "check_io_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_io_hygiene", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the script uses `from __future__ import
    # annotations`, so @dataclass resolves field types through sys.modules at
    # class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(source: str, *, path: str = "sample.py") -> list:
    checker_module = _load_checker()
    tree = ast.parse(source)
    # Mirrors `collect()`: the alias pass runs first so the checker can tell
    # `httpx.AsyncClient` from every other `AsyncClient` in the dependency tree.
    aliases = checker_module._ImportAliases()
    aliases.visit(tree)
    checker = checker_module.IoHygieneChecker(path, aliases)
    checker.visit(tree)
    return checker.violations


def _rules(source: str, **kwargs) -> list[str]:
    return [v.rule for v in _run(source, **kwargs)]


# --- unbounded thread offloads ----------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "await asyncio.to_thread(work)",
        "await anyio.to_thread.run_sync(work)",
        "await to_thread.run_sync(work)",
    ],
)
def test_an_offload_with_no_limiter_is_reported(call: str) -> None:
    assert _rules(f"async def f():\n    {call}\n") == ["unlimited-offload"]


def test_run_in_executor_is_an_offload_too() -> None:
    """The two gates disagreed about this call, so it passed one of them.

    `check_session_scope` has always counted `run_in_executor` as a thread
    offload; this gate did not. A `loop.run_in_executor(None, ...)` hands work
    to the *default* executor, which is unbounded -- the exact thing
    `unlimited-offload` exists to catch.
    """
    source = "async def f():\n    await loop.run_in_executor(None, work)\n"
    assert _rules(source) == ["unlimited-offload"]


def test_the_offload_helper_itself_is_exempt() -> None:
    """`run_blocking` is the sanctioned wrapper, so its own module may offload."""
    checker_module = _load_checker()
    source = "async def run_blocking(fn):\n    await anyio.to_thread.run_sync(fn)\n"
    assert _rules(source, path=checker_module.OFFLOAD_OWNER) == []


def test_the_limited_wrapper_is_not_reported() -> None:
    assert _rules("async def f():\n    await run_blocking(work)\n") == []


# --- aiohttp timeouts --------------------------------------------------------


def test_a_session_with_no_timeout_is_reported() -> None:
    assert _rules("def f():\n    return aiohttp.ClientSession()\n") == [
        "untimed-aiohttp-session"
    ]


def test_a_session_whose_timeout_is_none_is_reported() -> None:
    """`timeout=None` disables aiohttp's timeout entirely.

    The rule used to check only that the keyword was *present*, so this passed
    -- and a request to a hung upstream would then hang for the life of the
    process, which is the failure the rule exists to prevent.
    """
    source = "def f():\n    return aiohttp.ClientSession(timeout=None)\n"
    assert _rules(source) == ["disabled-aiohttp-timeout"]


def test_a_real_timeout_is_accepted() -> None:
    source = (
        "def f():\n"
        "    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))\n"
    )
    assert _rules(source) == []


def test_a_timeout_from_a_variable_is_accepted() -> None:
    """Not everything is a literal, and the gate must not demand one.

    A configured timeout passed by name is the normal shape in this codebase.
    Only a literal `None` is provably wrong; anything else is beyond what an
    AST pass can honestly judge, and guessing would produce the false positives
    that get a gate switched off.
    """
    source = "def f():\n    return aiohttp.ClientSession(timeout=self._timeout)\n"
    assert _rules(source) == []


# --- the baseline ------------------------------------------------------------


def test_the_baseline_counts_occurrences_rather_than_keys() -> None:
    """A baselined function must not get a free slot for a second violation.

    Keys deliberately carry no line number, so an edit above a violation does
    not churn the file. Storing them as a *set* meant a second identical call
    in the same function matched the same key and was accepted in silence. On
    the sibling gate that was hiding two real ones, in a webhook handler that
    publishes to Redis three times.
    """
    checker_module = _load_checker()
    violation = checker_module.Violation(
        path="a.py", line=1, scope="f", rule="unlimited-offload", detail="x"
    )
    twice = Counter([violation.key(), violation.key()])

    assert twice[violation.key()] == 2, "two occurrences must not collapse into one"

    baseline = {violation.key(): 1}
    seen: Counter[str] = Counter()
    reported = []
    for _ in range(2):
        seen[violation.key()] += 1
        if seen[violation.key()] > baseline.get(violation.key(), 0):
            reported.append(violation)
    assert len(reported) == 1, "the second occurrence must be reported as new"


def test_an_old_list_baseline_still_loads() -> None:
    """A checkout that has not regenerated its baseline must keep working."""
    import json
    import tempfile

    checker_module = _load_checker()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"violations": ["a::b::c::d", "a::b::c::d", "e::f::g::h"]}, handle)
        path = Path(handle.name)

    loaded = checker_module._load_baseline(path)
    assert loaded == {"a::b::c::d": 2, "e::f::g::h": 1}


# --- process-lifetime construction ------------------------------------------


# The shape that got away. A DI builder reached from an async request handler
# built an object store in a plain `def`; the constructor resolves credentials
# over the network, so the loop stopped for 350-500ms on every call. The other
# rules read `async def` bodies and never saw it.
_THE_ORIGINAL_BUG = """
from obstore.store import GCSStore

def _gcs_store(bucket):
    return GCSStore(bucket=bucket, prefix=None)
"""


def test_the_original_bug_is_caught() -> None:
    assert _rules(_THE_ORIGINAL_BUG) == ["process-lifetime-construction"]


def test_the_owning_module_may_construct_it() -> None:
    """`object_storage.py` memoizes with a keyed dict, which no decorator shows."""
    assert _rules(_THE_ORIGINAL_BUG, path="app/core/object_storage.py") == []


def test_a_memoized_factory_is_the_fix_not_the_offence() -> None:
    source = """
from functools import lru_cache
from obstore.store import GCSStore

@lru_cache(maxsize=1)
def store():
    return GCSStore(bucket="b")
"""
    assert _rules(source) == []


def test_module_scope_construction_is_fine() -> None:
    """Built once at import, which is what this rule is asking people to do."""
    source = 'from obstore.store import GCSStore\n\nSTORE = GCSStore(bucket="b")\n'
    assert _rules(source) == []


def test_a_client_in_a_sync_constructor_is_caught() -> None:
    source = """
import httpx

class Service:
    def __init__(self, url):
        self._client = httpx.AsyncClient(base_url=url)
"""
    assert _rules(source) == ["process-lifetime-construction"]


def test_someone_elses_asyncclient_is_not_our_business() -> None:
    """`AsyncClient` is exported by openai, composio and kubernetes too.

    Matching the bare name would flag all of them. The rule resolves the name
    through the importing module's own aliases so it stays specific.
    """
    source = """
from openai import AsyncClient

def build():
    return AsyncClient(api_key="k")
"""
    assert _rules(source) == []


def test_an_aliased_import_is_still_resolved() -> None:
    source = """
import httpx as hx

def build():
    return hx.AsyncClient()
"""
    assert _rules(source) == ["process-lifetime-construction"]


def test_a_closure_is_judged_on_its_own_caching() -> None:
    """A cached factory returning an uncached closure is still per-call.

    This is exactly how the app storage factory was written: `_get_app_storage_
    factory()` ran once, but the `build` it returned ran per request and built a
    store every time.
    """
    source = """
from functools import lru_cache
from obstore.store import GCSStore

@lru_cache(maxsize=1)
def factory():
    def build(app_id):
        return GCSStore(bucket="b", prefix=f"apps/{app_id}")
    return build
"""
    assert _rules(source) == ["process-lifetime-construction"]
