"""A service comes back after a pause from its durable definition, with a
token minted afresh for the claims it first ran with -- and its caller's
variables were never stored in the clear."""

from __future__ import annotations

from uuid import uuid4

from pydantic import SecretStr

from app.modules.workspace.services.local_sandbox_client import LocalProcessRef
from app.modules.workspace.services.machine_processes import MachineProcesses
from app.modules.workspace.services.machine_services import (
    ServiceDefinition,
    TokenClaims,
    _open,
    _seal,
)
from sandbox_runtime.protocol import ProcessState

MACHINE = uuid4()


def test_the_callers_variables_are_ciphertext_at_rest() -> None:
    sealed = _seal({"API_KEY": "s3cret-value"})

    assert sealed is not None and "s3cret-value" not in sealed
    opened = _open(sealed)
    assert isinstance(opened["API_KEY"], SecretStr)
    assert opened["API_KEY"].get_secret_value() == "s3cret-value"


def test_claims_survive_the_round_trip() -> None:
    claims = TokenClaims(
        user_id=uuid4(),
        pod_id=uuid4(),
        workload_type="agent",
        workload_id=uuid4(),
        workload_name="reviewer",
        scope=("files.read",),
        conversation_id=uuid4(),
    )

    assert TokenClaims.from_json(claims.to_json()) == claims


class _Registry:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    async def put(self, machine_id, record) -> None:
        self.records[record.process_id] = record

    async def drop(self, machine_id, process_ids) -> None:
        for process_id in process_ids:
            self.records.pop(process_id, None)

    async def all(self, machine_id):
        return list(self.records.values())


class _Runtime:
    def __init__(self) -> None:
        self.started: list[dict[str, str]] = []

    async def list_processes(self, kind, machine_id):
        return ()

    async def start_process(self, kind, machine_id, *, operation_id, environment, **_):
        self.started.append({v.name: v.value for v in environment})


class _Services:
    def __init__(self, definitions: list[ServiceDefinition]) -> None:
        self.definitions = definitions
        self.recorded: list[dict[str, object]] = []

    async def running(self, machine_id):
        return self.definitions

    async def record(self, machine_id, **values) -> None:
        self.recorded.append(values)


class _Processes(MachineProcesses):
    def __init__(self, services: _Services) -> None:
        super().__init__(registry=_Registry(), services=services)  # type: ignore[arg-type]
        self.runtime = _Runtime()

    def _client(self):
        return self.runtime


async def test_a_restarted_service_gets_a_fresh_token_for_its_own_claims() -> None:
    owner = uuid4()
    claims = TokenClaims(user_id=owner, pod_id=uuid4(), workload_type="agent")
    services = _Services(
        [
            ServiceDefinition(
                machine_id=MACHINE,
                name="web",
                command="npm run dev",
                cwd="/home/user/lemma/site",
                claims=claims,
                started_by=owner,
                restart_on_resume=True,
                process_id=str(uuid4()),
                env={"PORT": SecretStr("3000")},
            ),
            ServiceDefinition(
                machine_id=MACHINE,
                name="once",
                command="true",
                cwd="/home/user/lemma",
                claims=claims,
                started_by=owner,
                restart_on_resume=False,
                process_id=str(uuid4()),
            ),
        ]
    )
    processes = _Processes(services)
    minted: list[TokenClaims] = []

    async def mint(asked: TokenClaims) -> dict[str, str]:
        minted.append(asked)
        return {"LEMMA_TOKEN": "fresh"}

    restarted = await processes.restart_services(MACHINE, mint=mint)

    assert [record.service for record in restarted] == ["web"]
    assert minted == [claims]
    assert processes.runtime.started == [{"LEMMA_TOKEN": "fresh", "PORT": "3000"}]
    assert services.recorded[0]["claims"] == claims


async def test_a_running_service_is_not_started_twice() -> None:
    process = uuid4()
    services = _Services(
        [
            ServiceDefinition(
                machine_id=MACHINE,
                name="web",
                command="npm run dev",
                cwd="/home/user/lemma",
                claims=None,
                started_by=None,
                restart_on_resume=True,
                process_id=str(process),
            )
        ]
    )
    processes = _Processes(services)

    async def alive(kind, machine_id):
        return (LocalProcessRef(operation_id=process, state=ProcessState.RUNNING),)

    processes.runtime.list_processes = alive  # type: ignore[method-assign]

    assert await processes.restart_services(MACHINE) == []


def test_an_agent_can_start_a_service() -> None:
    from app.modules.agent.infrastructure.tools.workspace_cli.models import (
        ExecCommandRequest,
    )

    request = ExecCommandRequest(cmd="npm run dev", service="web")

    assert request.service == "web"


async def test_an_agent_service_outside_the_home_is_refused() -> None:
    import pytest

    from app.modules.workspace.contracts.machine_services import (
        AgentService,
        start_agent_service,
    )
    from app.modules.workspace.domain.machine_errors import MachineConflict

    with pytest.raises(MachineConflict):
        await start_agent_service(
            AgentService(
                machine_id=MACHINE,
                name="web",
                command="true",
                session_cwd="/home/user/lemma",
                workdir="../../../etc",
                base_env={},
                user_id=uuid4(),
                pod_id=None,
                organization_id=None,
                workload_type=None,
                workload_id=None,
                workload_name=None,
                conversation_id=None,
            )
        )
