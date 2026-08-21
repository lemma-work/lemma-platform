"""The Desktop provider, against a fake bridge executable.

The bridge is a real subprocess speaking one JSON request per invocation, so
the fake here is an actual script on disk. That keeps the thing under test
honest: argument passing, stdio framing, exit codes and error envelopes are all
exercised rather than mocked away.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProviderCreateAmbiguous,
    ProviderCreateSpec,
    ProviderGone,
    ProviderRejected,
    ProviderStorageKind,
)
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.lemma_local import (
    LemmaLocalProviderConfig,
    LemmaLocalSandboxProvider,
)

pytestmark = pytest.mark.asyncio

PINNED = "lemma-workspace@sha256:" + "c" * 64


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _bridge(tmp_path: Path, script: str) -> Path:
    """Write an executable stand-in for the native bridge."""
    path = tmp_path / "lemma-bridge"
    path.write_text("#!/usr/bin/env python3\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    return path


_RECORDING_BRIDGE = """
import hashlib, json, os, sys
state_path = os.environ["BRIDGE_STATE"]
state = json.load(open(state_path)) if os.path.exists(state_path) else {"sandboxes": {}, "calls": []}
request = json.loads(sys.stdin.read())
op = request["operation"]
params = request["parameters"]
state["calls"].append(op)
sandbox_id = params.get("sandbox_id")

def ok(result):
    json.dump(state, open(state_path, "w"))
    print(json.dumps({"ok": True, "result": result}))
    sys.exit(0)

def fail(code, message, retryable=True):
    json.dump(state, open(state_path, "w"))
    print(json.dumps({"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}))
    sys.exit(1)

if op == "sandbox.ensure":
    existing = state["sandboxes"].get(sandbox_id)
    state["sandboxes"][sandbox_id] = {
        "metadata": params.get("metadata", {}),
        "state": "running",
        "created": (existing or {}).get("created", 0) + 1,
    }
    ok({"status": {"state": "running", "runtime_url": "http://127.0.0.1:9999",
                   "apps": {"runtime": {"port": 8080, "private_url": "http://127.0.0.1:9999"},
                            "browser": {"port": 4848, "private_url": "http://127.0.0.1:4848"}}},
        "provider_id": sandbox_id})
if op == "sandbox.status":
    entry = state["sandboxes"].get(sandbox_id)
    if entry is None:
        fail("not_found", "no such sandbox", retryable=False)
    ok({"status": {"state": entry["state"], "runtime_url": "http://127.0.0.1:9999",
                   "apps": {"runtime": {"port": 8080, "private_url": "http://127.0.0.1:9999"},
                            "browser": {"port": 4848, "private_url": "http://127.0.0.1:4848"}}},
        "provider_id": sandbox_id})
if op == "sandbox.release":
    entry = state["sandboxes"].get(sandbox_id)
    if entry is None:
        fail("not_found", "no such sandbox", retryable=False)
    entry["state"] = "stopped"
    ok({})
if op == "sandbox.delete":
    state["sandboxes"].pop(sandbox_id, None)
    ok({})
if op == "sandbox.purge_storage":
    ok({})
if op == "sandbox.list":
    # Shaped exactly like the real guest: a list entry wraps the snapshot, so
    # the guest id is at status.id and provider_id is the container hash. An
    # earlier version of this fake invented a top-level "sandbox_id", which
    # agreed with a provider bug and hid it until a real VM was driven.
    ok({"sandboxes": [
        {
            "provider_id": hashlib.sha256(k.encode()).hexdigest(),
            "image": "ghcr.io/example/image@sha256:" + "d" * 64,
            "metadata": v["metadata"],
            "status": {"id": k, "status": v["state"], "ready": v["state"] == "running"},
        }
        for k, v in state["sandboxes"].items()
    ]})
fail("unsupported", "unknown operation: " + op, retryable=False)
"""


@pytest.fixture
def provider(tmp_path: Path, monkeypatch) -> LemmaLocalSandboxProvider:
    bridge = _bridge(tmp_path, _RECORDING_BRIDGE)
    monkeypatch.setenv("BRIDGE_STATE", str(tmp_path / "state.json"))
    return LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(bridge)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )


def _spec(sandbox_id, *, epoch: int = 1, kind=SandboxKind.WORKSPACE):
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=kind,
        epoch=epoch,
        name=naming.container_name(sandbox_id, kind, epoch),
        image=PINNED,
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )


def _state(provider: LemmaLocalSandboxProvider) -> dict:
    return json.loads(Path(os.environ["BRIDGE_STATE"]).read_text())


# ---------------------------------------------------------------------------
# Storage model
# ---------------------------------------------------------------------------


async def test_the_guest_sandbox_is_its_own_storage(
    provider: LemmaLocalSandboxProvider,
) -> None:
    """Desktop's guest binds a workspace's disk to the sandbox, so replacing
    the sandbox would take the user's files with it."""
    assert provider.storage_kind is ProviderStorageKind.SANDBOX_NATIVE
    assert (
        await provider.find_volume(sandbox_id=uuid4(), deadline_at=_deadline()) is None
    )
    with pytest.raises(ProviderRejected):
        await provider.ensure_volume(
            sandbox_id=uuid4(), name="anything", deadline_at=_deadline()
        )


async def test_a_new_epoch_adopts_the_same_guest_sandbox(
    provider: LemmaLocalSandboxProvider,
) -> None:
    """Same reasoning as E2B, and the opposite of Docker: the disk lives with
    the sandbox, so a second one would strand the user's files."""
    sandbox_id = uuid4()
    first = await provider.create(_spec(sandbox_id, epoch=1))
    second = await provider.create(_spec(sandbox_id, epoch=2))

    assert first.provider_id == second.provider_id
    assert first.storage_adopted is False
    assert second.storage_adopted is True


async def test_the_guest_id_is_derived_and_stable(
    provider: LemmaLocalSandboxProvider,
) -> None:
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id))
    assert instance.provider_id == f"w-{sandbox_id.hex}"

    functions = await provider.create(_spec(sandbox_id, kind=SandboxKind.FUNCTION))
    # A pod id and a user id may coincide, so the kinds must not collide.
    assert functions.provider_id == f"f-{sandbox_id.hex}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_ensure_is_idempotent(provider: LemmaLocalSandboxProvider) -> None:
    sandbox_id = uuid4()
    await provider.create(_spec(sandbox_id))
    await provider.create(_spec(sandbox_id))

    entry = _state(provider)["sandboxes"][f"w-{sandbox_id.hex}"]
    # The bridge saw two ensures; both resolved to one sandbox.
    assert entry["created"] == 2
    assert len(_state(provider)["sandboxes"]) == 1


async def test_unpinned_images_are_refused(
    provider: LemmaLocalSandboxProvider,
) -> None:
    """A moving tag means the image that ran is not the one that was reviewed,
    and Desktop runs on someone's own machine."""
    import dataclasses

    with pytest.raises(ProviderRejected, match="sha256"):
        await provider.create(
            dataclasses.replace(_spec(uuid4()), image="workspace:latest")
        )


async def test_release_stops_without_deleting(
    provider: LemmaLocalSandboxProvider,
) -> None:
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id))

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    entry = _state(provider)["sandboxes"][instance.provider_id]
    assert entry["state"] == "stopped"
    # Still there, so the next ensure resumes rather than rebuilds.
    assert instance.provider_id in _state(provider)["sandboxes"]


async def test_inspect_reports_absence_rather_than_failing(
    provider: LemmaLocalSandboxProvider,
) -> None:
    assert (
        await provider.inspect(
            naming.container_name(uuid4(), SandboxKind.WORKSPACE, 1),
            deadline_at=_deadline(),
        )
        is None
    )


async def test_a_foreign_name_is_not_looked_up(
    provider: LemmaLocalSandboxProvider,
) -> None:
    assert await provider.inspect("postgres", deadline_at=_deadline()) is None


async def test_destroying_something_absent_is_success(
    provider: LemmaLocalSandboxProvider,
) -> None:
    await provider.destroy(
        naming.container_name(uuid4(), SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )


async def test_an_operation_against_a_deleted_sandbox_is_definitively_gone(
    provider: LemmaLocalSandboxProvider,
) -> None:
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id))
    await provider.destroy(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )

    with pytest.raises(ProviderGone):
        await provider.port_base_url(instance, port=8080, deadline_at=_deadline())


# ---------------------------------------------------------------------------
# Bridge protocol
# ---------------------------------------------------------------------------


async def test_a_retryable_bridge_failure_is_ambiguous_not_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """The bridge may have completed the ensure before failing to answer, and
    ensure is idempotent, so the caller must be told to ask again rather than
    that it definitively failed."""
    bridge = _bridge(
        tmp_path,
        "import json,sys; sys.stdin.read();"
        ' print(json.dumps({"ok": False, "error": {"code": "busy",'
        ' "message": "guest is starting", "retryable": True}})); sys.exit(1)',
    )
    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(bridge)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )
    with pytest.raises(ProviderCreateAmbiguous):
        await provider.create(_spec(uuid4()))


async def test_a_definitive_bridge_failure_is_rejected(
    tmp_path: Path,
) -> None:
    bridge = _bridge(
        tmp_path,
        "import json,sys; sys.stdin.read();"
        ' print(json.dumps({"ok": False, "error": {"code": "bad_image",'
        ' "message": "unknown image", "retryable": False}})); sys.exit(1)',
    )
    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(bridge)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )
    with pytest.raises(ProviderRejected, match="unknown image"):
        await provider.create(_spec(uuid4()))


async def test_garbage_from_the_bridge_is_surfaced_not_swallowed(
    tmp_path: Path,
) -> None:
    bridge = _bridge(
        tmp_path,
        "import sys; sys.stdin.read(); "
        'sys.stderr.write("guest panicked\\n"); print("not json"); sys.exit(1)',
    )
    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(bridge)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )
    with pytest.raises(ProviderCreateAmbiguous, match="not JSON"):
        await provider.create(_spec(uuid4()))


async def test_a_missing_bridge_is_refused_at_construction(tmp_path: Path) -> None:
    """Failing here beats failing on the first user's first tool call."""
    with pytest.raises(RuntimeError, match="does not exist"):
        LemmaLocalSandboxProvider(
            LemmaLocalProviderConfig(executable=str(tmp_path / "absent")),
            RuntimeCredentialSigner(key=b"k" * 32),
        )


# ---------------------------------------------------------------------------
# Reclamation
# ---------------------------------------------------------------------------


async def test_the_sweep_identifies_its_own_sandboxes(
    provider: LemmaLocalSandboxProvider,
) -> None:
    sandbox_id = uuid4()
    await provider.create(_spec(sandbox_id))

    objects = await provider.list_objects(deadline_at=_deadline())

    assert [obj.sandbox_id for obj in objects] == [sandbox_id]
    assert objects[0].legacy is False
    # The name is what the sweeper hands back to destroy(), so a listing that
    # identifies the owner but not the object is useless: destroy() would parse
    # nothing out of it and silently no-op, leaking the sandbox forever.
    assert objects[0].name == f"w-{sandbox_id.hex}"
    assert objects[0].provider_id == objects[0].name
    assert objects[0].running is True


async def test_a_pre_consolidation_guest_sandbox_is_still_identifiable(
    provider: LemmaLocalSandboxProvider, tmp_path: Path
) -> None:
    """Guests created before this module carry no metadata, but their id is
    still `{w|f}-{hex}`. Without that fallback they would run forever with
    nobody able to say who they belong to."""
    legacy_owner = uuid4()
    state_path = Path(os.environ["BRIDGE_STATE"])
    state_path.write_text(
        json.dumps(
            {
                "sandboxes": {
                    f"w-{legacy_owner.hex}": {"metadata": {}, "state": "running"}
                },
                "calls": [],
            }
        )
    )

    objects = await provider.list_objects(deadline_at=_deadline())

    assert [obj.sandbox_id for obj in objects] == [legacy_owner]
    assert objects[0].legacy is True


async def test_a_port_resolves_to_the_guest_endpoint(
    provider: LemmaLocalSandboxProvider,
) -> None:
    instance = await provider.create(_spec(uuid4()))
    assert (
        await provider.port_base_url(instance, port=4848, deadline_at=_deadline())
        == "http://127.0.0.1:4848"
    )


async def test_an_unexposed_port_is_refused(
    provider: LemmaLocalSandboxProvider,
) -> None:
    instance = await provider.create(_spec(uuid4()))
    with pytest.raises(ProviderRejected, match="does not expose"):
        await provider.port_base_url(instance, port=1234, deadline_at=_deadline())
