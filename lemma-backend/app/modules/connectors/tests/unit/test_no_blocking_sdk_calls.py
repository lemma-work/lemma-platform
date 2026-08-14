"""The Composio SDK is synchronous; it must never be called on the event loop.

Every ``composio.*`` call issues a blocking HTTP request. Made directly from an
``async def`` it stalls the entire loop for the duration of a round trip to
Composio's API -- which surfaces as unrelated requests timing out, the hardest
kind of flakiness to trace back to its cause. The operation gateway always got
this right; the auth provider did not, and its ``refresh_credentials`` sat on the
hot execution path.

This walks the source rather than trusting review, so the fix cannot quietly
regress the next time someone adds an SDK call.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CONNECTORS_ROOT = Path(__file__).resolve().parents[2]

# Attribute chains on the SDK client that perform network I/O.
_SDK_ROOTS = {"composio"}
_SDK_NAMESPACES = {"connected_accounts", "auth_configs", "tools", "toolkits", "client"}


def _sdk_call_target(node: ast.Call) -> str | None:
    """Return ``composio.<namespace>.<method>`` if this call hits the SDK."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    namespace = func.value
    if not isinstance(namespace, ast.Attribute):
        return None
    root = namespace.value
    if not isinstance(root, ast.Name) or root.id not in _SDK_ROOTS:
        return None
    if namespace.attr not in _SDK_NAMESPACES:
        return None
    return f"{root.id}.{namespace.attr}.{func.attr}"


# Building the client is not free either: it reads config, builds an httpx
# client, and imports the SDK's lazy namespaces on first use — 76ms cold, 4ms
# warm. Only the METHOD calls were guarded, so every call site constructed its
# client on the loop and then carefully offloaded the cheap part.
_SDK_CONSTRUCTORS = {"Composio"}
_SDK_FACTORIES = {"_composio_client_factory", "composio_client_factory"}


def _sdk_construction_target(node: ast.Call) -> str | None:
    """Return the name if this call BUILDS an SDK client."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in _SDK_CONSTRUCTORS:
        return f"{func.id}()"
    if isinstance(func, ast.Attribute) and func.attr in _SDK_FACTORIES:
        return f"{func.attr}()"
    return None


def _calls_on_the_event_loop(tree: ast.AST) -> list[ast.Call]:
    """Calls whose *nearest enclosing function* is an ``async def``.

    Nesting is what matters, not containment. A synchronous ``def _execute()``
    declared inside an ``async def`` and handed to ``run_blocking`` is lexically
    inside the coroutine but never runs on the loop -- the offload thread calls
    it. Only a call whose closest enclosing scope is itself async can block.
    """
    on_loop: list[ast.Call] = []

    def visit(node: ast.AST, in_async: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                visit(child, True)
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                # A new synchronous scope: its body is off the loop.
                visit(child, False)
            else:
                if in_async and isinstance(child, ast.Call):
                    on_loop.append(child)
                visit(child, in_async)

    visit(tree, False)
    return on_loop


def _runtime_files() -> list[Path]:
    return [
        path
        for path in sorted(_CONNECTORS_ROOT.rglob("*.py"))
        if "tests" not in path.parts
    ]


@pytest.mark.parametrize(
    "path", _runtime_files(), ids=lambda p: str(p.relative_to(_CONNECTORS_ROOT))
)
def test_composio_sdk_is_never_called_on_the_event_loop(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = [
        f"{path.name}:{node.lineno}: {target} runs on the event loop; "
        f"wrap it in run_blocking(limiter='external_http')"
        for node in _calls_on_the_event_loop(tree)
        if (target := _sdk_call_target(node) or _sdk_construction_target(node))
    ]
    assert not violations, "\n".join(violations)


def _violations(source: str) -> list[str]:
    return [
        target
        for node in _calls_on_the_event_loop(ast.parse(source))
        if (target := _sdk_call_target(node))
    ]


def test_the_guard_catches_a_bare_call_in_a_coroutine() -> None:
    assert _violations(
        "async def refresh(composio):\n"
        "    return composio.connected_accounts.get('abc')\n"
    ) == ["composio.connected_accounts.get"]


def test_the_guard_accepts_a_lambda_handed_to_run_blocking() -> None:
    assert _violations(
        "async def refresh(composio):\n"
        "    return await run_blocking(\n"
        "        lambda: composio.connected_accounts.get('abc'),\n"
        "        limiter='external_http',\n"
        "    )\n"
    ) == []


def test_the_guard_accepts_a_nested_sync_def_handed_to_run_blocking() -> None:
    # The shape the operation gateway uses: a sync closure passed by name. It is
    # lexically inside the coroutine but runs on the offload thread.
    assert _violations(
        "async def execute(composio):\n"
        "    def _execute():\n"
        "        return composio.tools.execute('SLUG', {})\n"
        "    return await run_blocking(_execute, limiter='external_http')\n"
    ) == []


def test_the_guard_ignores_calls_in_plain_synchronous_functions() -> None:
    assert _violations(
        "def build(composio):\n    return composio.auth_configs.create()\n"
    ) == []
