"""A borrowed GitHub token in a sandbox session: re-checked on each use, and
taken back from every session that holds it when the loan stops.

Nothing is patched: the bridge takes its Redis client, its resolver and its
loan check as arguments, and the withdrawal takes its store.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.tools.workspace_cli import github_credential_bridge as bridge
from app.modules.agent.tools.workspace_cli import github_loan
from app.modules.agent.tools.workspace_cli.github_loan import BorrowedLoan
from app.modules.connectors.contracts import sandbox_borrowers

_SESSION = "session-1"
_MARKER_KEY = sandbox_borrowers.sandbox_credential_marker_key(_SESSION)


class _Redis:
    """Strings and sets, as the bridge and a withdrawal use them."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expiries: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expiries[key] = ex

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def srem(self, key: str, *values: str) -> None:
        self.sets.get(key, set()).difference_update(values)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def expire(self, key: str, time: int) -> None:
        self.expiries[key] = time

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.sets.pop(key, None)
            self.values.pop(key, None)


class _Session:
    def __init__(self, session_id: str = _SESSION) -> None:
        self.session_id = session_id
        self.written: list[tuple[str, bytes]] = []
        self.commands: list[str] = []

    async def write_file(self, path: str, data: bytes) -> None:
        self.written.append((path, data))

    async def exec_command(self, *, cmd: str, timeout: int | None = None):
        del timeout
        self.commands.append(cmd)
        return {"success": True}


def _context() -> ToolContext:
    return ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())


def _lent(pod_account_id: UUID, borrower: UUID | None = None):
    async def _resolve(ctx: ToolContext):
        return bridge._GithubCredential(
            access_token="gho_lent",
            login="alice",
            email=None,
            borrowed_pod_account_id=pod_account_id,
            borrower_user_id=borrower or ctx.user_id,
        )

    return _resolve


def _borrowers_key(pod_account_id: UUID) -> str:
    return sandbox_borrowers._borrowers_key(pod_account_id)


@pytest.mark.asyncio
async def test_each_use_re_runs_the_rule_for_the_borrower_and_takes_the_copy_back():
    """The marker records the whole loan, so every later git command asks the
    pod-account rule again for the person it was lent to -- not only the
    owner's opt-in -- and the first one after it stops holding removes it."""
    pod_account_id = uuid4()
    ctx = _context()
    redis = _Redis()
    session = _Session()
    asked: list[BorrowedLoan] = []
    holds = {"now": True}

    async def _still_holds(loan: BorrowedLoan) -> bool:
        asked.append(loan)
        return holds["now"]

    async def _ensure() -> str | None:
        return await bridge.ensure_github_credentials(
            ctx,
            session,
            redis=redis,
            resolve_credential=_lent(pod_account_id),
            sandbox_use_allowed=_still_holds,
        )

    assert await _ensure() is None
    loan = BorrowedLoan(
        pod_account_id=pod_account_id, pod_id=ctx.pod_id, borrower=ctx.user_id
    )
    assert redis.values[_MARKER_KEY] == loan.marker()
    # Recorded as a borrower, for a withdrawal to find.
    assert redis.sets[_borrowers_key(pod_account_id)] == {_SESSION}
    commands_after_provisioning = len(session.commands)

    assert await _ensure() is None
    assert asked == [loan]
    assert len(session.commands) == commands_after_provisioning

    holds["now"] = False
    notice = await _ensure()

    assert notice is not None and "no longer available" in notice
    [take_back] = session.commands[commands_after_provisioning:]
    assert take_back.startswith("rm -f ")
    assert "/tmp/lemma-sess/session-1/git-credentials" in take_back
    assert "/tmp/lemma-sess/session-1/gh/hosts.yml" in take_back
    assert redis.values[_MARKER_KEY] == "unavailable"
    assert redis.sets[_borrowers_key(pod_account_id)] == set()


@pytest.mark.asyncio
async def test_a_withdrawn_marker_takes_the_copy_out_and_asks_again():
    """A withdrawal from outside marks the session; its next git command
    removes the copy first, then resolves afresh -- and says why when the
    answer is now no."""
    redis = _Redis()
    session = _Session()
    redis.values[_MARKER_KEY] = sandbox_borrowers.MARKER_WITHDRAWN

    async def _nothing(ctx):
        del ctx

    notice = await bridge.ensure_github_credentials(
        _context(), session, redis=redis, resolve_credential=_nothing
    )

    assert [cmd for cmd in session.commands if cmd.startswith("rm -f ")]
    assert session.written == []
    assert notice is not None and "no longer available" in notice
    assert redis.values[_MARKER_KEY] == "unavailable"


@pytest.mark.asyncio
async def test_a_withdrawn_session_may_be_given_its_own_account_afresh():
    """Re-pointing the connector at the person's own account withdraws the
    loan; the session then gets their own token, not nothing."""
    redis = _Redis()
    session = _Session()
    redis.values[_MARKER_KEY] = sandbox_borrowers.MARKER_WITHDRAWN

    async def _own(ctx):
        del ctx
        return bridge._GithubCredential(
            access_token="gho_mine", login="bob", email=None
        )

    notice = await bridge.ensure_github_credentials(
        _context(), session, redis=redis, resolve_credential=_own
    )

    assert notice is None
    assert session.commands[0].startswith("rm -f ")
    assert any(b"gho_mine" in data for _path, data in session.written)
    assert redis.values[_MARKER_KEY] == "provisioned"


@pytest.mark.asyncio
async def test_a_marker_from_before_it_named_the_borrower_is_not_trusted():
    """``borrowed:<id>`` alone cannot be re-checked for anybody: the copy is
    taken out and the session resolves afresh."""
    redis = _Redis()
    session = _Session()
    redis.values[_MARKER_KEY] = f"borrowed:{uuid4()}"
    resolved: list[ToolContext] = []

    async def _nothing(ctx):
        resolved.append(ctx)

    async def _must_not_check(loan):
        raise AssertionError("an unparseable loan has nothing to check")

    await bridge.ensure_github_credentials(
        _context(),
        session,
        redis=redis,
        resolve_credential=_nothing,
        sandbox_use_allowed=_must_not_check,
    )

    assert session.commands[0].startswith("rm -f ")
    assert len(resolved) == 1


@pytest.mark.asyncio
async def test_withdrawing_marks_every_borrowing_session():
    """Turning the opt-in off reaches every session that holds a copy, not
    only the next one to run git."""
    pod_account_id, other = uuid4(), uuid4()
    redis = _Redis()
    redis.sets[_borrowers_key(pod_account_id)] = {"s-1", "s-2"}
    redis.sets[_borrowers_key(other)] = {"s-3"}
    for sid in ("s-1", "s-2", "s-3"):
        redis.values[sandbox_borrowers.sandbox_credential_marker_key(sid)] = "x"

    await sandbox_borrowers.withdraw_sandbox_borrowers([pod_account_id], redis=redis)

    for sid in ("s-1", "s-2"):
        key = sandbox_borrowers.sandbox_credential_marker_key(sid)
        assert redis.values[key] == sandbox_borrowers.MARKER_WITHDRAWN
    assert redis.values[sandbox_borrowers.sandbox_credential_marker_key("s-3")] == "x"
    assert _borrowers_key(pod_account_id) not in redis.sets


@pytest.mark.asyncio
async def test_noting_a_borrower_bounds_the_set_by_the_credential_ceiling():
    redis = _Redis()
    pod_account_id = uuid4()

    await sandbox_borrowers.note_sandbox_borrower(
        redis, pod_account_id=pod_account_id, session_id="s-1"
    )

    key = _borrowers_key(pod_account_id)
    assert redis.expiries[key] == sandbox_borrowers.SANDBOX_CREDENTIAL_TTL_SECONDS
    assert sandbox_borrowers.SANDBOX_CREDENTIAL_TTL_SECONDS >= (
        bridge._PROVISIONED_TTL_SECONDS
    )


@pytest.mark.asyncio
async def test_a_withdrawal_redis_cannot_reach_is_logged_not_raised():
    """The change has committed by then; each use still re-checks the rule."""

    class _Down(_Redis):
        async def smembers(self, key: str) -> set[str]:
            raise RedisConnectionError("down")

    await sandbox_borrowers.withdraw_sandbox_borrowers([uuid4()], redis=_Down())


@pytest.mark.asyncio
async def test_nothing_to_withdraw_touches_no_store():
    class _Untouchable:
        def __getattr__(self, name: str):
            raise AssertionError(f"must not call {name}")

    await sandbox_borrowers.withdraw_sandbox_borrowers([], redis=_Untouchable())


def test_the_bridge_and_the_loan_name_the_same_marker_files_and_connector():
    """Defined on both sides so the loan's modules load only when a session
    borrows; held together here."""
    assert (
        bridge._MARKER_KEY_PREFIX == sandbox_borrowers.SANDBOX_CREDENTIAL_MARKER_PREFIX
    )
    assert bridge._PROVISIONED_TTL_SECONDS == (
        sandbox_borrowers.SANDBOX_CREDENTIAL_TTL_SECONDS
    )
    assert bridge._STALE_SESSION_MINUTES == (
        sandbox_borrowers.SANDBOX_STALE_SESSION_MINUTES
    )
    assert bridge._GH_HOSTS_NAME == github_loan.GH_HOSTS_NAME
    assert bridge._POD_CONNECTOR_SLUG == github_loan.POD_CONNECTOR_SLUG
