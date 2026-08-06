from __future__ import annotations

from uuid import uuid4

from app.modules.workspace.domain.errors import (
    SandboxCapabilityUnsupported,
    SandboxNotReady,
    SandboxRejected,
    SandboxUnavailable,
)
from app.modules.workspace.domain.sandbox import (
    FUNCTION_CAPABILITIES,
    WORKSPACE_CAPABILITIES,
    Sandbox,
    SandboxCapability,
    SandboxDesiredState,
    SandboxHandle,
    SandboxKind,
    SandboxOwnerKind,
    capabilities_for,
)


def _sandbox(kind: SandboxKind) -> Sandbox:
    owner_id = uuid4()
    return Sandbox(
        id=owner_id,
        kind=kind,
        owner_kind=(
            SandboxOwnerKind.USER
            if kind is SandboxKind.WORKSPACE
            else SandboxOwnerKind.POD
        ),
        owner_id=owner_id,
        slug="default",
        display_name="Default workspace",
        profile_name="workspace-python-v1",
        profile_digest="sha256:" + "a" * 64,
        desired_state=SandboxDesiredState.PRESENT,
        epoch=1,
        storage_generation=1,
    )


def test_function_sandboxes_expose_only_a_port() -> None:
    """A function runtime runs an immutable artifact pinned by digest.

    Handing it a shell, a filesystem, or a python session would make that
    digest a lie about what actually ran, so the capability set is one entry.
    """
    assert FUNCTION_CAPABILITIES == frozenset({SandboxCapability.PORT_ACCESS})
    assert capabilities_for(SandboxKind.FUNCTION) == FUNCTION_CAPABILITIES

    function = _sandbox(SandboxKind.FUNCTION)
    assert not function.has(SandboxCapability.PROCESSES)
    assert not function.has(SandboxCapability.FILESYSTEM)
    assert not function.has(SandboxCapability.PYTHON_SESSIONS)
    assert not function.has(SandboxCapability.DURABLE_STORAGE)
    assert function.has(SandboxCapability.PORT_ACCESS)


def test_workspace_sandboxes_expose_the_full_surface() -> None:
    workspace = _sandbox(SandboxKind.WORKSPACE)
    for capability in WORKSPACE_CAPABILITIES:
        assert workspace.has(capability)
    assert workspace.has(SandboxCapability.DURABLE_STORAGE)


def test_a_handle_carries_its_epoch_so_stale_operations_cannot_be_replayed() -> None:
    """The epoch travels with the handle, not with the sandbox lookup.

    A caller holding a handle across a recreate must address the old container
    name, so the operation fails definitively instead of landing on the
    replacement.
    """
    sandbox_id = uuid4()
    old = SandboxHandle(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=4,
        provider="docker",
        provider_id=f"lemma-workspace-{sandbox_id.hex}-4",
        capabilities=WORKSPACE_CAPABILITIES,
    )
    new = SandboxHandle(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=5,
        provider="docker",
        provider_id=f"lemma-workspace-{sandbox_id.hex}-5",
        capabilities=WORKSPACE_CAPABILITIES,
    )

    assert old.provider_id != new.provider_id
    assert old != new


def test_retryable_and_definitive_failures_are_distinguishable() -> None:
    """A caller must be able to tell "try again" from "this cannot work"."""
    assert issubclass(SandboxNotReady, SandboxUnavailable)
    assert not issubclass(SandboxUnavailable, SandboxRejected)
    assert not issubclass(SandboxRejected, SandboxUnavailable)

    unavailable = SandboxUnavailable("provider busy", retry_after_ms=250)
    assert unavailable.retry_after_ms == 250
    assert SandboxUnavailable("no hint").retry_after_ms is None


def test_capability_refusal_names_the_capability_and_the_kind() -> None:
    """The message is what an agent sees, so it has to say what was refused."""
    error = SandboxCapabilityUnsupported("python_sessions", kind="function")
    assert isinstance(error, SandboxRejected)
    assert error.capability == "python_sessions"
    assert error.kind == "function"
    assert "python_sessions" in str(error)
    assert "function" in str(error)
