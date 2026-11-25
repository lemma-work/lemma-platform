"""The vault against real Postgres: what it stores, and what it refuses.

Real Postgres because the claims are about the database -- the owner trigger,
row locks, a lease taken by a conditional UPDATE -- and because the bytes on
disk are the thing a leaked dump would expose.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncio

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.modules.vault.contracts import (
    ActorKind,
    AuditActor,
    SecretIntegrityError,
    SecretNotFound,
    SecretScope,
    SecretScopeMismatch,
    SecretVersionConflict,
    vault_for,
)
from app.modules.vault.infrastructure.models import (
    VaultSecret,
    VaultSecretEvent,
    owner_trigger_sql,
)
from app.modules.vault.domain.errors import KeyRetirementRefused
from app.modules.vault.services.keyring import ENCRYPT
from app.modules.vault.services.rewrap import rewrap_all, secrets_per_key
from app.modules.vault.services.runtime import get_vault_keyring

pytestmark = pytest.mark.e2e

PURPOSE = "tests.vault.credentials"


def _scope() -> SecretScope:
    return SecretScope(organization_id=uuid4(), user_id=uuid4())


async def _put(session, scope, value, purpose=PURPOSE):
    ref = await vault_for(session).put(
        scope=scope, purpose=purpose, value=value, owner_table="tests"
    )
    await session.commit()
    return ref


async def test_a_secret_round_trips_and_is_not_on_disk_in_the_clear(db_session):
    scope = _scope()
    ref = await _put(db_session, scope, {"api_key": "sk-live-PLAINTEXT-MARKER"})

    revealed = await vault_for(db_session).reveal(ref.id, expect=scope, purpose=PURPOSE)

    assert revealed.json() == {"api_key": "sk-live-PLAINTEXT-MARKER"}
    assert "PLAINTEXT-MARKER" not in repr(revealed)
    raw = (
        await db_session.execute(select(VaultSecret).where(VaultSecret.id == ref.id))
    ).scalar_one()
    assert b"PLAINTEXT-MARKER" not in raw.ciphertext
    assert b"PLAINTEXT-MARKER" not in raw.wrapped_dek


@pytest.mark.parametrize(
    "wrong",
    [
        lambda s: SecretScope(organization_id=uuid4(), user_id=s.user_id),
        lambda s: SecretScope(organization_id=s.organization_id, user_id=uuid4()),
        lambda s: SecretScope(organization_id=s.organization_id),
    ],
)
async def test_another_scope_gets_not_found(db_session, wrong):
    scope = _scope()
    ref = await _put(db_session, scope, "token")
    with pytest.raises(SecretNotFound):
        await vault_for(db_session).reveal(ref.id, expect=wrong(scope), purpose=PURPOSE)


async def test_another_purpose_gets_not_found(db_session):
    scope = _scope()
    ref = await _put(db_session, scope, "token")
    with pytest.raises(SecretScopeMismatch):
        await vault_for(db_session).reveal(ref.id, expect=scope, purpose="tests.other")


async def test_a_ciphertext_moved_onto_another_tenants_row_does_not_decrypt(db_session):
    """The associated data, not the scope columns, is what refuses it.

    Someone with write access to the database copies tenant A's ciphertext and
    data key onto tenant B's row. The scope columns now say B, so the cheap
    check passes -- and the decryption, bound to A's id and scope, still fails.
    """
    a, b = _scope(), _scope()
    ref_a = await _put(db_session, a, "tenant-a-secret")
    ref_b = await _put(db_session, b, "tenant-b-secret")
    await db_session.execute(
        text(
            "UPDATE vault_secrets SET ciphertext = src.ciphertext, wrapped_dek = src.wrapped_dek "
            "FROM vault_secrets src WHERE vault_secrets.id = :b AND src.id = :a"
        ),
        {"a": ref_a.id, "b": ref_b.id},
    )
    await db_session.commit()

    with pytest.raises(SecretIntegrityError):
        await vault_for(db_session).reveal(ref_b.id, expect=b, purpose=PURPOSE)


async def test_replace_is_a_compare_and_set(db_session):
    scope = _scope()
    ref = await _put(db_session, scope, "v1")
    vault = vault_for(db_session)

    newer = await vault.replace(
        ref.id, expect=scope, purpose=PURPOSE, value="v2", expected_version=1
    )
    await db_session.commit()
    with pytest.raises(SecretVersionConflict):
        await vault.replace(
            ref.id, expect=scope, purpose=PURPOSE, value="stale", expected_version=1
        )
    await db_session.rollback()

    revealed = await vault.reveal(ref.id, expect=scope, purpose=PURPOSE)
    assert (newer.version, revealed.version, revealed.text()) == (2, 2, "v2")


async def test_replace_with_an_equal_value_writes_nothing(db_session):
    scope = _scope()
    ref = await _put(db_session, scope, {"a": 1})
    unchanged = await vault_for(db_session).replace(
        ref.id, expect=scope, purpose=PURPOSE, value={"a": 1}, skip_if_equal=True
    )
    assert unchanged.version == 1


async def test_only_one_caller_gets_the_lease(db_session, db_manager):
    scope = _scope()
    ref = await _put(db_session, scope, "refresh-token")

    async with (
        db_manager.session_factory() as first,
        db_manager.session_factory() as second,
    ):
        lease = await vault_for(first).try_lease(
            ref.id,
            expect=scope,
            purpose=PURPOSE,
            holder="one",
            ttl=timedelta(seconds=30),
            if_version=1,
        )
        await first.commit()
        refused = await vault_for(second).try_lease(
            ref.id,
            expect=scope,
            purpose=PURPOSE,
            holder="two",
            ttl=timedelta(seconds=30),
            if_version=1,
        )
        assert lease is not None and refused is None

        # Ending the lease with the write; the loser then sees the new version.
        await vault_for(first).replace(
            ref.id,
            expect=scope,
            purpose=PURPOSE,
            value="rotated",
            lease=lease,
            expected_version=1,
        )
        await first.commit()
        meta = await vault_for(second).meta(ref.id, expect=scope, purpose=PURPOSE)
        assert meta is not None and meta.version == 2 and meta.lease_until is None


async def test_changes_and_requested_reveals_are_audited(db_session):
    scope = _scope()
    ref = await _put(db_session, scope, "v1")
    vault = vault_for(db_session)
    await vault.replace(ref.id, expect=scope, purpose=PURPOSE, value="v2")
    await vault.reveal(
        ref.id, expect=scope, purpose=PURPOSE
    )  # platform read: not audited
    await vault.reveal(
        ref.id,
        expect=scope,
        purpose=PURPOSE,
        actor=AuditActor(ActorKind.WORKLOAD, "agent-1"),
    )
    await db_session.commit()

    events = (
        await db_session.execute(
            select(
                VaultSecretEvent.action,
                VaultSecretEvent.actor_kind,
                VaultSecretEvent.version,
            )
            .where(VaultSecretEvent.secret_id == ref.id)
            .order_by(VaultSecretEvent.occurred_at, VaultSecretEvent.id)
        )
    ).all()
    assert [tuple(e) for e in events] == [
        ("created", "system", 1),
        ("replaced", "system", 2),
        ("revealed", "workload", 2),
    ]


async def test_deleting_or_repointing_the_owner_row_deletes_its_secret(db_session):
    # A temporary owner table. Postgres will not let it hold a foreign key to
    # a permanent table; the trigger is what is under test, and needs none.
    await db_session.execute(
        text("CREATE TEMP TABLE vault_test_owner (id uuid PRIMARY KEY, secret_id uuid)")
    )
    await db_session.execute(
        text(owner_trigger_sql("vault_test_owner", ("secret_id",)))
    )
    scope = _scope()
    vault = vault_for(db_session)
    first = await vault.put(
        scope=scope, purpose=PURPOSE, value="a", owner_table="vault_test_owner"
    )
    second = await vault.put(
        scope=scope, purpose=PURPOSE, value="b", owner_table="vault_test_owner"
    )
    owner = uuid4()
    await db_session.execute(
        text("INSERT INTO vault_test_owner VALUES (:id, :secret)"),
        {"id": owner, "secret": first.id},
    )

    await db_session.execute(
        text("UPDATE vault_test_owner SET secret_id = :secret WHERE id = :id"),
        {"id": owner, "secret": second.id},
    )
    remaining = await _ids(db_session, first.id, second.id)
    assert remaining == {second.id}

    await db_session.execute(
        text("DELETE FROM vault_test_owner WHERE id = :id"), {"id": owner}
    )
    assert await _ids(db_session, first.id, second.id) == set()
    # The deletion is attributed to the tenant and purpose it belonged to.
    deleted = (
        await db_session.execute(
            select(
                VaultSecretEvent.secret_id,
                VaultSecretEvent.organization_id,
                VaultSecretEvent.purpose,
                VaultSecretEvent.version,
            ).where(
                VaultSecretEvent.secret_id.in_((first.id, second.id)),
                VaultSecretEvent.action == "deleted",
            )
        )
    ).all()
    assert {tuple(row) for row in deleted} == {
        (first.id, scope.organization_id, PURPOSE, 1),
        (second.id, scope.organization_id, PURPOSE, 1),
    }
    await db_session.rollback()


async def _ids(session, *ids):
    rows = await session.execute(select(VaultSecret.id).where(VaultSecret.id.in_(ids)))
    return set(rows.scalars().all())


async def test_rotating_the_kek_and_rewrapping_keeps_every_secret_readable(
    db_session, db_manager
):
    scope = _scope()
    refs = [await _put(db_session, scope, f"value-{i}") for i in range(3)]
    keyring = get_vault_keyring()
    old_kek, _ = await keyring.active_encryption_key()

    new_kek = await keyring.rotate(ENCRYPT)
    moved = await rewrap_all(db_manager.session_factory, keyring, batch_size=2)

    assert moved >= 3
    counts = await secrets_per_key(db_session)
    assert counts.get(old_kek, 0) == 0 and counts[new_kek] >= 3
    vault = vault_for(db_session)
    for i, ref in enumerate(refs):
        revealed = await vault.reveal(ref.id, expect=scope, purpose=PURPOSE)
        assert revealed.text() == f"value-{i}"


async def test_a_key_is_retired_only_once_nothing_is_wrapped_under_it(
    db_session, db_manager
):
    scope = _scope()
    ref = await _put(db_session, scope, "kept")
    keyring = get_vault_keyring()
    old_kek, _ = await keyring.active_encryption_key()

    with pytest.raises(KeyRetirementRefused, match="active"):
        await keyring.retire(old_kek)
    await keyring.rotate(ENCRYPT)
    with pytest.raises(KeyRetirementRefused, match="still wraps"):
        await keyring.retire(old_kek)

    await rewrap_all(db_manager.session_factory, keyring, batch_size=50)
    # Other processes may still write under the old key until they refresh.
    with pytest.raises(KeyRetirementRefused, match="rotated out less than"):
        await keyring.retire(old_kek)
    await db_session.execute(
        text(
            "UPDATE vault_keys SET created_at = now() - interval '1 day' "
            "WHERE purpose = 'encrypt' AND state = 'active'"
        )
    )
    await db_session.commit()
    await keyring.retire(old_kek)

    # A process that had not refreshed would still write under the old key;
    # the database refuses it rather than storing a secret nothing can open.
    with pytest.raises(DBAPIError) as refused:
        await db_session.execute(
            update(VaultSecret).where(VaultSecret.id == ref.id).values(kek_id=old_kek)
        )
    assert getattr(refused.value.orig, "sqlstate", None) == "55000"
    await db_session.rollback()
    revealed = await vault_for(db_session).reveal(ref.id, expect=scope, purpose=PURPOSE)
    assert revealed.text() == "kept"


async def test_rewrap_does_not_stop_at_a_row_live_traffic_is_holding(
    db_session, db_manager
):
    scope = _scope()
    held = await _put(db_session, scope, "held")
    keyring = get_vault_keyring()
    old_kek, _ = await keyring.active_encryption_key()
    await keyring.rotate(ENCRYPT)

    async with db_manager.session_factory() as traffic:
        await traffic.execute(
            select(VaultSecret.id).where(VaultSecret.id == held.id).with_for_update()
        )
        rewrap = asyncio.create_task(
            rewrap_all(db_manager.session_factory, keyring, batch_size=50)
        )
        await asyncio.sleep(1.0)
        assert not rewrap.done()
        await traffic.rollback()
    await asyncio.wait_for(rewrap, timeout=10)

    assert (await secrets_per_key(db_session)).get(old_kek, 0) == 0


async def test_a_lease_token_ends_only_its_own_lease(db_session):
    scope = _scope()
    ref = await _put(db_session, scope, "refresh-token")
    vault = vault_for(db_session)

    async def lease():
        token = await vault.try_lease(
            ref.id,
            expect=scope,
            purpose=PURPOSE,
            holder="worker",
            ttl=timedelta(seconds=30),
            if_version=1,
        )
        await db_session.commit()
        return token

    first = await lease()
    await vault.release_lease(first)
    await db_session.commit()
    second = await lease()
    assert first is not None and second is not None
    assert first.holder != second.holder

    # The first acquisition's token, same caller name, cannot touch the second.
    await vault.release_lease(first)
    await db_session.commit()
    with pytest.raises(SecretVersionConflict):
        await vault.replace(
            ref.id, expect=scope, purpose=PURPOSE, value="stale", lease=first
        )
    await db_session.rollback()
    meta = await vault.meta(ref.id, expect=scope, purpose=PURPOSE)
    assert meta is not None and meta.lease_until is not None

    # Writing the same value back still ends the lease and sets the expiry.
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    kept = await vault.replace(
        ref.id,
        expect=scope,
        purpose=PURPOSE,
        value="refresh-token",
        lease=second,
        expires_at=expiry,
        skip_if_equal=True,
    )
    await db_session.commit()
    meta = await vault.meta(ref.id, expect=scope, purpose=PURPOSE)
    assert kept.version == 1
    assert meta is not None and meta.lease_until is None
    assert meta.expires_at == expiry
