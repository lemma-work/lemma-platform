from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.context import ResourceType
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.permissions import Permissions
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.table_context import TableContext


def _table_context(*, enable_rls: bool = True) -> TableContext:
    table = DatastoreTableEntity(
        id=uuid4(),
        pod_id=uuid4(),
        table_name="expenses",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="merchant", type=DatastoreDataType.TEXT),
        ],
        enable_rls=enable_rls,
    )
    return TableContext.from_table_entity(table, "pod_test")


def _pod_file(*, visibility: str, owner_user_id=None) -> DatastoreFileEntity:
    return DatastoreFileEntity(
        id=uuid4(),
        pod_id=uuid4(),
        owner_user_id=owner_user_id,
        kind=FileKind.FILE,
        visibility=visibility,
        path="/docs/readme.md",
        name="readme.md",
        mime_type="text/markdown",
        size_bytes=12,
        search_enabled=False,
        status=FileStatus.NOT_REQUIRED,
    )


@pytest.mark.asyncio
async def test_table_read_uses_core_context_when_available():
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    table_id = uuid4()
    pod_id = uuid4()
    authz = DatastoreAuthorization(authorization_service)

    await authz.require_table_read(
        user_id=uuid4(),
        pod_id=pod_id,
        table_id=table_id,
        table_name="expenses",
        ctx=ctx,
    )

    ctx.require.assert_awaited_once()
    assert ctx.require.await_args.args[0] == Permissions.DATASTORE_TABLE_READ
    assert ctx.require.await_args.args[1].resource_id == table_id
    authorization_service.require_user_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_table_read_uses_current_context_without_explicit_context():
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    table_id = uuid4()
    pod_id = uuid4()
    user_id = uuid4()
    authz = DatastoreAuthorization(authorization_service)

    token = set_current_context(ctx)
    try:
        await authz.require_table_read(
            user_id=user_id,
            pod_id=pod_id,
            table_id=table_id,
            table_name="expenses",
        )
    finally:
        reset_current_context(token)

    ctx.require.assert_awaited_once()
    assert ctx.require.await_args.args[0] == Permissions.DATASTORE_TABLE_READ
    assert ctx.require.await_args.args[1].resource_type == ResourceType.DATASTORE_TABLE
    assert ctx.require.await_args.args[1].resource_id == table_id


@pytest.mark.asyncio
async def test_record_write_uses_record_permission_for_rls_tables():
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    authz = DatastoreAuthorization(authorization_service)

    token = set_current_context(ctx)
    try:
        await authz.require_record_write(
            user_id=uuid4(),
            ctx=_table_context(enable_rls=True),
        )
    finally:
        reset_current_context(token)

    assert ctx.require.await_args.args[0] == Permissions.DATASTORE_RECORD_WRITE


@pytest.mark.asyncio
async def test_record_write_uses_record_permission_for_non_rls_tables():
    # Data writes are governed by record permissions regardless of RLS;
    # table.update is schema-only and must not gate record writes.
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    authz = DatastoreAuthorization(authorization_service)

    token = set_current_context(ctx)
    try:
        await authz.require_record_write(
            user_id=uuid4(),
            ctx=_table_context(enable_rls=False),
        )
    finally:
        reset_current_context(token)

    assert ctx.require.await_args.args[0] == Permissions.DATASTORE_RECORD_WRITE


@pytest.mark.asyncio
async def test_record_scope_is_enforced_for_everyone_by_default():
    # Without admin mode, RLS rows are scoped to the caller even for a pod admin
    # (ctx.can would say they administer the table) — so app apps keep their
    # per-user semantics. Non-RLS tables are never scoped.
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    ctx.can.return_value = True
    authz = DatastoreAuthorization(authorization_service)

    token = set_current_context(ctx)
    try:
        assert await authz.should_enforce_record_user_scope(
            user_id=uuid4(),
            ctx=_table_context(enable_rls=True),
        )
        assert not await authz.should_enforce_record_user_scope(
            user_id=uuid4(),
            ctx=_table_context(enable_rls=False),
        )
        # The admin signal is never consulted unless admin mode is requested.
        ctx.can.assert_not_awaited()
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_admin_mode_bypasses_scope_for_table_admin():
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    ctx.can.return_value = True  # caller administers the table
    authz = DatastoreAuthorization(authorization_service)

    token = set_current_context(ctx)
    try:
        assert not await authz.should_enforce_record_user_scope(
            user_id=uuid4(),
            ctx=_table_context(enable_rls=True),
            admin_mode=True,
        )
    finally:
        reset_current_context(token)

    assert ctx.can.await_args.args[0] == Permissions.DATASTORE_TABLE_DELETE


@pytest.mark.asyncio
async def test_admin_mode_rejected_for_non_admin():
    from app.modules.datastore.domain.errors import DatastoreAccessDeniedError

    authorization_service = AsyncMock()
    ctx = AsyncMock()
    ctx.can.return_value = False  # caller does not administer the table
    authz = DatastoreAuthorization(authorization_service)

    token = set_current_context(ctx)
    try:
        with pytest.raises(DatastoreAccessDeniedError):
            await authz.should_enforce_record_user_scope(
                user_id=uuid4(),
                ctx=_table_context(enable_rls=True),
                admin_mode=True,
            )
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_personal_file_owner_bypasses_document_write_check():
    authorization_service = AsyncMock()
    user_id = uuid4()
    authz = DatastoreAuthorization(authorization_service)

    await authz.require_file_write(
        file_entity=_pod_file(visibility="PERSONAL", owner_user_id=user_id),
        user_id=user_id,
    )

    authorization_service.require_user_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_file_write_uses_document_permission():
    authorization_service = AsyncMock()
    ctx = AsyncMock()
    authz = DatastoreAuthorization(authorization_service)
    file_entity = _pod_file(visibility="POD")

    token = set_current_context(ctx)
    try:
        await authz.require_file_write(file_entity=file_entity, user_id=uuid4())
    finally:
        reset_current_context(token)

    assert ctx.require.await_args.args[0] == Permissions.FOLDER_WRITE
    assert ctx.require.await_args.args[1].resource_type == ResourceType.DOCUMENT
    assert ctx.require.await_args.args[1].resource_id == file_entity.id


async def test_the_check_releases_the_application_connection_when_nothing_is_pending():
    """Authorization reads from the application DB; what follows uses the datastore.

    Holding the application connection past the check is what made a bulk
    record write sit on one for seconds with the database asked nothing.
    """
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = SimpleNamespace(new=(), dirty=(), deleted=(), commit=AsyncMock())
    authz = DatastoreAuthorization(SimpleNamespace(session=session))

    await authz._release_application_connection()

    session.commit.assert_awaited_once()


async def test_the_release_leaves_a_session_with_pending_writes_alone():
    """Committing here would make someone else's work durable earlier than asked.

    The checks are read-only, so normally nothing is pending. When something
    is, the safe answer is to keep the old behaviour rather than to commit on
    the caller's behalf.
    """
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = SimpleNamespace(new=(object(),), dirty=(), deleted=(), commit=AsyncMock())
    authz = DatastoreAuthorization(SimpleNamespace(session=session))

    await authz._release_application_connection()

    session.commit.assert_not_awaited()
