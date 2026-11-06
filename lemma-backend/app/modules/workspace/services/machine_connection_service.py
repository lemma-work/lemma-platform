"""What a person does with connected machines over HTTP: approve, list, revoke.

The machine's half of pairing is on its link (``machine_link.session``); this
is the browser's half, plus the code request a machine makes before it has
any credential. See docs/architecture/connected-machines.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from redis.exceptions import RedisError

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.compute_link import (
    COMPUTE_PAIRING_TTL_SECONDS,
)
from app.modules.pod.contracts.members import pod_member_holds
from app.modules.workspace.domain.machine_connection import (
    NAME_PATTERN,
    RESERVED_NAMES,
    MachineConnection,
    InvalidMachineName,
    PodShareRefused,
    PendingPairing,
    default_name,
    generate_pairing_code,
    normalize_pairing_code,
    secret_hash,
)
from app.modules.workspace.infrastructure.machine_connection_repository import (
    MachineConnectionRepository,
)
from app.modules.workspace.services.machine_link.dialer import link_routes
from app.modules.workspace.services.machine_policy import policy_for_user
from app.modules.workspace.services.machine_link.routes import notify_revoked
from app.modules.workspace.services.machine_link.session import pairing_channel

logger = get_logger(__name__)


class MachineConnectionService:
    def __init__(
        self, uow_factory: Callable[[], SqlAlchemyUnitOfWork] | None = None
    ) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)

    # ------------------------------------------------------------ the machine

    async def request_code(
        self, *, claim_key_sha256: str, display_name: str, mode: str, platform: str
    ) -> tuple[str, PendingPairing]:
        """A fresh code for a machine that holds no credential yet.

        The machine sends the hash of its claim key, never the key: the key is
        presented once, on the link, when it claims the approved connection.
        """
        code = generate_pairing_code()
        async with self._uow_factory() as uow:
            pending = await MachineConnectionRepository(uow).create_pairing(
                code_hash=secret_hash(code),
                claim_key_hash=claim_key_sha256,
                display_name=display_name,
                mode=mode,
                platform=platform,
                ttl=timedelta(seconds=COMPUTE_PAIRING_TTL_SECONDS),
            )
            await uow.commit()
        return code, pending

    # ------------------------------------------------------------- the person

    async def pending(self, code: str) -> PendingPairing:
        async with self._uow_factory() as uow:
            return await MachineConnectionRepository(uow).pending_pairing(
                secret_hash(normalize_pairing_code(code))
            )

    async def approve(
        self,
        *,
        user_id: UUID,
        code: str,
        name: str | None,
        pod_id: UUID | None,
    ) -> MachineConnection:
        """Make the connection the code names, owned by ``user_id``.

        The plan's limit is read in the same transaction that counts, and a
        pod share needs an editor of that pod: sharing a machine into a pod
        lets its agents run commands there.
        """
        code_hash = secret_hash(normalize_pairing_code(code))
        async with self._uow_factory() as uow:
            repository = MachineConnectionRepository(uow)
            pending = await repository.pending_pairing(code_hash)
            chosen = (name or default_name(pending.display_name)).strip().lower()
            if not NAME_PATTERN.fullmatch(chosen) or chosen in RESERVED_NAMES:
                raise InvalidMachineName(
                    "A machine's name is lowercase letters, digits and dashes, "
                    "and not `me` or `default`."
                )
            if pod_id is not None and not await pod_member_holds(
                uow, pod_id, user_id, Permissions.POD_UPDATE
            ):
                raise PodShareRefused(
                    "Only an editor or admin of that pod can share a machine into it."
                )
            limit = (await policy_for_user(uow, user_id)).max_connected_machines
            connection = await repository.approve_pairing(
                code_hash=code_hash,
                user_id=user_id,
                name=chosen,
                pod_id=pod_id,
                limit=limit,
            )
            await uow.commit()
        await self._announce_approval(code_hash)
        logger.info(
            "workspace.machine_connection.approved",
            connection_id=str(connection.id),
            user_id=str(user_id),
            mode=connection.mode,
            shared_with_pod=pod_id is not None,
        )
        return connection

    async def _announce_approval(self, code_hash: str) -> None:
        """Wake the machine's waiting ``pair``; it also re-checks on a timer."""
        try:
            channels = await get_channel_service()
            await channels.publish(pairing_channel(code_hash), {"type": "poke"})
        except RedisError, RuntimeError, OSError:
            logger.warning(
                "workspace.machine_connection.approval_notice_skipped.degraded",
                exc_info=True,
            )

    async def list(self, *, user_id: UUID) -> list[MachineConnection]:
        async with self._uow_factory() as uow:
            connections = await MachineConnectionRepository(uow).list_for_user(user_id)
        return [
            replace(item, online=await link_routes.read(item.id) is not None)
            for item in connections
        ]

    async def revoke(self, *, user_id: UUID, connection_id: UUID) -> MachineConnection:
        async with self._uow_factory() as uow:
            revoked = await MachineConnectionRepository(uow).revoke(
                connection_id, user_id=user_id
            )
            await uow.commit()
        await notify_revoked(connection_id)
        logger.info(
            "workspace.machine_connection.revoked",
            connection_id=str(connection_id),
            user_id=str(user_id),
        )
        return revoked
