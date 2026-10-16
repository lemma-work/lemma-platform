"""FAST work handlers cannot reach the network: checked at registration and over the source.

The architecture half imports the worker's handler set (what ``python -m
app.worker`` imports) and parses every FAST handler's module for imports of a
network client, including imports inside functions that the registration check
cannot see. The rest pins that both halves actually refuse.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.infrastructure.work.fast_guard import (
    FastHandlerNotIsolated,
    fast_handler_violations,
    imported_modules,
    is_network_module,
)
from app.core.infrastructure.work.types import WorkMode


def test_no_registered_fast_handler_module_imports_a_network_client() -> None:
    from app.worker import register_worker_modules  # noqa: E402

    register_worker_modules()  # every module's kinds, as the worker registers them
    from app.core.infrastructure.work.registry import registry

    fast = [
        (kind, registry.get(kind).handler)
        for kind in sorted({k for cls in _classes() for k in registry.kinds_for(cls)})
        if registry.get(kind).mode is WorkMode.FAST
    ]
    assert fast, "expected at least the runtime's own retention handler"
    assert fast_handler_violations(fast) == []


def _classes() -> list[object]:
    from app.core.infrastructure.work.types import WorkClass

    return list(WorkClass)


@pytest.fixture
def scratch_package(tmp_path: Path) -> Iterator[Path]:
    package = tmp_path / "fastguard_scratch"
    package.mkdir()
    (package / "__init__.py").write_text("")
    sys.path.insert(0, str(tmp_path))
    try:
        yield package
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m.startswith("fastguard_scratch")]:
            del sys.modules[name]


_HEADER = """
from pydantic import BaseModel
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.types import WorkClass, WorkMode

registry = WorkRegistry()

class P(BaseModel):
    pass
"""


def _module(package: Path, name: str, body: str) -> str:
    (package / f"{name}.py").write_text(_HEADER + textwrap.dedent(body))
    return f"fastguard_scratch.{name}"


def test_registration_refuses_a_fast_handler_whose_module_imported_httpx(
    scratch_package: Path,
) -> None:
    name = _module(
        scratch_package,
        "with_httpx",
        """
        import httpx

        @registry.handler(kind="k", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=P)
        async def handle(ctx: FastContext, payload: P) -> None:
            await httpx.AsyncClient().get("https://example.invalid")
        """,
    )
    with pytest.raises(FastHandlerNotIsolated, match="imports httpx"):
        importlib.import_module(name)


def test_the_source_check_finds_an_import_hidden_inside_the_handler(
    scratch_package: Path,
) -> None:
    name = _module(
        scratch_package,
        "hidden_import",
        """
        @registry.handler(kind="k", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=P)
        async def handle(ctx: FastContext, payload: P) -> None:
            from app.core.net import http_client  # noqa: F401
        """,
    )
    module = importlib.import_module(name)
    handler = module.registry.get("k").handler
    assert fast_handler_violations([("k", handler)]) == [
        f"k ({name}): app.core.net, app.core.net.http_client"
    ]


def test_a_handler_must_declare_the_context_its_mode_gives_it(
    scratch_package: Path,
) -> None:
    name = _module(
        scratch_package,
        "wrong_context",
        """
        @registry.handler(kind="k", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=P)
        async def handle(ctx: JobContext, payload: P) -> None:
            pass
        """,
    )
    with pytest.raises(
        FastHandlerNotIsolated, match="annotated FastContext, got JobContext"
    ):
        importlib.import_module(name)


def test_a_job_may_import_whatever_it_needs(scratch_package: Path) -> None:
    name = _module(
        scratch_package,
        "job_with_httpx",
        """
        import httpx  # noqa: F401

        @registry.handler(kind="k", cls=WorkClass.EVENTS, mode=WorkMode.JOB, payload=P)
        async def handle(ctx: JobContext, payload: P) -> None:
            pass
        """,
    )
    assert importlib.import_module(name).registry.get("k").mode is WorkMode.JOB


@pytest.mark.parametrize(
    ("name", "network"),
    [
        ("httpx", True),
        ("httpx._client", True),
        ("httpxx", False),
        ("app.core.net.http_client", True),
        ("app.core.network_policy", False),
        ("sqlalchemy", False),
        ("app.modules.datastore.infrastructure.session", True),
    ],
)
def test_prefixes_match_whole_module_names_only(name: str, network: bool) -> None:
    assert is_network_module(name) is network


def test_imported_modules_sees_every_form() -> None:
    source = "import a.b\nfrom c import d\ndef f():\n    import e\nfrom . import rel\n"
    assert imported_modules(source) == {"a.b", "c", "c.d", "e"}
