"""A named service is one process: a second under the same name is refused.

The runtime and the registry are both stood in for by the test's own objects,
handed in rather than patched: the registry through the constructor, the
runtime through the one method that builds it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services.local_sandbox_client import LocalProcessRef
from app.modules.workspace.services.machine_processes import (
    MachineProcesses,
    ProcessRecord,
    StartProcess,
)
from sandbox_runtime.protocol import ProcessState

MACHINE = uuid4()


class _Registry:
    def __init__(self) -> None:
        self.records: dict[str, ProcessRecord] = {}

    async def put(self, machine_id: UUID, record: ProcessRecord) -> None:
        self.records[record.process_id] = record

    async def all(self, machine_id: UUID) -> list[ProcessRecord]:
        return list(self.records.values())

    async def drop(self, machine_id: UUID, process_ids: list[str]) -> None:
        for process_id in process_ids:
            self.records.pop(process_id, None)


class _Runtime:
    def __init__(self) -> None:
        self.states: dict[UUID, ProcessState] = {}

    async def start_process(self, kind, machine_id, *, operation_id, **_) -> None:
        self.states[operation_id] = ProcessState.RUNNING

    async def list_processes(self, kind, machine_id) -> tuple[LocalProcessRef, ...]:
        return tuple(
            LocalProcessRef(
                operation_id=operation_id,
                state=state,
                exit_code=0 if state is ProcessState.SUCCEEDED else None,
            )
            for operation_id, state in self.states.items()
        )


class _Processes(MachineProcesses):
    def __init__(self) -> None:
        super().__init__(registry=_Registry())  # type: ignore[arg-type]
        self.runtime = _Runtime()

    def _client(self):
        return self.runtime


def _service(name: str = "web") -> StartProcess:
    return StartProcess(command="npm run dev", cwd="/home/user/lemma", service=name)


async def test_a_second_service_by_the_same_name_is_refused_while_the_first_runs():
    processes = _Processes()
    first = await processes.start_process(MACHINE, _service(), base_env={})

    with pytest.raises(MachineConflict) as refused:
        await processes.start_process(MACHINE, _service(), base_env={})

    assert refused.value.code == "MACHINE_SERVICE_RUNNING"
    assert first.process_id in str(refused.value)


async def test_another_name_or_a_finished_service_does_not_count():
    processes = _Processes()
    first = await processes.start_process(MACHINE, _service(), base_env={})
    await processes.start_process(MACHINE, _service("api"), base_env={})

    processes.runtime.states[UUID(first.process_id)] = ProcessState.SUCCEEDED
    again = await processes.start_process(MACHINE, _service(), base_env={})

    assert again.service == "web"


async def test_a_plain_process_is_never_held_to_a_name():
    processes = _Processes()
    plain = StartProcess(command="sleep 60", cwd="/home/user/lemma")
    await processes.start_process(MACHINE, plain, base_env={})
    await processes.start_process(MACHINE, plain, base_env={})
