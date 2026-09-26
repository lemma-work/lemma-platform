"""Tests for the memory-hazard gate (scripts/check_memory_hazards.py)."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_checker():
    script = Path(__file__).resolve().parents[4] / "scripts" / "check_memory_hazards.py"
    spec = importlib.util.spec_from_file_location("check_memory_hazards", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(source: str, *, path: str = "app/modules/x/sample.py") -> list:
    checker = _load_checker().MemoryHazardChecker(path, source)
    return checker.check(ast.parse(source))


def _found(source: str, **kwargs) -> list[tuple[str, str]]:
    return [(v.rule, v.detail) for v in _run(source, **kwargs)]


# --- MH001 ---------------------------------------------------------------


def test_engine_without_query_cache_size_fires() -> None:
    assert _found("e = create_async_engine(url)\n") == [
        ("MH001", "create_async_engine")
    ]
    assert _found("e = sa.create_engine(url)\n") == [("MH001", "create_engine")]


def test_engine_with_query_cache_size_or_kwargs_is_quiet() -> None:
    assert _found("e = create_async_engine(url, query_cache_size=50)\n") == []
    assert _found("e = create_async_engine(url, **opts)\n") == []


# --- MH002 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("decorator", "detail"),
    [
        ("@cache", "cache"),
        ("@functools.cache", "cache"),
        ("@lru_cache(maxsize=None)", "lru_cache(maxsize=None)"),
        ("@functools.lru_cache(None)", "lru_cache(maxsize=None)"),
    ],
)
def test_unbounded_cache_fires(decorator: str, detail: str) -> None:
    assert _found(f"{decorator}\ndef f(x):\n    return x\n") == [("MH002", detail)]


def test_lru_cache_on_a_method_fires() -> None:
    source = (
        "class A:\n    @lru_cache(maxsize=8)\n    def f(self, x):\n        return x\n"
    )
    assert _found(source) == [("MH002", "method-lru_cache")]
    assert _run(source)[0].scope == "A.f"


def test_bounded_lru_cache_on_a_function_or_staticmethod_is_quiet() -> None:
    assert _found("@lru_cache(maxsize=8)\ndef f(x):\n    return x\n") == []
    assert _found("@lru_cache\ndef f():\n    return 1\n") == []
    assert (
        _found(
            "class A:\n    @staticmethod\n    @lru_cache(maxsize=8)\n    def f(x):\n        return x\n"
        )
        == []
    )


# --- MH003 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "mutation"),
    [
        ("{}", "_C[k] = v"),
        ("dict()", "_C.setdefault(k, v)"),
        ("set()", "_C.add(k)"),
        ("[]", "_C.append(k)"),
        ("OrderedDict()", "_C.update({k: v})"),
        ("defaultdict(list)", "_C[k].append(v)"),
        ("list()", "_C.extend([k])"),
    ],
)
def test_module_container_mutated_in_a_function_fires(
    value: str, mutation: str
) -> None:
    source = f"_C = {value}\ndef f(k, v):\n    {mutation}\n"
    assert _found(source) == [("MH003", "_C")]


def test_annotated_module_container_fires() -> None:
    assert _found("_C: dict[str, int] = {}\ndef f(k):\n    _C[k] = 1\n") == [
        ("MH003", "_C")
    ]


def test_class_level_container_mutated_via_self_or_cls_fires() -> None:
    source = (
        "class S:\n    _seen = set()\n    def f(self, k):\n        self._seen.add(k)\n"
    )
    assert _found(source) == [("MH003", "_seen")]
    assert _run(source)[0].scope == "S"


@pytest.mark.parametrize(
    "source",
    [
        # never mutated: a constant table
        "_C = {'a': 1}\ndef f():\n    return _C['a']\n",
        # bounded containers
        "_C = BoundedDict(maxsize=10)\ndef f(k):\n    _C[k] = 1\n",
        "_C = BoundedSet(maxsize=10)\ndef f(k):\n    _C.add(k)\n",
        "_C = weakref.WeakValueDictionary()\ndef f(k, v):\n    _C[k] = v\n",
        "_C = deque(maxlen=10)\ndef f(k):\n    _C.append(k)\n",
        # explicit marker
        "_C = {}  # memory: bounded one entry per enum member\ndef f(k):\n    _C[k] = 1\n",
        # mutated only at import time
        "_C = {}\n_C['a'] = 1\n",
        # local shadow
        "_C = {}\ndef f(k):\n    _C = {}\n    _C[k] = 1\n",
        # instance attribute shadows the class attribute
        "class S:\n    _c = {}\n    def __init__(self):\n        self._c = {}\n    def f(self, k):\n        self._c[k] = 1\n",
    ],
)
def test_container_negatives_are_quiet(source: str) -> None:
    assert _found(source) == []


def test_global_declaration_does_not_count_as_a_shadow() -> None:
    source = "_C = []\ndef f(k):\n    global _C\n    _C = _C + []\n    _C.append(k)\n"
    assert _found(source) == [("MH003", "_C")]


# --- MH004 ---------------------------------------------------------------


def test_httpx_client_outside_with_fires() -> None:
    assert _found("async def f():\n    c = httpx.AsyncClient()\n") == [
        ("MH004", "httpx.AsyncClient")
    ]
    assert _found("from httpx import Client\ndef f():\n    c = Client()\n") == [
        ("MH004", "httpx.Client")
    ]


@pytest.mark.parametrize(
    ("source", "path"),
    [
        (
            "async def f():\n    async with httpx.AsyncClient() as c:\n        pass\n",
            "app/x.py",
        ),
        (
            "def f():\n    with httpx.Client(timeout=1) as c:\n        pass\n",
            "app/x.py",
        ),
        ("c = httpx.AsyncClient()  # memory: shared client\n", "app/x.py"),
        ("c = httpx.AsyncClient()\n", "app/core/net/pool.py"),
        # a memoized factory builds the process singleton, once
        (
            "@lru_cache(maxsize=1)\ndef f():\n    return httpx.AsyncClient()\n",
            "app/x.py",
        ),
        # a non-httpx Client is not our business
        ("c = Client()\n", "app/x.py"),
    ],
)
def test_httpx_negatives_are_quiet(source: str, path: str) -> None:
    assert _found(source, path=path) == []


# --- MH005 ---------------------------------------------------------------


def test_whole_body_reads_fire() -> None:
    assert _found("async def f(request):\n    b = await request.body()\n") == [
        ("MH005", "request.body()")
    ]
    assert _found("async def f(file: UploadFile):\n    b = await file.read()\n") == [
        ("MH005", "file.read()")
    ]


@pytest.mark.parametrize(
    "source",
    [
        "async def f(file: UploadFile):\n    b = await file.read(1024)\n",
        "async def f(stream):\n    b = await stream.read()\n",
        "async def f(request):\n    b = await request.body()  # memory: bounded size-checked upstream\n",
    ],
)
def test_whole_body_negatives_are_quiet(source: str) -> None:
    assert _found(source) == []


# --- MH006 ---------------------------------------------------------------


def test_tracing_without_limits_fires() -> None:
    assert _found(
        "p = BatchSpanProcessor(exporter)\nt = TracerProvider(resource=r)\n"
    ) == [
        ("MH006", "BatchSpanProcessor"),
        ("MH006", "TracerProvider"),
    ]


def test_tracing_with_limits_is_quiet() -> None:
    assert (
        _found(
            "p = BatchSpanProcessor(exporter, max_queue_size=512)\n"
            "t = TracerProvider(resource=r, span_limits=SpanLimits())\n"
        )
        == []
    )


# --- MH007 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "asyncio.create_task(work())",
        "loop.create_task(work())",
        "asyncio.ensure_future(work())",
        "asyncio.get_running_loop().create_task(work())",
    ],
)
def test_discarded_task_fires(call: str) -> None:
    found = _found(f"async def f():\n    {call}\n")
    assert [rule for rule, _ in found] == ["MH007"]
    assert found[0][1].endswith("(work)")


@pytest.mark.parametrize(
    "line",
    [
        "t = asyncio.create_task(work())",
        "tasks.add(asyncio.create_task(work()))",
        "await asyncio.create_task(work())",
        "tg.create_task(work())",
    ],
)
def test_kept_or_owned_task_is_quiet(line: str) -> None:
    assert _found(f"async def f(tg, tasks):\n    {line}\n") == []


# --- keys and baseline ---------------------------------------------------


def test_key_has_no_line_number_and_survives_edits_above() -> None:
    before = _run("async def f():\n    asyncio.create_task(work())\n")
    after = _run("\n\n\nasync def f():\n    x = 1\n    asyncio.create_task(work())\n")
    assert before[0].key() == after[0].key()
    assert before[0].line != after[0].line
