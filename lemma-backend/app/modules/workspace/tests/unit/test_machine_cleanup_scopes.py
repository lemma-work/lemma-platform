"""Reset scopes and the disk janitor: what each may touch.

The ``runs`` prune is run for real, in ``sh`` against a temporary tree, with
the runs root pointed there -- the decision is the shell's, so the shell is
what is tested.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services import disk_janitor
from app.modules.workspace.services.machine_lifecycle import (
    AGENT_SCOPES,
    MachineLifecycle,
    scope_command,
)
from sandbox_runtime.paths import RUNS_ROOT


def _run_tree(root: Path, agent: str, run: str, *, age_minutes: int) -> Path:
    directory = root / agent / run
    directory.mkdir(parents=True)
    note = directory / "notes.txt"
    note.write_text("work")
    then = time.time() - age_minutes * 60
    for path in (note, directory):
        os.utime(path, (then, then))
    return directory


def _prune(root: Path, keep: frozenset[str]) -> None:
    command = scope_command("runs", keep=keep)
    assert command is not None
    subprocess.run(
        ["sh", "-c", command.replace(RUNS_ROOT, str(root))], check=True, timeout=30
    )


def test_runs_prune_removes_only_finished_runs_nobody_holds(tmp_path: Path) -> None:
    finished = _run_tree(tmp_path, "reviewer", "old-run", age_minutes=180)
    held = _run_tree(tmp_path, "reviewer", "held-run", age_minutes=180)
    recent = _run_tree(tmp_path, "builder", "busy-run", age_minutes=180)
    # A file written ten minutes ago deep inside: the directory's own mtime is
    # three hours old, which is exactly what used to get a live run deleted.
    (recent / "build").mkdir()
    (recent / "build" / "out.o").write_text("fresh")
    old = time.time() - 180 * 60
    os.utime(recent, (old, old))

    _prune(tmp_path, keep=frozenset({"held-run"}))

    assert not finished.exists()
    assert held.exists(), "a run holding a slot is live"
    assert recent.exists(), "a file written in the last hour means a live run"


def test_runs_prune_with_no_runs_root_is_a_no_op(tmp_path: Path) -> None:
    _prune(tmp_path / "absent", keep=frozenset())


def test_docker_is_not_an_agent_scope() -> None:
    """Docker's prune takes every run's images and build cache."""
    assert "docker" not in AGENT_SCOPES
    assert {"processes", "runs", "caches"} == set(AGENT_SCOPES)


class _Processes:
    def __init__(self) -> None:
        self.ended: list[tuple[UUID, str]] = []
        self.killed_all = 0

    async def end_run(self, machine_id: UUID, run_id: str) -> list[str]:
        self.ended.append((machine_id, run_id))
        return ["p1"]

    async def kill_all(self, machine_id: UUID, *, keep_services: bool) -> list[str]:
        self.killed_all += 1
        return ["p1", "p2", "p3"]


def _machine() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), capabilities=SimpleNamespace(docker=True))


async def test_an_agents_processes_reset_stops_only_its_own_run() -> None:
    processes = _Processes()
    lifecycle = MachineLifecycle(None, processes=processes)  # type: ignore[arg-type]
    machine = _machine()

    [outcome] = await lifecycle.reset(
        machine,  # type: ignore[arg-type]
        ["processes"],
        acting_user=uuid4(),
        run_id="conversation-1",
    )

    assert processes.ended == [(machine.id, "conversation-1")]
    assert processes.killed_all == 0
    assert outcome.detail == "stopped 1 process"


async def test_a_managers_processes_reset_stops_everything_but_services() -> None:
    processes = _Processes()
    lifecycle = MachineLifecycle(None, processes=processes)  # type: ignore[arg-type]

    [outcome] = await lifecycle.reset(
        _machine(),  # type: ignore[arg-type]
        ["processes"],
        acting_user=uuid4(),
    )

    assert processes.killed_all == 1
    assert outcome.detail == "stopped 3 processes"


def test_an_agent_caller_resets_its_conversations_processes() -> None:
    from app.modules.workspace.api.controllers.machines_controller import _agent_run
    from app.modules.workspace.domain.machine_access import CallerKind

    conversation = uuid4()
    agent = SimpleNamespace(kind=CallerKind.AGENT, conversation_id=conversation)
    orphan = SimpleNamespace(kind=CallerKind.AGENT, conversation_id=None)
    person = SimpleNamespace(kind=CallerKind.USER, conversation_id=None)

    assert _agent_run(agent) == str(conversation)  # type: ignore[arg-type]
    assert _agent_run(orphan) == "", "an agent with no conversation owns nothing"  # type: ignore[arg-type]
    assert _agent_run(person) is None  # type: ignore[arg-type]


# --- the janitor --------------------------------------------------------------


def _report(fraction: float) -> disk_janitor.DiskReport:
    return disk_janitor.DiskReport(used_bytes=int(fraction * 100), total_bytes=100)


def _measuring(*fractions: float):
    readings = iter(fractions)

    async def measure(machine_id, *, acting_user):
        return _report(next(readings))

    return measure


async def test_a_full_disk_is_refused_inline() -> None:
    with pytest.raises(MachineConflict) as refused:
        await disk_janitor.refuse_if_full(
            uuid4(), acting_user=uuid4(), measure=_measuring(0.95)
        )

    assert refused.value.code == "MACHINE_DISK_FULL"


async def test_a_disk_under_the_line_is_let_through() -> None:
    report = await disk_janitor.refuse_if_full(
        uuid4(), acting_user=uuid4(), measure=_measuring(0.5)
    )

    assert report == _report(0.5)


async def test_the_janitor_prunes_runs_without_the_live_ones() -> None:
    commands: list[str] = []

    async def shell(machine_id, *, acting_user, command, timeout_seconds=300):
        commands.append(command)

    async def live(machine_id):
        return frozenset({"live-run"})

    await disk_janitor.prune(
        uuid4(),
        acting_user=uuid4(),
        measure=_measuring(0.8, 0.5),
        shell=shell,  # type: ignore[arg-type]
        live=live,
    )

    assert len(commands) == 1, "stops once under the prune line"
    assert "live-run" in commands[0]
