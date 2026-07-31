"""Repository-scoped GitHub publish lock semantics."""

from uuid import uuid4

from app.modules.pod_bundle.infrastructure.publish_lock import (
    PublishConcurrencyLock,
)


class FakeCache:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set_raw_if_absent(self, suffix, payload):
        if suffix in self.values:
            return False
        self.values[suffix] = payload
        return True

    async def delete_if_value(self, suffix, expected):
        if self.values.get(suffix) != expected:
            return False
        del self.values[suffix]
        return True


async def test_account_repository_lock_is_case_insensitive_and_owner_safe():
    cache = FakeCache()
    lock = PublishConcurrencyLock(cache=cache)
    account_id, first_owner, second_owner = uuid4(), uuid4(), uuid4()

    assert await lock.acquire(
        account_id=account_id,
        repo_name="My-Repo",
        owner=first_owner,
    )
    assert not await lock.acquire(
        account_id=account_id,
        repo_name="my-repo",
        owner=second_owner,
    )

    await lock.release(
        account_id=account_id,
        repo_name="MY-REPO",
        owner=second_owner,
    )
    assert not await lock.acquire(
        account_id=account_id,
        repo_name="my-repo",
        owner=second_owner,
    )

    await lock.release(
        account_id=account_id,
        repo_name="my-repo",
        owner=first_owner,
    )
    assert await lock.acquire(
        account_id=account_id,
        repo_name="my-repo",
        owner=second_owner,
    )
