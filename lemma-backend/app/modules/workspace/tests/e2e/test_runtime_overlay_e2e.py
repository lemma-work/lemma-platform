"""The runtime overlay, installed into the real workspace image, leaves it working.

The unit tests prove the bundle's contents against this checkout's own Python.
This proves them where they run: the image's interpreter, its baked floor in
`/app`, the `.pth` files the image writes and the `PATH` it sets. The overlay
goes in through `install_command`, the exact command the backend sends, as the
sandbox user.

After the install, every process a sandbox starts imports `sandbox_runtime`
from the overlay first. The package is a regular one, so a submodule the
overlay lacks is not found in the image's copy behind it: it is gone. Each
import below is one a real process makes -- the relay through
`browser_relay.chrome`, `execute_python` through the Python worker, the
workspace server when a stopped container starts again, the loopback
fall-through.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from app.modules.workspace.services.workspace_runtime_bundle import (
    ARCHIVE_PATH,
    INSTALLER_PATH,
    install_command,
)
from sandbox_runtime import runtime_install

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(600)]

_BACKEND = Path(__file__).resolve().parents[5]
_SANDBOX_UID = "10001:10001"

_IMPORTED_BY_SANDBOX_PROCESSES = (
    "sandbox_runtime.paths",
    "sandbox_runtime.browser_relay.chrome",
    "sandbox_runtime.browser_relay.marks",
    # `app`, not `server`: importing `server` builds the app, which consumes
    # the runtime's single-use token.
    "sandbox_runtime.workspace.app",
    "sandbox_runtime.workspace.python_worker",
    "sandbox_runtime.host_fallback",
)


def _load_bundle_builder():
    spec = spec_from_file_location(
        "build_runtime_bundle", _BACKEND / "scripts" / "build_runtime_bundle.py"
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docker(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, check=False
    )


def _exec(container: str, command: str) -> subprocess.CompletedProcess:
    """A login shell as the sandbox user, which is how an agent's command runs."""
    return _docker("exec", "-u", _SANDBOX_UID, container, "bash", "-lc", command)


def _write_as_sandbox_user(container: str, path: str, data: bytes) -> None:
    """Owned by the sandbox user, like a file the files API delivered.

    `docker cp` would leave it owned by root, and the installer, which deletes
    what it consumed from sticky `/tmp`, would then fail for a reason no real
    delivery has.
    """
    written = _docker(
        "exec",
        "-i",
        "-u",
        _SANDBOX_UID,
        container,
        "sh",
        "-c",
        f"cat > {path}",
        stdin=data,
    )
    assert written.returncode == 0, written.stderr.decode()


@pytest.fixture(scope="module")
def sandbox(workspace_image: str) -> Iterator[str]:
    name = f"lemma-overlay-e2e-{uuid.uuid4().hex[:10]}"
    started = _docker(
        "run",
        "-d",
        "--rm",
        "--network",
        "none",
        "--name",
        name,
        "--entrypoint",
        "sleep",
        workspace_image,
        "infinity",
    )
    assert started.returncode == 0, started.stderr.decode()
    try:
        yield name
    finally:
        _docker("rm", "-f", name)


@pytest.fixture(scope="module")
def installed(sandbox: str, tmp_path_factory: pytest.TempPathFactory) -> str:
    out_dir = tmp_path_factory.mktemp("bundle")
    manifest = _load_bundle_builder().build(out_dir)
    _write_as_sandbox_user(
        sandbox, INSTALLER_PATH, Path(runtime_install.__file__).read_bytes()
    )
    _write_as_sandbox_user(
        sandbox, ARCHIVE_PATH, (out_dir / manifest["archive"]).read_bytes()
    )
    result = _exec(
        sandbox,
        install_command(
            version=manifest["version"],
            requires=manifest["requires"],
            archive_sha256=manifest["archive_sha256"],
        ),
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode()
    return sandbox


@pytest.mark.parametrize("module", _IMPORTED_BY_SANDBOX_PROCESSES)
def test_every_sandbox_process_still_imports_after_the_overlay_lands(
    installed: str, module: str
) -> None:
    result = _exec(
        installed,
        f"/opt/lemma-python/bin/python -c 'import {module} as m; print(m.__file__)'",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().startswith("/opt/lemma-runtime/current/"), (
        f"{module} came from {result.stdout.decode().strip()}, not the overlay"
    )


@pytest.mark.parametrize("command", ("save-webpage", "lemma-ensure-display", "lemma"))
def test_the_overlay_scripts_come_first_on_the_path(
    installed: str, command: str
) -> None:
    """The scripts ship in the overlay, so a fix to one needs no new image."""
    result = _exec(installed, f"command -v {command}")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().strip() == (
        f"/opt/lemma-runtime/current/bin/{command}"
    )
