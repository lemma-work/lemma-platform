"""A service comes back after a pause from its durable definition, with a
token minted afresh for the claims it first ran with -- and its caller's
variables were never stored in the clear."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import SecretStr

from app.modules.workspace.services.local_sandbox_client import LocalProcessRef
from app.modules.workspace.services.machine_processes import (
    MachineProcesses,
    StartProcess,
)
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
        session_id="shell-1",
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

    async def claim_start(self, machine_id, name, token) -> bool:
        return True

    async def release_start(self, machine_id, name, token) -> None:
        return None


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
        self.forgotten: list[str] = []

    async def running(self, machine_id):
        return self.definitions

    async def record(self, machine_id, **values) -> None:
        self.recorded.append(values)

    async def others_kept(self, machine_id, name) -> int:
        return 0

    async def forget(self, machine_id, name) -> None:
        self.forgotten.append(name)
        self.definitions = [d for d in self.definitions if d.name != name]

    async def forget_all(self, machine_id) -> None:
        self.forgotten.extend(d.name for d in self.definitions)
        self.definitions = []


async def _anyone(definition: ServiceDefinition) -> bool:
    return True


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

    restarted = await processes.restart_services(
        MACHINE, may_restart=_anyone, mint=mint
    )

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

    assert await processes.restart_services(MACHINE, may_restart=_anyone) == []


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


def _service(name: str, process: UUID) -> ServiceDefinition:
    return ServiceDefinition(
        machine_id=MACHINE,
        name=name,
        command="python3 -m http.server 8001",
        cwd="/home/user/lemma",
        claims=None,
        started_by=None,
        restart_on_resume=True,
        process_id=str(process),
    )


async def test_a_service_the_pause_lost_is_started_again() -> None:
    """QA on a real E2B machine: after a pause E2B still lists the process it
    lost, as ``unknown``, and reading that as alive left the service down."""
    lost = uuid4()
    processes = _Processes(_Services([_service("web", lost)]))

    async def listed(kind, machine_id):
        return (LocalProcessRef(operation_id=lost, state=ProcessState.UNKNOWN),)

    processes.runtime.list_processes = listed  # type: ignore[method-assign]

    restarted = await processes.restart_services(MACHINE, may_restart=_anyone)

    assert [record.service for record in restarted] == ["web"]


async def test_a_service_that_failed_on_its_own_is_not_started_on_every_resume() -> (
    None
):
    """The pause did not end it; starting it again only fails again."""
    failed = uuid4()
    processes = _Processes(_Services([_service("web", failed)]))

    async def listed(kind, machine_id):
        return (
            LocalProcessRef(
                operation_id=failed, state=ProcessState.FAILED, exit_code=2
            ),
        )

    processes.runtime.list_processes = listed  # type: ignore[method-assign]

    assert await processes.restart_services(MACHINE, may_restart=_anyone) == []


def _web(owner: UUID, *, name: str = "web") -> ServiceDefinition:
    return ServiceDefinition(
        machine_id=MACHINE,
        name=name,
        command="npm run dev",
        cwd="/home/user/lemma",
        claims=TokenClaims(user_id=owner, pod_id=uuid4()),
        started_by=owner,
        restart_on_resume=True,
        process_id=str(uuid4()),
    )


async def test_a_service_whose_starter_lost_the_machine_is_forgotten_not_restarted() -> (
    None
):
    """A restart mints a fresh token: a person who left the pod, or an agent
    whose grant was taken, must not get one by their service coming back."""
    gone, kept = uuid4(), uuid4()
    services = _Services([_web(gone, name="old"), _web(kept, name="web")])
    processes = _Processes(services)
    minted: list[UUID] = []

    async def mint(asked: TokenClaims) -> dict[str, str]:
        minted.append(asked.user_id)
        return {}

    async def still_allowed(definition: ServiceDefinition) -> bool:
        return definition.started_by == kept

    restarted = await processes.restart_services(
        MACHINE, may_restart=still_allowed, mint=mint
    )

    assert [record.service for record in restarted] == ["web"]
    assert minted == [kept]
    assert services.forgotten == ["old"]


async def test_one_service_that_fails_to_restart_does_not_stop_the_rest() -> None:
    unlucky, owner = uuid4(), uuid4()
    processes = _Processes(
        _Services([_web(unlucky, name="broken"), _web(owner, name="web")])
    )

    async def mint(asked: TokenClaims) -> dict[str, str]:
        if asked.user_id == unlucky:
            raise RuntimeError("the identity service did not answer")
        return {}

    restarted = await processes.restart_services(
        MACHINE, may_restart=_anyone, mint=mint
    )

    assert [record.service for record in restarted] == ["web"]


async def test_forgetting_a_machines_services_drops_definitions_and_entries() -> None:
    owner = uuid4()
    services = _Services([_web(owner)])
    processes = _Processes(services)
    await processes.start_process(
        MACHINE,
        StartProcess(command="npm run dev", cwd="/home/user/lemma", service="web"),
        base_env={},
    )
    await processes.start_process(
        MACHINE, StartProcess(command="ls", cwd="/home/user/lemma"), base_env={}
    )

    await processes.forget_services(MACHINE)

    assert services.forgotten == ["web"]
    assert [r.service for r in await processes.registry.all(MACHINE)] == [None]


def test_the_starter_is_rebuilt_as_the_caller_that_started_it() -> None:
    from app.modules.workspace.domain.machine_access import CallerKind
    from app.modules.workspace.services.machine_service_starter import starter_of

    person, pod, agent, conversation = uuid4(), uuid4(), uuid4(), uuid4()

    assert starter_of(None, None) is None
    viewer = starter_of(None, person)
    assert viewer is not None and viewer.kind is CallerKind.USER
    own = starter_of(TokenClaims(user_id=person, pod_id=pod), person)
    assert own is not None and (own.kind, own.user_id) == (CallerKind.USER, person)
    run = starter_of(
        TokenClaims(
            user_id=person,
            pod_id=pod,
            workload_type="AGENT",
            workload_id=agent,
            conversation_id=conversation,
        ),
        person,
    )
    assert run is not None
    assert (run.kind, run.workload_id, run.pod_id, run.conversation_id) == (
        CallerKind.AGENT,
        agent,
        pod,
        conversation,
    )
    assistant = starter_of(
        TokenClaims(user_id=person, pod_id=pod, workload_type="agent"), person
    )
    assert assistant is not None and assistant.workload_id == pod
    function = starter_of(
        TokenClaims(
            user_id=person, pod_id=pod, workload_type="function", workload_id=agent
        ),
        person,
    )
    assert function is not None and function.kind is CallerKind.FUNCTION


def test_an_agent_service_keeps_the_session_its_token_was_minted_under() -> None:
    from app.modules.workspace.contracts.machine_services import (
        AgentService,
        _start_request,
    )

    request = _start_request(
        AgentService(
            machine_id=MACHINE,
            name="web",
            command="npm run dev",
            session_cwd="/home/user/lemma",
            workdir=None,
            base_env={},
            user_id=uuid4(),
            pod_id=uuid4(),
            organization_id=None,
            workload_type="agent",
            workload_id=uuid4(),
            workload_name="builder",
            conversation_id=None,
            session_id="shell-abc",
        )
    )

    assert request.claims is not None
    assert request.claims.session_id == "shell-abc"
