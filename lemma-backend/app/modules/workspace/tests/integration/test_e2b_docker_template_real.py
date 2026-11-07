"""A `workspace-docker` sandbox runs this repository's stack, across a pause.

The template's whole promise is a machine a pod can run `docker compose` on, as
the sandbox user, that still has its images and data after the provider pauses
it. Every step of that is a property of the real service -- whether the guest
mounts overlayfs, whether systemd brings dockerd back after a filesystem-only
resume, whether `/var/lib/docker` survives one -- so it is checked against E2B,
on a template built from `build_templates.py --target workspace-docker`.

The disk is fixed by the E2B plan and is the binding limit, so the last step is
the one the backend's janitor relies on: `df` reports usage the janitor can
parse, and pruning gives space back.

Safety, because the account may be shared: the sandbox carries its own metadata
namespace, and only the sandbox this module created is ever killed.
"""

from __future__ import annotations

import os
import shlex
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.integration, pytest.mark.provider, pytest.mark.asyncio]

pytest.importorskip("e2b")
from e2b import AsyncSandbox  # noqa: E402
from e2b.sandbox.commands.command_handle import CommandExitException  # noqa: E402

_KEY = os.getenv("E2B_API_KEY", "")
_TEMPLATE = os.getenv("E2B_DOCKER_TEMPLATE", "")
# Deliberately not the production namespace.
_NAMESPACE = os.getenv("E2B_TEST_METADATA_NAMESPACE", "lemma-docker-template")

_BACKEND = Path(__file__).resolve().parents[5]
_STACK = "/home/user/lemma/stack"
#: What the provider runs after a resume. A filesystem-only pause cold-boots the
#: VM; systemd starts `docker.service` on its own, and this waits out a slow
#: boot instead of calling it a failure.
_POST_RESUME = (
    "for i in $(seq 1 60); do docker info >/dev/null 2>&1 && exit 0; "
    "[ $i = 10 ] && sudo systemctl start docker; sleep 1; done; "
    "docker info"
)
_DISK = "df --output=used,size -B1 / | tail -1"


async def _sh(sandbox: AsyncSandbox, command: str, *, timeout: float = 300) -> str:
    """Run as the sandbox user and return stdout; a non-zero exit fails the test
    with what the command printed, which is the only clue a remote VM gives."""
    try:
        result = await sandbox.commands.run(
            command,
            user="user",
            timeout=timeout,
            request_timeout=timeout + 30,
        )
    except CommandExitException as exc:
        pytest.fail(
            f"`{command}` exited {exc.exit_code}\n"
            f"stdout:\n{exc.stdout[-4000:]}\nstderr:\n{exc.stderr[-4000:]}"
        )
    return result.stdout


async def _disk_used(sandbox: AsyncSandbox) -> tuple[int, int]:
    used, size = (await _sh(sandbox, _DISK)).split()
    return int(used), int(size)


@pytest_asyncio.fixture
async def sandbox() -> AsyncIterator[AsyncSandbox]:
    if not _KEY or not _TEMPLATE:
        pytest.skip("set E2B_API_KEY and E2B_DOCKER_TEMPLATE to run this suite")
    started = time.monotonic()
    created = await AsyncSandbox.create(
        template=_TEMPLATE,
        timeout=1800,
        metadata={f"{_NAMESPACE}.run": uuid4().hex},
        api_key=_KEY,
    )
    print(f"create: {time.monotonic() - started:.1f}s ({created.sandbox_id})")
    try:
        yield created
    finally:
        # Only ever the sandbox this test made, connected to by its own id.
        try:
            await AsyncSandbox.kill(created.sandbox_id, api_key=_KEY)
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the failure
            print(f"kill {created.sandbox_id} failed: {exc!r}")


async def _upload_stack(sandbox: AsyncSandbox) -> None:
    """This repository's own infra compose file, with the init scripts it
    mounts, at the relative paths it mounts them from."""
    await sandbox.files.write(
        f"{_STACK}/docker-compose.yml",
        (_BACKEND / "docker-compose.yml").read_text(encoding="utf-8"),
        user="user",
    )
    init = _BACKEND / "installer" / "postgres-init"
    for path in sorted(init.iterdir()):
        if path.is_file():
            await sandbox.files.write(
                f"{_STACK}/installer/postgres-init/{path.name}",
                path.read_bytes(),
                user="user",
            )


def _compose(arguments: str) -> str:
    return f"cd {shlex.quote(_STACK)} && docker compose -p lemma {arguments}"


_PSQL = _compose("exec -T db psql -U postgres -d lemma -tAc")


async def test_a_docker_machine_runs_the_stack_and_keeps_it_across_a_pause(
    sandbox: AsyncSandbox,
) -> None:
    timings: dict[str, float] = {}

    def lap(name: str, since: float) -> None:
        timings[name] = round(time.monotonic() - since, 1)

    # -- dockerd answers the sandbox user, with no sudo and no start command.
    started = time.monotonic()
    info = await _sh(
        sandbox,
        "docker info --format '{{.ServerVersion}} {{.Driver}} {{.CgroupVersion}}'",
    )
    lap("docker_info", started)
    version, driver, cgroup = info.split()
    assert version
    assert driver.startswith("overlay"), f"storage driver is {driver}"
    assert cgroup == "2"
    assert "docker" in (await _sh(sandbox, "id -nG")).split()
    assert "Docker Compose" in await _sh(sandbox, "docker compose version")
    assert await _sh(sandbox, "docker buildx version")

    # -- this repository's db + redis come up healthy.
    await _upload_stack(sandbox)
    started = time.monotonic()
    await _sh(sandbox, _compose("up -d --wait db redis"), timeout=600)
    lap("compose_up_cold", started)
    health = await _sh(
        sandbox,
        "docker inspect --format '{{.State.Health.Status}}' "
        "$(" + _compose("ps -q db") + ")",
    )
    assert health.strip() == "healthy"
    assert (await _sh(sandbox, _compose("exec -T redis redis-cli ping"))).strip() == (
        "PONG"
    )
    marker = uuid4().hex
    await _sh(
        sandbox,
        f"{_PSQL} \"create table survives(v text); insert into survives values ('{marker}')\"",
    )
    images_before = set((await _sh(sandbox, "docker image ls -q")).split())
    assert images_before

    # -- a filesystem-only pause, which is what the provider does, and a resume.
    started = time.monotonic()
    assert await sandbox.pause(keep_memory=False, api_key=_KEY)
    lap("pause", started)
    started = time.monotonic()
    resumed = await AsyncSandbox.connect(sandbox.sandbox_id, timeout=1800, api_key=_KEY)
    lap("resume", started)
    started = time.monotonic()
    await _sh(resumed, _POST_RESUME, timeout=120)
    lap("docker_info_after_resume", started)

    # Images and the volume are on disk still; `db` has no restart policy, so it
    # needs `up`, and comes back from cache with the row it had.
    assert set((await _sh(resumed, "docker image ls -q")).split()) >= images_before
    assert "lemma_postgres_data" in await _sh(resumed, "docker volume ls -q")
    started = time.monotonic()
    await _sh(resumed, _compose("up -d --wait db redis"), timeout=300)
    lap("compose_up_after_resume", started)
    assert (await _sh(resumed, f'{_PSQL} "select v from survives"')).strip() == marker

    # -- disk: `df` reports it, and the janitor's prune gives space back.
    used, size = await _disk_used(resumed)
    assert 0 < used < size
    await _sh(resumed, _compose("down"), timeout=120)
    started = time.monotonic()
    await _sh(
        resumed,
        "docker system prune -af >/dev/null && docker builder prune -af >/dev/null",
        timeout=300,
    )
    lap("prune", started)
    pruned, _ = await _disk_used(resumed)
    assert pruned < used, f"prune freed nothing: {used} -> {pruned} bytes"
    assert (await _sh(resumed, "docker image ls -q")).strip() == ""

    print(
        "timings:",
        timings,
        f"disk: {used / 1e9:.2f} GB used of {size / 1e9:.1f} GB, "
        f"{pruned / 1e9:.2f} GB after prune",
    )
    # Pacing, not correctness: pause and resume are seconds when healthy.
    assert timings["pause"] < 60
    assert timings["resume"] < 60
