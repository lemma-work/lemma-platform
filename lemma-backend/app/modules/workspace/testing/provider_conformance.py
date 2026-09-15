"""One set of checks every sandbox provider has to pass.

Written because the alternative was tried. A method was added to the ops
protocol, implemented for Docker, and shipped: E2B and the desktop guest --
which between them carry every sandbox that is not a developer's laptop -- had
no implementation at all, so the call raised `AttributeError` from inside a
request handler rather than the typed refusal the protocol's own docstring
promised. Nothing failed until a person clicked the thing.

A protocol in Python is a promise a type checker keeps only where somebody
declares the type. These are the same promises, asserted at runtime, against
whatever object a provider actually assembles. Each provider's unit tests call
them with a fake fabric underneath, and each real-fabric integration test calls
the same ones again -- so "it works on Docker" and "it works on E2B" are the
same sentence rather than two different ones.

Deliberately not a base test class: a provider that inherited a suite would have
to be constructed the way the suite expects, and the three are built quite
differently. Functions take the assembled provider instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
import inspect

from sandbox_runtime.errors import SandboxCapabilityUnsupported

from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderInstance,
    SandboxEndpoint,
    SandboxOpsProvider,
    SandboxProvider,
)

#: Every method the ops protocol declares. Derived from the protocol rather than
#: listed, so a method added there is checked on every provider without anyone
#: remembering to add it here.
OPS_PROTOCOL_METHODS: tuple[str, ...] = tuple(
    sorted(
        name
        for name, value in vars(SandboxOpsProvider).items()
        if not name.startswith("_") and inspect.isfunction(value)
    )
)

LIFECYCLE_PROTOCOL_METHODS: tuple[str, ...] = tuple(
    sorted(
        name
        for name, value in vars(SandboxProvider).items()
        if not name.startswith("_") and inspect.isfunction(value)
    )
)


def assert_implements_ops_protocol(provider: object) -> None:
    """Every ops method exists and is callable on this provider.

    `isinstance` against a runtime-checkable Protocol would only check names on
    the class; this also catches a method left as an inherited stub.
    """
    missing = [
        name
        for name in OPS_PROTOCOL_METHODS
        if not callable(getattr(provider, name, None))
    ]
    assert not missing, (
        f"{type(provider).__name__} does not implement {sorted(missing)} — "
        "a caller reaching for one of these gets AttributeError, not a refusal"
    )


def assert_implements_lifecycle_protocol(provider: object) -> None:
    missing = [
        name
        for name in LIFECYCLE_PROTOCOL_METHODS
        if not callable(getattr(provider, name, None))
    ]
    assert not missing, (
        f"{type(provider).__name__} does not implement {sorted(missing)}"
    )


def assert_declares_capabilities(provider: object) -> None:
    """Capabilities are declared, and they are the enum rather than strings."""
    declared = getattr(provider, "capabilities", None)
    assert declared is not None, (
        f"{type(provider).__name__} declares no capabilities; "
        "`require_capability` reads absence as 'cannot', which would refuse "
        "everything this provider can in fact do"
    )
    assert isinstance(declared, frozenset), "capabilities must be a frozenset"
    unknown = {c for c in declared if not isinstance(c, ProviderCapability)}
    assert not unknown, f"not ProviderCapability members: {unknown}"


def assert_capability_is_consistent(provider: object) -> None:
    """What is declared and what is implemented agree, in both directions.

    A provider that implements `reach_port` but does not declare `PORT_REACH`
    is refused by `require_capability` and nobody finds out why; one that
    declares it without implementing it is the failure this file exists for.
    """
    declared = getattr(provider, "capabilities", frozenset())
    for capability, method in (
        (ProviderCapability.PORT_REACH, "reach_port"),
        (ProviderCapability.SECRET_DELIVERY, "deliver_secret"),
    ):
        implemented = callable(getattr(provider, method, None))
        assert (capability in declared) == implemented, (
            f"{type(provider).__name__}: capability {capability!s} is "
            f"{'declared' if capability in declared else 'not declared'} but "
            f"{method} is {'present' if implemented else 'absent'}"
        )


async def check_reach_port_shape(
    provider: object,
    instance: ProviderInstance,
    *,
    port: int = 8080,
    deadline_at: datetime | None = None,
) -> SandboxEndpoint:
    """`reach_port` answers with a usable endpoint, and says if it is public."""
    endpoint = await provider.reach_port(
        instance,
        port=port,
        deadline_at=deadline_at or _deadline(),
    )
    assert isinstance(endpoint, SandboxEndpoint), (
        "reach_port must answer with SandboxEndpoint — a bare string leaves a "
        "fabric that needs a header with nowhere to put it"
    )
    assert endpoint.url.startswith(("http://", "https://")), endpoint.url
    assert not endpoint.url.endswith("/"), (
        f"{endpoint.url!r} ends in a slash; callers append their own path"
    )
    assert all(
        isinstance(k, str) and isinstance(v, str) for k, v in endpoint.headers.items()
    ), "headers must be plain strings"
    assert isinstance(endpoint.public, bool)
    return endpoint


async def check_secret_delivery_leaves_no_trace(
    provider: object,
    instance: ProviderInstance,
    *,
    path: str = "/tmp/lemma-conformance/token",
    secret: bytes = b"conformance-secret-value",
    commands_run: Callable[[], Sequence[str]],
    deadline_at: datetime | None = None,
) -> None:
    """A delivered secret never appears in anything the fabric was told to run.

    `commands_run` returns whatever command strings the fabric recorded. The
    point of delivering a file rather than echoing one is that the value stays
    out of argv, where any process in the sandbox can read it from
    `/proc/<pid>/cmdline`; that is only true if nobody quietly re-adds a shell.
    """
    before = len(commands_run())
    await provider.deliver_secret(
        instance,
        path=path,
        value=secret,
        deadline_at=deadline_at or _deadline(),
    )
    text = secret.decode()
    leaked = [c for c in commands_run()[before:] if text in c]
    assert not leaked, f"the secret appeared in a command: {leaked}"


async def check_unsupported_is_typed(
    call: Callable[[], Awaitable[object]],
) -> None:
    """A capability a fabric lacks is refused in words, not by AttributeError."""
    try:
        await call()
    except SandboxCapabilityUnsupported as exc:
        assert exc.capability, "the refusal must name the capability"
        assert exc.kind, "the refusal must name the provider"
        return
    except AttributeError as exc:  # pragma: no cover - the bug being prevented
        raise AssertionError(
            "an unsupported capability surfaced as AttributeError, which no "
            f"caller can render as a sentence: {exc}"
        ) from exc
    raise AssertionError("expected SandboxCapabilityUnsupported, nothing raised")


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)
