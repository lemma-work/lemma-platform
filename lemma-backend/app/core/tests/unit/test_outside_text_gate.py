"""An agent reading strangers' words asks its person before it acts.

A member's run in a group is shown what people outside the pod wrote there
(``core.authorization.outside_text``). Each test pins one rule of the gate the
authorizer applies while such a run's conversation is marked:

* anything beyond reading and the pod's own record and file writes is refused
  with an approvable code, until the person approves it;
* reads, record writes and file writes go through unasked;
* nothing is asked of a conversation that is not marked, of a person acting
  for themselves, or of a function an approved call runs;
* the mark's store failing answers "not marked".
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.authorization import authorizer as authorizer_module
from app.core.authorization import outside_text
from app.core.authorization import outside_text_gate as gate_module
from app.core.authorization.authorizer import Authorizer
from app.core.authorization.context import (
    Assistant,
    Context,
    Delegation,
    Person,
    PrincipalRef,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
    Workload,
    WorkloadKind,
)
from app.core.authorization.outside_text import OUTSIDE_TEXT_REQUIRES_APPROVAL
from app.core.authorization.permissions import Permissions
from app.core.authorization.session_approvals import APPROVABLE_REFUSALS

pytestmark = pytest.mark.unit

_MEMBER = frozenset({PrincipalRef("POD_MEMBER", uuid4())})
_HELD = frozenset(
    {
        Permissions.CHANNEL_WRITE,
        Permissions.CONNECTOR_ACCOUNT_USE,
        Permissions.DATASTORE_RECORD_READ,
        Permissions.DATASTORE_RECORD_WRITE,
        Permissions.FOLDER_WRITE,
        Permissions.FUNCTION_EXECUTE,
    }
)


@pytest.fixture
def marked(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Which conversations read outside text, and which permissions the person
    approved for the session -- both stores the gate asks, held in memory."""
    state: dict[str, bool] = {"marked": True, "approved": False}

    async def has_read(session_id: str | None) -> bool:
        return state["marked"] and session_id is not None

    async def has_approval(**_: object) -> bool:
        return state["approved"]

    monkeypatch.setattr(gate_module, "has_read_outside_text", has_read)
    monkeypatch.setattr(authorizer_module, "has_session_approval", has_approval)
    return state


def _agent_ctx(
    authorizer: Authorizer, *, session_id: str = "conversation-1"
) -> Context:
    user_id = uuid4()
    return Context(
        actor=Assistant(user_id=user_id, delegation=Delegation(session_id=session_id)),
        actor_id=f"agent:{uuid4()}",
        user_id=user_id,
        pod_id=uuid4(),
        principal_refs=_MEMBER,
        permission_ids=_HELD,
        authorizer=authorizer,
    )


def _table(ctx: Context) -> ResourceRef:
    return ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=uuid4(),
        pod_id=ctx.pod_id,
        visibility=ResourceVisibility.POD,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission_id",
    [
        Permissions.CHANNEL_WRITE,
        Permissions.CONNECTOR_ACCOUNT_USE,
        Permissions.FUNCTION_EXECUTE,
    ],
)
async def test_an_agent_reading_outside_text_asks_before_it_acts(
    marked: dict[str, bool], permission_id: str
) -> None:
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    decision = await authorizer.authorize(_agent_ctx(authorizer), permission_id)

    assert not decision.allowed
    assert decision.reason_code == OUTSIDE_TEXT_REQUIRES_APPROVAL
    # An approval answers it, so the sandbox's refusals are kept for the card.
    assert OUTSIDE_TEXT_REQUIRES_APPROVAL in APPROVABLE_REFUSALS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission_id",
    [Permissions.DATASTORE_RECORD_READ, Permissions.DATASTORE_RECORD_WRITE],
)
async def test_reading_and_writing_the_pods_records_are_not_asked(
    marked: dict[str, bool], permission_id: str
) -> None:
    """The person's own "add this" is what they asked; it stays a write."""
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    ctx = _agent_ctx(authorizer)

    decision = await authorizer.authorize(ctx, permission_id, _table(ctx))

    assert decision.allowed


@pytest.mark.asyncio
async def test_writing_the_pods_files_is_not_asked(marked: dict[str, bool]) -> None:
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    decision = await authorizer.authorize(
        _agent_ctx(authorizer), Permissions.FOLDER_WRITE
    )

    assert decision.allowed


@pytest.mark.asyncio
async def test_an_approval_lets_the_action_through(marked: dict[str, bool]) -> None:
    marked["approved"] = True
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    decision = await authorizer.authorize(
        _agent_ctx(authorizer), Permissions.CHANNEL_WRITE
    )

    assert decision.allowed


@pytest.mark.asyncio
async def test_a_conversation_reading_nothing_from_outside_is_not_asked(
    marked: dict[str, bool],
) -> None:
    marked["marked"] = False
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]

    decision = await authorizer.authorize(
        _agent_ctx(authorizer), Permissions.CHANNEL_WRITE
    )

    assert decision.allowed


@pytest.mark.asyncio
async def test_a_person_acting_for_themselves_is_not_asked(
    marked: dict[str, bool],
) -> None:
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    user_id = uuid4()
    ctx = Context(
        actor=Person(user_id=user_id),
        actor_id=str(user_id),
        user_id=user_id,
        pod_id=uuid4(),
        principal_refs=_MEMBER,
        permission_ids=_HELD,
        authorizer=authorizer,
    )

    decision = await authorizer.authorize(ctx, Permissions.CHANNEL_WRITE)

    assert decision.allowed


@pytest.mark.asyncio
async def test_a_function_an_approved_call_runs_is_not_asked_again(
    marked: dict[str, bool],
) -> None:
    """Running the function was the approved action; its steps have nowhere
    to ask."""
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    invoker = uuid4()
    function_ref = PrincipalRef("FUNCTION", uuid4())
    ctx = Context(
        actor=Workload(
            kind=WorkloadKind.FUNCTION,
            workload_id=function_ref.id,
            for_user_id=invoker,
            principal_refs=frozenset({function_ref}),
            delegation=Delegation(session_id="conversation-1"),
        ),
        actor_id=f"function:{function_ref.id}",
        user_id=invoker,
        pod_id=uuid4(),
        invoker_principal_refs=_MEMBER,
        permission_ids=_HELD,
        authorizer=authorizer,
    )

    async def never_approved(_: str) -> bool:
        return False

    decision = await gate_module.outside_text_decision(
        ctx, Permissions.CHANNEL_WRITE, None, approved=never_approved
    )

    assert decision is None


@pytest.mark.asyncio
async def test_the_mark_is_asked_once_per_request(
    monkeypatch: pytest.MonkeyPatch, marked: dict[str, bool]
) -> None:
    asked: list[str | None] = []

    async def has_read(session_id: str | None) -> bool:
        asked.append(session_id)
        return False

    monkeypatch.setattr(gate_module, "has_read_outside_text", has_read)
    authorizer = Authorizer(session=None)  # type: ignore[arg-type]
    ctx = _agent_ctx(authorizer)

    await authorizer.authorize(ctx, Permissions.CHANNEL_WRITE)
    await authorizer.authorize(ctx, Permissions.CONNECTOR_ACCOUNT_USE)

    assert asked == ["conversation-1"]


class _BrokenStore:
    async def get_json(self, suffix: str) -> object:
        raise RedisConnectionError("down")

    async def set_json(self, suffix: str, value: object) -> None:
        raise RedisConnectionError("down")

    async def delete(self, suffix: str) -> None:
        raise RedisConnectionError("down")


@pytest.mark.asyncio
async def test_an_unreachable_store_reads_as_not_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second line behind the prompt's: its outage must not stop every
    member's run from writing, nor fail the run that tried to mark."""
    monkeypatch.setattr(outside_text, "_store", lambda: _BrokenStore())

    await outside_text.note_outside_text("conversation-1")
    await outside_text.forget_outside_text("conversation-1")

    assert await outside_text.has_read_outside_text("conversation-1") is False


@pytest.mark.asyncio
async def test_no_conversation_is_never_marked() -> None:
    assert await outside_text.has_read_outside_text(None) is False
