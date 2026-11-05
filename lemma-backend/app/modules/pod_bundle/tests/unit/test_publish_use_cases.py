"""Publish use-case plumbing with faked queue/store (no DB, no Redis)."""

from uuid import uuid4

import pytest

from app.modules.pod_bundle.application.publish_use_cases import (
    PublishUseCases,
    publish_job_id,
)
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobExpiredError,
)
from app.modules.connectors.contracts.pod_connectors import ConnectionRequiredError
from app.modules.pod_bundle.domain.state import PublishStatus
from app.modules.pod_bundle.domain.state import PublishMode
from app.modules.pod_bundle.infrastructure.publish_lock import publish_lock_key


class FakeStore:
    def __init__(self):
        self.publishes = {}

    async def stage_publish(self, uow, state):
        await self.save_publish(state)

    async def save_publish(self, state):
        state.touch()
        self.publishes[state.publish_id] = state

    async def get_publish(self, publish_id):
        return self.publishes.get(publish_id)


class FakeQueue:
    def __init__(self, *, duplicate=False):
        self.calls = []
        self._dup = duplicate

    async def queue_job(self, uow, kind, payload, *, pod_id, dedup_key):
        self.calls.append((kind, payload.model_dump(mode="json"), dedup_key))
        return not self._dup


class FakeLock:
    def __init__(self):
        self.locked = set()
        self.released = []

    async def acquire(self, *, key, owner):
        if key in self.locked:
            return False
        self.locked.add(key)
        return True

    async def release(self, *, key, owner):
        self.locked.discard(key)
        self.released.append((key, owner))


class FakeUow:
    session = object()

    async def commit(self):
        return None

    async def rollback(self):
        return None

    def after_commit(self, callback):
        del callback


class FakeUowFactory:
    def __call__(self):
        class _Ctx:
            async def __aenter__(self):
                return FakeUow()

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.fixture(autouse=True)
def _patch_auth(monkeypatch):
    async def _noop(self, *, pod_id, user_id, action):
        del pod_id, user_id, action
        return

    monkeypatch.setattr(PublishUseCases, "_authorize", _noop)


_ACCOUNT = uuid4()


def _resolves_to(account_id):
    """The pod's `github` connector, resolving for the person to one account."""
    calls = []

    async def _github_account(*, pod_id, user_id):
        calls.append((pod_id, user_id))
        return account_id

    _github_account.calls = calls
    return _github_account


def _uc(github_account=None, **kw):
    store, queue, lock = FakeStore(), FakeQueue(**kw), FakeLock()
    use_cases = PublishUseCases(
        FakeUowFactory(),
        state_store=store,
        job_queue=queue,
        publish_lock=lock,
        github_account=github_account or _resolves_to(_ACCOUNT),
    )
    return use_cases, store, queue, lock


async def test_start_publish_enqueues_with_unique_job_id_and_repo_lock():
    """No account is named: the lock is on whatever the pod's `github`
    connector resolves to for the person publishing."""
    github_account = _resolves_to(_ACCOUNT)
    uc, store, queue, lock = _uc(github_account)
    pod_id, user_id = uuid4(), uuid4()
    state = await uc.start_publish(
        pod_id=pod_id,
        user_id=user_id,
        repo_name="crm",
        mode=PublishMode.UPDATE,
        private=True,
        ai_readme=True,
    )
    assert state.status == PublishStatus.QUEUED
    assert (
        state.repo_name == "crm"
        and state.private is True
        and state.ai_readme is True
        and state.mode is PublishMode.UPDATE
    )
    assert github_account.calls == [(pod_id, user_id)]
    assert queue.calls[0][0] == "pod_bundle.publish"
    assert queue.calls[0][2] == publish_job_id(state.publish_id)
    assert publish_lock_key(_ACCOUNT, "crm") in lock.locked
    assert state.publish_lock_key == publish_lock_key(_ACCOUNT, "crm")


async def test_a_missing_github_connection_refuses_before_anything_is_queued():
    """The person is told to connect, with the link, at the request -- not
    minutes later in a failed job."""

    async def _not_connected(*, pod_id, user_id):
        raise ConnectionRequiredError(
            slug="github",
            connector_id="github",
            connect_url="https://app.example/pods/p/connections?connector=github",
        )

    uc, store, queue, lock = _uc(_not_connected)
    with pytest.raises(ConnectionRequiredError) as refused:
        await uc.start_publish(
            pod_id=uuid4(),
            user_id=uuid4(),
            repo_name="crm",
            private=False,
            ai_readme=False,
        )
    assert refused.value.details["connect_url"].endswith("connector=github")
    assert queue.calls == [] and store.publishes == {} and lock.locked == set()


async def test_duplicate_publish_conflicts():
    uc, _, _, lock = _uc()
    lock.locked.add(publish_lock_key(_ACCOUNT, "crm"))
    with pytest.raises(BundleJobConflictError):
        await uc.start_publish(
            pod_id=uuid4(),
            user_id=uuid4(),
            repo_name="crm",
            private=False,
            ai_readme=False,
        )


async def test_enqueue_failure_releases_repo_lock():
    uc, _, _, lock = _uc(duplicate=True)
    with pytest.raises(BundleJobConflictError):
        await uc.start_publish(
            pod_id=uuid4(),
            user_id=uuid4(),
            repo_name="crm",
            private=False,
            ai_readme=False,
        )
    assert publish_lock_key(_ACCOUNT, "crm") not in lock.locked


async def test_get_publish_missing_is_expired():
    uc, *_ = _uc()
    with pytest.raises(BundleJobExpiredError):
        await uc.get_publish(pod_id=uuid4(), publish_id=uuid4(), user_id=uuid4())
