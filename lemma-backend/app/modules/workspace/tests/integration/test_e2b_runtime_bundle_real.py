"""The overlay, against a real E2B sandbox, because every claim here is physical.

The design rests on four properties of a fabric this repository does not own:
that the first-party code can be replaced inside a running sandbox at all, that
`.pth` ordering actually puts the overlay ahead of the image's own copy, that a
filesystem-only pause carries `/opt` across, and that the sandbox user can
elevate far enough to write there. None of those can be established by a fake --
a fake would agree with whatever this code assumed, which is exactly how the
fleet ended up pinned to templates nobody was running.

Measured while writing this, against the published template: the sandbox came up
running `lemma 0.7.0` while the repository was at 0.8.0. That gap is the whole
problem, and closing it *without replacing the sandbox* is what this asserts.

Safety: everything runs under its own metadata namespace, and only sandboxes
these tests created are ever killed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from sandbox_runtime.protocol import (
    EnvironmentVariable,
    ProcessState,
    StartProcessRequest,
)

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.infrastructure.runtime_bundle import RuntimeBundle
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderCreateSpec
from app.modules.workspace.providers.e2b import E2BProviderConfig, E2BSandboxProvider
from app.modules.workspace.services.workspace_runtime_bundle import (
    ARCHIVE_PATH,
    INSTALLER_PATH,
    RUNTIME_ROOT,
    install_command,
)
from sandbox_runtime import runtime_install
from sandbox_runtime.paths import WORKSPACE_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.provider, pytest.mark.asyncio]

_KEY = os.getenv("E2B_API_KEY", "")
_TEMPLATE = os.getenv("E2B_WORKSPACE_TEMPLATE", "")
_NAMESPACE = os.getenv("E2B_TEST_METADATA_NAMESPACE", "lemma-runtime-bundle")

_BACKEND = Path(__file__).resolve().parents[5]


def _deadline(seconds: int = 300) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def bundle() -> RuntimeBundle:
    """One real bundle, built from this checkout.

    Built rather than stubbed: the thing under test is whether *these* wheels,
    unpacked by *this* installer, end up ahead of the image's copy.
    """
    import subprocess
    import tempfile

    from app.modules.workspace.infrastructure.runtime_bundle import load_bundle

    with tempfile.TemporaryDirectory() as raw:
        out_dir = Path(raw)
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                str(_BACKEND / "scripts" / "build_runtime_bundle.py"),
                "--out-dir",
                str(out_dir),
            ],
            cwd=_BACKEND,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        built = load_bundle(out_dir)
        assert built is not None
        return built


@pytest_asyncio.fixture
async def provider() -> AsyncIterator[E2BSandboxProvider]:
    if not _KEY or not _TEMPLATE:
        pytest.skip("set E2B_API_KEY and E2B_WORKSPACE_TEMPLATE to run this suite")
    instance = E2BSandboxProvider(
        E2BProviderConfig(
            api_key=_KEY,
            workspace_template=_TEMPLATE,
            function_template=_TEMPLATE,
            sandbox_timeout_seconds=600,
            metadata_namespace=_NAMESPACE,
        )
    )
    created: list[str] = []
    instance._created = created  # type: ignore[attr-defined]
    try:
        yield instance
    finally:
        for provider_id in created:
            try:
                sandbox = await instance._connect(provider_id)
                await sandbox.kill(**instance._api())
            except Exception:
                continue


def _spec(sandbox_id: UUID, *, epoch: int = 1) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image="",
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )


async def _create(provider: E2BSandboxProvider, sandbox_id: UUID, *, epoch: int = 1):
    instance = await provider.create(_spec(sandbox_id, epoch=epoch))
    provider._created.append(instance.provider_id)  # type: ignore[attr-defined]
    await provider.wait_ready(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    return instance


async def _run(provider: E2BSandboxProvider, instance, command: str) -> tuple[str, int]:
    """Run a command to completion and return its output and exit code."""
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command=command,
            argv=None,
            cwd=WORKSPACE_ROOT,
            environment=(EnvironmentVariable(name="LEMMA_TEST", value="1"),),
            tty=None,
            output_limit_bytes=256 * 1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )
    collected = bytearray()
    after = 0
    for _ in range(60):
        snapshot = await provider.read_process_output(
            instance,
            process_id=process_id,
            after_sequence=after,
            wait_seconds=5.0,
            deadline_at=_deadline(),
        )
        for chunk in snapshot.chunks:
            after = max(after, chunk.sequence)
            collected.extend(chunk.data)
        if snapshot.state is not ProcessState.RUNNING:
            return bytes(collected).decode("utf-8", "replace"), snapshot.exit_code or 0
    raise AssertionError(f"command never finished: {command}")


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _install(
    provider: E2BSandboxProvider, instance, bundle: RuntimeBundle
) -> tuple[str, int]:
    """Deliver and install exactly the way the service does."""
    await provider.write_file(
        instance,
        path=INSTALLER_PATH,
        data=_stream(Path(runtime_install.__file__).read_bytes()),
        expected_sha256=None,
        deadline_at=_deadline(),
    )
    await provider.write_file(
        instance,
        path=ARCHIVE_PATH,
        data=_stream(bundle.archive),
        expected_sha256=bundle.archive_sha256.removeprefix("sha256:"),
        deadline_at=_deadline(),
    )
    return await _run(
        provider,
        instance,
        install_command(version=bundle.version, requires=bundle.requires),
    )


async def test_a_stale_sandbox_is_upgraded_without_being_replaced(
    provider: E2BSandboxProvider, bundle: RuntimeBundle
) -> None:
    """The whole thesis, in one sequence.

    A sandbox created from the published template runs whatever first-party code
    that template was built with. Before this existed the only way to move it
    was to destroy the sandbox, and on this provider the sandbox is the disk.
    Here the same sandbox keeps its id, keeps its files, and runs the new code.
    """
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    await _run(provider, instance, f"echo mine > {WORKSPACE_ROOT}/user-work.txt")

    output, exit_code = await _install(provider, instance, bundle)
    assert exit_code == 0, output

    # The `lemma` on PATH is the image's own console script, and it must now be
    # running the overlay's code. Compared against the overlay's own copy rather
    # than against a version string: how far apart the two are depends on how
    # stale the deployed template happens to be, and an assertion that only
    # holds while something is out of date stops holding the moment it is fixed.
    # (It was genuinely far apart when this was written -- the published
    # template answered `lemma 0.7.0` against a 0.8.0 checkout, which is the
    # staleness this whole mechanism exists to close.)
    on_path, _ = await _run(provider, instance, "lemma --version")
    from_overlay, _ = await _run(
        provider, instance, f"{RUNTIME_ROOT}/current/bin/lemma --version"
    )
    assert on_path.strip() == from_overlay.strip(), (
        f"`lemma` on PATH reports {on_path.strip()!r} but the overlay's own "
        f"copy reports {from_overlay.strip()!r} -- the overlay is not in front"
    )
    survived, _ = await _run(provider, instance, f"cat {WORKSPACE_ROOT}/user-work.txt")
    assert "mine" in survived, "the user's files did not survive the upgrade"


async def test_every_first_party_package_resolves_from_the_overlay(
    provider: E2BSandboxProvider, bundle: RuntimeBundle
) -> None:
    """`.pth` ordering is invisible and load-bearing, so check the real answer.

    The image's own copy stays on `sys.path` behind ours -- that is what makes it
    a floor rather than a casualty -- so "installed" and "actually imported" are
    different claims and only the second one matters.
    """
    instance = await _create(provider, uuid4())
    await _install(provider, instance, bundle)

    for module in bundle.requires:
        output, exit_code = await _run(
            provider,
            instance,
            f"/opt/lemma-python/bin/python -c "
            f'\'import {module} as m; print(getattr(m, "__file__", ""))\'',
        )
        assert exit_code == 0, f"{module}: {output}"
        assert RUNTIME_ROOT in output, (
            f"{module} resolved to {output.strip()!r}, not the overlay"
        )


async def test_a_users_own_install_still_wins_over_the_overlay(
    provider: E2BSandboxProvider, bundle: RuntimeBundle
) -> None:
    """Platform code must not quietly outrank what the agent installed itself.

    `PIP_PREFIX` sends `pip install` under the user's home, and the workspace
    overlay `.pth` puts that ahead of everything. Ours sorts before it by name,
    so it is inserted first and ends up behind -- which is the order that keeps
    a pinned dependency pinned.
    """
    instance = await _create(provider, uuid4())
    await _install(provider, instance, bundle)
    site = f"{WORKSPACE_ROOT}/.python/lib/python3.14/site-packages"
    await _run(
        provider,
        instance,
        f"mkdir -p {site}/lemma_sdk && "
        f"printf '__version__ = \"users-own\"\\n' > {site}/lemma_sdk/__init__.py",
    )

    output, exit_code = await _run(
        provider,
        instance,
        "/opt/lemma-python/bin/python -c 'import lemma_sdk; print(lemma_sdk.__file__)'",
    )

    assert exit_code == 0, output
    assert f"{WORKSPACE_ROOT}/.python" in output, (
        f"the overlay shadowed the user's own install: {output.strip()!r}"
    )


async def test_the_overlay_survives_a_pause_and_resume(
    provider: E2BSandboxProvider, bundle: RuntimeBundle
) -> None:
    """A workspace is paused on every idle release, so this is the common case.

    It rests on a property of the fabric rather than of this code: E2B's
    filesystem-only pause snapshots the whole rootfs, not just the home the
    user's files live in -- and the overlay is in `/opt`, outside it. If
    that were ever untrue the overlay would silently reinstall on every resume,
    which would be a cost bug rather than a correctness one -- but it would be
    invisible, so it is asserted.
    """
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    await _install(provider, instance, bundle)

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    resumed = await provider.create(_spec(sandbox_id, epoch=2))
    await provider.wait_ready(
        resumed, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    assert resumed.provider_id == instance.provider_id
    stamp, exit_code = await _run(
        provider, resumed, f"cat {RUNTIME_ROOT}/{runtime_install.CURRENT_LINK}/.stamp"
    )
    assert exit_code == 0, stamp
    assert bundle.version in stamp


async def test_installing_the_same_bundle_twice_changes_nothing(
    provider: E2BSandboxProvider, bundle: RuntimeBundle
) -> None:
    """The probe is what keeps the warm path off this road, but it can miss --
    a fresh replica has no memory of the sandbox. Re-running must be cheap and
    harmless rather than a second unpack."""
    instance = await _create(provider, uuid4())
    await _install(provider, instance, bundle)

    output, exit_code = await _install(provider, instance, bundle)

    assert exit_code == 0, output
    assert '"installed": false' in output, output


async def test_the_staged_archive_does_not_outlive_the_install(
    provider: E2BSandboxProvider, bundle: RuntimeBundle
) -> None:
    """Otherwise a superseded 1.6 MB archive rides in every later snapshot."""
    instance = await _create(provider, uuid4())
    await _install(provider, instance, bundle)

    output, _ = await _run(provider, instance, f"ls {ARCHIVE_PATH} 2>&1 || true")

    assert "No such file" in output, output
