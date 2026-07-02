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
from app.modules.pod_bundle.domain.state import PublishStatus


class FakeStore:
    def __init__(self):
        self.publishes = {}

    async def save_publish(self, state):
        state.touch()
        self.publishes[state.publish_id] = state

    async def get_publish(self, publish_id):
        return self.publishes.get(publish_id)


class FakeQueue:
    def __init__(self, *, duplicate=False):
        self.calls = []
        self._dup = duplicate

    async def enqueue(self, name, *, context, _job_id):
        self.calls.append((name, context, _job_id))
        return None if self._dup else object()


class FakeUow:
    session = object()


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
    async def _noop(self, *, pod_id, user_id):
        return None

    monkeypatch.setattr(PublishUseCases, "_authorize", _noop)


def _uc(**kw):
    store, queue = FakeStore(), FakeQueue(**kw)
    return PublishUseCases(FakeUowFactory(), state_store=store, job_queue=queue), store, queue


async def test_start_publish_enqueues_with_dedup_id():
    uc, store, queue = _uc()
    pod_id, user_id = uuid4(), uuid4()
    state = await uc.start_publish(
        pod_id=pod_id, user_id=user_id, repo_name="crm", private=True, account_id=None, ai_readme=True
    )
    assert state.status == PublishStatus.QUEUED
    assert state.repo_name == "crm" and state.private is True and state.ai_readme is True
    assert queue.calls[0][0] == "publish_pod_github"
    assert queue.calls[0][2] == publish_job_id(state.publish_id)


async def test_duplicate_publish_conflicts():
    uc, *_ = _uc(duplicate=True)
    with pytest.raises(BundleJobConflictError):
        await uc.start_publish(
            pod_id=uuid4(), user_id=uuid4(), repo_name="crm", private=False, account_id=None, ai_readme=False
        )


async def test_get_publish_missing_is_expired():
    uc, *_ = _uc()
    with pytest.raises(BundleJobExpiredError):
        await uc.get_publish(pod_id=uuid4(), publish_id=uuid4(), user_id=uuid4())
