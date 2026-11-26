"""Hooks: making them, and taking what services post to them.

Members make, list, rotate, switch off and delete a pod's hooks here, as
themselves. Making a hook, or changing one, takes what changing its table
takes -- it is the same power as opening the table to a form -- and seeing one
takes reading its table. Every row a hook adds is added as the member who made
it, through ``public_rows.insert_from_outside``: the same validation and
permission check as their own hand, and an insert event that names the hook,
not them.

A delivery is taken in two steps, because the webhook endpoint asks for two:
``admit_delivery`` proves the address and reads the body, ``keep_delivery``
adds the row. Neither holds a database session across Redis or the body.
Each refusal after the address has proved itself is told to the sender and
kept on the hook as its last error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.datastore.config_hooks import hook_settings
from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.errors import DatastoreTableNotFoundError
from app.modules.datastore.domain.hooks import (
    MAX_HOOKS_PER_POD,
    UNKNOWN_HOOK_HASH,
    HookDeliveryRefused,
    HookFields,
    HookProblem,
    content_type_problem,
    fields_problem,
    hook_row,
    name_problem,
    new_secret,
    parse_body,
    secret_hash,
    secret_matches,
)
from app.modules.datastore.domain.public_rows import (
    OpenTable,
    PublicAudience,
    PublicRowDuplicate,
    PublicRowRefused,
    PublicRowsClosed,
    public_column,
)
from app.modules.datastore.infrastructure.models import (
    DatastoreHookModel,
    DatastoreTable,
)
from app.modules.datastore.infrastructure.repositories.table_repository import (
    readable_by,
)
from app.modules.datastore.services.hook_limits import HookAllowance, HookLimiter
from app.modules.datastore.services.public_rows import insert_from_outside
from app.modules.datastore.services.wiring import build_table_service

logger = get_logger(__name__)

#: A refusal said again within this long is not written again: a sender stuck
#: on one mistake would otherwise write the hook's row on every attempt.
_REFUSAL_REWRITE_AFTER = timedelta(minutes=1)

_TURNED_OFF = "This hook is switched off. Switch it on to take deliveries again"
_DUPLICATE = (
    "A delivery added no row: the table already holds a row with the same "
    "value in a unique column. The sender was told it was received"
)
_CLOSED = (
    "This hook can't add rows to its table any more: the member who made it "
    "can no longer change the table, or the table is gone"
)


class HookNotFound(LookupError):
    """No hook by that name in the pod, or none the caller may see."""


@dataclass(frozen=True, slots=True)
class HookView:
    """A hook as its pod's members see it. Never the secret."""

    id: UUID
    name: str
    table: str
    enabled: bool
    fields: dict[str, str] | None
    created_by: UUID
    created_at: datetime
    last_received_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None


@dataclass(frozen=True, slots=True)
class IssuedHook:
    """A hook with its secret, as it is shown once: on making it, or rotating it."""

    hook: HookView
    secret: str


@dataclass(frozen=True, slots=True)
class HookReceipt:
    #: The table already held this row -- a unique column said so -- which is
    #: what a redelivery looks like, so it is answered as received.
    duplicate: bool = False


def _view(hook: DatastoreHookModel, table_name: str) -> HookView:
    return HookView(
        id=hook.id,
        name=hook.name,
        table=table_name,
        enabled=hook.enabled,
        fields=dict(hook.fields) if hook.fields is not None else None,
        created_by=hook.created_by,
        created_at=hook.created_at,
        last_received_at=hook.last_received_at,
        last_error=hook.last_error,
        last_error_at=hook.last_error_at,
    )


def _schemas(table: DatastoreTable) -> list[ColumnSchema]:
    return [ColumnSchema.model_validate(column) for column in table.columns or []]


# --- Members ---------------------------------------------------------------


async def _changeable_table(uow, pod_id: UUID, table_name: str, ctx: Context):
    """The table, once the caller is known to be allowed to change it."""
    tables = build_table_service(uow)
    table = await tables.get_table(pod_id, table_name, ctx)
    await tables.authz.require_table_update(
        user_id=ctx.user_id,
        pod_id=pod_id,
        table_id=table.id,
        table_name=table.table_name,
        ctx=ctx,
    )
    if table.enable_rls:
        raise HookProblem(
            "Each member sees only their own rows in this table, so a hook "
            "can't add rows to it"
        )
    return table


async def _named(
    uow, pod_id: UUID, name: str
) -> tuple[DatastoreHookModel, DatastoreTable]:
    found = (
        await uow.session.execute(
            select(DatastoreHookModel, DatastoreTable)
            .join(DatastoreTable, DatastoreTable.id == DatastoreHookModel.table_id)
            .where(DatastoreHookModel.pod_id == pod_id, DatastoreHookModel.name == name)
        )
    ).first()
    if found is None:
        raise HookNotFound(name)
    return found[0], found[1]


async def _changeable_hook(
    uow, pod_id: UUID, name: str, ctx: Context
) -> tuple[DatastoreHookModel, DatastoreTable]:
    hook, table = await _named(uow, pod_id, name)
    try:
        await _changeable_table(uow, pod_id, table.table_name, ctx)
    except DatastoreTableNotFoundError as exc:
        # A table the caller cannot see is a hook they cannot see.
        raise HookNotFound(name) from exc
    return hook, table


async def create_hook(
    uow,
    *,
    pod_id: UUID,
    name: str,
    table_name: str,
    fields: dict[str, str] | None,
    ctx: Context,
) -> IssuedHook:
    """Make a hook that adds rows to ``table_name``, as the member asking."""
    problem = name_problem(name)
    if problem:
        raise HookProblem(problem)
    table = await _changeable_table(uow, pod_id, table_name, ctx)
    problem = fields_problem(
        table.columns, table.primary_key_column, HookFields(fields)
    )
    if problem:
        raise HookProblem(problem)
    taken = await uow.session.scalar(
        select(func.count())
        .select_from(DatastoreHookModel)
        .where(DatastoreHookModel.pod_id == pod_id)
    )
    if taken >= MAX_HOOKS_PER_POD:
        raise HookProblem(f"A Lemma keeps at most {MAX_HOOKS_PER_POD} hooks")
    if await uow.session.scalar(
        select(DatastoreHookModel.id).where(
            DatastoreHookModel.pod_id == pod_id, DatastoreHookModel.name == name
        )
    ):
        raise HookProblem(f"There is already a hook named '{name}'")
    secret = new_secret()
    hook = DatastoreHookModel(
        pod_id=pod_id,
        table_id=table.id,
        name=name,
        secret_hash=secret_hash(secret),
        enabled=True,
        fields=dict(fields) if fields is not None else None,
        created_by=ctx.user_id,
    )
    uow.session.add(hook)
    await uow.session.flush()
    logger.info(
        "datastore.hooks.created",
        pod_id=str(pod_id),
        hook_id=str(hook.id),
        user_id=str(ctx.user_id),
        mapped=fields is not None,
    )
    return IssuedHook(hook=_view(hook, table.table_name), secret=secret)


async def list_hooks(uow, *, pod_id: UUID, ctx: Context) -> list[HookView]:
    """The pod's hooks on tables the caller can read."""
    rows = await uow.session.execute(
        select(DatastoreHookModel, DatastoreTable.table_name)
        .join(DatastoreTable, DatastoreTable.id == DatastoreHookModel.table_id)
        .where(DatastoreHookModel.pod_id == pod_id, readable_by(ctx))
        .order_by(DatastoreHookModel.name)
        .limit(MAX_HOOKS_PER_POD)
    )
    return [_view(hook, table_name) for hook, table_name in rows]


async def get_hook(uow, *, pod_id: UUID, name: str, ctx: Context) -> HookView:
    hook, table = await _named(uow, pod_id, name)
    try:
        await build_table_service(uow).get_table(pod_id, table.table_name, ctx)
    except DatastoreTableNotFoundError as exc:
        raise HookNotFound(name) from exc
    return _view(hook, table.table_name)


async def rotate_hook(uow, *, pod_id: UUID, name: str, ctx: Context) -> IssuedHook:
    """Give the hook a new secret. The old address stops working at once."""
    hook, table = await _changeable_hook(uow, pod_id, name, ctx)
    secret = new_secret()
    hook.secret_hash = secret_hash(secret)
    await uow.session.flush()
    logger.info(
        "datastore.hooks.rotated",
        pod_id=str(pod_id),
        hook_id=str(hook.id),
        user_id=str(ctx.user_id),
    )
    return IssuedHook(hook=_view(hook, table.table_name), secret=secret)


async def update_hook(
    uow,
    *,
    pod_id: UUID,
    name: str,
    ctx: Context,
    enabled: bool | None = None,
    fields: dict[str, str] | None = None,
    set_fields: bool = False,
) -> HookView:
    """Switch a hook on or off, or change what a delivery becomes.

    ``set_fields`` says ``fields`` was sent at all, since ``None`` is itself a
    mapping: the whole body to ``payload``.
    """
    hook, table = await _changeable_hook(uow, pod_id, name, ctx)
    if set_fields:
        problem = fields_problem(
            _schemas(table), table.primary_key_column, HookFields(fields)
        )
        if problem:
            raise HookProblem(problem)
        hook.fields = dict(fields) if fields is not None else None
    if enabled is not None:
        hook.enabled = enabled
    await uow.session.flush()
    logger.info(
        "datastore.hooks.updated",
        pod_id=str(pod_id),
        hook_id=str(hook.id),
        user_id=str(ctx.user_id),
        enabled=hook.enabled,
    )
    return _view(hook, table.table_name)


async def delete_hook(uow, *, pod_id: UUID, name: str, ctx: Context) -> None:
    hook, _ = await _changeable_hook(uow, pod_id, name, ctx)
    await uow.session.execute(
        delete(DatastoreHookModel).where(DatastoreHookModel.id == hook.id)
    )
    logger.info(
        "datastore.hooks.deleted",
        pod_id=str(pod_id),
        hook_id=str(hook.id),
        user_id=str(ctx.user_id),
    )


# --- Deliveries ------------------------------------------------------------


async def admit_delivery(
    uow_factory: UnitOfWorkFactory,
    *,
    hook_id: UUID,
    key: str,
    content_type: str | None,
    raw_body: bytes,
    limiter: HookLimiter | None = None,
) -> dict[str, object] | None:
    """The delivery's body, once its address has proved itself.

    ``None`` when it has not -- no such hook, or the wrong secret -- and the
    caller says nothing more than that. After that every refusal raises
    :class:`HookDeliveryRefused` and is kept on the hook.
    """
    async with uow_factory() as uow:
        stored = (
            await uow.session.execute(
                select(
                    DatastoreHookModel.secret_hash, DatastoreHookModel.enabled
                ).where(DatastoreHookModel.id == hook_id)
            )
        ).first()
    if not secret_matches(key, stored[0] if stored else UNKNOWN_HOOK_HASH):
        return None
    if stored is None:
        return None
    try:
        if not stored[1]:
            raise HookDeliveryRefused(403, _TURNED_OFF)
        allowance = await (limiter or HookLimiter()).allow(hook_id)
        if allowance is HookAllowance.LIMITED:
            raise HookDeliveryRefused(
                429, "This hook is taking deliveries too fast. Try again shortly"
            )
        if allowance is HookAllowance.UNAVAILABLE:
            raise HookDeliveryRefused(
                503, "This hook can't take deliveries right now. Try again shortly"
            )
        problem = content_type_problem(content_type)
        if problem:
            raise HookDeliveryRefused(415, problem)
        cap = hook_settings.datastore_hook_body_max_bytes
        if len(raw_body) > cap:
            raise HookDeliveryRefused(
                413, f"A delivery to a hook is at most {cap // 1024} KB"
            )
        return parse_body(raw_body)
    except HookDeliveryRefused as refused:
        await _record_refusal(uow_factory, hook_id, refused.reason)
        raise


async def keep_delivery(
    uow_factory: UnitOfWorkFactory, *, hook_id: UUID, body: dict[str, object]
) -> HookReceipt:
    """Add the row a delivery makes, as the member who made the hook."""
    try:
        return await _keep(uow_factory, hook_id, body)
    except HookDeliveryRefused as refused:
        await _record_refusal(uow_factory, hook_id, refused.reason)
        raise


async def _keep(
    uow_factory: UnitOfWorkFactory, hook_id: UUID, body: dict[str, object]
) -> HookReceipt:
    received_at = datetime.now(UTC)
    async with uow_factory() as uow:
        found = (
            await uow.session.execute(
                select(DatastoreHookModel, DatastoreTable)
                .join(DatastoreTable, DatastoreTable.id == DatastoreHookModel.table_id)
                .where(DatastoreHookModel.id == hook_id)
            )
        ).first()
        if found is None:
            raise HookDeliveryRefused(404, "This hook no longer exists")
        hook, table = found[0], found[1]
        if not hook.enabled:
            raise HookDeliveryRefused(403, _TURNED_OFF)
        if table.enable_rls:
            raise HookDeliveryRefused(409, _CLOSED)
        columns = _schemas(table)
        # The table may have changed since the hook was made: a column dropped,
        # a new one every row needs. Said in the same words making it would.
        problem = fields_problem(
            columns, table.primary_key_column, HookFields(hook.fields)
        )
        if problem:
            raise HookDeliveryRefused(
                409, f"The hook's table has changed since it was made. {problem}"
            )
        row = hook_row(
            columns,
            HookFields(hook.fields),
            body,
            hook_name=hook.name,
            received_at=received_at,
        )
        by_name = {column.name: column for column in columns}
        opened = OpenTable(
            pod_id=hook.pod_id,
            name=table.table_name,
            audience=PublicAudience.ANYONE,
            contact_owned=table.contact_owned,
            columns=tuple(public_column(by_name[name]) for name in row),
        )
        duplicate = False
        try:
            # The record is written, and committed, on the datastore's own
            # session; this one only carries the hook's last delivery.
            await insert_from_outside(
                uow, hook.created_by, opened, row, outside_actor=f"hook:{hook.name}"
            )
        except PublicRowDuplicate:
            duplicate = True
        except PublicRowsClosed as exc:
            raise HookDeliveryRefused(409, _CLOSED) from exc
        except PublicRowRefused as exc:
            raise HookDeliveryRefused(422, exc.message) from exc
        if not duplicate:
            # A row arrived: the hook works, so an older refusal is no longer
            # what its members need to read.
            await uow.session.execute(
                update(DatastoreHookModel)
                .where(DatastoreHookModel.id == hook_id)
                .values(
                    last_received_at=received_at, last_error=None, last_error_at=None
                )
            )
            await uow.commit()
    if duplicate:
        # Answered as received, because a redelivery must not be retried, but
        # no row was added -- and a new submission that repeats a unique value
        # looks the same. Kept where the hook's members read, so neither goes
        # unseen; ``last_received_at`` says only when a row last arrived.
        await _record_refusal(uow_factory, hook_id, _DUPLICATE)
    return HookReceipt(duplicate=duplicate)


async def _record_refusal(
    uow_factory: UnitOfWorkFactory, hook_id: UUID, reason: str
) -> None:
    """Keep ``reason`` as the hook's last error, unless it was just kept.

    Throttled because a sender stuck on one mistake would otherwise write the
    hook's row on every attempt.
    """
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        await uow.session.execute(
            update(DatastoreHookModel)
            .where(
                DatastoreHookModel.id == hook_id,
                or_(
                    DatastoreHookModel.last_error.is_distinct_from(reason),
                    DatastoreHookModel.last_error_at.is_(None),
                    DatastoreHookModel.last_error_at < now - _REFUSAL_REWRITE_AFTER,
                ),
            )
            .values(last_error=reason, last_error_at=now)
        )
        await uow.commit()
    logger.info(
        "datastore.hooks.delivery_refused.observed",
        hook_id=str(hook_id),
        reason=reason,
    )
