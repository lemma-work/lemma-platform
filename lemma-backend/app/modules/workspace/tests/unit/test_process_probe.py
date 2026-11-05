"""Reading one process's state, and the four honest answers that can give.

The distinction that matters is between "it stopped" and "I could not tell".
A durable wait resolves on the first and must not on the second: an unreachable
provider says nothing about the process, and treating silence as an ending is
the mistake the idle sweep already made once, releasing a sandbox mid-command.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderGone,
    ProviderNotReady,
)
from app.modules.workspace.services.process_probe import (
    ProcessProbeStatus,
    probe_process,
)
from sandbox_runtime.protocol import ProcessState

_PROCESS_ID = "proc-1"


class _Uow:
    def __init__(self, instances):
        self._instances = instances

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def __call__(self):
        return self


def _repository(*, sandboxes, instance):
    """The two reads the probe makes, injected rather than patched in.

    Replacing the name inside the subject's module would have left it replaced
    for everything that ran afterwards in the same process, which is a spooky
    failure two files later rather than a readable one here.
    """

    class _Repository:
        def __init__(self, _uow):
            pass

        async def list_for_owner(self, **_kwargs):
            return sandboxes

        async def current_instance(self, _sandbox_id):
            return instance

    return _Repository


def _descriptor(state, exit_code=None):
    return ProcessDescriptor(
        process_id=_PROCESS_ID,
        state=state,
        exit_code=exit_code,
        started_at=datetime.now(timezone.utc),
        command="npm ci",
    )


def _provider(*, processes=None, inspect_result=SimpleNamespace(), raises=None):
    async def _inspect(_provider_id, *, deadline_at):
        if raises is not None:
            raise raises
        return inspect_result

    async def _list_processes(_instance, *, deadline_at):
        if raises is not None:
            raise raises
        return processes or []

    return SimpleNamespace(inspect=_inspect, list_processes=_list_processes)


@pytest.fixture
def one_live_sandbox():
    sandbox = SimpleNamespace(id=uuid4())
    instance = SimpleNamespace(provider_id="sbx-1")
    return _repository(sandboxes=[sandbox], instance=instance)


@pytest.mark.asyncio
async def test_a_running_process_reads_as_running(one_live_sandbox):
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(processes=[_descriptor(ProcessState.RUNNING)]),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.RUNNING


@pytest.mark.asyncio
async def test_an_exited_process_reads_as_finished_with_its_code(one_live_sandbox):
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(processes=[_descriptor(ProcessState.FAILED, exit_code=2)]),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.FINISHED
    assert probe.exit_code == 2


@pytest.mark.asyncio
async def test_a_cancelled_process_with_no_exit_code_still_reads_as_finished(
    one_live_sandbox,
):
    """E2B records a cancelled process with `exit_code=None`.

    Reading finishedness off the exit code alone made every process an agent
    killed look alive for the hour the output buffer retains it — and agents
    kill processes exactly when one looks stuck.
    """
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(processes=[_descriptor(ProcessState.CANCELLED)]),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.FINISHED


@pytest.mark.asyncio
async def test_a_process_the_sandbox_no_longer_lists_reads_as_gone(one_live_sandbox):
    """Aged out of the index, or never there. Nothing can tell those apart."""
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(processes=[]),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.GONE


@pytest.mark.asyncio
async def test_an_unreachable_provider_is_not_read_as_an_ending(one_live_sandbox):
    """The distinction the whole type exists for.

    UNREADABLE keeps the wait going; GONE ends it. Collapsing them would wake an
    agent to "your process is gone" every time the provider had a blip.
    """
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(raises=ProviderGone("nope")),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.UNREADABLE


@pytest.mark.asyncio
async def test_an_empty_metadata_listing_is_not_read_as_an_ending(one_live_sandbox):
    """`inspect` returning None is the same transient, one layer up."""
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(inspect_result=None),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.UNREADABLE


@pytest.mark.asyncio
async def test_a_user_with_no_live_sandbox_reads_as_gone():
    """Released compute takes the process with it; the outcome is unknowable."""
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(),
        uow_factory=_Uow(None),
        repository=_repository(sandboxes=[], instance=None),
    )

    assert probe.status is ProcessProbeStatus.GONE


class _PerSandboxProvider:
    """Answers differently per sandbox: the shape a desktop-plus-cloud user has."""

    def __init__(self, answers):
        self._answers = answers

    async def inspect(self, provider_id, *, deadline_at):
        del deadline_at
        answer = self._answers[provider_id]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(provider_id=provider_id)

    async def list_processes(self, instance, *, deadline_at):
        del deadline_at
        return self._answers[instance.provider_id]


def _two_live_sandboxes():
    sandboxes = [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]
    instances = {
        sandboxes[0].id: SimpleNamespace(provider_id="sbx-offline"),
        sandboxes[1].id: SimpleNamespace(provider_id="sbx-cloud"),
    }

    class _Repository:
        def __init__(self, _uow):
            pass

        async def list_for_owner(self, **_kwargs):
            return sandboxes

        async def current_instance(self, sandbox_id):
            return instances[sandbox_id]

    return _Repository


@pytest.mark.asyncio
async def test_an_unreadable_sandbox_does_not_hide_the_one_that_lists_the_process():
    """The first sandbox not answering used to end the probe as UNREADABLE, so
    a process that finished in the second was waited on to its deadline."""
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_PerSandboxProvider(
            {
                "sbx-offline": ProviderNotReady("the Mac is asleep"),
                "sbx-cloud": [_descriptor(ProcessState.SUCCEEDED, exit_code=0)],
            }
        ),
        uow_factory=_Uow(None),
        repository=_two_live_sandboxes(),
    )

    assert probe.status is ProcessProbeStatus.FINISHED
    assert probe.exit_code == 0


@pytest.mark.asyncio
async def test_unreadable_wins_over_gone_when_no_sandbox_lists_the_process():
    """The sandbox that did not answer may be the one running it."""
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_PerSandboxProvider(
            {"sbx-offline": ProviderNotReady("asleep"), "sbx-cloud": []}
        ),
        uow_factory=_Uow(None),
        repository=_two_live_sandboxes(),
    )

    assert probe.status is ProcessProbeStatus.UNREADABLE


@pytest.mark.asyncio
async def test_a_lost_watch_the_provider_could_not_settle_reads_as_unreadable(
    one_live_sandbox,
):
    """UNKNOWN is "the provider stopped watching", not "still running". Read as
    running, it was waited on for the whole deadline with nothing to say."""
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(processes=[_descriptor(ProcessState.UNKNOWN)]),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.UNREADABLE


@pytest.mark.asyncio
async def test_a_process_that_exited_unobserved_reads_as_finished(one_live_sandbox):
    probe = await probe_process(
        user_id=uuid4(),
        process_id=_PROCESS_ID,
        provider=_provider(
            processes=[
                replace(_descriptor(ProcessState.UNKNOWN), exited_unobserved=True)
            ]
        ),
        uow_factory=_Uow(None),
        repository=one_live_sandbox,
    )

    assert probe.status is ProcessProbeStatus.FINISHED
    assert probe.exit_code is None
