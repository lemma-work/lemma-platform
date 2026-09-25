"""Who gets host execution, and what their run is given when they do.

The truth table of docs/architecture/desktop-host-execution.md §2, row by row,
with each fact the rules read stated by the test through
``HostExecutionFacts`` rather than patched in. Then §7: the tools an Agent Host
run loses and the prompt a host run is shown.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from sandbox_runtime.errors import SandboxRejected

from app.modules.agent.domain.agent_host import AGENT_HOST_SESSION_METADATA_KEY
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.domain.prompt_directories import _directory_sections
from app.modules.agent.domain.prompts import load_agent_host_runtime_prompt
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.infrastructure.agent_host.host_execution import (
    host_execution_of,
)
from app.modules.agent.infrastructure.agent_host.repository import _stored_capacity
from app.modules.agent.services.host_execution_selection import (
    HostExecutionFacts,
    choose_host_workspace,
    default_folder,
    host_runs_native_commands,
    recorded_host_workspace,
    triggered_by_run_user,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.browser.vm_browser import vm_browser_toolset
from app.modules.agent.tools.tool_assembler import (
    RunToolAssembler,
    _for_host_execution,
)
from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
    is_workspace_cli_toolset,
)
from app.modules.workspace.contracts.host_execution import (
    HostWorkspace,
    host_sandbox_id,
)

#: The user this Mac's Agent Host is paired to.
PAIRED = uuid4()
#: Somebody else on the same installation, with no host of their own.
TEAMMATE = uuid4()
HOST = uuid4()
ROOT = "/Users/owner/lemma/c/2026-09-25/abc12345"


class Facts:
    """The world, as one row of the truth table states it."""

    def __init__(
        self,
        *,
        desktop: bool = True,
        hosts: dict[UUID, UUID] | None = None,
        host: UUID | None = HOST,
        opens: bool = True,
    ) -> None:
        self.desktop = desktop
        # Which usable host each user is paired to. `host=None` states "the
        # paired user's host is offline, off or unavailable".
        self.hosts = hosts if hosts is not None else ({PAIRED: host} if host else {})
        self.opens = opens
        self.opened: list[dict] = []
        self.records: dict[UUID, dict] = {}
        self.host_lookups: list[tuple[UUID, UUID | None]] = []
        # Sources of the runs before the one being chosen for, newest first.
        self.earlier: list[str | None] = ["user_message"]

    def build(self) -> HostExecutionFacts:
        async def usable_host(
            user_id: UUID, conversation_id: UUID | None
        ) -> UUID | None:
            self.host_lookups.append((user_id, conversation_id))
            return self.hosts.get(user_id)

        async def recorded(run_id: UUID) -> dict | None:
            return self.records.get(run_id)

        async def record(run_id: UUID, value: dict) -> None:
            self.records[run_id] = value

        async def open_workspace(**kwargs) -> HostWorkspace:
            self.opened.append(kwargs)
            if not self.opens:
                raise SandboxRejected("This Mac is not connected")
            return HostWorkspace(
                sandbox_id=host_sandbox_id(kwargs["conversation_id"]), root=ROOT
            )

        async def earlier_sources(
            conversation_id: UUID, run_id: UUID
        ) -> list[str | None]:
            return self.earlier

        return HostExecutionFacts(
            earlier_sources=earlier_sources,
            is_desktop=lambda: self.desktop,
            usable_host=usable_host,
            open_workspace=open_workspace,
            recorded=recorded,
            record=record,
        )


def _conversation(user_id: UUID = PAIRED, **metadata) -> Conversation:
    return Conversation(
        user_id=user_id,
        pod_id=uuid4(),
        metadata={"cwd": "/home/user/lemma/c/2026-09-25/abc12345", **metadata},
        created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


def _run(conversation: Conversation, source: str | None = "user_message") -> AgentRun:
    return AgentRun(
        conversation_id=conversation.id,
        started_at=datetime.now(timezone.utc),
        metadata={"source": source} if source else {},
    )


async def _choose(
    facts: Facts,
    *,
    conversation: Conversation | None = None,
    source: str | None = "user_message",
    user_id: UUID = PAIRED,
    run: AgentRun | None = None,
):
    conversation = conversation or _conversation()
    return await choose_host_workspace(
        conversation=conversation,
        agent_run=run or _run(conversation, source),
        user_id=user_id,
        facts=facts.build(),
    )


# ------------------------------------------------------------- truth table


async def test_the_paired_users_own_message_runs_on_their_mac():
    facts = Facts()
    chosen = await _choose(facts)

    assert chosen is not None and chosen.root == ROOT
    assert facts.opened[0]["host_id"] == HOST
    assert facts.opened[0]["owner_id"] == PAIRED
    assert (facts.opened[0]["day"], facts.opened[0]["slug"]) == (
        "2026-09-25",
        "abc12345",
    )
    assert facts.opened[0]["root_hint"] is None


async def test_a_user_with_no_paired_host_never_runs_on_the_host():
    """Somebody else on the same installation: no pairing, no host."""
    facts = Facts()
    conversation = _conversation(user_id=TEAMMATE)

    assert await _choose(facts, conversation=conversation, user_id=TEAMMATE) is None
    assert facts.opened == []


async def test_each_user_runs_only_on_the_host_paired_to_them():
    """No account is special: a teammate with a host of their own uses it."""
    theirs = uuid4()
    facts = Facts(hosts={PAIRED: HOST, TEAMMATE: theirs})
    conversation = _conversation(user_id=TEAMMATE)

    assert await _choose(facts, conversation=conversation, user_id=TEAMMATE)
    assert facts.opened[0]["host_id"] == theirs
    assert facts.opened[0]["owner_id"] == TEAMMATE


async def test_a_run_not_acting_as_the_conversations_user_does_not_qualify():
    """A run acting as someone other than the conversation's user never does,
    even when that someone has a paired host."""
    facts = Facts(hosts={PAIRED: HOST, TEAMMATE: uuid4()})
    assert await _choose(facts, user_id=TEAMMATE) is None
    assert facts.opened == []


@pytest.mark.parametrize("platform", ["slack", "email", "telegram", "whatsapp"])
async def test_an_inbound_channel_run_never_runs_on_the_host(platform):
    facts = Facts()
    conversation = _conversation(surface_platform=platform)

    assert await _choose(facts, conversation=conversation) is None
    assert facts.opened == []


async def test_a_host_that_is_offline_or_has_host_execution_off_gives_the_vm():
    # `usable_host` is None for offline, toggled off, and not available alike;
    # how a stored report reads as usable is tested below.
    facts = Facts(host=None)
    assert await _choose(facts) is None
    assert facts.opened == []


async def test_a_mac_that_cannot_open_the_workspace_gives_the_vm_before_anything_ran():
    facts = Facts(opens=False)
    assert await _choose(facts) is None
    assert len(facts.opened) == 1


async def test_a_hosted_deployment_never_runs_on_a_host():
    facts = Facts(desktop=False)
    assert await _choose(facts) is None
    assert facts.opened == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("user_message", True),
        ("queued_messages", True),
        ("manual_retry", True),
        ("approval_resume", True),
        ("person", True),
        ("subagent", False),
        # A teammate's reply must not drive commands on the user's Mac.
        ("message_replies", False),
        (None, False),
        ("something_new", False),
    ],
)
def test_which_run_sources_count_as_the_run_user(source, expected):
    conversation = _conversation()
    assert triggered_by_run_user(conversation, _run(conversation, source)) is expected


@pytest.mark.parametrize(
    ("earlier", "expected"),
    [
        (["user_message"], True),
        # Wakes chain: the first run that is not a wake decides.
        (["agent_wait", "wait_resume", "manual_retry"], True),
        (["message_replies"], False),
        (["agent_wait", "message_replies", "user_message"], False),
        (["something_new"], False),
        ([], False),
        (["agent_wait"], False),
    ],
)
@pytest.mark.parametrize("source", ["agent_wait", "wait_resume"])
def test_a_wake_qualifies_only_through_the_run_it_continues(source, earlier, expected):
    conversation = _conversation()
    run = _run(conversation, source)
    assert (
        triggered_by_run_user(conversation, run, earlier_sources=earlier) is expected
    )
    # Without the earlier runs, a wake never qualifies.
    assert not triggered_by_run_user(conversation, run)


@pytest.mark.parametrize(
    "metadata",
    [
        {"source": "WORKFLOW_RUN", "workflow_run_id": str(uuid4())},
        {"workflow_run_id": str(uuid4())},
        {"source": "SCHEDULE", "started_by": "SCHEDULE"},
        {"started_by": "SCHEDULE"},
        {"source": "agent_surfaces"},
        {"source": "notification"},
    ],
    ids=["workflow", "workflow-id-only", "schedule", "started-by", "surface", "notice"],
)
@pytest.mark.parametrize("source", ["user_message", "agent_wait", "wait_resume"])
async def test_a_conversation_something_else_opened_never_runs_on_the_host(
    metadata, source
):
    """A workflow's conversation waking from its wait, or a message typed into
    it, is still the workflow's: no run in it executes on the Mac."""
    facts = Facts()
    conversation = _conversation(**metadata)

    assert await _choose(facts, conversation=conversation, source=source) is None
    assert facts.opened == []


async def test_a_wake_of_the_users_own_work_runs_on_their_mac():
    facts = Facts()
    facts.earlier = ["agent_wait", "user_message"]
    assert await _choose(facts, source="agent_wait") is not None


async def test_a_wake_of_a_teammates_reply_stays_in_the_vm():
    facts = Facts()
    facts.earlier = ["message_replies", "user_message"]
    assert await _choose(facts, source="agent_wait") is None
    assert facts.opened == []


def test_a_sub_agent_conversation_is_not_a_person_at_the_keyboard():
    conversation = _conversation(is_sub_agent=True)
    assert not triggered_by_run_user(conversation, _run(conversation))


async def test_a_bound_folder_is_the_root_hint():
    facts = Facts()
    conversation = _conversation(
        **{AGENT_HOST_SESSION_METADATA_KEY: {"host_cwd": "/Users/owner/code/app"}}
    )
    await _choose(facts, conversation=conversation)
    assert facts.opened[0]["root_hint"] == "/Users/owner/code/app"


def test_a_conversation_without_a_dated_cwd_still_gets_a_folder():
    conversation = _conversation(cwd="/home/user/lemma/repos/o/r")
    day, slug = default_folder(conversation)
    assert day == "2026-09-25" and slug == conversation.id.hex[:8]


async def test_agent_host_runs_drop_lemmas_command_tools_only_with_a_paired_host():
    assert await host_runs_native_commands(_conversation(), facts=Facts().build())
    assert not await host_runs_native_commands(
        _conversation(user_id=TEAMMATE), facts=Facts().build()
    )
    assert not await host_runs_native_commands(
        _conversation(), facts=Facts(host=None).build()
    )
    assert not await host_runs_native_commands(
        _conversation(), facts=Facts(desktop=False).build()
    )


@pytest.mark.parametrize(
    ("stored", "usable"),
    [
        ({"host_execution": {"enabled": True, "available": True}}, True),
        ({"host_execution": {"enabled": False, "available": True}}, False),
        ({"host_execution": {"enabled": True, "available": False}}, False),
        ({}, False),
        ({"host_execution": "garbage"}, False),
    ],
    ids=["on", "toggle-off", "not-available", "old-host", "malformed"],
)
def test_a_stored_host_report_is_usable_only_when_on_and_available(stored, usable):
    host = SimpleNamespace(capacity={"max_runs": 1, **stored})
    assert host_execution_of(host).usable is usable


def test_run_slots_and_the_host_execution_report_share_capacity_without_clobbering():
    on = {"enabled": True, "platform": "macos", "available": True}
    slots = {"max_runs": 1, "active_runs": 0, "available_runs": 1}

    hello = _stored_capacity({}, slots, on)
    assert hello == {**slots, "host_execution": on}

    # A control frame without the report: new slots, the report kept.
    busier = {"max_runs": 1, "active_runs": 1, "available_runs": 0}
    assert _stored_capacity(hello, busier, None) == {**busier, "host_execution": on}

    # With it: the report replaced, the slots as sent.
    off = {**on, "enabled": False}
    assert _stored_capacity(hello, slots, off) == {**slots, "host_execution": off}

    # A host too old to report it stores slots alone.
    assert _stored_capacity(None, slots, None) == slots


# ------------------------------------------------------------- tool filtering


async def test_the_assembler_withholds_only_the_workspace_command_tools():
    conversation = SimpleNamespace(id=uuid4(), metadata={})
    assembler = RunToolAssembler(None)

    everything = await assembler.assemble(agent=None, conversation=conversation)
    native = await assembler.assemble(
        agent=None, conversation=conversation, host_execution="native"
    )
    sandbox = await assembler.assemble(
        agent=None, conversation=conversation, host_execution="sandbox"
    )

    assert any(is_workspace_cli_toolset(t) for t in everything)
    assert vm_browser_toolset not in everything
    # An Agent Host run loses the shell and gains the VM browser in its place.
    assert not any(is_workspace_cli_toolset(t) for t in native)
    assert native == [t for t in everything if not is_workspace_cli_toolset(t)] + [
        vm_browser_toolset
    ]
    # An in-process host run keeps its shell (now on the Mac) and gains it too.
    assert sandbox == [*everything, vm_browser_toolset]


def test_no_browser_tool_for_an_agent_that_never_had_a_shell():
    assert _for_host_execution([], "native") == []
    assert _for_host_execution([], "sandbox") == []


# ------------------------------------------------------------------- prompts


def _ctx(**fields) -> ConversationContext:
    return ConversationContext(
        user_id=PAIRED,
        pod_id=uuid4(),
        conversation_id=uuid4(),
        workspace_cwd="/home/user/lemma/c/2026-09-25/abc12345",
        **fields,
    )


def test_a_host_run_is_told_it_is_on_the_users_mac_and_where():
    ctx = _ctx(host_workspace=HostWorkspace(sandbox_id=uuid4(), root=ROOT))
    sections = _directory_sections(
        ctx=ctx,
        conversation=_conversation(),
        enabled={AgentToolset.WORKSPACE_CLI, AgentToolset.POD},
        runs_as_remote_process=False,
    )
    text = "\n".join(sections)

    assert "on the user's own Mac" in text
    assert f"`{ROOT}`" in text
    assert "separate machine" in text and "localhost" in text
    assert "/home/user/lemma" not in sections[0]
    assert ctx.get_workspace_cwd() == ROOT


def test_a_vm_run_keeps_its_sandbox_section():
    sections = _directory_sections(
        ctx=_ctx(),
        conversation=_conversation(),
        enabled={AgentToolset.WORKSPACE_CLI},
        runs_as_remote_process=False,
    )
    assert "user's own Mac" not in sections[0]
    assert "/home/user/lemma/c/2026-09-25/abc12345" in sections[0]


def test_an_agent_host_run_with_host_execution_is_not_sent_to_sandbox_tools():
    sections = _directory_sections(
        ctx=_ctx(host_runs_native_commands=True),
        conversation=_conversation(),
        enabled={AgentToolset.WORKSPACE_CLI},
        runs_as_remote_process=True,
    )
    assert "no Lemma sandbox execution tools" in sections[0]

    runtime = load_agent_host_runtime_prompt(host_execution=True)
    assert "lemma_exec_command" not in runtime
    assert "on the user's own Mac" in runtime
    # The sections that are not about commands are the shared ones.
    assert "ends the turn and resumes later" in runtime
    assert "lemma_exec_command" in load_agent_host_runtime_prompt()


# ------------------------------------------------------ recorded on the run


async def test_the_choice_is_recorded_on_the_run_either_way():
    facts = Facts()
    conversation = _conversation()
    on_host, in_vm = _run(conversation), _run(conversation, "subagent")

    await _choose(facts, conversation=conversation, run=on_host)
    await _choose(facts, conversation=conversation, run=in_vm)

    assert facts.records[on_host.id] == {
        "target": "host",
        # The record is what later routes the sandbox's ops to this Mac.
        "host_id": str(HOST),
        "sandbox_id": str(host_sandbox_id(conversation.id)),
        "root": ROOT,
    }
    assert facts.records[in_vm.id] == {"target": "vm"}


async def test_a_reclaimed_run_reuses_its_choice_even_with_the_mac_gone():
    """§2: never moves mid-flight. The ops then answer host_offline."""
    facts = Facts()
    conversation = _conversation()
    run = _run(conversation)
    first = await _choose(facts, conversation=conversation, run=run)

    facts.host = None  # the Mac went away before the worker reclaimed the run
    again = await _choose(facts, conversation=conversation, run=run)

    assert again == first
    assert len(facts.opened) == 1
    assert len(facts.host_lookups) == 1


async def test_the_host_is_asked_for_with_the_conversation_so_it_can_keep_its_mac():
    """With several Macs, the conversation's last one is preferred; the lookup
    is told which conversation (host_execution_host_id)."""
    facts = Facts()
    conversation = _conversation()
    await _choose(facts, conversation=conversation)
    assert facts.host_lookups == [(PAIRED, conversation.id)]


async def test_a_reclaimed_vm_run_stays_in_the_vm_when_the_mac_appears():
    facts = Facts(host=None)
    conversation = _conversation()
    run = _run(conversation)
    assert await _choose(facts, conversation=conversation, run=run) is None

    facts.host = HOST
    assert await _choose(facts, conversation=conversation, run=run) is None
    assert facts.opened == []


async def test_an_approved_tool_runs_where_its_paused_run_ran():
    facts = Facts()
    conversation = _conversation()
    run = _run(conversation)
    chosen = await _choose(facts, conversation=conversation, run=run)
    facts.host = None

    assert await recorded_host_workspace(run.id, facts=facts.build()) == chosen
    # A run with nothing recorded selects nothing here: the approval path never
    # decides afresh.
    assert await recorded_host_workspace(uuid4(), facts=facts.build()) is None
    assert len(facts.host_lookups) == 1


async def test_an_unreadable_host_record_is_refused_not_read_as_the_vm():
    facts = Facts()
    run_id = uuid4()
    facts.records[run_id] = {"target": "host"}
    with pytest.raises(ValueError):
        await recorded_host_workspace(run_id, facts=facts.build())


async def test_nothing_is_recorded_off_desktop():
    facts = Facts(desktop=False)
    await _choose(facts)
    assert facts.records == {}
