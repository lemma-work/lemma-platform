"""The contract that lets the session stay untouched across the cutover.

``AgentBoxWorkspaceSession`` -- its output cursor, backpressure handling,
process collection loop and deterministic python-session ids -- is subtle code
that works. The cutover keeps it by swapping only the object behind
``self.client``, which is safe exactly as long as the replacement accepts the
same calls. That is not something to verify by eye once; it is verified here.
"""

from __future__ import annotations

import inspect

from agentbox_client import AgentBoxClient

from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient


def _parameters(func) -> list[tuple[str, inspect._ParameterKind]]:
    return [
        (parameter.name, parameter.kind)
        for parameter in inspect.signature(func).parameters.values()
        if parameter.name != "self"
    ]


def _public_methods(cls) -> dict[str, object]:
    return {
        name: getattr(cls, name)
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name, None))
    }


def test_the_local_client_accepts_every_call_the_agentbox_client_does() -> None:
    missing = sorted(
        set(_public_methods(AgentBoxClient)) - set(_public_methods(LocalSandboxClient))
    )
    assert not missing, (
        "the session may call these on either client, so the local one must "
        f"implement them: {missing}"
    )


def test_every_shared_method_takes_the_same_arguments() -> None:
    differences: list[str] = []
    for name, real in _public_methods(AgentBoxClient).items():
        mine = getattr(LocalSandboxClient, name, None)
        if mine is None:
            continue
        try:
            expected, actual = _parameters(real), _parameters(mine)
        except (TypeError, ValueError):  # pragma: no cover - builtins
            continue
        if expected != actual:
            differences.append(f"{name}\n  agentbox: {expected}\n  local:    {actual}")
    assert not differences, "\n".join(differences)


def test_the_local_client_is_usable_wherever_the_agentbox_one_is() -> None:
    """Callers use the client as an async context manager, and a missing
    __aexit__ fails at the call site with a TypeError rather than anywhere
    near this module. Dunders are part of the surface, not an afterthought."""
    for dunder in ("__aenter__", "__aexit__"):
        assert hasattr(AgentBoxClient, dunder)
        assert hasattr(LocalSandboxClient, dunder), (
            f"the local client must support {dunder}"
        )


def test_the_session_only_calls_methods_the_local_client_has() -> None:
    """A narrower check that fails loudly if the session grows a new call."""
    import re
    from pathlib import Path

    source = Path(
        "app/modules/workspace/agentbox_session.py"
    ).read_text(encoding="utf-8")
    called = set(re.findall(r"self\.client\.([a-z_]+)\(", source))
    available = set(_public_methods(LocalSandboxClient))

    assert called, "expected to find client calls in the session"
    assert called <= available, f"session calls unavailable methods: {called - available}"
