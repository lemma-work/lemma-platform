"""What the changes route answers, given what the rows and the machine say.

The rows half -- authorization, a repository, a Mac -- runs against Postgres in
``test_conversation_changes_e2e``; here it is a function returning what those
rows would, so each machine outcome is one case.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.application.conversations.changes import (
    CHANGES_TIMED_OUT,
    CHANGES_UNREADABLE,
    ChangesTarget,
    CommandFor,
    ConversationChangesReader,
)
from app.modules.agent.domain.conversation_changes import (
    NOT_CHECKED_OUT_EXIT,
    ConversationChanges,
)
from app.modules.agent.domain.project_repo import ProjectRepo
from sandbox_runtime.paths import WORKSPACE_ROOT

_REPO = ProjectRepo(owner="acme", repo="web", ref="main")
_WORKTREE = f"{WORKSPACE_ROOT}/worktrees/acme/web/abc12345"


def _target() -> ChangesTarget:
    return ChangesTarget(
        conversation_id=uuid4(),
        pod_id=uuid4(),
        owner_id=uuid4(),
        repo=_REPO,
        cwd=_WORKTREE,
        agent_id=None,
        agent_name=None,
        machine_ref=None,
    )


def _rows(answer: ChangesTarget | ConversationChanges):
    async def load(
        *, conversation_id: UUID, user_id: UUID, pod_id: UUID
    ) -> ChangesTarget | ConversationChanges:
        del conversation_id, user_id, pod_id
        return answer

    return load


@dataclass
class _Output:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Machine:
    """A machine that answers with ``output``, and keeps what it was asked."""

    output: _Output | None
    run_workdir: str | None = None
    commands: list[str] = field(default_factory=list)

    async def __call__(
        self, target: ChangesTarget, command_for: CommandFor
    ) -> _Output | None:
        del target
        self.commands.append(command_for(self.run_workdir))
        return self.output


def _ready_stdout() -> str:
    meta = b"branch=lemma/abc12345\nstart=origin/main\nbase=bbb\nhead=hhh\nahead=1\n"
    meta += b"dirty=0\nmore=0\n"
    numstat = b"2\t1\tapp.py\x00"
    status = b"M\x00app.py\x00"
    return "\n".join(
        [
            "lemma-changes 1",
            f"meta {base64.b64encode(meta).decode()}",
            f"numstat {base64.b64encode(numstat).decode()}",
            f"status {base64.b64encode(status).decode()}",
            "untracked ",
            f"patch {base64.b64encode(b'diff --git a/app.py b/app.py').decode()}",
        ]
    )


async def _read(reader: ConversationChangesReader, *, patch_bytes=None):
    return await reader.read(
        conversation_id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        patch_bytes=patch_bytes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["no_repository", "elsewhere"])
async def test_an_answer_the_rows_settle_never_asks_a_machine(state) -> None:
    machine = _Machine(output=_Output(exit_code=0))
    reader = ConversationChangesReader(
        _rows(ConversationChanges(state=state)), reach=machine
    )

    assert (await _read(reader)).state == state
    assert machine.commands == []


@pytest.mark.asyncio
async def test_a_machine_that_is_not_there_to_ask_is_unavailable() -> None:
    """Asleep, refused to a run of the conversation, or unreachable: the
    reach answers None for each, and none of them is woken."""
    reader = ConversationChangesReader(_rows(_target()), reach=_Machine(output=None))

    assert (await _read(reader)).state == "machine_unavailable"


@pytest.mark.asyncio
async def test_a_command_still_running_at_its_deadline_is_a_504_not_an_asleep_machine() -> (
    None
):
    """The machine answered and the read outlived its ceiling: saying the
    machine is asleep would send the person to retry a read still running."""
    reader = ConversationChangesReader(
        _rows(_target()), reach=_Machine(output=_Output(exit_code=None))
    )

    with pytest.raises(DomainError) as refused:
        await _read(reader)

    assert refused.value.status_code == 504
    assert refused.value.code == CHANGES_TIMED_OUT


@pytest.mark.asyncio
async def test_no_checkout_in_the_directory_is_not_checked_out() -> None:
    reader = ConversationChangesReader(
        _rows(_target()),
        reach=_Machine(output=_Output(exit_code=NOT_CHECKED_OUT_EXIT)),
    )

    assert (await _read(reader)).state == "not_checked_out"


@pytest.mark.asyncio
async def test_a_ready_checkout_answers_its_files_and_patch() -> None:
    machine = _Machine(output=_Output(exit_code=0, stdout=_ready_stdout()))
    reader = ConversationChangesReader(_rows(_target()), reach=machine)

    changes = await _read(reader, patch_bytes=4096)

    assert changes.state == "ready"
    assert changes.base_ref == "origin/main"
    assert [(f.path, f.additions, f.deletions) for f in changes.files] == [
        ("app.py", 2, 1)
    ]
    assert changes.patch == "diff --git a/app.py b/app.py"
    # The conversation's own worktree, on the ref it was opened on.
    assert f"cd {_WORKTREE} " in machine.commands[0]
    assert "start=origin/main" in machine.commands[0]


@pytest.mark.asyncio
async def test_on_a_shared_machine_the_runs_own_checkout_is_read() -> None:
    run_dir = f"{WORKSPACE_ROOT}/runs/builder/conversation"
    machine = _Machine(
        output=_Output(exit_code=NOT_CHECKED_OUT_EXIT), run_workdir=run_dir
    )
    reader = ConversationChangesReader(_rows(_target()), reach=machine)

    await _read(reader)

    assert f"cd {run_dir}/repos/acme/web " in machine.commands[0]


@pytest.mark.asyncio
async def test_git_failing_is_a_502_with_what_git_said() -> None:
    reader = ConversationChangesReader(
        _rows(_target()),
        reach=_Machine(output=_Output(exit_code=128, stderr="fatal: bad object HEAD")),
    )

    with pytest.raises(DomainError) as refused:
        await _read(reader)

    assert refused.value.status_code == 502
    assert refused.value.code == CHANGES_UNREADABLE
    assert "fatal: bad object HEAD" in refused.value.message


@pytest.mark.asyncio
async def test_output_the_command_never_prints_is_a_502() -> None:
    reader = ConversationChangesReader(
        _rows(_target()), reach=_Machine(output=_Output(exit_code=0, stdout="hi"))
    )

    with pytest.raises(DomainError) as refused:
        await _read(reader)

    assert refused.value.status_code == 502
