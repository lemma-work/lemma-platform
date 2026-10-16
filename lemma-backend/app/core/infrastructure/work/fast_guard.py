"""What keeps a FAST handler on the database, structurally rather than by review.

A FAST handler runs inside its item's row lock with a two-second statement
timeout, and its effect commits with the item's deletion -- that is the whole
exactly-once argument. A network call in there holds the row lock, a pooled
connection and a dispatcher slot for as long as the remote side likes, and it
is not undone when the transaction rolls back. So a FAST handler gets only
``FastContext`` (a unit of work, the claimed item, a writer on the same
transaction), and its module may not import a network client at all.

Two checks, both on the same list:

* at registration (``WorkRegistry.handler``), the handler's first parameter
  must be annotated ``FastContext`` and the module namespace it is defined in
  must hold nothing from a network package -- so a violation fails the import
  that introduced it, in every process;
* in the unit suite (``test_work_fast_handler_isolation``), every FAST
  handler's module source is parsed and every ``import`` in it -- including
  ones inside functions, which the registration check cannot see -- is matched
  against the list.

A kind that needs the network is a JOB. Anything that writes another store
(the datastore database) is a JOB too, which is why the datastore engine is on
the list.
"""

from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType

#: Module prefixes a FAST handler's module may not import: network clients,
#: model SDKs, other stores, and the app's own wrappers around them.
NETWORK_MODULES: tuple[str, ...] = (
    # HTTP and sockets.
    "aiohttp",
    "http.client",
    "httpx",
    "requests",
    "socket",
    "urllib.request",
    "urllib3",
    "websockets",
    "grpc",
    # Other stores and brokers: a FAST handler touches the main database only,
    # through the unit of work it is given.
    "asyncpg",
    "coredis",
    "redis",
    "faststream",
    "streaq",
    "boto3",
    "botocore",
    "aiobotocore",
    "google.cloud",
    # Model and provider SDKs.
    "anthropic",
    "openai",
    "litellm",
    "pydantic_ai",
    "google.genai",
    "e2b",
    "composio",
    "slack_sdk",
    "telegram",
    "resend",
    "mcp",
    "smtplib",
    "aiosmtplib",
    # The app's wrappers around all of the above.
    "app.core.net",
    "app.core.email",
    "app.core.web_search",
    "app.core.embeddings",
    "app.core.infrastructure.redis",
    "app.core.infrastructure.cache",
    "app.core.infrastructure.jobs",
    "app.core.infrastructure.events.message_bus",
    "app.modules.connectors",
    "app.modules.datastore.infrastructure.session",
    "app.modules.agent.infrastructure.harnesses",
    "app.modules.workspace",
)


class FastHandlerNotIsolated(ValueError):
    """A FAST handler could reach the network; register it as a JOB instead."""


def is_network_module(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in NETWORK_MODULES)


def _origin(value: object) -> str | None:
    if isinstance(value, ModuleType):
        return value.__name__
    module = getattr(value, "__module__", None)
    return module if isinstance(module, str) else None


def _context_annotation(fn: Callable[..., object]) -> str | None:
    try:
        parameters = list(inspect.signature(fn).parameters.values())
    except TypeError, ValueError:
        return None
    if not parameters:
        return None
    annotation = parameters[0].annotation
    if annotation is inspect.Parameter.empty:
        return None
    if isinstance(annotation, str):
        return annotation.rsplit(".", 1)[-1].strip("'\"")
    return getattr(annotation, "__name__", None)


def check_handler_signature(
    kind: str, fn: Callable[..., object], expected: str
) -> None:
    """The handler's first parameter must say which context it gets."""
    if not inspect.iscoroutinefunction(fn):
        raise FastHandlerNotIsolated(f"work kind {kind!r}: handler must be async")
    found = _context_annotation(fn)
    if found != expected:
        raise FastHandlerNotIsolated(
            f"work kind {kind!r}: the handler's first parameter must be annotated "
            f"{expected}, got {found or 'no annotation'}"
        )


def check_fast_module_namespace(kind: str, fn: Callable[..., object]) -> None:
    """Refuse a FAST handler whose module has already imported a network client."""
    module = sys.modules.get(fn.__module__)
    if module is None:
        return
    reached = sorted(
        {
            origin
            for value in vars(module).values()
            if (origin := _origin(value)) is not None and is_network_module(origin)
        }
    )
    if reached:
        raise FastHandlerNotIsolated(
            f"work kind {kind!r} is FAST but {fn.__module__} imports "
            f"{', '.join(reached)}; a FAST handler touches only the main database "
            "-- make it a JOB, or move it to a module without network clients"
        )


def imported_modules(source: str) -> set[str]:
    """Every module an ``import`` statement in ``source`` names, at any depth."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def module_network_imports(module_name: str) -> list[str]:
    """Network modules imported anywhere in ``module_name``'s source."""
    module = sys.modules.get(module_name)
    path = getattr(module, "__file__", None)
    if path is None:
        return []
    source = Path(path).read_text(encoding="utf-8")
    return sorted(name for name in imported_modules(source) if is_network_module(name))


def fast_handler_violations(
    handlers: Iterable[tuple[str, Callable[..., object]]],
) -> list[str]:
    """One line per FAST handler whose module imports a network client."""
    problems: list[str] = []
    for kind, fn in handlers:
        reached = module_network_imports(fn.__module__)
        if reached:
            problems.append(f"{kind} ({fn.__module__}): {', '.join(reached)}")
    return problems
